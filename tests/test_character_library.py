import copy
import io
import json

import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import Config
from app.studio_characters import reference_bytes
from app.studio_domain import cards
from app.studio_generation import image_context
from app.studio_library import catalog, face_pixels, reference_pixels
from test_studio import path
from test_studio_generation import client, setup, PromptProvider, submissions, request_image
from test_studio_batch import portrait_targets, preparation, finish_batch


class SelectingProvider(PromptProvider):
    def call(self, task, context, schema, *, images=()):
        if schema.__name__ == "CharacterSelection":
            self.selection_calls = getattr(self, "selection_calls", 0) + 1
            self.selection_context = copy.deepcopy(context)
            available = list(reversed(context["availableCharacters"]))
            choices = [{"partyId": party["partyId"], "characterId": available[index]["characterId"]}
                       for index, party in enumerate(context["unassignedParties"])]
            if getattr(self, "duplicate_selection", False):
                choices = [{**c, "characterId": available[0]["characterId"]} for c in choices]
            if getattr(self, "unknown_party", False):
                choices[0]["partyId"] = "invented-party"
            return schema(choices=choices)
        return super().call(task, context, schema, images=images)


def enable_library(setup):
    _, service, _, _, _, _ = setup
    service.provider = SelectingProvider()
    service.config.studio_character_mode = "library"
    service.config.comfy_api_key = ""


@pytest.mark.parametrize("version", ["stock-characters-v1", "stock-characters-v2"])
def test_library_has_ten_independent_face_and_reference_assets(version):
    data = catalog(version)
    assert len(data["characters"]) == 10
    faces, refs = [], []
    for entry in data["characters"]:
        face, ref = face_pixels(entry["id"], version), reference_pixels(entry["id"], version)
        assert face != ref
        for pixels in (face, ref):
            with Image.open(io.BytesIO(pixels)) as image:
                assert image.size == (768, 768)
                assert image.getpixel((0, 0)) == (255, 255, 255)
        faces.append(face)
        refs.append(ref)
    assert len(set(faces)) == len(set(refs)) == 10


def test_ai_selects_once_and_portraits_need_no_comfy_or_vision(setup):
    _, service, project, _, _, control = setup
    portraits, _ = portrait_targets(setup)
    enable_library(setup)
    for index in range(2):
        response = preparation(setup)
        assert response.status_code == 200, response.text
    state = service.store.get(project["id"], "alice")[0]
    assert state["character_library"]["version"] == "stock-characters-v2"
    bindings = state["character_library"]["bindings"]
    assert [bindings[p["partyId"]] for p in portraits] == ["cast-10", "cast-09"]
    assert service.provider.selection_calls == 1
    assert len(service.provider.selection_context["availableCharacters"]) == 10
    assert not submissions(control)
    assert not getattr(service.provider, "portrait_validation_calls", 0)
    assert set(state["locked_portraits"]) == set(bindings)
    for portrait in portraits:
        locked = state["locked_portraits"][portrait["partyId"]]["image"]
        asset = next(a for a in state["assets"] if a["src"] == locked["src"])
        assert service.store.asset(asset["id"])[1] == face_pixels(bindings[portrait["partyId"]])


def test_existing_v1_library_keeps_original_faces_references_and_assignments(setup):
    _, service, project, _, _, _ = setup
    portraits, _ = portrait_targets(setup)
    enable_library(setup)
    legacy = catalog("stock-characters-v1")
    bindings = {p["partyId"]: f"cast-{index + 1:02d}" for index, p in enumerate(portraits)}
    original = {"version": legacy["version"], "digest": legacy["digest"], "bindings": bindings}
    state, revision = service.store.get(project["id"], "alice")
    state["character_library"] = original
    service.store.save(state, "alice", revision)
    for _ in portraits:
        response = preparation(setup)
        assert response.status_code == 200, response.text
    state = service.store.get(project["id"], "alice")[0]
    assert state["character_library"] == original
    assert service.library.assign(project["id"], "alice") == original
    assert getattr(service.provider, "selection_calls", 0) == 0
    for portrait in portraits:
        locked = state["locked_portraits"][portrait["partyId"]]["image"]
        asset = next(a for a in state["assets"] if a["src"] == locked["src"])
        character_id = bindings[portrait["partyId"]]
        assert service.store.asset(asset["id"])[1] == face_pixels(character_id, legacy["version"])
        assert face_pixels(character_id, legacy["version"]) != face_pixels(character_id)
    scene = next(c for c in cards(state["document"]) if c["role"] != "person")
    for ref in image_context(state, scene["id"])["characterReferences"]:
        assert reference_bytes(service.store, state, "alice", ref) == reference_pixels(
            ref["libraryCharacterId"], legacy["version"])


