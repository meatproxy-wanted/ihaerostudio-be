import copy
import io
import json

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw

from app.config import Config
from app.sources import clean_image
from app.studio_domain import cards
from app.studio_storyboard import (LEGACY_PRESET, StoryboardPlan, Panel, compile_storyboard, context_for,
                                  crop_sheet, digest_for, normalized_sheet)
from app.video_models import VideoPreflight
from test_studio import path
from test_studio_generation import client, setup, submissions
from test_studio_batch import preparation, finish_batch, portrait_targets
from test_character_library import SelectingProvider
from test_remote_store import remote

COLORS = ("red", "green", "blue", "yellow")


def sheet(size=1024):
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    half = size // 2
    for i, color in enumerate(COLORS):
        x, y = (i % 2) * half, (i // 2) * half
        draw.rectangle((x, y, x + half - 1, y + half - 1), fill=color)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class StoryboardProvider(SelectingProvider):
    def call(self, task, context, schema, *, images=()):
        if schema is StoryboardPlan:
            self.storyboard_calls = getattr(self, "storyboard_calls", 0) + 1
            self.storyboard_context = copy.deepcopy(context)
            panels = [Panel(cardId=c["cardId"], mainMessage="The supplied situation is a request or order, not a completed payment.",
                keyTerms=[], prompt="Reference characters considering the supplied situation.",
                focalAction="Image 1 opens an empty hand toward image 2.",
                staging="Image 1 left, image 2 right, separate hands and clearly visible faces.",
                objectsAndSetting="A neutral space; no money or objects changing hands.",
                semanticBoundary="A request or order is not an established event or completed payment.",
                alt="컷 " + str(i), meaning="문장의 의미를 설명하는 삽화") for i, c in enumerate(context["panels"])]
            if getattr(self, "bad_order", False):
                panels[0].cardId = "invented-card"
            return schema(panels=panels)
        return super().call(task, context, schema, images=images)


def enable(setup, count=4):
    client, service, project, _, _, control = setup
    portrait_targets(setup)
    doc = client.get(path(project) + "/document").json()
    scenes = [c for c in cards(doc) if c["role"] != "person"]
    template = copy.deepcopy(scenes[0])
    for section in doc["sections"]:
        section["cards"] = [c for c in section["cards"] if c["role"] == "person"]
    target = next(s for s in doc["sections"] if s["kind"] == "reasons")
    for index in range(count):
        card = copy.deepcopy(template)
        card["id"] = "scene-" + str(index)
        for i, sentence in enumerate(card["sentences"]):
            sentence["id"] = f"sentence-{index}-{i}"
        target["cards"].append(card)
    saved = client.put(path(project) + "/document", json=doc)
    assert saved.status_code == 200, saved.text
    service.provider = StoryboardProvider()
    service.config.studio_character_mode = "library"
    service.config.studio_scene_mode = "storyboard4"
    control["png"] = sheet()
    for _ in range(2):
        assert preparation(setup).status_code == 200
    return [c["id"] for c in target["cards"]]


def test_crop_row_major_and_original_resolution():
    pixels = sheet()
    result = crop_sheet(pixels, 4)
    for data, color in zip(result, COLORS):
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (512, 512)
            assert image.getpixel((500, 500)) == Image.new("RGB", (1, 1), color).getpixel((0, 0))
    assert len(crop_sheet(pixels, 2)) == 2
    assert clean_image(sheet(2048))[1:] == (1600, 1600)  # uploads stay unchanged
    assert clean_image(pixels, max_side=4096)[1:] == (1024, 1024)
    with pytest.raises(HTTPException) as error:
        crop_sheet(sheet(768), 4)
    assert error.value.detail["code"] == "storyboard_size_invalid"


def test_legacy_sheet_downsizing_validates_original_workflow():
    result = normalized_sheet(sheet(2048), 2048)
    for data, color in zip(crop_sheet(result, 4), COLORS):
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (512, 512)
            assert image.getpixel((50, 50)) == Image.new("RGB", (1, 1), color).getpixel((0, 0))
    for actual, expected in [(1024, 2048), (2048, 1024), (768, 768)]:
        with pytest.raises(HTTPException) as error:
            normalized_sheet(sheet(actual), expected)
        assert error.value.detail["code"] == "storyboard_size_invalid"


@pytest.mark.parametrize("status", ["running", "prepared", "submission_unknown"])
def test_legacy_job_upgrade_does_not_duplicate_paid_work(setup, monkeypatch, status):
    _, service, project, _, _, control = setup
    ids = enable(setup)
    monkeypatch.setattr("app.studio_storyboard.WAIT_SECONDS", 0)
    control["status"] = "running"
    control["submit_error"] = 400 if status == "prepared" else "timeout" if status == "submission_unknown" else None
    first = preparation(setup)
    assert first.status_code == (200 if status == "running" else 503)
    state, version = service.store.get(project["id"], "alice")
    group = state["image_batch"]["storyboardGroup"]
    old_digest = group["digest"]
    group.pop("preset")  # persisted groups from before the resolution change
    group["digest"] = digest_for(context_for(state, ids), LEGACY_PRESET)
    service.store.save(state, "alice", version)
    with service.store.store.connect() as db:
        row = db.execute("SELECT id,body FROM studio_image_jobs WHERE fingerprint=?", (old_digest,)).fetchone()
        job = json.loads(row["body"])
        assert job["status"] == status
        job["fingerprint"] = group["digest"]
        job["workflow"]["6"]["inputs"].update(width=2048, height=2048)
        job["workflow"]["11"]["inputs"]["steps"] = 40
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=? WHERE id=?",
                   (group["digest"], json.dumps(job), row["id"]))
    control["submit_error"] = None
    control["status"] = "succeeded"
    control["png"] = sheet(1024 if status == "prepared" else 2048)
    result = preparation(setup)
    if status == "submission_unknown":
        assert result.status_code == 503
        assert result.json()["detail"]["code"] == "image_submission_unknown"
        assert len(submissions(control)) == 1
    else:
        assert result.status_code == 200, result.text
        assert result.json()["generation"]["status"] == "ready"
        assert len(submissions(control)) == (2 if status == "prepared" else 1)
        stored = service.store.get(project["id"], "alice")[0]
        original = stored["image_batch"]["storyboardSheets"][0]["sheetId"]
        with Image.open(io.BytesIO(service.store.asset(original)[1])) as image:
            assert image.size == (1024, 1024)
        if status == "prepared":
            assert json.loads(submissions(control)[-1].content)["workflow"]["6"]["inputs"]["width"] == 1024
            assert json.loads(submissions(control)[-1].content)["workflow"]["11"]["inputs"]["steps"] == 50
    assert service.provider.storyboard_calls == 1


