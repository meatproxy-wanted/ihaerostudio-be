import json

import pytest

from app.image_workflows import MOOD_ALLOWED, compile_reference_image
from app.studio_generation import IllustrationPlan, image_context
from app.studio_style import sample_pixels, style_sample
from app.video_workflows import validate_graph
from test_studio_generation import client, setup, request_image, submissions, attach_characters
from test_studio_batch import portrait_targets, finish_batch, preparation
from app.studio_domain import cards


def enable_mood(monkeypatch):
    monkeypatch.setattr("app.studio_generation.style_sample", style_sample)


@pytest.mark.parametrize("with_characters", [False, True])
def test_mood_is_uploaded_last_and_cached(setup, monkeypatch, with_characters):
    _, service, project, _, card, control = setup
    if with_characters:
        attach_characters(setup)
    enable_mood(monkeypatch)
    result = request_image(setup)
    assert result.status_code == 200, result.text
    graph = json.loads(submissions(control)[0].content)["workflow"]
    context = image_context(service.store.get(project["id"], "alice")[0], card["id"])
    count = len(context["characterReferences"])
    assert context["styleReference"] == style_sample()
    uploads = [r for r in control["calls"] if r.url.path == "/api/upload/image"]
    assert len(uploads) == count + 1
    assert sample_pixels() in uploads[-1].content
    assert f"Picture {count + 1} is only a light reference" in graph["4"]["inputs"]["prompt"]
    assert "Existing character identity and the requested situation take priority" in graph["4"]["inputs"]["prompt"]
    assert graph["11"]["inputs"]["steps"] == 40
    if not count:
        assert graph["6"]["class_type"] == "EmptySD3LatentImage"
        assert graph["6"]["inputs"] == {"width": 512, "height": 512, "batch_size": 1}
    assert request_image(setup).status_code == 200
    assert len(submissions(control)) == 1


def test_mood_portrait_is_new_solo_composition():
    plan = IllustrationPlan(prompt="One fictional adult with short black hair and a blue shirt.", alt="인물", meaning="인물 소개")
    graph = compile_reference_image(plan, 42, "test", [], mood="mood.png", portrait=True)
    validate_graph(graph, MOOD_ALLOWED)
    assert graph["6"].class_type == "EmptySD3LatentImage"
    assert graph["11"].inputs["steps"] == 40
    assert "exactly one" in graph["4"].inputs["prompt"].lower()
    assert "#FFFFFF" in graph["4"].inputs["prompt"]
    assert "two people" in graph["5"].inputs["prompt"]
    assert "Do not copy its people" in graph["4"].inputs["prompt"]


def test_mood_batch_generates_locked_portraits_before_scenes(setup, monkeypatch):
    _, service, project, _, _, control = setup
    portraits, _ = portrait_targets(setup)
    enable_mood(monkeypatch)
    result = finish_batch(setup)
    assert all(c["imageId"] for c in cards(result["document"]))
    graphs = [json.loads(r.content)["workflow"] for r in submissions(control)]
    assert all(g["1"]["inputs"]["unet_name"] == "qwen_image_edit_2511_fp8mixed.safetensors" for g in graphs)
    assert all(g["11"]["inputs"]["steps"] == 40 for g in graphs)
    assert all(g["6"]["class_type"] == "EmptySD3LatentImage" for g in graphs[:2])
    assert service.provider.portrait_validation_calls == 2
    assert service.provider.profile_calls == 2  # Never describe the mood sample as a person.
    state = service.store.get(project["id"], "alice")[0]
    assert set(state["locked_portraits"]) == {p["partyId"] for p in portraits}
    assert preparation(setup).json()["document"] == result["document"]
    assert len(submissions(control)) == len(graphs)


def test_three_characters_keep_all_slots_without_mood(setup, monkeypatch):
    _, service, project, _, card, _ = setup
    state = service.store.get(project["id"], "alice")[0]
    monkeypatch.setattr("app.studio_generation.character_context", lambda *a, **k: ([], [{"imageNumber": i} for i in range(1, 4)]))
    enable_mood(monkeypatch)
    context = image_context(state, card["id"])
    assert "styleReference" not in context
    assert len(context["characterReferences"]) == 3


@pytest.mark.parametrize("status", ["prepared", "running", "submission_unknown", "ready"])
def test_mood_upgrade_never_resubmits_paid_work(setup, monkeypatch, status):
    _, service, _, _, _, control = setup
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = 429 if status == "prepared" else "timeout" if status == "submission_unknown" else None
    assert request_image(setup).status_code == (200 if status == "ready" else 503)
    original = json.loads(submissions(control)[0].content)["workflow"]
    enable_mood(monkeypatch)
    control["status"], control["submit_error"] = "succeeded", None
    result = request_image(setup)
    assert result.status_code == (503 if status == "submission_unknown" else 200), result.text
    assert len(submissions(control)) == (2 if status in {"prepared", "ready"} else 1)
    if status in {"running", "submission_unknown"}:
        assert json.loads(submissions(control)[0].content)["workflow"] == original
        assert service.provider.calls == 1
    else:
        assert service.provider.calls == 2
        assert json.loads(submissions(control)[-1].content)["workflow"]["11"]["inputs"]["steps"] == 40
