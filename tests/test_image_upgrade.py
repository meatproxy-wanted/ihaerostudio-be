import copy
import json

import pytest

from app.image_workflows import (ALLOWED, ILLUSTRATION_STYLE, STYLE_VERSION, PORTRAIT_ALLOWED, PORTRAIT_PRESET, REFERENCE_ALLOWED,
                                 compile_image, compile_portrait, compile_reference_image)
from app.studio_generation import IllustrationPlan, fingerprint, image_context
from app.studio_scene import SCENE_VERSION
from app.video_workflows import validate_graph
from test_studio_generation import client, setup, request_image, submissions, attach_characters


@pytest.mark.parametrize("with_references", [False, True])
@pytest.mark.parametrize("old_scene_revision", [False, True])
@pytest.mark.parametrize("status", ["prepared", "running", "submission_unknown", "ready"])
def test_scene_upgrade_replans_unsubmitted_but_preserves_paid_512_jobs(setup, monkeypatch, with_references, old_scene_revision, status):
    _, service, project, _, card, control = setup
    if with_references:
        attach_characters(setup)
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = 429 if status == "prepared" else "timeout" if status == "submission_unknown" else None
    assert request_image(setup).status_code == (200 if status == "ready" else 503)
    context = image_context(service.store.get(project["id"], "alice")[0], card["id"])
    old_preset = "qwen-image-edit-2511-cartoon-512-identity-v5" if with_references else "flux2-dev-cartoon-512-illustration-v5"
    if old_scene_revision:
        old_preset = "qwen-image-edit-2511-cartoon-512-action-scene-v6" if with_references else "flux2-dev-cartoon-512-action-scene-v6"
    old_digest = fingerprint(context, old_preset)
    key = "prompt" if with_references else "text"
    with service.store.store.connect() as db:
        job = json.loads(db.execute("SELECT body FROM studio_image_jobs").fetchone()["body"])
        original_id = job["id"]
        job["fingerprint"] = old_digest
        job.pop("scene_revision", None)
        job["plan_style_revision"] = "soft-hand-drawn-cartoon-v2"
        job["plan"] = {"prompt": "Legacy portrait-like scene on a white background.", "alt": "이전 그림", "meaning": "이전 설명"}
        job["workflow"]["4"]["inputs"][key] = "Legacy portrait-like scene on a white background."
        original_workflow = copy.deepcopy(job["workflow"])
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=?,lease_until=0 WHERE id=?",
                   (old_digest, json.dumps(job), original_id))
    control["status"], control["submit_error"] = "succeeded", None
    response = request_image(setup)
    if status == "submission_unknown":
        assert response.status_code == 503 and response.json()["detail"]["code"] == "image_submission_unknown"
    else:
        assert response.status_code == 200, response.text
    updated = status in {"prepared", "ready"}
    assert service.provider.calls == (2 if updated else 1)
    assert len(submissions(control)) == (2 if updated else 1)
    with service.store.store.connect() as db:
        jobs = [json.loads(r["body"]) for r in db.execute("SELECT body FROM studio_image_jobs").fetchall()]
    current = next(j for j in jobs if j["fingerprint"] == fingerprint(context))
    assert (current["id"] != original_id) == (status == "ready")
    if updated:
        assert current["scene_revision"] == SCENE_VERSION
        assert "The requesting person" in current["workflow"]["4"]["inputs"][key]
        assert "Main visible action:" not in current["workflow"]["4"]["inputs"][key]
        assert current["style_revision"] == STYLE_VERSION
        assert current["resolution_revision"] == "768px-v3"
    else:
        assert current["workflow"] == original_workflow


