import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.comfy import ComfyCloud, ComfyFailure, ORIGIN, job_link
from app.config import Config
from app.main import create_app
from app.video_models import ScenePlanResult, VideoScene
from app.video_workflows import compile_scene, validate_graph
from test_workflow import action, approved, client, content, draft


def plan_for(client, reviewed=True, count=1, **options):
    doc = draft(client)
    if reviewed:
        doc = approved(client, doc)
    # Start with the decision, not multiple background cards.
    blocks = list(reversed(doc["blocks"]))[:count]
    r = client.post(f'/api/v1/documents/{doc["id"]}/video-plans', json={
        "expected_version": doc["version"], "block_ids": [b["id"] for b in blocks], **options})
    assert r.status_code == 201, r.text
    return doc, r.json()


def submit_body(plan):
    return {"expected_plan_version": plan["version"], "idempotency_key": str(uuid4()),
            "confirmed": True, "note": "가상 장면, 외부 전송과 생성 비용 확인"}


def job_body(status="queued", outputs=None):
    return {"id": "cloud-job-1", "status": status, "outputs": outputs or [], "progress": {"value": 0.25},
            "expires_at": "2099-01-01T00:00:00Z",
            "urls": {"self": "/api/v2/jobs/cloud-job-1", "cancel": "/api/v2/jobs/cloud-job-1/cancel"}}


def output_body():
    return {"id": "video-asset-1", "type": "video", "node_id": "11", "name": "scene.mp4", "size_bytes": 1024,
            "content_type": "video/mp4", "url": "https://cdn.example.com/signed-scene.mp4",
            "url_expires_at": "2099-01-01T00:00:00Z", "hash": None}


def catalog():
    def node(required, output, optional=None):
        return {"input": {"required": required, "optional": optional or {}}, "output": output}
    return {
        "UNETLoader": node({"unet_name": [["wan2.2_ti2v_5B_fp16.safetensors"]], "weight_dtype": [["default"]]}, ["MODEL"]),
        "CLIPLoader": node({"clip_name": [["umt5_xxl_fp8_e4m3fn_scaled.safetensors"]], "type": [["wan"]]}, ["CLIP"], {"device": [["default"]]}),
        "VAELoader": node({"vae_name": [["wan2.2_vae.safetensors"]]}, ["VAE"]),
        "CLIPTextEncode": node({"text": ["STRING", {}], "clip": ["CLIP"]}, ["CONDITIONING"]),
        "ModelSamplingSD3": node({"model": ["MODEL"], "shift": ["FLOAT", {"min": 0, "max": 100}]}, ["MODEL"]),
        "Wan22ImageToVideoLatent": node({"vae": ["VAE"], "width": ["INT", {"min": 32}], "height": ["INT", {"min": 32}], "length": ["INT", {"min": 1}], "batch_size": ["INT", {"min": 1}]}, ["LATENT"]),
        "KSampler": node({"model": ["MODEL"], "positive": ["CONDITIONING"], "negative": ["CONDITIONING"], "latent_image": ["LATENT"], "seed": ["INT"], "steps": ["INT"], "cfg": ["FLOAT"], "sampler_name": [["uni_pc"]], "scheduler": [["simple"]], "denoise": ["FLOAT"]}, ["LATENT"]),
        "VAEDecode": node({"samples": ["LATENT"], "vae": ["VAE"]}, ["IMAGE"]),
        "CreateVideo": node({"images": ["IMAGE"], "fps": ["FLOAT", {"min": 1, "max": 120}]}, ["VIDEO"]),
        "SaveVideo": node({"video": ["VIDEO"], "filename_prefix": ["STRING"], "format": ["COMFY_DYNAMICCOMBO_V3", {"options": [
            {"key": "auto", "inputs": {"required": {"codec": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "auto", "inputs": {"required": {}}}]}]}}}]}]}, ["VIDEO"]),
    }