def test_qwen_receives_full_neutral_references_not_faces_or_pose_sheets(setup):
    _, service, project, _, _, control = setup
    portrait_targets(setup)
    enable_library(setup)
    assert preparation(setup).status_code == 200
    assert preparation(setup).status_code == 200
    service.config.comfy_api_key = "test-key"
    result = request_image(setup)
    assert result.status_code == 200, result.text
    refs = service.provider.context["characterReferences"]
    assert len(refs) == 2
    assert "styleReference" not in service.provider.context
    uploads = [r for r in control["calls"] if r.url.path == "/api/upload/image"]
    assert len(uploads) == 2
    for ref, upload in zip(refs, uploads):
        assert reference_pixels(ref["libraryCharacterId"]) in upload.content
        assert face_pixels(ref["libraryCharacterId"]) not in upload.content
    graph = json.loads(submissions(control)[0].content)["workflow"]
    assert graph["11"]["inputs"]["steps"] == 50
    assert graph["30"]["inputs"]["scale_by"] == 0.75
    assert "image3" not in graph["4"]["inputs"]
    assert request_image(setup).status_code == 200
    assert service.provider.selection_calls == 1 and len(submissions(control)) == 1


def test_library_batch_preserves_faces_and_ai_assignments_after_reload(setup):
    _, service, project, _, _, control = setup
    portraits, before = portrait_targets(setup)
    enable_library(setup)
    service.config.comfy_api_key = "test-key"
    result = finish_batch(setup)
    assert all(c["imageId"] for c in cards(result["document"]))
    state = service.store.get(project["id"], "alice")[0]
    frozen = copy.deepcopy(state["character_library"])
    assert len(submissions(control)) == len(cards(before)) - len(portraits)
    assert preparation(setup).json()["document"] == result["document"]
    assert service.store.get(project["id"], "alice")[0]["character_library"] == frozen
    assert service.provider.selection_calls == 1


@pytest.mark.parametrize("bad", ["duplicate_selection", "unknown_party"])
def test_invalid_ai_choices_do_not_spend_again_or_apply_any_character(setup, bad):
    _, service, project, _, _, control = setup
    portrait_targets(setup)
    enable_library(setup)
    setattr(service.provider, bad, True)
    for _ in range(2):
        result = preparation(setup)
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "character_selection_invalid"
    assert service.provider.selection_calls == 1
    state = service.store.get(project["id"], "alice")[0]
    assert "character_library" not in state and not state.get("locked_portraits")
    assert not submissions(control)


def test_stock_portrait_is_locked_and_reference_binding_cannot_be_forged(setup):
    client, service, project, _, _, _ = setup
    portrait_targets(setup)
    enable_library(setup)
    assert preparation(setup).status_code == 200
    state = service.store.get(project["id"], "alice")[0]
    person = next(c for c in cards(state["document"]) if c["role"] == "person" and c["imageId"])
    document = copy.deepcopy(state["document"])
    next(c for c in cards(document) if c["id"] == person["id"])["imageId"] = None
    assert client.put(path(project) + "/document", json=document).json()["detail"]["code"] == "character_locked"
    scene = next(c for c in cards(document) if c["role"] != "person")
    ref = image_context(state, scene["id"])["characterReferences"][0]
    ref["libraryCharacterId"] = "cast-01"
    with pytest.raises(HTTPException) as error:
        reference_bytes(service.store, state, "alice", ref)
    assert error.value.detail["code"] == "character_reference_invalid"


@pytest.mark.parametrize("status", ["running", "submission_unknown", "prepared"])
def test_switching_to_library_preserves_unfinished_paid_scene(setup, monkeypatch, status):
    _, service, project, _, _, control = setup
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = 429 if status == "prepared" else "timeout" if status == "submission_unknown" else None
    assert request_image(setup).status_code == 503
    graph = json.loads(submissions(control)[0].content)["workflow"]
    service.config.studio_character_mode = "library"
    control["submit_error"], control["status"] = None, "succeeded"
    response = request_image(setup)
    assert response.status_code == (503 if status == "submission_unknown" else 200)
    assert "character_library" not in service.store.get(project["id"], "alice")[0]
    assert json.loads(submissions(control)[-1].content)["workflow"] == graph
    assert len(submissions(control)) == (2 if status == "prepared" else 1)


def test_character_mode_validation():
    with pytest.raises(ValueError):
        Config(studio_character_mode="unknown")
