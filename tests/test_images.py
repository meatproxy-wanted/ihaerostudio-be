import io
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from app.comfy import ComfyFailure, ORIGIN
from app.image_models import Illustration
from app.image_workflows import ALLOWED, PRESET, compile_image
from app.main import create_app
from app.video_models import VideoOutput, VideoPreflight
from app.video_workflows import validate_graph
from test_workflow import action, approved, call, client, content, create


def ready(client):
    d = create(client)
    d = action(client, d, "structure/analyze")
    return action(client, d, "structure/confirm", {"confirmed": True, "note": "가상 자료 확인"})


def remote(job_id, status="queued", image=True):
    return {"id": job_id, "status": status, "outputs": [{
        "id": "image-asset", "name": "illustration.png", "type": "image", "node_id": "9",
        "content_type": "image/png", "size_bytes": 128, "url": "https://cloud.comfy.org/asset.png",
        "url_expires_at": "2099-01-01T00:00:00Z"}] if status == "succeeded" and image else [],
        "progress": {"value": 1 if status == "succeeded" else 0.1},
        "urls": {"self": f"/api/v2/jobs/{job_id}", "cancel": f"/api/v2/jobs/{job_id}/cancel"}}


@pytest.fixture
def images(client, monkeypatch):
    service = client.app.state.images
    client.app.state.config.comfy_api_key = "test-secret-never-return"
    submissions = []
    monkeypatch.setattr(service.comfy, "preflight", lambda graph, **kwargs:
                        VideoPreflight(compatible=True, preset=PRESET, node_count=len(graph), issues=[]))
    def request(method, url, **kwargs):
        if method == "POST":
            submissions.append(kwargs)
            return remote(kwargs["key"])
        if "/api/v2/assets/" in url:
            return remote("unused", "succeeded")["outputs"][0]
        return remote(url.rsplit("/", 1)[-1], "succeeded")
    monkeypatch.setattr(service.comfy, "request", request)
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(out, format="PNG")
    monkeypatch.setattr(service, "download", lambda output: (out.getvalue(), 32, 32))
    return service, submissions


def generate(client, doc=None, **options):
    return action(client, doc or ready(client), "draft", {"generate_images": True, "confirm_image_cost": True, **options})


def jobs(client, doc):
    return call(client, "get", f'/api/v1/documents/{doc["id"]}/image-jobs')


def refresh(client, job):
    return call(client, "post", f'/api/v1/image-jobs/{job["id"]}/refresh')


def test_combined_draft_attaches_all_and_invalidates_review(client, images):
    service, submitted = images
    doc = generate(client)
    assert len(doc["image_job_ids"]) == len(doc["blocks"]) == len(submitted) == 7
    assert all(b["picture"] is None for b in doc["blocks"])
    doc = approved(client, doc)
    for job in jobs(client, doc):
        result = refresh(client, job)
        assert result["status"] == "attached" and result["asset_id"]
        assert refresh(client, result)["version"] == result["version"]
    current = client.get(f'/api/v1/documents/{doc["id"]}').json()
    assert current["review"] is None and current["version"] == doc["version"] + 7
    assert all(b["picture"]["meaning"] == "neutral" for b in current["blocks"])
    for block in current["blocks"]:
        assert service.store.asset(doc["id"], block["picture"]["asset_id"]).startswith(b"\x89PNG")
    assert len(submitted) == 7


def test_cost_key_settings_and_limit_guards(client, images, monkeypatch):
    service, submitted = images
    doc = ready(client)
    action(client, doc, "draft", {"generate_images": True}, status=422)
    service.comfy.config.comfy_api_key = ""
    action(client, doc, "draft", {"generate_images": True, "confirm_image_cost": True}, status=503)
    service.comfy.config.comfy_api_key = "fake"
    action(client, doc, "draft", {"generate_images": True, "confirm_image_cost": True, "max_images": 1}, status=422)
    assert jobs(client, doc) == [] and submitted == []
    assert client.get(f'/api/v1/documents/{doc["id"]}').json()["version"] == doc["version"]
    saved = service.store.get(doc["id"], "alice")
    saved.settings.use_images = False
    service.store.save(saved, "alice", saved.version, "test_settings")
    action(client, saved.model_dump(), "draft", {"generate_images": True, "confirm_image_cost": True}, status=422)


def test_unsupported_cloud_preflight_no_draft_or_submission(client, images, monkeypatch):
    service, submitted = images
    monkeypatch.setattr(service.comfy, "preflight", lambda *a, **kw: VideoPreflight(compatible=False, preset=PRESET, node_count=9, issues=["missing model"]))
    doc = ready(client)
    action(client, doc, "draft", {"generate_images": True, "confirm_image_cost": True}, status=422)
    assert submitted == [] and jobs(client, doc) == []