@pytest.mark.parametrize("count", [1, 2, 3, 4, 6, 10])
def test_batch_groups_and_crops_atomically_without_contract_change(setup, count):
    client, service, project, _, _, control = setup
    ids = enable(setup, count)
    result = finish_batch(setup)
    assert result["generation"]["status"] == "ready"
    assert result["generation"]["completed"] == count + 2
    expected = (count + 3) // 4
    assert len(submissions(control)) == service.provider.storyboard_calls == expected
    state = service.store.get(project["id"], "alice")[0]
    assert len(state["image_batch"]["storyboardSheets"]) == expected
    images = {i["id"]: i for i in result["document"]["images"]}
    for index, card_id in enumerate(ids):
        card = next(c for c in cards(result["document"]) if c["id"] == card_id)
        image = images[card["imageId"]]
        with Image.open(io.BytesIO(service.store.asset(image["id"])[1])) as pixels:
            assert pixels.size == (512, 512)
            assert pixels.getpixel((50, 50)) == Image.new("RGB", (1, 1), COLORS[index % 4]).getpixel((0, 0))
        assert not any(s["verified"] for s in card["sentences"])
    for original in state["image_batch"]["storyboardSheets"]:
        assert original["sheetId"] not in images
        with Image.open(io.BytesIO(service.store.asset(original["sheetId"])[1])) as pixels:
            assert pixels.size == (1024, 1024)
    for request in submissions(control):
        graph = json.loads(request.content)["workflow"]
        assert graph["6"]["class_type"] == "EmptySD3LatentImage"
        assert graph["6"]["inputs"] == {"width": 1024, "height": 1024, "batch_size": 1}
        assert graph["11"]["inputs"]["steps"] == 50
        prompt = graph["4"]["inputs"]["prompt"]
        assert "one educational scene" not in prompt and "Do not create" not in prompt
        assert "BOTTOM RIGHT quadrant" in prompt
        assert "image1" in graph["4"]["inputs"]
    repeated = preparation(setup).json()
    assert repeated["document"] == result["document"]
    assert repeated["generation"] == result["generation"]
    assert len(submissions(control)) == expected
    assert "private" not in json.dumps(result)


