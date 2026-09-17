import copy
import json

import pytest

from app.studio_domain import cards
from app.studio_generation import StudioGeneration
from test_studio import path, create, draft
from test_studio_generation import client, setup, attach_characters, submissions


def preparation(setup):
    client, _, project, _, _, _ = setup
    return client.post(path(project) + "/document/prepare-images")


def portrait_targets(setup):
    client, _, project, _, _, _ = setup
    portraits = attach_characters(setup)
    document = client.get(path(project) + "/document").json()
    for card in cards(document):
        card["imageId"] = None
    document["images"] = []
    response = client.put(path(project) + "/document", json=document)
    assert response.status_code == 200
    document = response.json()["document"]
    return portraits, document


def finish_batch(setup):
    for _ in range(20):
        response = preparation(setup)
        assert response.status_code == 200, response.text
        if response.json()["generation"]["status"] != "running":
            return response.json()
    raise AssertionError("batch did not finish")


def test_editor_preparation_generates_portraits_first_then_reference_scenes(setup):
    _, service, project, _, _, control = setup
    portraits, before = portrait_targets(setup)
    result = finish_batch(setup)
    assert result["generation"] == {"status": "ready", "phase": "complete", "completed": len(cards(before)),
                                     "total": len(cards(before)), "currentCardId": None}
    graphs = [json.loads(r.content)["workflow"] for r in submissions(control)]
    assert [g["1"]["inputs"]["unet_name"] for g in graphs[:2]] == ["flux1-schnell.safetensors"] * 2
    assert all(g["1"]["inputs"]["unet_name"] == "qwen_image_edit_2511_fp8mixed.safetensors" for g in graphs[2:])
    assert all(c["imageId"] for c in cards(result["document"]))
    assert result["document"]["saveRevision"] == before["saveRevision"] + len(graphs)
    state = service.store.get(project["id"], "alice")[0]
    assert set(state["locked_portraits"]) == {p["partyId"] for p in portraits}
    assert preparation(setup).json()["document"] == result["document"]
    assert len(submissions(control)) == len(graphs)


def test_pending_batch_and_page_reload_resume_one_submission(setup, monkeypatch):
    _, _, _, _, _, control = setup
    portrait_targets(setup)
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running"
    first = preparation(setup).json()
    assert first["generation"]["status"] == "running" and first["generation"]["completed"] == 0
    assert preparation(setup).json()["generation"] == first["generation"]
    assert len(submissions(control)) == 1
    control["status"] = "succeeded"
    resumed = preparation(setup).json()
    assert resumed["generation"]["completed"] == 1
    assert len(submissions(control)) == 1


def test_older_drafts_gain_portrait_cards_before_scene_generation(setup):
    _, _, _, document, _, control = setup
    assert not any(c["role"] == "person" for c in cards(document))
    response = preparation(setup)
    assert response.status_code == 200, response.text
    result = response.json()
    people = [c for c in cards(result["document"]) if c["role"] == "person"]
    assert len(people) == len(document["partyNames"])
    assert people[0]["imageId"] and people[1]["imageId"] is None
    assert json.loads(submissions(control)[0].content)["workflow"]["1"]["inputs"]["unet_name"] == "flux1-schnell.safetensors"


def test_unknown_batch_submission_stops_without_paid_retry(setup):
    _, _, _, _, _, control = setup
    portrait_targets(setup)
    control["submit_error"] = "timeout"
    assert preparation(setup).json()["detail"]["code"] == "comfy_connection_or_response_error"
    control["submit_error"] = None
    assert preparation(setup).json()["detail"]["code"] == "image_submission_unknown"
    assert len(submissions(control)) == 1