def test_dev_text_generation_and_qwen_edit_receive_ordered_reference_images():
    plan = IllustrationPlan(prompt="Two anonymous adults discussing a request.", alt="대화", meaning="요청")
    graph = compile_image(plan, 42, "test")
    validate_graph(graph, ALLOWED)
    assert graph["1"].inputs["unet_name"] == "flux2_dev_fp8mixed.safetensors"
    assert graph["2"].inputs["clip_name"] == "mistral_3_small_flux2_bf16.safetensors"
    assert graph["3"].inputs["vae_name"] == "full_encoder_small_decoder.safetensors"
    assert graph["9"].inputs == {"steps": 20, "width": 768, "height": 768}
    assert graph["6"].inputs == {"width": 768, "height": 768, "batch_size": 1}
    assert graph["6"].inputs["batch_size"] == 1
    assert graph["5"].inputs["guidance"] == 4.0
    refs = compile_reference_image(plan, 42, "test", ["first.png", "second.png"])
    validate_graph(refs, REFERENCE_ALLOWED)
    assert refs["1"].inputs["unet_name"] == "qwen_image_edit_2511_fp8mixed.safetensors"
    for key in ("4", "5"):
        assert refs[key].class_type == "TextEncodeQwenImageEditPlus"
        assert refs[key].inputs["vae"] == ["3", 0]
        assert refs[key].inputs["image1"] == ["30", 0]
        assert refs[key].inputs["image2"] == ["31", 0]
        assert "image3" not in refs[key].inputs
    assert refs["6"].inputs["pixels"] == ["30", 0]
    assert refs["30"].class_type == refs["31"].class_type == "ImageScaleBy"
    assert refs["30"].inputs == {"image": ["21", 0], "upscale_method": "area", "scale_by": 0.75}
    assert refs["31"].inputs == {"image": ["23", 0], "upscale_method": "area", "scale_by": 0.75}
    assert refs["11"].inputs["steps"] == 50 and refs["11"].inputs["cfg"] == 4.0
    assert [refs[k].inputs["image"] for k in ["20", "22"]] == ["first.png", "second.png"]
    assert not any(n.class_type in {"ReferenceLatent", "ConditioningZeroOut", "LoraLoaderModelOnly"} for n in refs.values())


def test_scene_upgrade_finds_unknown_text_job_with_unrelated_project_portraits(setup, monkeypatch):
    _, service, project, _, card, control = setup
    attach_characters(setup)
    original_context = image_context

    def selected_context(state, card_id, *, select_relevant=True):
        result = original_context(state, card_id, select_relevant=select_relevant)
        if select_relevant:
            result["characterReferences"] = []
            result["characters"] = []
        return result

    monkeypatch.setattr("app.studio_generation.image_context", selected_context)
    control["submit_error"] = "timeout"
    assert request_image(setup).status_code == 503
    context = selected_context(service.store.get(project["id"], "alice")[0], card["id"])
    old_digest = fingerprint(context, "flux2-dev-cartoon-512-illustration-v5")
    with service.store.store.connect() as db:
        job = json.loads(db.execute("SELECT body FROM studio_image_jobs").fetchone()["body"])
        job["fingerprint"] = old_digest
        job.pop("scene_revision", None)
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=?,lease_until=0 WHERE id=?",
                   (old_digest, json.dumps(job), job["id"]))
    assert request_image(setup).json()["detail"]["code"] == "image_submission_unknown"
    assert service.provider.calls == 1 and len(submissions(control)) == 1


def test_reference_comparison_changes_only_steps():
    plan = IllustrationPlan(prompt="Image 1 listens to image 2 in a neutral space.", alt="대화", meaning="상황")
    low = compile_reference_image(plan, 42, "comparison", ["first.png", "second.png"], steps=20)
    medium = compile_reference_image(plan, 42, "comparison", ["first.png", "second.png"], steps=40)
    high = compile_reference_image(plan, 42, "comparison", ["first.png", "second.png"])
    assert low["11"].inputs["steps"] == 20
    assert medium["11"].inputs["steps"] == 40 and high["11"].inputs["steps"] == 50
    low["11"].inputs["steps"] = 50
    medium["11"].inputs["steps"] = 50
    assert low == medium == high
    with pytest.raises(ValueError):
        compile_reference_image(plan, 42, "comparison", ["first.png"], steps=100)