def test_edited_card_never_overwritten_other_cards_attach(client, images):
    doc = generate(client)
    original_jobs = jobs(client, doc)
    block = doc["blocks"][0]
    updated = {**content(block), "text": block["text"] + " (제작자 편집)"}
    doc = action(client, doc, f'blocks/{block["id"]}', {"content": updated}, method="put")
    result = refresh(client, original_jobs[0])
    assert result["status"] == "stale" and result["asset_id"]
    assert refresh(client, original_jobs[1])["status"] == "attached"
    current = client.get(f'/api/v1/documents/{doc["id"]}').json()
    assert current["blocks"][0]["text"] == updated["text"] and current["blocks"][0]["picture"] is None


def test_ownership_and_private_job_fields(client, images):
    doc = generate(client)
    job = jobs(client, doc)[0]
    headers = {"Authorization": "Bearer bob-key"}
    assert client.get(f'/api/v1/documents/{doc["id"]}/image-jobs', headers=headers).status_code == 404
    for suffix in ("", "/refresh", "/retry", "/reconcile"):
        method = client.get if not suffix else client.post
        kwargs = {} if not suffix else {"json": {"expected_job_version": job["version"], **(
            {"confirmed": True} if suffix == "/retry" else {"provider_job_id": "abc"} if suffix == "/reconcile" else {})}}
        assert method(f'/api/v1/image-jobs/{job["id"]}{suffix}', headers=headers, **kwargs).status_code == 404
    assert "test-secret" not in str(job)


def test_single_failed_image_retry_is_idempotent(client, images, monkeypatch):
    service, submitted = images
    doc = generate(client)
    job = service.get(doc["image_job_ids"][0], "alice")
    job.status = "failed"
    service.save(job, "alice")
    body = {"expected_job_version": job.version, "confirmed": True}
    first = call(client, "post", f'/api/v1/image-jobs/{job.id}/retry', body, 202)
    again = call(client, "post", f'/api/v1/image-jobs/{job.id}/retry', body, 202)
    assert first["id"] == again["id"] and len(submitted) == 8
    assert refresh(client, again)["status"] == "attached"
    assert len(jobs(client, doc)) == 8


def test_submission_unknown_no_paid_retry_and_reconcile(client, images, monkeypatch):
    service, submitted = images
    def unknown(*a, **kw):
        raise ComfyFailure("timeout", uncertain=True)
    monkeypatch.setattr(service.comfy, "submit", unknown)
    doc = generate(client)
    job = jobs(client, doc)[0]
    assert job["status"] == "submission_unknown"
    assert refresh(client, job)["status"] == "submission_unknown"
    call(client, "post", f'/api/v1/image-jobs/{job["id"]}/retry', {"expected_job_version": job["version"], "confirmed": True}, 409)
    def reconciled(method, url, **kw):
        if "/assets/" in url:
            return remote("recovered", "succeeded")["outputs"][0]
        return {"format": "api", "workflow": job["workflow"]} if url.endswith("/workflow") else remote("recovered", "succeeded")
    monkeypatch.setattr(service.comfy, "request", reconciled)
    result = call(client, "post", f'/api/v1/image-jobs/{job["id"]}/reconcile', {"expected_job_version": job["version"], "provider_job_id": "recovered"})
    assert result["provider_job_id"] == "recovered"
    assert refresh(client, result)["status"] == "attached" and submitted == []


def test_download_error_retries_import_not_generation(client, images, monkeypatch):
    service, submitted = images
    doc = generate(client)
    job = jobs(client, doc)[0]
    download = service.download
    def failure(output):
        raise ComfyFailure("image_download_host_not_allowed")
    monkeypatch.setattr(service, "download", failure)
    result = refresh(client, job)
    assert result["status"] == "succeeded" and result["error_code"] == "image_download_host_not_allowed"
    monkeypatch.setattr(service, "download", download)
    assert refresh(client, result)["status"] == "attached" and len(submitted) == 7


def test_parallel_refresh_creates_only_one_asset_and_version(client, images):
    service, submitted = images
    doc = generate(client)
    job_id = doc["image_job_ids"][0]
    def run():
        try:
            return service.refresh(job_id, "alice").status
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: run(), range(2)))
    assert "attached" in statuses
    with service.store.connect() as db:
        assert db.execute("SELECT count(*) FROM assets WHERE document_id=?", (doc["id"],)).fetchone()[0] == 1
    assert service.store.get(doc["id"], "alice").version == doc["version"] + 1