@pytest.fixture
def cloud(client, monkeypatch):
    calls = []
    client.app.state.config.comfy_api_key = "never-return-comfy-secret"
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/api/object_info"):
            return catalog()
        if url.endswith("/api/v2/jobs"):
            return job_body()
        if url.endswith("/cancel"):
            return job_body("canceling")
        if "/assets/" in url:
            return {**output_body(), "job_id": "cloud-job-1", "url": "https://cdn.example.com/fresh.mp4"}
        return job_body("succeeded", [output_body()])
    monkeypatch.setattr(client.app.state.comfy, "request", request)
    return calls


def test_dynamic_plan_and_manual_edit(client):
    doc, plan = plan_for(client, count=2, aspect_ratio="9:16")
    assert len(plan["scenes"]) == 2 and plan["planner"] == "demo"
    a, b = plan["scenes"]
    assert a["width"] == 576 and a["height"] == 1024
    assert a["frames"] % 4 == 1
    assert a["workflow"]["4"]["inputs"]["text"] != b["workflow"]["4"]["inputs"]["text"]
    assert a["workflow"]["11"]["inputs"]["format.codec"] == "auto"
    scenes = [s["scene"] for s in plan["scenes"]]
    scenes[0]["duration_seconds"] = 2
    scenes[0]["visual_prompt"] = "A calm illustration of a court order, not a completed payment."
    r = client.put(f'/api/v1/video-plans/{plan["id"]}', json={"expected_version": 1, "scenes": scenes})
    assert r.status_code == 200
    assert r.json()["version"] == 2 and r.json()["scenes"][0]["frames"] == 49
    assert r.json()["scenes"][0]["seed"] == a["seed"]
    assert client.get(f'/api/v1/documents/{doc["id"]}').json() == doc


def test_gpt_scene_plan_contract(client, monkeypatch):
    doc = draft(client)
    block = doc["blocks"][-1]
    provider = client.app.state.provider
    provider.name = "openai"
    def call(task, payload, schema):
        assert schema is ScenePlanResult and payload["scenario"] == "결정을 먼저 보여주세요."
        assert "실행 노드" in task and payload["cards"][0]["id"] == block["id"]
        assert "evidence" not in payload["cards"][0]
        return ScenePlanResult(scenes=[VideoScene(title="법원의 결정", source_block_ids=[block["id"]], narration=block["text"], visual_prompt="A conceptual illustration of a court order.")])
    monkeypatch.setattr(provider, "call", call)
    r = client.post(f'/api/v1/documents/{doc["id"]}/video-plans', json={"expected_version": doc["version"], "block_ids": [block["id"]], "scenario": "결정을 먼저 보여주세요."})
    assert r.status_code == 201 and r.json()["planner"] == "openai"
    assert r.json()["scenes"][0]["workflow"]["4"]["inputs"]["text"].startswith("A conceptual")


def test_scene_reference_validation(client):
    _, plan = plan_for(client, count=2)
    scenes = copy.deepcopy([s["scene"] for s in plan["scenes"]])
    scenes[0]["source_block_ids"] += scenes[1]["source_block_ids"]
    r = client.put(f'/api/v1/video-plans/{plan["id"]}', json={"expected_version": 1, "scenes": scenes})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "mixed_video_claims"
    scenes[0]["source_block_ids"] = ["other-document-block"]
    assert client.put(f'/api/v1/video-plans/{plan["id"]}', json={"expected_version": 1, "scenes": scenes}).status_code == 422
    assert client.get(f'/api/v1/video-plans/{plan["id"]}').json() == plan


def test_preflight_and_missing_model(client, cloud, monkeypatch):
    _, plan = plan_for(client)
    url = f'/api/v1/video-plans/{plan["id"]}/scenes/0/preflight'
    r = client.post(url)
    assert r.status_code == 200 and r.json()["compatible"] is True
    bad = catalog()
    bad["UNETLoader"]["input"]["required"]["unet_name"] = [["another-model"]]
    monkeypatch.setattr(client.app.state.comfy, "request", lambda *a, **kw: bad)
    r = client.post(url)
    assert r.status_code == 200 and not r.json()["compatible"]
    assert client.post(url.replace("preflight", "jobs"), json=submit_body(plan)).status_code == 422
    assert not client.get(f'/api/v1/video-plans/{plan["id"]}/jobs').json()