@pytest.mark.parametrize("status", ["prepared", "running", "submission_unknown", "ready"])
@pytest.mark.parametrize("old_steps", [20, 40])
def test_50step_upgrade_preserves_accepted_jobs_and_seed(setup, monkeypatch, status, old_steps):
    _, service, project, _, card, control = setup
    attach_characters(setup)
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = 429 if status == "prepared" else "timeout" if status == "submission_unknown" else None
    assert request_image(setup).status_code == (200 if status == "ready" else 503)
    context = image_context(service.store.get(project["id"], "alice")[0], card["id"])
    old_preset = "qwen-image-edit-2511-animation-512-action-scene-v7" if old_steps == 20 else "qwen-image-edit-2511-animation-512-40steps-action-scene-v8"
    old_digest = fingerprint(context, old_preset)
    with service.store.store.connect() as db:
        job = json.loads(db.execute("SELECT body FROM studio_image_jobs").fetchone()["body"])
        job["fingerprint"] = old_digest
        job["workflow"]["11"]["inputs"]["steps"] = old_steps
        original_id, original_seed = job["id"], job["seed"]
        original_workflow = copy.deepcopy(job["workflow"])
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=?,lease_until=0 WHERE id=?",
                   (old_digest, json.dumps(job), job["id"]))
    control["status"], control["submit_error"] = "succeeded", None
    response = request_image(setup)
    if status == "submission_unknown":
        assert response.status_code == 503 and response.json()["detail"]["code"] == "image_submission_unknown"
    else:
        assert response.status_code == 200, response.text
    assert len(submissions(control)) == (2 if status in {"prepared", "ready"} else 1)
    assert service.provider.calls == (2 if status == "ready" else 1)
    with service.store.store.connect() as db:
        jobs = [json.loads(r["body"]) for r in db.execute("SELECT body FROM studio_image_jobs").fetchall()]
    current = next(j for j in jobs if j["fingerprint"] == fingerprint(context))
    assert current["workflow"]["11"]["inputs"]["steps"] == (50 if status in {"prepared", "ready"} else old_steps)
    assert (current["id"] != original_id) == (status == "ready")
    if status == "prepared":
        assert current["seed"] == original_seed
        original_workflow["11"]["inputs"]["steps"] = 50
        assert current["workflow"] == original_workflow
    elif status in {"running", "submission_unknown"}:
        assert current["workflow"] == original_workflow


def test_initial_portrait_uses_qwen_and_new_cache_key():
    plan = IllustrationPlan(prompt="An anonymous adult with a clearly visible face.", alt="인물", meaning="등장인물")
    graph = compile_portrait(plan, 42, "test")
    validate_graph(graph, PORTRAIT_ALLOWED)
    assert graph["1"].inputs["unet_name"] == "qwen_image_2512_fp8_e4m3fn.safetensors"
    assert graph["2"].inputs == {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}
    assert graph["3"].inputs["vae_name"] == "qwen_image_vae.safetensors"
    assert graph["6"].inputs == {"width": 768, "height": 768, "batch_size": 1}
    assert graph["7"].inputs["steps"] == 20
    assert graph["7"].inputs["cfg"] == 4.0
    assert graph["7"].inputs["model"] == ["10", 0]
    assert graph["10"].inputs == {"model": ["1", 0], "shift": 3.1}
    assert graph["7"].inputs["seed"] == 42
    context = {"role": "person", "characterReferences": []}
    assert PORTRAIT_PRESET == "qwen-image-2512-animation-solo-portrait-768-20steps-v9"
    assert fingerprint(context) != fingerprint(context, "qwen-image-2512-cartoon-solo-portrait-768-20steps-v6")
    assert fingerprint(context) != fingerprint(context, "qwen-image-2512-flat-2d-solo-portrait-768-20steps-v5")
    assert fingerprint(context) != fingerprint(context, "qwen-image-2512-flat-2d-solo-portrait-20steps-v3")
    assert fingerprint(context) != fingerprint(context, "qwen-image-2512-solo-portrait-20steps-v2")
    assert fingerprint(context) != fingerprint(context, "qwen-image-2512-solo-portrait-v1")
    assert fingerprint(context) != fingerprint(context, "flux-schnell-illustration-v2")


def test_portrait_enforces_one_person_and_empty_white_background_in_both_encoders():
    plan = IllustrationPlan(prompt="An adult at a busy office with background figures.", alt="인물", meaning="등장인물")
    graph = compile_portrait(plan, 42, "test")
    inputs = graph["4"].inputs
    prompt = inputs["text"]
    assert "angle both people" not in prompt
    assert graph["5"].class_type == "CLIPTextEncode"
    assert "multiple people" in graph["5"].inputs["text"]
    assert "colored background" in graph["5"].inputs["text"]
    assert "exactly one fictional adult, alone and centered" in prompt
    assert "solid pure white (#FFFFFF), empty background" in prompt
    assert "No other people, background figures" in prompt
    assert "No scenery, furniture, props, icons or background decorations" in prompt
    assert prompt.endswith("These portrait constraints override conflicting scene descriptions.")


