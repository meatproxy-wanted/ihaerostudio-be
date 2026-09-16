import io
import json

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from app.studio_generation import StudioGeneration
from app.video_models import VideoPreflight
from test_studio import client, create, draft, path


class PromptProvider:
    name = "openai"
    calls = 0

    def call(self, task, context, schema):
        self.calls += 1
        return schema(prompt="An anonymous adult judge explaining an order; no completed payment.",
                      alt="판사가 명령을 설명하는 모습", meaning="돈을 돌려주라는 법원의 결정")


@pytest.fixture
def setup(client, monkeypatch):
    project = create(client, {"tone": "haeyo", "naming": "initial", "illustrations": "with"})
    document = draft(client, project)
    card = document["sections"][0]["cards"][0]
    service = client.app.state.studio_generation
    service.provider = PromptProvider()
    service.config.comfy_api_key = "private-comfy-test-key"
    service.cloud.preflight = lambda *a, **k: VideoPreflight(compatible=True, preset="test", node_count=9, issues=[])
    png = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(png, "PNG")
    control = {"calls": [], "status": "succeeded", "host": "cloud.comfy.org", "submit_error": None, "on_poll": None}

    def job():
        return {"id": "job-1", "status": control["status"], "progress": None,
                "urls": {"self": "/api/v2/jobs/job-1", "cancel": "/api/v2/jobs/job-1/cancel"},
                "outputs": [{"id": "asset-1", "name": "image.png", "type": "image", "content_type": "",
                             "size_bytes": 0, "url": "https://cloud.comfy.org/image.png", "url_expires_at": "2026-09-15T00:00:00Z"}]}

    def handler(request):
        control["calls"].append(request)
        if request.url.path == "/api/v2/jobs" and request.method == "POST":
            if control["submit_error"] == "timeout":
                raise httpx.ReadTimeout("private upstream message", request=request)
            if control["submit_error"]:
                return httpx.Response(control["submit_error"], json={"private": "do not expose"})
            return httpx.Response(201, json=job())
        if request.url.path == "/api/v2/jobs/job-1":
            if callback := control.pop("on_poll", None):
                callback()
            return httpx.Response(200, json=job())
        if request.url.path == "/api/v2/assets/asset-1":
            return httpx.Response(200, json={"id": "asset-1", "job_id": "job-1", "content_type": "",
                "size_bytes": len(png.getvalue()), "url": "https://" + control["host"] + "/image.png?signed=private"})
        if request.url.path == "/image.png":
            assert "Authorization" not in request.headers and "X-API-Key" not in request.headers
            return httpx.Response(200, content=png.getvalue())
        raise AssertionError(str(request.url))

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return client, service, project, document, card, control


def request_image(setup, **kwargs):
    client, _, project, _, card, _ = setup
    return client.post(path(project) + "/assist/images", json={"cardId": card["id"]}, **kwargs)


def submissions(control):
    return [r for r in control["calls"] if r.method == "POST"]


def test_generates_caches_persists_and_saves_fe_image(setup):
    client, service, project, document, card, control = setup
    result = request_image(setup)
    assert result.status_code == 200, result.text
    image = result.json()["candidates"][0]
    assert set(image) == {"src", "alt", "meaning"}
    assert image["src"].startswith("http://127.0.0.1:8100/api/studio/assets/")
    served = client.get(image["src"].split("http://127.0.0.1:8100", 1)[1], headers={"Authorization": ""})
    assert served.status_code == 200 and served.content.startswith(b"\x89PNG")
    assert "private" not in result.text and "signed=" not in result.text
    assert client.get(path(project) + "/document").json() == document  # No implicit attachment or approval.
    assert request_image(setup).json() == result.json()
    restarted = StudioGeneration(service.store, service.config, service.provider)
    assert restarted.candidates(project["id"], "alice", card["id"]) == result.json()
    assert service.provider.calls == len(submissions(control)) == 1
    posted = json.loads(submissions(control)[0].content)
    assert len(posted["workflow"]) == 9 and posted["workflow"]["6"]["inputs"]["batch_size"] == 1
    assert submissions(control)[0].headers["Idempotency-Key"]
    document["images"] = [{"id": "fe-generated-image", **image, "source": "library"}]
    card["imageId"] = "fe-generated-image"
    saved = client.put(path(project) + "/document", json=document)
    assert saved.status_code == 200, saved.text
    assert saved.json()["document"]["images"][0]["src"] == image["src"]
    other = create(client)
    other_doc = draft(client, other)
    other_doc["images"] = document["images"]
    assert client.put(path(other) + "/document", json=other_doc).status_code == 422


