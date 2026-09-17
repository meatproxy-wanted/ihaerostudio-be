import io
import json
import base64
import copy

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

    def call(self, task, context, schema, *, images=()):
        if schema.__name__ == "PortraitComposition":
            self.portrait_validation_calls = getattr(self, "portrait_validation_calls", 0) + 1
            assert len(images) == 1 and images[0].startswith(b"\x89PNG")
            if getattr(self, "portrait_inspection_failed", False):
                raise HTTPException(502, {"code": "ai_unavailable", "message": "이미지 분석 실패"})
            return schema(personCount=getattr(self, "portrait_person_count", 1),
                          plainWhiteBackground=getattr(self, "portrait_white_background", True))
        if schema.__name__ == "CharacterAppearance":
            self.profile_calls = getattr(self, "profile_calls", 0) + 1
            assert len(images) == 1 and images[0].startswith(b"\x89PNG")
            return schema(personCount=1, faceVisible=True, face="oval face", hair="short brown hair",
                facialHair="none", facialHairDescription="clean-shaven", upperClothing="gray shirt",
                lowerClothing="brown trousers", shoes="black sneakers", accessories="none", style="flat illustration")
        if schema.__name__ == "SceneIdentity":
            self.check_calls = getattr(self, "check_calls", 0) + 1
            assert len(images) == 1
            ok = getattr(self, "identity_ok", True)
            return schema(characters=[{"referenceImageNumber": p["imageNumber"], "faceVisible": True,
                "facialHair": "none" if ok else "beard", "faceAndHairMatch": True,
                "upperClothingMatch": True, "lowerClothingMatch": True, "shoesMatch": True, "differences": []} for p in context["appearances"]],
                alt="실제 생성 결과를 확인한 설명")
        self.calls += 1
        self.context, self.task = context, task
        self.plans = getattr(self, "plans", []) + [copy.deepcopy(context)]
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
        if request.url.path == "/api/upload/image" and request.method == "POST":
            count = sum(r.url.path == "/api/upload/image" for r in control["calls"])
            return httpx.Response(201, json={"name": f"{count:064x}.png", "type": "input", "subfolder": ""})
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
    return [r for r in control["calls"] if r.method == "POST" and r.url.path == "/api/v2/jobs"]


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
    assert len(posted["workflow"]) == 13 and posted["workflow"]["6"]["inputs"]["batch_size"] == 1
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


def attach_characters(setup):
    client, _, project, document, _, _ = setup
    portraits = []
    for index, name in enumerate(document["partyNames"]):
        png = io.BytesIO()
        Image.new("RGB", (32, 32), "red" if index == 0 else "green").save(png, "PNG")
        response = client.post(path(project) + "/assist/upload-image",
                               files={"file": ("character.png", png.getvalue(), "image/png")},
                               data={"alt": name["displayName"] + " 기준 그림", "meaning": "등장인물"})
        assert response.status_code == 200, response.text
        image = response.json()["image"]
        document["images"].append(image)
        portraits.append({"id": f"person-{index}", "role": "person", "partyId": name["partyId"], "imageId": image["id"],
                          "sentences": [{"id": f"person-sentence-{index}", "text": name["displayName"] + "의 역할이에요.",
                                         "anchors": [], "origin": "manual", "verified": False}]})
    document["sections"][0]["cards"].extend(portraits)
    response = client.put(path(project) + "/document", json=document)
    assert response.status_code == 200, response.text
    return portraits