def test_all_image_paths_require_visible_faces_not_audience_facing():
    plan = IllustrationPlan(prompt="An adult reading a document with a clearly visible face.", alt="인물", meaning="설명")
    graphs = [compile_portrait(plan, 1, "test"), compile_image(plan, 1, "test"),
              compile_reference_image(plan, 1, "test", ["person.png"])]
    for graph in graphs:
        prompt = graph["4"].inputs.get("text", graph["4"].inputs.get("prompt"))
        assert "eyes, nose and mouth" in prompt
        assert "do not require" in prompt or "not forced eye contact with the viewer" in prompt
        assert "Face the viewer directly" not in prompt
        assert "angle both people" not in prompt
        assert "three-quarter front view" not in prompt


def test_all_image_paths_use_only_simple_animation_or_illustration_style():
    plan = IllustrationPlan(prompt="An adult considering a court order.", alt="인물", meaning="설명")
    graphs = [compile_portrait(plan, 1, "test"), compile_image(plan, 1, "test"),
              compile_reference_image(plan, 1, "test", ["person.png"])]
    for graph in graphs:
        prompt = graph["4"].inputs.get("text", graph["4"].inputs.get("prompt"))
        assert prompt.startswith(ILLUSTRATION_STYLE)
        assert prompt.count(ILLUSTRATION_STYLE.strip()) == 1
        for removed in ("2D", "3D", "2.5D", "shading", "gradient", "photograph", "photorealistic",
                        "skin pores", "hand-drawn", "soft colors", "simple drawn shapes", "calm colors"):
            assert removed not in prompt
    for graph in (graphs[0], graphs[2]):
        negative = graph["5"].inputs.get("text", graph["5"].inputs.get("prompt"))
        for removed in ("2D", "3D", "2.5D", "shading", "gradient", "photograph", "photorealistic", "skin pores"):
            assert removed not in negative
    assert graphs[2]["5"].inputs["prompt"] == ""


@pytest.mark.parametrize("count", [1, 3])
def test_qwen_edit_only_connects_supplied_image_slots(count):
    plan = IllustrationPlan(prompt="Edit image 1 into the requested scene.", alt="장면", meaning="설명")
    graph = compile_reference_image(plan, 7, "test", [f"person-{n}.png" for n in range(count)])
    for key in ("4", "5"):
        image_inputs = {k: v for k, v in graph[key].inputs.items() if k.startswith("image")}
        assert image_inputs == {f"image{n + 1}": [str(30 + n), 0] for n in range(count)}
        for n in range(count):
            assert graph[str(30 + n)].inputs == {"image": [str(21 + n * 2), 0], "upscale_method": "area", "scale_by": 0.75}
    assert f"Picture {count}" in graph["4"].inputs["prompt"]
    assert f"Picture {count + 1}" not in graph["4"].inputs["prompt"]


@pytest.mark.parametrize("count", [0, 4])
def test_qwen_edit_rejects_invalid_reference_counts(count):
    plan = IllustrationPlan(prompt="An educational scene.", alt="장면", meaning="설명")
    with pytest.raises(ValueError):
        compile_reference_image(plan, 7, "test", ["person.png"] * count)


def test_scene_selects_mentioned_character_and_renumbers_references(setup):
    _, service, project, _, card, _ = setup
    portraits = attach_characters(setup)
    state = service.store.get(project["id"], "alice")[0]
    target = state["document"]["sections"][0]["cards"][0]
    target["partyId"] = None
    target["sentences"] = [{**target["sentences"][0], "text": state["document"]["partyNames"][1]["displayName"] + "가 혼자 생각합니다.", "anchors": []}]
    context = image_context(state, card["id"])
    assert [r["partyId"] for r in context["characterReferences"]] == [portraits[1]["partyId"]]
    assert context["characterReferences"][0]["imageNumber"] == 1
    assert len(image_context(state, card["id"], select_relevant=False)["characterReferences"]) == 2
    target["sentences"][0]["text"] = "결정을 확인합니다."
    assert len(image_context(state, card["id"])["characterReferences"]) == 2