@pytest.mark.parametrize("change", ["detach", "delete", "reassign", "pixels"])
def test_portrait_locks_cannot_be_bypassed_by_document_save(setup, change):
    client, _, project, _, _, control = setup
    portrait_targets(setup)
    result = finish_batch(setup)
    document = copy.deepcopy(result["document"])
    people = [c for c in cards(document) if c["role"] == "person"]
    person = people[0]
    if change == "detach":
        person["imageId"] = None
    elif change == "delete":
        for section in document["sections"]:
            section["cards"] = [c for c in section["cards"] if c["id"] != person["id"]]
    elif change == "reassign":
        person["partyId"] = people[1]["partyId"]
    else:
        image = next(i for i in document["images"] if i["id"] == person["imageId"])
        image["src"] = next(i["src"] for i in document["images"] if i["id"] != person["imageId"])
    response = client.put(path(project) + "/document", json=document)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "character_locked"
    count = len(submissions(control))
    response = client.post(path(project) + "/assist/images", json={"cardId": people[0]["id"]})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "character_locked"
    assert len(submissions(control)) == count


def test_text_and_scene_edits_remain_available_without_automatic_regeneration(setup):
    client, _, project, _, _, control = setup
    portrait_targets(setup)
    result = finish_batch(setup)
    document = result["document"]
    scene = next(c for c in cards(document) if c["role"] != "person")
    scene["imageId"] = None
    scene["sentences"][0]["text"] = "원문과 비교해 문장을 고쳤어요."
    response = client.put(path(project) + "/document", json=document)
    assert response.status_code == 200, response.text
    count = len(submissions(control))
    assert preparation(setup).json()["generation"]["status"] == "ready"
    assert len(submissions(control)) == count


def test_batch_survives_service_restart(setup, monkeypatch):
    from app.studio_batch import StudioBatch
    _, service, project, _, _, control = setup
    portrait_targets(setup)
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running"
    assert preparation(setup).json()["generation"]["completed"] == 0
    restarted = StudioGeneration(service.store, service.config, service.provider)
    restarted.cloud.preflight = service.cloud.preflight
    control["status"] = "succeeded"
    result = StudioBatch(restarted).prepare(project["id"], "alice")
    assert result["generation"]["completed"] == 1
    assert len(submissions(control)) == 1


def test_no_image_setting_skips_without_cloud_calls(setup):
    client, _, project, _, _, control = setup
    response = client.put(path(project) + "/settings", json={"tone": "haeyo", "naming": "initial", "illustrations": "none"})
    assert response.status_code == 200
    assert preparation(setup).json()["generation"]["status"] == "skipped"
    assert not control["calls"]


def test_text_regeneration_preserves_fixed_portrait_images(setup, monkeypatch):
    client, service, project, _, _, _ = setup
    portrait_targets(setup)
    before = finish_batch(setup)["document"]
    new_draft = {k: copy.deepcopy(before[k]) for k in ("title", "subtitle", "partyNames", "sections", "glossary", "images")}
    new_draft["images"] = []
    for c in cards(new_draft):
        c["id"] += "-new"
        c["imageId"] = None
        for sentence in c["sentences"]:
            sentence["id"] += "-new"
    monkeypatch.setattr("app.studio_provider.draft", lambda *_: new_draft)
    response = client.post(path(project) + "/document/generate")
    assert response.status_code == 200, response.text
    after = response.json()["document"]
    assert [c["imageId"] for c in cards(after) if c["role"] == "person"] == [c["imageId"] for c in cards(before) if c["role"] == "person"]
    assert all(c["imageId"] is None for c in cards(after) if c["role"] != "person")
    assert "image_batch" not in service.store.get(project["id"], "alice")[0]


def test_prepare_owner_isolation_and_demo_skip(client):
    project = create(client, {"tone": "haeyo", "naming": "initial", "illustrations": "with"})
    draft(client, project)
    url = path(project) + "/document/prepare-images"
    assert client.post(url, headers={"Authorization": "Bearer other-test-token"}).status_code == 404
    response = client.post(url)
    assert response.status_code == 200 and response.json()["generation"]["status"] == "skipped"