def test_authorization_happens_before_paid_calls(setup):
    _, service, _, _, _, control = setup
    assert request_image(setup, headers={"Authorization": "Bearer other-test-token"}).status_code == 404
    assert request_image(setup, headers={"Authorization": ""}).status_code == 401
    assert service.provider.calls == 0 and not control["calls"]


def test_pending_job_resumes_without_second_submission(setup, monkeypatch):
    _, service, project, _, card, control = setup
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running"
    first = request_image(setup)
    assert first.status_code == 503 and first.json()["detail"]["code"] == "image_in_progress"
    control["status"] = "succeeded"
    restarted = StudioGeneration(service.store, service.config, service.provider)
    assert len(restarted.candidates(project["id"], "alice", card["id"])["candidates"]) == 1
    assert service.provider.calls == len(submissions(control)) == 1


def test_uncertain_submission_never_resubmits(setup):
    _, service, _, _, _, control = setup
    control["submit_error"] = "timeout"
    assert request_image(setup).status_code == 503
    response = request_image(setup)
    assert response.json()["detail"]["code"] == "image_submission_unknown"
    assert "private" not in response.text
    assert service.provider.calls == len(submissions(control)) == 1


def test_definitive_rejection_can_retry_same_prepared_job(setup):
    _, service, _, _, _, control = setup
    control["submit_error"] = 402
    assert request_image(setup).json()["detail"]["code"] == "comfy_insufficient_credits"
    control["submit_error"] = None
    assert request_image(setup).status_code == 200
    assert service.provider.calls == 1
    assert len(submissions(control)) == 2
    assert len({r.headers["Idempotency-Key"] for r in submissions(control)}) == 1


def test_candidate_keeps_concurrent_autosave_and_prevents_duplicate_calls(setup):
    client, service, project, document, _, control = setup
    def concurrent():
        assert request_image(setup).json()["detail"]["code"] == "image_in_progress"
        document["title"] = "생성 중 사용자가 바꾼 제목"
        assert client.put(path(project) + "/document", json=document).status_code == 200
    control["on_poll"] = concurrent
    assert request_image(setup).status_code == 200
    latest = client.get(path(project) + "/document").json()
    assert latest["title"] == document["title"] and latest["saveRevision"] == 1
    assert len(submissions(control)) == 1


def test_changed_card_does_not_receive_stale_candidate(setup):
    client, _, project, document, card, control = setup
    def edit():
        card["sentences"][0]["text"] = "생성 중 바뀐 카드 내용이에요."
        assert client.put(path(project) + "/document", json=document).status_code == 200
    control["on_poll"] = edit
    response = request_image(setup)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "image_card_changed"
    assert client.app.state.studio.get(project["id"], "alice")[0]["assets"] == []


def test_untrusted_asset_host_is_not_contacted_and_can_resume(setup):
    _, service, _, _, _, control = setup
    control["host"] = "attacker.invalid"
    assert request_image(setup).json()["detail"]["code"] == "comfy_asset_host_not_allowed"
    assert all(r.url.host != "attacker.invalid" for r in control["calls"])
    control["host"] = "cloud.comfy.org"
    assert request_image(setup).status_code == 200
    assert len(submissions(control)) == 1


def test_prompt_failure_does_not_submit_to_comfy(setup):
    _, service, _, _, _, control = setup
    def reject(*a):
        raise HTTPException(502, {"code": "ai_provider_error", "message": "AI 오류"})
    service.provider.call = reject
    assert request_image(setup).status_code == 502
    assert not control["calls"]
