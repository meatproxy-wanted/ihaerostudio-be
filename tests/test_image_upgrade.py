import copy
import json

import pytest

from app.image_workflows import (ALLOWED, PORTRAIT_ALLOWED, PORTRAIT_PRESET, REFERENCE_ALLOWED,
                                 compile_image, compile_portrait, compile_reference_image)
from app.studio_generation import IllustrationPlan, fingerprint, image_context
from app.video_workflows import validate_graph
from test_studio_generation import client, setup, request_image, submissions, attach_characters


def test_dev_text_generation_and_qwen_edit_receive_ordered_reference_images():
    plan = IllustrationPlan(prompt="Two anonymous adults discussing a request.", alt="대화", meaning="요청")
    graph = compile_image(plan, 42, "test")
    validate_graph(graph, ALLOWED)
    assert graph["1"].inputs["unet_name"] == "flux2_dev_fp8mixed.safetensors"
    assert graph["2"].inputs["clip_name"] == "mistral_3_small_flux2_bf16.safetensors"
    assert graph["3"].inputs["vae_name"] == "full_encoder_small_decoder.safetensors"
    assert graph["9"].inputs == {"steps": 20, "width": 1024, "height": 1024}
    assert graph["6"].inputs["batch_size"] == 1
    assert graph["5"].inputs["guidance"] == 4.0
    refs = compile_reference_image(plan, 42, "test", ["first.png", "second.png"])
    validate_graph(refs, REFERENCE_ALLOWED)
    assert refs["1"].inputs["unet_name"] == "qwen_image_edit_2511_fp8mixed.safetensors"
    for key in ("4", "5"):
        assert refs[key].class_type == "TextEncodeQwenImageEditPlus"
        assert refs[key].inputs["vae"] == ["3", 0]
        assert refs[key].inputs["image1"] == ["21", 0]
        assert refs[key].inputs["image2"] == ["23", 0]
        assert "image3" not in refs[key].inputs
    assert refs["6"].inputs["pixels"] == ["21", 0]
    assert refs["11"].inputs["steps"] == 20 and refs["11"].inputs["cfg"] == 4.0
    assert [refs[k].inputs["image"] for k in ["20", "22"]] == ["first.png", "second.png"]
    assert not any(n.class_type in {"ReferenceLatent", "ConditioningZeroOut", "LoraLoaderModelOnly"} for n in refs.values())


def test_initial_portrait_retains_original_model_and_cache_key():
    plan = IllustrationPlan(prompt="An anonymous adult facing the viewer.", alt="인물", meaning="등장인물")
    graph = compile_portrait(plan, 42, "test")
    validate_graph(graph, PORTRAIT_ALLOWED)
    assert graph["1"].inputs["unet_name"] == "flux1-schnell.safetensors"
    assert graph["6"].inputs == {"width": 768, "height": 768, "batch_size": 1}
    assert graph["7"].inputs["steps"] == 4
    assert graph["7"].inputs["seed"] == 42
    context = {"role": "person", "characterReferences": []}
    assert PORTRAIT_PRESET == "flux-schnell-illustration-v2"
    assert fingerprint(context) == fingerprint(context, "flux-schnell-illustration-v2")


@pytest.mark.parametrize("count", [1, 3])
def test_qwen_edit_only_connects_supplied_image_slots(count):
    plan = IllustrationPlan(prompt="Edit image 1 into the requested scene.", alt="장면", meaning="설명")
    graph = compile_reference_image(plan, 7, "test", [f"person-{n}.png" for n in range(count)])
    for key in ("4", "5"):
        image_inputs = {k: v for k, v in graph[key].inputs.items() if k.startswith("image")}
        assert image_inputs == {f"image{n + 1}": [str(21 + n * 2), 0] for n in range(count)}
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