def test_review_and_key_required(client):
    _, plan = plan_for(client, reviewed=False)
    url = f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs'
    assert client.post(url, json=submit_body(plan)).status_code == 409
    _, plan = plan_for(client)
    r = client.post(f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs', json=submit_body(plan))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "comfy_not_configured"


def test_submission_idempotency_owner_persistence(client, cloud):
    _, plan = plan_for(client)
    body = submit_body(plan)
    url = f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs'
    first = client.post(url, json=body)
    assert first.status_code == 202
    job = first.json()
    assert job["status"] == "queued" and job["progress"] == 0.25
    assert client.post(url, json=body).json() == job
    assert client.post(url, json=submit_body(plan)).status_code == 409
    assert len([c for c in cloud if c[1].endswith("/api/v2/jobs")]) == 1
    assert "never-return" not in first.text
    assert client.get(f'/api/v1/video-jobs/{job["id"]}', headers={"Authorization": "Bearer bob-key"}).status_code == 404
    assert client.get(f'/api/v1/video-plans/{plan["id"]}', headers={"Authorization": "Bearer bob-key"}).status_code == 404
    with TestClient(create_app(client.app.state.config), headers=client.headers) as reopened:
        assert reopened.get(f'/api/v1/video-jobs/{job["id"]}').json() == job
    scenes = [s["scene"] for s in plan["scenes"]]
    assert client.put(f'/api/v1/video-plans/{plan["id"]}', json={"expected_version": 1, "scenes": scenes}).status_code == 409


def test_concurrent_submit_once(client, cloud):
    _, plan = plan_for(client)
    body = submit_body(plan)
    url = f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: client.post(url, json=body), range(2)))
    assert all(r.status_code == 202 for r in results)
    assert len({r.json()["id"] for r in results}) == 1
    assert len([c for c in cloud if c[1].endswith("/api/v2/jobs")]) == 1


@pytest.mark.parametrize("uncertain", [True, False])
def test_submission_failures_are_durable_not_retried(client, cloud, monkeypatch, uncertain):
    _, plan = plan_for(client)
    def broken(*a):
        raise ComfyFailure("safe_error_code", uncertain=uncertain)
    monkeypatch.setattr(client.app.state.comfy, "submit", broken)
    body = submit_body(plan)
    url = f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs'
    r = client.post(url, json=body)
    assert r.status_code == 202
    assert r.json()["status"] == ("submission_unknown" if uncertain else "submission_failed")
    assert client.post(url, json=body).json() == r.json()