def test_in_progress_resumes_after_flag_rollback_without_second_submission(setup, monkeypatch):
    _, service, _, _, _, control = setup
    enable(setup)
    monkeypatch.setattr("app.studio_storyboard.WAIT_SECONDS", 0)
    control["status"] = "running"
    result = preparation(setup)
    assert result.status_code == 200 and result.json()["generation"]["completed"] == 2
    service.config.studio_scene_mode = "single"
    assert preparation(setup).json()["generation"]["completed"] == 2
    control["status"] = "succeeded"
    assert preparation(setup).json()["generation"]["status"] == "ready"
    assert len(submissions(control)) == service.provider.storyboard_calls == 1


def test_uncertain_submission_blocks_fallback_and_paid_retries(setup):
    _, service, _, _, _, control = setup
    enable(setup)
    control["submit_error"] = "timeout"
    assert preparation(setup).status_code == 503
    service.config.studio_scene_mode = "single"
    result = preparation(setup)
    assert result.status_code == 503 and result.json()["detail"]["code"] == "image_submission_unknown"
    assert len(submissions(control)) == service.provider.storyboard_calls == 1


@pytest.mark.parametrize("change", ["text", "attachment"])
def test_concurrent_document_change_never_overwrites_or_regroups(setup, change):
    client, service, project, _, _, control = setup
    ids = enable(setup)

    def edit():
        doc = client.get(path(project) + "/document").json()
        card = next(c for c in cards(doc) if c["id"] == ids[0])
        if change == "text":
            card["sentences"][0]["text"] += " 변경한 문장"
        else:
            card["imageId"] = doc["images"][0]["id"]
        assert client.put(path(project) + "/document", json=doc).status_code == 200

    control["on_poll"] = edit
    before = service.store.get(project["id"], "alice")[0]
    assert preparation(setup).status_code == 409
    assert preparation(setup).status_code == 409
    state = service.store.get(project["id"], "alice")[0]
    assert len(state["assets"]) == len(before["assets"])
    assert len(submissions(control)) == service.provider.storyboard_calls == 1
    assert not state["image_batch"].get("storyboardSheets")


def test_wrong_size_not_applied_or_recharged(setup):
    _, service, project, _, _, control = setup
    enable(setup)
    control["png"] = sheet(768)
    before = service.store.get(project["id"], "alice")[0]
    for _ in range(2):
        result = preparation(setup)
        assert result.status_code == 502 and result.json()["detail"]["code"] == "storyboard_size_invalid"
    state = service.store.get(project["id"], "alice")[0]
    assert len(state["assets"]) == len(before["assets"])
    assert len(submissions(control)) == 1


def test_bad_plan_not_submitted(setup):
    _, service, _, _, _, control = setup
    enable(setup)
    service.provider.bad_order = True
    result = preparation(setup)
    assert result.status_code == 502 and result.json()["detail"]["code"] == "storyboard_plan_invalid"
    assert not submissions(control)


def test_terminal_failure_not_resubmitted(setup):
    _, _, _, _, _, control = setup
    enable(setup)
    control["status"] = "failed"
    assert preparation(setup).status_code == 502
    assert preparation(setup).status_code == 502
    assert len(submissions(control)) == 1


def test_project_capacity_checks_all_crops_and_sheet_atomically(setup):
    _, service, project, _, _, control = setup
    enable(setup)
    state, version = service.store.get(project["id"], "alice")
    state["assets"] += [{"id": f"dummy-{i}", "src": "x", "byteSize": 1} for i in range(95)]
    service.store.save(state, "alice", version)
    assert preparation(setup).status_code == 413
    assert not submissions(control) and not getattr(service.provider, "storyboard_calls", 0)


def test_global_reference_numbering_is_consistent(setup):
    _, service, project, _, _, _ = setup
    ids = enable(setup)
    state = service.store.get(project["id"], "alice")[0]
    context = context_for(state, ids)
    numbers = {r["partyId"]: r["imageNumber"] for r in context["characterReferences"]}
    assert set(numbers.values()) == {1, 2}
    for panel in context["panels"]:
        assert "styleReference" not in panel
        assert all(r["imageNumber"] == numbers[r["partyId"]] for r in panel["characterReferences"])