def test_saved_portraits_are_uploaded_and_condition_the_same_scene(setup):
    client, service, project, _, _, control = setup
    portraits = attach_characters(setup)
    result = request_image(setup)
    assert result.status_code == 200, result.text
    references = service.provider.context["characterReferences"]
    assert [r["partyId"] for r in references] == [p["partyId"] for p in portraits]
    assert [r["imageNumber"] for r in references] == [1, 2]
    assert all(c["descriptions"] and c["legalStatus"] for c in service.provider.context["characters"])
    assert "data:image" not in json.dumps(service.provider.context)
    uploads = [r for r in control["calls"] if r.url.path == "/api/upload/image"]
    assert len(uploads) == 2
    assert all(b"\x89PNG" in r.content and r.headers["X-API-Key"] == "private-comfy-test-key" for r in uploads)
    graph = json.loads(submissions(control)[0].content)["workflow"]
    assert graph["1"]["inputs"]["unet_name"] == "qwen_image_edit_2511_fp8mixed.safetensors"
    assert [n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"] == [f"{n:064x}.png" for n in [1, 2]]
    assert graph["4"]["class_type"] == graph["5"]["class_type"] == "TextEncodeQwenImageEditPlus"
    assert graph["4"]["inputs"]["image1"] == graph["5"]["inputs"]["image1"]
    assert graph["4"]["inputs"]["image2"] == graph["5"]["inputs"]["image2"]
    assert request_image(setup).json() == result.json()
    assert len(submissions(control)) == 1 and len([r for r in control["calls"] if r.url.path == "/api/upload/image"]) == 2
    # Changing the saved identity information invalidates the dependent scene cache.
    latest = client.get(path(project) + "/document").json()
    latest["sections"][0]["cards"][-1]["sentences"][0]["text"] = "보증금을 돌려줘야 하는 사람이에요."
    assert client.put(path(project) + "/document", json=latest).status_code == 200
    assert request_image(setup).status_code == 200
    assert len(submissions(control)) == 2
    assert service.provider.profile_calls == 2  # Saved image profiles survive a scene edit.


def test_replaced_portrait_during_generation_rejects_stale_scene(setup):
    client, _, project, _, _, control = setup
    attach_characters(setup)
    def replace():
        latest = client.get(path(project) + "/document").json()
        latest["sections"][0]["cards"][-1]["imageId"] = None
        assert client.put(path(project) + "/document", json=latest).status_code == 200
    control["on_poll"] = replace
    response = request_image(setup)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "image_card_changed"
    assert len(client.app.state.studio.get(project["id"], "alice")[0]["assets"]) == 2  # Only portrait uploads.


def test_portrait_generation_does_not_copy_other_characters(setup):
    client, service, project, _, _, control = setup
    portraits = attach_characters(setup)
    response = client.post(path(project) + "/assist/images", json={"cardId": portraits[0]["id"]})
    graph = json.loads(submissions(control)[0].content)["workflow"]
    assert graph["1"]["inputs"]["unet_name"] == "qwen_image_2512_fp8_e4m3fn.safetensors"
    assert graph["6"]["inputs"] == {"width": 1328, "height": 1328, "batch_size": 1}
    assert graph["7"]["inputs"]["steps"] == 20
    assert response.status_code == 200, response.text
    assert service.provider.context["characterReferences"] == []
    assert [c["partyId"] for c in service.provider.context["characters"]] == [portraits[0]["partyId"]]
    contrasts = service.provider.context["existingCharacterAppearances"]
    assert [p["partyId"] for p in contrasts] == [portraits[1]["partyId"]]
    assert contrasts[0]["hair"] == "short brown hair" and contrasts[0]["upperClothing"] == "gray shirt"
    assert "imageNumber" not in contrasts[0]
    assert "적어도 두 가지" in service.provider.task
    assert service.provider.profile_calls == 1
    assert not [r for r in control["calls"] if r.url.path == "/api/upload/image"]
    latest = client.get(path(project) + "/document").json()
    latest["images"].append({"id": "selected-new-portrait", **response.json()["candidates"][0], "source": "library"})
    next(c for c in latest["sections"][0]["cards"] if c["id"] == portraits[0]["id"])["imageId"] = "selected-new-portrait"
    assert client.put(path(project) + "/document", json=latest).status_code == 200
    assert client.post(path(project) + "/assist/images", json={"cardId": portraits[0]["id"]}).json() == response.json()
    assert len(submissions(control)) == 1
    assert service.provider.portrait_validation_calls == 1
    assert service.provider.profile_calls == 1


def test_reference_must_belong_to_the_same_project_and_owner(setup):
    _, service, project, _, _, control = setup
    attach_characters(setup)
    with service.store.store.connect() as db:
        db.execute("UPDATE studio_assets SET owner='someone-else' WHERE project_id=?", (project["id"],))
    response = request_image(setup)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "character_reference_invalid"
    assert service.provider.calls == 0 and not control["calls"]


def test_reference_retry_reuses_uploads_plan_and_paid_submission_key(setup):
    _, service, _, _, _, control = setup
    attach_characters(setup)
    control["submit_error"] = 402
    assert request_image(setup).json()["detail"]["code"] == "comfy_insufficient_credits"
    control["submit_error"] = None
    assert request_image(setup).status_code == 200
    assert service.provider.calls == 1
    assert len([r for r in control["calls"] if r.url.path == "/api/upload/image"]) == 2
    assert len({r.headers["Idempotency-Key"] for r in submissions(control)}) == 1


def test_unapplied_candidate_is_not_used_as_a_character_reference(setup):
    client, service, project, _, _, _ = setup
    portraits = attach_characters(setup)
    document = client.get(path(project) + "/document").json()
    for card in document["sections"][0]["cards"]:
        if card["id"] in {p["id"] for p in portraits}:
            card["imageId"] = None
    assert client.put(path(project) + "/document", json=document).status_code == 200
    assert request_image(setup).status_code == 200
    assert service.provider.context["characterReferences"] == []


def test_conflicting_portraits_and_too_many_references_fail_before_spending(setup):
    from app.studio_generation import image_context
    _, service, project, _, target, control = setup
    portraits = attach_characters(setup)
    state = service.store.get(project["id"], "alice")[0]
    duplicate = copy.deepcopy(portraits[0])
    duplicate["id"] = "conflicting-portrait"
    duplicate["imageId"] = portraits[1]["imageId"]
    state["document"]["sections"][0]["cards"].append(duplicate)
    with pytest.raises(HTTPException) as error:
        image_context(state, target["id"])
    assert error.value.detail["code"] == "character_reference_ambiguous"
    state["document"]["sections"][0]["cards"].pop()
    for n in range(5):
        card = copy.deepcopy(portraits[0])
        card.update(id=f"extra-{n}", partyId=f"extra-party-{n}")
        state["document"]["partyNames"].append({"partyId": card["partyId"], "displayName": f"추가 인물 {n}"})
        state["document"]["sections"][0]["cards"].append(card)
    with pytest.raises(HTTPException) as error:
        image_context(state, target["id"])
    assert error.value.detail["code"] == "character_reference_limit"
    assert service.provider.calls == 0 and not control["calls"]


def test_legacy_data_uri_portraits_are_supported_without_url_fetch(setup):
    from app.studio_characters import reference_bytes
    from app.studio_generation import image_context
    _, service, project, _, target, _ = setup
    attach_characters(setup)
    state = service.store.get(project["id"], "alice")[0]
    first = state["assets"][0]
    mime, raw = service.store.asset(first["id"])
    old = first["src"]
    first["src"] = f"data:{mime};base64," + base64.b64encode(raw).decode()
    next(i for i in state["document"]["images"] if i["src"] == old)["src"] = first["src"]
    reference = image_context(state, target["id"])["characterReferences"][0]
    assert reference_bytes(service.store, state, "alice", reference).startswith(b"\x89PNG")


def test_unknown_reference_submission_never_reuploads_or_resubmits(setup):
    _, service, _, _, _, control = setup
    attach_characters(setup)
    control["submit_error"] = "timeout"
    assert request_image(setup).status_code == 503
    assert request_image(setup).json()["detail"]["code"] == "image_submission_unknown"
    assert service.provider.calls == 1 and len(submissions(control)) == 1
    assert len([r for r in control["calls"] if r.url.path == "/api/upload/image"]) == 2


def test_new_combo_catalog_and_uploaded_hash_preflight(setup):
    _, service, _, _, _, _ = setup
    from app.comfy import ComfyCloud
    from app.video_models import WorkflowNode
    cloud = ComfyCloud(service.config)
    cloud.request = lambda *a, **k: {
        "LoadImage": {"input": {"required": {"image": [[], {"image_upload": True}]}}, "output": ["IMAGE"]},
        "Choice": {"input": {"required": {"method": ["COMBO", {"options": ["lanczos"]}]}}, "output": []}}
    content_hash = "a" * 64 + ".png"
    graph = {"1": WorkflowNode(class_type="LoadImage", inputs={"image": content_hash}),
             "2": WorkflowNode(class_type="Choice", inputs={"method": "lanczos"})}
    assert cloud.preflight(graph, allowed={"LoadImage", "Choice"}, uploaded_images=[content_hash]).compatible
    assert not cloud.preflight(graph, allowed={"LoadImage", "Choice"}).compatible
    graph["2"].inputs["method"] = "not-supported"
    assert not cloud.preflight(graph, allowed={"LoadImage", "Choice"}, uploaded_images=[content_hash]).compatible


def test_decision_workflow_keeps_order_separate_from_completed_payment(setup):
    client, _, project, document, card, control = setup
    document["sections"][0]["cards"].remove(card)
    card["role"] = "decision"
    document["sections"][1]["cards"].append(card)
    assert client.put(path(project) + "/document", json=document).status_code == 200
    assert request_image(setup).status_code == 200
    graph = json.loads(submissions(control)[0].content)["workflow"]
    prompt = graph["4"]["inputs"]["text"]
    assert "Mandatory court-decision scene constraint" in prompt and "Keep their hands apart" in prompt


def test_generated_candidate_is_returned_without_identity_check(setup):
    _, service, _, _, _, control = setup
    attach_characters(setup)
    original = service.provider.call
    def no_check(task, payload, schema, **kwargs):
        assert schema.__name__ != "SceneIdentity"
        return original(task, payload, schema, **kwargs)
    service.provider.call = no_check
    response = request_image(setup)
    assert response.status_code == 200 and response.json()["candidates"]
    assert request_image(setup).json() == response.json()
    assert len(submissions(control)) == 1


@pytest.mark.parametrize("attempt", [0, 1])
def test_previously_rejected_job_returns_existing_output_without_paid_retry(setup, attempt):
    _, service, project, _, _, control = setup
    attach_characters(setup)
    download = service.download
    def interrupted(*args):
        raise HTTPException(503, {"code": "download_interrupted"})
    service.download = interrupted
    assert request_image(setup).status_code == 503
    with service.store.store.connect() as db:
        row = db.execute("SELECT id,body FROM studio_image_jobs WHERE project_id=?", (project["id"],)).fetchone()
        job = json.loads(row["body"])
        job.update(status="identity_rejected", identity_attempt=attempt,
                   identity_check={"consistent": False, "issues": ["mismatch"], "correction": "old correction"})
        db.execute("UPDATE studio_image_jobs SET body=? WHERE id=?", (json.dumps(job), row["id"]))
    service.download = download
    response = request_image(setup)
    assert response.status_code == 200 and response.json()["candidates"]
    assert len(submissions(control)) == 1


def test_multiple_people_reference_is_rejected_before_comfy_submission(setup):
    _, service, _, _, _, control = setup
    attach_characters(setup)
    original = service.provider.call
    def hidden_face(task, payload, schema, **kwargs):
        result = original(task, payload, schema, **kwargs)
        return result.model_copy(update={"personCount": 2}) if schema.__name__ == "CharacterAppearance" else result
    service.provider.call = hidden_face
    response = request_image(setup)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "character_reference_unclear"
    assert not submissions(control) and service.provider.calls == 0



def test_non_portrait_emphasizes_situation_without_visible_writing(setup):
    assert request_image(setup).status_code == 200
    prompt = json.loads(submissions(setup[-1])[0].content)["workflow"]["4"]["inputs"]["text"]
    assert "Situation-first composition" in prompt
    assert "Absolutely no visible writing" in prompt
    assert "neutral setting when unspecified" in prompt