def test_stale_document_blocks_paid_request(client, cloud):
    doc, plan = plan_for(client)
    action(client, doc, "settings", {"settings": {"use_images": False}}, method="patch")
    r = client.post(f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs', json=submit_body(plan))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_video_plan"
    assert not cloud


def test_document_race_during_preflight(client, cloud, monkeypatch):
    doc, plan = plan_for(client)
    original = client.app.state.comfy.preflight
    def racing(graph):
        result = original(graph)
        action(client, doc, "settings", {"settings": {"use_images": False}}, method="patch")
        return result
    monkeypatch.setattr(client.app.state.comfy, "preflight", racing)
    r = client.post(f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs', json=submit_body(plan))
    assert r.status_code == 409
    assert not [c for c in cloud if c[1].endswith("/api/v2/jobs")]


def test_poll_outputs_refresh_url_cancel(client, cloud):
    _, plan = plan_for(client)
    job = client.post(f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs', json=submit_body(plan)).json()
    base = f'/api/v1/video-jobs/{job["id"]}'
    assert client.post(base + "/cancel").json()["status"] == "canceling"
    r = client.post(base + "/refresh")
    assert r.status_code == 200 and r.json()["status"] == "succeeded"
    assert r.json()["outputs"][0]["url"].endswith("signed-scene.mp4")
    fresh = client.post(base + "/outputs/video-asset-1/refresh")
    assert fresh.status_code == 200 and fresh.json()["url"].endswith("fresh.mp4")
    assert client.post(base + "/outputs/other-asset/refresh").status_code == 404
    calls = len(cloud)
    assert client.post(base + "/cancel").status_code == 200 and len(cloud) == calls


def test_reconcile_requires_identical_workflow(client, cloud, monkeypatch):
    _, plan = plan_for(client)
    monkeypatch.setattr(client.app.state.comfy, "submit", lambda *a: (_ for _ in ()).throw(ComfyFailure("unknown", True)))
    job = client.post(f'/api/v1/video-plans/{plan["id"]}/scenes/0/jobs', json=submit_body(plan)).json()
    url = f'/api/v1/video-jobs/{job["id"]}/reconcile'
    monkeypatch.setattr(client.app.state.comfy, "request", lambda *a, **kw: {"format": "api", "workflow": {}})
    assert client.post(url, json={"provider_job_id": "cloud-job-1"}).status_code == 409
    def remote(method, target, **kwargs):
        if target.endswith("/workflow"):
            return {"format": "api", "workflow": plan["scenes"][0]["workflow"]}
        return job_body()
    monkeypatch.setattr(client.app.state.comfy, "request", remote)
    r = client.post(url, json={"provider_job_id": "cloud-job-1"})
    assert r.status_code == 200 and r.json()["provider_job_id"] == "cloud-job-1"


@pytest.mark.parametrize("status,code,uncertain", [(401, "comfy_auth_error", False), (402, "comfy_insufficient_credits", False), (429, "comfy_rate_limited", False), (500, "comfy_upstream_error", True), (422, "comfy_submission_unknown", True)])
def test_comfy_http_contract_errors(monkeypatch, status, code, uncertain):
    captured = []
    real_client = httpx.Client
    def handler(request):
        captured.append(request)
        return httpx.Response(status, json={"error": {"code": "idempotency_key_reuse", "message": "secret-never-return"}})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    cloud = ComfyCloud(Config(comfy_api_key="test-comfy-key"))
    with pytest.raises(ComfyFailure) as error:
        cloud.request("POST", ORIGIN + "/api/v2/jobs", data={"workflow": {}}, key="idempotent-key")
    assert error.value.code == code and error.value.uncertain is uncertain
    assert "secret-never-return" not in str(error.value)
    assert captured[0].headers["Authorization"] == "Bearer test-comfy-key"
    assert captured[0].headers["Idempotency-Key"] == "idempotent-key"
    assert len(captured) == 1


def test_untrusted_provider_links_rejected():
    cloud = ComfyCloud(Config())
    body = job_body()
    body["urls"]["self"] = "https://evil.example/steal-key"
    with pytest.raises(ComfyFailure):
        cloud.parse_job(body)
    with pytest.raises(ComfyFailure):
        job_link("//evil.example/api/v2/jobs/cloud-job-1", "cloud-job-1")
    body = job_body("succeeded", [output_body()])
    body["outputs"][0]["url"] = "javascript:alert(1)"
    with pytest.raises(ComfyFailure):
        cloud.parse_job(body)


def test_graph_rejects_cycles_and_unknown_nodes():
    scene = VideoScene(title="장면", narration="결정", source_block_ids=["block"], visual_prompt="court")
    graph = compile_scene(scene, "16:9", 1, "safe-prefix").workflow
    graph["1"].inputs["cycle"] = ["11", 0]
    with pytest.raises(HTTPException):
        validate_graph(graph)
    graph["1"].class_type = "ExecuteShell"
    with pytest.raises(HTTPException):
        validate_graph(graph)


def test_video_swagger(client):
    schema = client.get("/openapi.json").json()
    path = schema["paths"]["/api/v1/video-plans/{plan_id}/scenes/{scene_index}/jobs"]["post"]
    assert path["security"] and path["responses"]["202"]
    assert "VideoPlan" in schema["components"]["schemas"]