def test_pending_survives_restart_and_stale_draft_skips_paid_submit(client, images, monkeypatch):
    service, submitted = images
    monkeypatch.setattr(service, "start_many", lambda *args: None)
    doc = generate(client)
    assert jobs(client, doc)[0]["status"] == "pending"
    restarted = create_app(client.app.state.config).state.images
    assert restarted.get(doc["image_job_ids"][0], "alice").status == "pending"
    assert refresh(client, jobs(client, doc)[0])["status"] == "attached"
    doc = client.get(f'/api/v1/documents/{doc["id"]}').json()
    action(client, doc, "draft", {"replace_existing": True})
    assert refresh(client, jobs(client, doc)[1])["status"] == "stale"
    assert len(submitted) == 1


def test_image_workflow_is_bounded_and_video_allowlist_unchanged():
    illustration = Illustration(prompt="An adult judge explaining an order, no money transfer.", alt_text="판사의 설명")
    graph = compile_image(illustration, 42, "ihaero-test")
    validate_graph(graph, ALLOWED)
    assert graph["7"].inputs["steps"] == 4 and graph["6"].inputs["batch_size"] == 1
    assert illustration.prompt in graph["4"].inputs["t5xxl"]
    with pytest.raises(HTTPException):
        validate_graph(graph)


def test_real_image_preflight_checks_models_and_ports(client, monkeypatch):
    from test_video import catalog
    info = catalog()
    def node(required, output, optional=None):
        return {"input": {"required": required, "optional": optional or {}}, "output": output}
    info.update({
        "UNETLoader": node({"unet_name": [["flux1-schnell.safetensors"]], "weight_dtype": [["default"]]}, ["MODEL"]),
        "DualCLIPLoader": node({"clip_name1": [["clip_l.safetensors"]], "clip_name2": [["t5xxl_fp16.safetensors"]], "type": [["flux"]]}, ["CLIP"], {"device": [["default"]]}),
        "VAELoader": node({"vae_name": [["ae.safetensors"]]}, ["VAE"]),
        "CLIPTextEncodeFlux": node({"clip": ["CLIP"], "clip_l": ["STRING"], "t5xxl": ["STRING"], "guidance": ["FLOAT"]}, ["CONDITIONING"]),
        "ConditioningZeroOut": node({"conditioning": ["CONDITIONING"]}, ["CONDITIONING"]),
        "EmptySD3LatentImage": node({"width": ["INT"], "height": ["INT"], "batch_size": ["INT"]}, ["LATENT"]),
        "SaveImage": node({"images": ["IMAGE"], "filename_prefix": ["STRING"]}, []),
    })
    info["KSampler"]["input"]["required"]["sampler_name"] = [["euler"]]
    monkeypatch.setattr(client.app.state.comfy, "request", lambda *a, **kw: info)
    graph = compile_image(Illustration(prompt="An anonymous adult judge.", alt_text="판사"), 1, "test")
    assert client.app.state.comfy.preflight(graph, allowed=ALLOWED, preset=PRESET).compatible
    info["UNETLoader"]["input"]["required"]["unet_name"] = [["other-model"]]
    assert not client.app.state.comfy.preflight(graph, allowed=ALLOWED, preset=PRESET).compatible


def output(url="https://cloud.comfy.org/asset.png"):
    return VideoOutput(asset_id="asset1", name="image.png", content_type="image/png", size_bytes=10, url=url, url_expires_at="2099")


@pytest.mark.parametrize("url", ["http://cloud.comfy.org/image", "https://127.0.0.1/image", "https://localhost/image", "https://169.254.169.254/image", "https://evil.example/image", "https://user:pass@cloud.comfy.org/image"])
def test_download_disallows_untrusted_hosts(client, url):
    with pytest.raises(ComfyFailure):
        client.app.state.images.download(output(url))


def test_download_cleaning_no_credentials_and_no_redirect(client, monkeypatch):
    out = io.BytesIO()
    Image.new("RGB", (12, 16), "white").save(out, "PNG")
    seen = []
    real_client = httpx.Client
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=out.getvalue())
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    result = client.app.state.images.download(output())
    assert result[1:] == (12, 16)
    assert "authorization" not in seen[0].headers
    def redirect(request):
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(redirect), **kw))
    with pytest.raises(ComfyFailure):
        client.app.state.images.download(output())


def test_gpt_combines_text_and_illustration_in_one_structured_call(client, monkeypatch):
    doc = ready(client)
    provider = client.app.state.provider
    source = client.app.state.store.get(doc["id"], "alice")
    demo = provider.illustrated_draft(source, 12)
    provider.name = "openai"
    calls = []
    def call(task, payload, schema):
        calls.append((task, payload, schema))
        return schema(blocks=demo)
    monkeypatch.setattr(provider, "call", call)
    result = provider.illustrated_draft(source, 12)
    assert len(calls) == 1 and len(result) == 7
    assert "max_images" in calls[0][1] and calls[0][2].__name__ == "IllustratedDraft"