def test_four_scene_references_fail_before_paid_calls(setup):
    _, service, project, _, card, control = setup
    portraits = attach_characters(setup)
    state, version = service.store.get(project["id"], "alice")
    target = state["document"]["sections"][0]["cards"][0]
    target["partyId"] = None
    target["sentences"] = [{**target["sentences"][0], "text": "모두 결정을 확인합니다.", "anchors": []}]
    for n in range(2):
        extra = copy.deepcopy(portraits[0])
        extra.update(id=f"extra-{n}", partyId=f"extra-party-{n}")
        state["document"]["partyNames"].append({"partyId": extra["partyId"], "displayName": f"추가 인물 {n}"})
        state["document"]["sections"][0]["cards"].append(extra)
    service.store.save(state, "alice", version)
    response = request_image(setup)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "character_reference_limit"
    assert service.provider.calls == 0 and not control["calls"]


@pytest.mark.parametrize("status", ["running", "submission_unknown", "ready"])
def test_model_upgrade_preserves_paid_work_and_invalidates_only_completed_cache(setup, monkeypatch, status):
    client, service, project, _, card, control = setup
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = "timeout" if status == "submission_unknown" else None
    first = request_image(setup)
    assert first.status_code == (200 if status == "ready" else 503)
    context = image_context(service.store.get(project["id"], "alice")[0], card["id"])
    old_digest = fingerprint(context, "flux-schnell-illustration-v2")
    with service.store.store.connect() as db:
        row = db.execute("SELECT * FROM studio_image_jobs").fetchone()
        job = json.loads(row["body"])
        job["fingerprint"] = old_digest
        # Simulate a persisted pre-upgrade graph: its model must not be recompiled.
        job["workflow"]["1"]["inputs"]["unet_name"] = "flux1-schnell.safetensors"
        original_id = job["id"]
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=?,lease_until=0 WHERE id=?",
                   (old_digest, json.dumps(job), original_id))
    control["status"], control["submit_error"] = "succeeded", None
    second = request_image(setup)
    if status == "submission_unknown":
        assert second.json()["detail"]["code"] == "image_submission_unknown"
    else:
        assert second.status_code == 200, second.text
    assert len(submissions(control)) == (2 if status == "ready" else 1)
    with service.store.store.connect() as db:
        rows = db.execute("SELECT body FROM studio_image_jobs").fetchall()
        current = next(json.loads(r["body"]) for r in rows if json.loads(r["body"])["fingerprint"] == fingerprint(context))
    if status == "ready":
        assert current["id"] != original_id
        assert current["workflow"]["1"]["inputs"]["unet_name"] == "flux2_dev_fp8mixed.safetensors"
    else:
        assert current["id"] == original_id
        assert current["workflow"]["1"]["inputs"]["unet_name"] == "flux1-schnell.safetensors"


@pytest.mark.parametrize("status", ["running", "submission_unknown", "ready"])
def test_qwen_upgrade_preserves_existing_dev_reference_submissions(setup, monkeypatch, status):
    _, service, project, _, card, control = setup
    attach_characters(setup)
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = "timeout" if status == "submission_unknown" else None
    assert request_image(setup).status_code == (200 if status == "ready" else 503)
    context = image_context(service.store.get(project["id"], "alice")[0], card["id"], select_relevant=False)
    old_digest = fingerprint(context, "flux2-dev-identity-reference-v1")
    with service.store.store.connect() as db:
        row = db.execute("SELECT * FROM studio_image_jobs").fetchone()
        job = json.loads(row["body"])
        job["fingerprint"] = old_digest
        job["workflow"]["1"]["inputs"]["unet_name"] = "flux2_dev_fp8mixed.safetensors"
        original_id = job["id"]
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=?,lease_until=0 WHERE id=?",
                   (old_digest, json.dumps(job), original_id))
    control["status"], control["submit_error"] = "succeeded", None
    response = request_image(setup)
    if status == "submission_unknown":
        assert response.json()["detail"]["code"] == "image_submission_unknown"
    else:
        assert response.status_code == 200, response.text
    assert len(submissions(control)) == (2 if status == "ready" else 1)
    with service.store.store.connect() as db:
        rows = db.execute("SELECT body FROM studio_image_jobs").fetchall()
        current = next(json.loads(r["body"]) for r in rows if json.loads(r["body"])["fingerprint"] == fingerprint(context))
    assert (current["id"] == original_id) == (status != "ready")
    assert current["workflow"]["1"]["inputs"]["unet_name"] == (
        "qwen_image_edit_2511_fp8mixed.safetensors" if status == "ready" else "flux2_dev_fp8mixed.safetensors")