def test_missing_references_fail_before_planning(setup, monkeypatch):
    _, service, _, _, _, control = setup
    enable(setup)
    from app.studio_storyboard import image_context as original

    def no_references(state, card_id):
        return {**original(state, card_id), "characterReferences": []}

    monkeypatch.setattr("app.studio_storyboard.image_context", no_references)
    assert preparation(setup).status_code == 422
    assert not submissions(control) and not getattr(service.provider, "storyboard_calls", 0)


def test_default_off_and_unknown_mode_rejected(monkeypatch):
    monkeypatch.delenv("STUDIO_SCENE_MODE", raising=False)
    assert Config().studio_scene_mode == "single"
    with pytest.raises(ValueError, match="STUDIO_SCENE_MODE"):
        Config(studio_scene_mode="unknown")


def test_incompatible_preflight_not_paid(setup):
    _, service, _, _, _, control = setup
    enable(setup)
    service.cloud.preflight = lambda *a, **k: VideoPreflight(compatible=False, preset="test", node_count=0, issues=[])
    assert preparation(setup).status_code == 503
    assert not submissions(control)


def test_flag_rollback_changes_only_next_group(setup):
    _, service, project, _, _, control = setup
    enable(setup, 6)
    assert preparation(setup).json()["generation"]["completed"] == 6
    service.config.studio_scene_mode = "single"
    control["png"] = sheet(768)
    result = finish_batch(setup)
    assert result["generation"]["status"] == "ready"
    assert len(submissions(control)) == 3  # one 4-panel sheet + two old-style scenes
    state = service.store.get(project["id"], "alice")[0]
    assert len(state["image_batch"]["storyboardSheets"]) == 1
    assert service.provider.storyboard_calls == 1


def test_storage_race_rolls_back_sheet_and_all_crops(setup):
    _, service, project, _, _, control = setup
    enable(setup)

    def consume_capacity():
        state, version = service.store.get(project["id"], "alice")
        state["assets"].append({"id": "capacity-race", "src": "x", "byteSize": 12 * 1024 * 1024})
        service.store.save(state, "alice", version)

    control["on_poll"] = consume_capacity
    before = service.store.get(project["id"], "alice")[0]
    assert preparation(setup).status_code == 413
    state = service.store.get(project["id"], "alice")[0]
    assert len(state["assets"]) == len(before["assets"]) + 1
    assert state["image_batch"]["completed"] == before["image_batch"]["completed"]
    with service.store.store.connect() as db:
        assert db.execute("SELECT COUNT(*) AS count FROM studio_assets WHERE project_id=?", (project["id"],)).fetchone()["count"] == len(before["assets"])


def test_four_reference_union_rejected_without_paid_planning(setup, monkeypatch):
    _, service, _, _, _, control = setup
    enable(setup)
    from app.studio_storyboard import image_context as original

    def too_many(state, card_id):
        context = original(state, card_id)
        context["characterReferences"] += [{"partyId": "third", "imageNumber": 3},
                                             {"partyId": "fourth", "imageNumber": 4}]
        return context

    monkeypatch.setattr("app.studio_storyboard.image_context", too_many)
    assert preparation(setup).status_code == 422
    assert not submissions(control) and not getattr(service.provider, "storyboard_calls", 0)


def test_skipped_group_resumes_same_accepted_job(setup, monkeypatch):
    client, _, _, _, _, control = setup
    enable(setup)
    project = setup[2]
    monkeypatch.setattr("app.studio_storyboard.WAIT_SECONDS", 0)
    control["status"] = "running"
    assert preparation(setup).json()["generation"]["status"] == "running"
    settings = {"tone": "haeyo", "naming": "initial", "illustrations": "none"}
    assert client.put(path(project) + "/settings", json=settings).status_code == 200
    assert preparation(setup).json()["generation"]["status"] == "skipped"
    settings["illustrations"] = "with"
    assert client.put(path(project) + "/settings", json=settings).status_code == 200
    control["status"] = "succeeded"
    assert preparation(setup).json()["generation"]["status"] == "ready"
    assert len(submissions(control)) == 1


def test_decision_constraints_and_unused_quadrants(setup):
    _, service, _, _, _, control = setup
    enable(setup, 1)
    original = service.provider.call

    def plan_decision(task, context, schema, *, images=()):
        plan = original(task, context, schema, images=images)
        if schema is StoryboardPlan:
            graph = compile_storyboard(plan, ["decision"], 1, "test", ["ref.png"], [])
            prompt = graph["4"].inputs["prompt"]
            assert "Court-order consideration only" in prompt
            assert prompt.count("leave entirely blank white") == 3
            assert "3D" not in prompt and "2.5D" not in prompt
        return plan

    service.provider.call = plan_decision
    assert preparation(setup).status_code == 200


def test_live_harness_with_mock_apis_exports_and_resumes(setup, monkeypatch, tmp_path, capsys):
    client, service, _, _, _, control = setup
    service.provider = StoryboardProvider()
    service.config.studio_character_mode = "library"
    service.config.studio_scene_mode = "storyboard4"
    control["png"] = sheet()
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", "private-comfy-test-key")

    def test_app(config):
        client.app.state.config.api_keys = config.api_keys
        return client.app

    monkeypatch.setattr("app.main.create_app", test_app)
    monkeypatch.setattr("tempfile.mkdtemp", lambda **k: str(tmp_path))
    monkeypatch.setattr("sys.argv", ["test_storyboard_live.py", "--live"])
    from scripts.test_storyboard_live import main
    main()
    assert (tmp_path / "sheet.png").is_file()
    for index in range(4):
        with Image.open(tmp_path / f"panel-{index + 1}.png") as image:
            assert image.size == (512, 512)
    assert len(submissions(control)) == 1
    monkeypatch.setattr("sys.argv", ["test_storyboard_live.py", "--live", "--resume", str(tmp_path)])
    main()
    assert len(submissions(control)) == 1
    output = capsys.readouterr().out
    assert "private-openai-test-key" not in output and "private-comfy-test-key" not in output


def test_swagger_documents_experiment_and_rollback(client):
    schema = client.get("/openapi.json").json()
    prepare = schema["paths"]["/api/studio/projects/{project_id}/document/prepare-images"]["post"]
    assert "storyboard4" in prepare["description"] and "1024×1024" in prepare["description"]
    assert "storyboard_size_invalid" in prepare["responses"]["502"]["content"]["application/json"]["examples"]


def test_unsubmitted_idle_group_can_be_rolled_back(setup):
    _, service, project, _, _, control = setup
    enable(setup)
    service.provider.bad_order = True
    assert preparation(setup).status_code == 502
    service.config.studio_scene_mode = "single"
    control["png"] = sheet(768)
    result = preparation(setup)
    assert result.status_code == 200
    assert result.json()["generation"]["completed"] == 3
    assert len(submissions(control)) == 1
    assert json.loads(submissions(control)[0].content)["workflow"]["6"]["class_type"] == "VAEEncode"
    state = service.store.get(project["id"], "alice")[0]
    assert "storyboardGroup" not in state["image_batch"]
    with service.store.store.connect() as db:
        jobs = [json.loads(r["body"]) for r in db.execute("SELECT body FROM studio_image_jobs WHERE project_id=?", (project["id"],)).fetchall()]
    assert any(j["status"] == "abandoned" for j in jobs)


def test_accepted_terminal_failure_does_not_fallback_on_rollback(setup):
    _, service, _, _, _, control = setup
    enable(setup)
    control["status"] = "failed"
    assert preparation(setup).status_code == 502
    service.config.studio_scene_mode = "single"
    assert preparation(setup).status_code == 502
    assert len(submissions(control)) == 1


def test_storyboard_persists_original_crops_and_job_on_turso(setup, remote):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from test_studio import create, draft, AUTH

    client, _, _ = remote
    project = create(client, {"tone": "haeyo", "naming": "initial", "illustrations": "with"})
    doc = draft(client, project)
    service = client.app.state.studio_generation
    service.config.comfy_api_key = "private-comfy-test-key"
    service.cloud.preflight = setup[1].cloud.preflight
    remote_setup = (client, service, project, doc, cards(doc)[0], setup[5])
    test_batch_groups_and_crops_atomically_without_contract_change(remote_setup, 4)
    previous = client.get(path(project) + "/document").json()
    with TestClient(create_app(client.app.state.config), headers=AUTH) as restarted:
        restarted.app.state.studio_generation.provider = StoryboardProvider()
        response = restarted.post(path(project) + "/document/prepare-images")
        assert response.status_code == 200 and response.json()["document"] == previous
    assert len(submissions(setup[5])) == 1