@pytest.mark.parametrize("status", ["running", "submission_unknown", "ready", "prepared"])
@pytest.mark.parametrize("old_preset,old_model,old_steps", [
    ("flux-schnell-illustration-v2", "flux1-schnell.safetensors", 4),
    ("qwen-image-2512-cartoon-solo-portrait-512-20steps-v7", "qwen_image_2512_fp8_e4m3fn.safetensors", 20),
    ("qwen-image-2512-solo-portrait-v1", "qwen_image_2512_fp8_e4m3fn.safetensors", 50)])
def test_qwen_portrait_upgrade_preserves_paid_jobs_not_ready_cache(setup, monkeypatch, status, old_preset, old_model, old_steps):
    client, service, project, _, _, control = setup
    portrait = attach_characters(setup)[0]
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = "timeout" if status == "submission_unknown" else 429 if status == "prepared" else None
    def request():
        return client.post(f"/api/studio/projects/{project['id']}/assist/images", json={"cardId": portrait["id"]})
    assert request().status_code == (200 if status == "ready" else 503)
    context = image_context(service.store.get(project["id"], "alice")[0], portrait["id"])
    old_digest = fingerprint(context, old_preset)
    with service.store.store.connect() as db:
        row = db.execute("SELECT * FROM studio_image_jobs").fetchone()
        job = json.loads(row["body"])
        job["fingerprint"] = old_digest
        job["workflow"]["1"]["inputs"]["unet_name"] = old_model
        job["workflow"]["7"]["inputs"]["steps"] = old_steps
        job["plan_style_revision"] = "soft-hand-drawn-cartoon-v2"
        job["plan"]["prompt"] = "A photograph of an adult in a legacy style."
        job["workflow"]["4"]["inputs"]["text"] = "Legacy photograph style"
        job.pop("portrait_composition", None)
        original_id = job["id"]
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=?,lease_until=0 WHERE id=?",
                   (old_digest, json.dumps(job), original_id))
    control["status"], control["submit_error"] = "succeeded", None
    response = request()
    if status == "submission_unknown":
        assert response.json()["detail"]["code"] == "image_submission_unknown"
        assert not getattr(service.provider, "portrait_validation_calls", 0)
    else:
        assert response.status_code == 200, response.text
        assert service.provider.portrait_validation_calls == (2 if status == "ready" else 1)
    assert len(submissions(control)) == (2 if status in {"ready", "prepared"} else 1)
    with service.store.store.connect() as db:
        rows = db.execute("SELECT body FROM studio_image_jobs").fetchall()
        current = next(json.loads(r["body"]) for r in rows if json.loads(r["body"])["fingerprint"] == fingerprint(context))
    assert (current["id"] == original_id) == (status != "ready")
    assert current["workflow"]["1"]["inputs"]["unet_name"] == (
        "qwen_image_2512_fp8_e4m3fn.safetensors" if status in {"ready", "prepared"} else old_model)
    assert current["workflow"]["7"]["inputs"]["steps"] == (20 if status in {"ready", "prepared"} else old_steps)
    prompt = current["workflow"]["4"]["inputs"]["text"]
    assert (ILLUSTRATION_STYLE.strip() in prompt) == (status in {"ready", "prepared"})


@pytest.mark.parametrize("kind,old_preset", [
    ("portrait", "qwen-image-2512-cartoon-solo-portrait-768-20steps-v6"),
    ("portrait", "qwen-image-2512-flat-2d-solo-portrait-768-20steps-v5"),
    ("portrait", "qwen-image-2512-flat-2d-solo-portrait-1024-20steps-v4"),
    ("portrait", "qwen-image-2512-flat-2d-solo-portrait-20steps-v3"),
    ("portrait", "qwen-image-2512-solo-portrait-20steps-v2"),
    ("text", "flux2-dev-illustration-v1"),
    ("text", "flux2-dev-cartoon-768-illustration-v4"),
    ("text", "flux2-dev-flat-2d-768-illustration-v3"),
    ("text", "flux2-dev-flat-2d-illustration-v2"),
    ("reference", "qwen-image-edit-2511-flat-2d-identity-v2"),
    ("reference", "qwen-image-edit-2511-cartoon-768-identity-v4"),
    ("reference", "qwen-image-edit-2511-flat-2d-768-identity-v3"),
    ("reference", "qwen-image-edit-2511-identity-v1")])
@pytest.mark.parametrize("status", ["running", "submission_unknown", "ready", "prepared"])
def test_style_upgrade_preserves_paid_jobs_rebuilds_unsubmitted_only(setup, monkeypatch, kind, old_preset, status):
    client, service, project, _, card, control = setup
    if kind != "text":
        portraits = attach_characters(setup)
        if kind == "portrait":
            card = portraits[0]
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = "timeout" if status == "submission_unknown" else 429 if status == "prepared" else None
    def request():
        return client.post(f"/api/studio/projects/{project['id']}/assist/images", json={"cardId": card["id"]})
    assert request().status_code == (200 if status == "ready" else 503)
    state = service.store.get(project["id"], "alice")[0]
    context = image_context(state, card["id"])
    old_digest = fingerprint(image_context(state, card["id"], select_relevant=False), old_preset)
    with service.store.store.connect() as db:
        job = json.loads(db.execute("SELECT body FROM studio_image_jobs").fetchone()["body"])
        original_id = job["id"]
        job["fingerprint"] = old_digest
        job.pop("style_revision", None)
        job.pop("resolution_revision", None)
        if "768" in old_preset:
            job["resolution_revision"] = "768px-v1"
            job["style_revision"] = "soft-hand-drawn-cartoon-v2" if "cartoon" in old_preset else "strict-flat-2d-v1"
        key = "prompt" if kind == "reference" else "text"
        job["workflow"]["4"]["inputs"][key] = "Legacy dimensional style"
        if kind == "portrait":
            old_size = 768 if "768" in old_preset else 1024 if "1024" in old_preset else 1328
            job["workflow"]["6"]["inputs"].update(width=old_size, height=old_size)
            if "flat-2d" in old_preset:
                # Previous 2D revision; resolution/steps may already match.
                job["style_revision"] = "strict-flat-2d-v1"
        elif kind == "text":
            old_size = 768 if "768" in old_preset else 1024
            job["workflow"]["6"]["inputs"].update(width=old_size, height=old_size)
            job["workflow"]["9"]["inputs"].update(width=old_size, height=old_size)
        elif "768" not in old_preset:
            for index in range(3):
                job["workflow"].pop(str(30 + index), None)
                for encoder in ("4", "5"):
                    slot = f"image{index + 1}"
                    if slot in job["workflow"][encoder]["inputs"]:
                        job["workflow"][encoder]["inputs"][slot] = [str(21 + index * 2), 0]
            job["workflow"]["6"]["inputs"]["pixels"] = ["21", 0]
        else:
            for index in range(3):
                if node := job["workflow"].get(str(30 + index)):
                    node["inputs"]["scale_by"] = 0.75
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=?,lease_until=0 WHERE id=?",
                   (old_digest, json.dumps(job), original_id))
    control["status"], control["submit_error"] = "succeeded", None
    response = request()
    if status == "submission_unknown":
        assert response.json()["detail"]["code"] == "image_submission_unknown"
    else:
        assert response.status_code == 200, response.text
    assert len(submissions(control)) == (2 if status in {"ready", "prepared"} else 1)
    with service.store.store.connect() as db:
        jobs = [json.loads(r["body"]) for r in db.execute("SELECT body FROM studio_image_jobs").fetchall()]
    current = next(j for j in jobs if j["fingerprint"] == fingerprint(context))
    assert (current["id"] != original_id) == (status == "ready")
    prompt = current["workflow"]["4"]["inputs"][key]
    assert (ILLUSTRATION_STYLE.strip() in prompt) == (status in {"ready", "prepared"})
    if kind == "portrait":
        size = 768 if status in {"ready", "prepared"} else old_size
        assert current["workflow"]["6"]["inputs"] == {"width": size, "height": size, "batch_size": 1}
    elif kind == "text":
        size = 768 if status in {"ready", "prepared"} else old_size
        assert current["workflow"]["6"]["inputs"]["width"] == current["workflow"]["9"]["inputs"]["width"] == size
    else:
        assert ("30" in current["workflow"]) == (status in {"ready", "prepared"} or "768" in old_preset)
        if "30" in current["workflow"]:
            assert current["workflow"]["30"]["inputs"]["scale_by"] == 0.75
