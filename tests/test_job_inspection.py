import json

import pytest

from app.image_workflows import compile_image, compile_portrait, compile_reference_image
from app.studio_generation import IllustrationPlan
from app.studio_job_inspection import ImageJobInspection
from app.studio_storyboard import compile_storyboard, StoryboardPlan, Panel
from test_studio import client, create, path
from test_studio_generation import setup, request_image, submissions
from test_remote_store import remote


def insert_job(client, project, job, *, owner="alice", target="card-1"):
    with client.app.state.studio.store.connect() as db:
        db.execute("INSERT INTO studio_image_jobs VALUES(?,?,?,?,?,?,?)",
            (job["id"], owner, project["id"], target, job["id"], json.dumps(job), 9876543210.0))


def encoded(graph):
    return {key: node.model_dump() for key, node in graph.items()}


def test_actual_sent_prompt_is_read_only_and_survives_restart(setup):
    client, service, project, _, _, control = setup
    assert request_image(setup).status_code == 200
    sent = json.loads(submissions(control)[0].content)["workflow"]
    before = service.store.get(project["id"], "alice")
    with service.store.store.connect() as db:
        jobs_before = [dict(r) for r in db.execute("SELECT * FROM studio_image_jobs").fetchall()]
    calls = len(control["calls"])
    listing = client.get(path(project) + "/image-jobs")
    assert listing.status_code == 200 and "no-store" in listing.headers["cache-control"]
    job_id = listing.json()["jobs"][0]["jobId"]
    response = client.get(path(project) + "/image-jobs/" + job_id)
    assert response.status_code == 200
    detail = response.json()
    assert detail["submissionState"] == "accepted" and detail["status"] == "ready"
    assert detail["prompts"] == [
        {"nodeId": "4", "classType": "CLIPTextEncode", "role": "positive", "text": sent["4"]["inputs"]["text"]},
    ]
    assert detail["createdAt"] is not None
    assert "private-comfy-test-key" not in response.text and "poll_url" not in response.text
    assert service.provider.calls == 1 and len(control["calls"]) == calls
    assert service.store.get(project["id"], "alice") == before
    with service.store.store.connect() as db:
        assert [dict(r) for r in db.execute("SELECT * FROM studio_image_jobs").fetchall()] == jobs_before
    assert ImageJobInspection(service.store).get(project["id"], "alice", job_id).model_dump() == detail


@pytest.mark.parametrize("kind", ["flux", "portrait", "edit"])
def test_saved_encoder_inputs_and_settings_without_private_graph(client, kind):
    project = create(client)
    plan = IllustrationPlan(prompt="The exact saved requested situation.", alt="삽화", meaning="의미")
    graph = (compile_image(plan, 123, "test") if kind == "flux" else
             compile_portrait(plan, 123, "test") if kind == "portrait" else
             compile_reference_image(plan, 123, "test", ["portrait.png"], mood="mood.png"))
    saved = encoded(graph)
    saved["secret-node"] = {"class_type": "Unknown", "inputs": {"api_key": "never-expose", "headers": {"Authorization": "never-expose"}}}
    job = {"id": "saved-job", "status": "submission_unknown", "workflow": saved,
           "plan": plan.model_dump(), "poll_url": "https://private.example/?signed=never-expose",
           "reference_bindings": [{"imageNumber": 1, "purpose": "character", "partyId": "party-original", "assetId": "portrait-original"},
                                  {"imageNumber": 2, "purpose": "mood"}]}
    insert_job(client, project, job)
    response = client.get(path(project) + "/image-jobs/saved-job")
    assert response.status_code == 200, response.text
    result = response.json()
    assert "never-expose" not in response.text and "secret-node" not in response.text
    assert result["submissionState"] == "uncertain" and result["createdAt"] is None
    key = "prompt" if kind == "edit" else "text"
    assert result["prompts"][0]["text"] == saved["4"]["inputs"][key]
    if kind == "flux":
        assert len(result["prompts"]) == 1  # No negative text encoder in the FLUX graph.
    else:
        assert result["prompts"][1]["text"] == saved["5"]["inputs"][key]
        assert result["prompts"][1]["role"] == "negative"
    assert any(s["values"].get("steps") == (50 if kind == "edit" else 20) for s in result["settings"])
    if kind == "edit":
        assert result["prompts"][1]["text"] == ""
        assert result["references"] == [
            {"imageNumber": 1, "loadNodeId": "20", "filename": "portrait.png", "purpose": "character", "partyId": "party-original", "assetId": "portrait-original"},
            {"imageNumber": 2, "loadNodeId": "22", "filename": "mood.png", "purpose": "mood", "partyId": None, "assetId": None}]


def test_storyboard_has_one_sheet_prompt_and_four_cut_designs(client):
    project = create(client)
    panels = [Panel(cardId=f"card-{i}", prompt=f"An actor considers the request for scene {i}.",
        mainMessage="A request, not a completed payment.", keyTerms=[], focalAction="considers the request",
        staging="stands on the left", objectsAndSetting="a document on a table", semanticBoundary="Not a completed payment.",
        alt="삽화", meaning="요청") for i in range(4)]
    plan = StoryboardPlan(panels=panels)
    graph = encoded(compile_storyboard(plan, ["claim"] * 4, 123, "test", ["old-reference.png"], []))
    insert_job(client, project, {"id": "sheet-job", "status": "ready", "provider_job_id": "provider-sheet",
        "workflow": graph, "plan": plan.model_dump(), "sheet_id": "sheet", "asset_ids": ["crop-1", "crop-2"]},
        target="storyboard4:group-old")
    response = client.get(path(project) + "/image-jobs/sheet-job")
    result = response.json()
    assert response.status_code == 200 and result["mode"] == "storyboard4"
    assert result["cardIds"] == [f"card-{i}" for i in range(4)]
    assert result["prompts"][0]["text"] == graph["4"]["inputs"]["prompt"]
    assert [d["cardId"] for d in result["designs"]] == result["cardIds"]
    assert result["assetIds"] == ["sheet", "crop-1", "crop-2"]
    assert result["references"][0]["purpose"] == "unknown" and result["references"][0]["partyId"] is None


@pytest.mark.parametrize("status", ["pending", "planned", "prepared", "running", "submission_unknown"])
def test_phase_distinguishes_current_workflow_from_stale_unsubmitted_graph(client, status):
    project = create(client)
    plan = IllustrationPlan(prompt="A saved but possibly stale illustration.", alt="삽화", meaning="의미")
    job = {"id": "phase-job", "status": status, "workflow": encoded(compile_image(plan, 1, "test")), "plan": plan.model_dump()}
    if status == "running":
        job["provider_job_id"] = "provider-phase"
    insert_job(client, project, job)
    result = client.get(path(project) + "/image-jobs/phase-job").json()
    assert result["workflowAvailable"] == (status not in {"pending", "planned"})
    assert bool(result["prompts"]) == result["workflowAvailable"]
    assert bool(result["designs"]) == (status != "pending")
    assert result["submissionState"] == ("accepted" if status == "running" else "uncertain" if status == "submission_unknown" else "not-submitted")


def test_auth_pagination_deleted_project_and_signed_reference_redaction(client):
    project = create(client)
    base = path(project) + "/image-jobs"
    assert client.get(base).json() == {"jobs": [], "nextOffset": None}
    for i in range(3):
        insert_job(client, project, {"id": f"job-{i}", "status": "pending"})
    assert [j["jobId"] for j in client.get(base + "?limit=2").json()["jobs"]] == ["job-2", "job-1"]
    assert client.get(base + "?limit=2").json()["nextOffset"] == 2
    assert client.get(base + "?limit=2&offset=2").json()["nextOffset"] is None
    assert client.get(base + "?limit=101").status_code == 422
    assert client.get(base + "?offset=-1").status_code == 422
    assert client.get(base, headers={"Authorization": ""}).status_code == 401
    assert client.get(base, headers={"Authorization": "Bearer other-test-token"}).status_code == 404
    assert client.get(base + "/job-0", headers={"Authorization": "Bearer other-test-token"}).status_code == 404
    other = create(client)
    assert client.get(path(other) + "/image-jobs/job-0").status_code == 404
    assert client.get(base + "/missing").status_code == 404
    plan = IllustrationPlan(prompt="A saved reference illustration.", alt="삽화", meaning="의미")
    graph = encoded(compile_reference_image(plan, 1, "test", ["https://private.example/image?signature=private-value"]))
    insert_job(client, project, {"id": "url-job", "status": "prepared", "workflow": graph})
    result = client.get(base + "/url-job")
    assert "private-value" not in result.text and result.json()["references"][0]["filename"] is None
    assert client.delete(path(project)).status_code == 204
    assert client.get(base).status_code == client.get(base + "/job-0").status_code == 404


def test_library_portrait_explicitly_has_no_workflow(client):
    project = create(client)
    insert_job(client, project, {"id": "library-job", "status": "ready", "library_portrait": True, "asset_id": "face"})
    result = client.get(path(project) + "/image-jobs/library-job").json()
    assert result["mode"] == "library" and result["submissionState"] == "not-applicable"
    assert result["workflowAvailable"] is False and result["prompts"] == []


def test_inspection_works_on_turso(remote):
    client, _, _ = remote
    project = create(client)
    insert_job(client, project, {"id": "remote-job", "status": "pending"})
    assert client.get(path(project) + "/image-jobs").json()["jobs"][0]["jobId"] == "remote-job"
    assert client.get(path(project) + "/image-jobs/remote-job").json()["prompts"] == []


def test_openapi_documents_typed_read_only_inspection(client):
    schema = client.get("/openapi.json").json()
    detail = schema["paths"]["/api/studio/projects/{project_id}/image-jobs/{job_id}"]["get"]
    assert "저장된 스냅샷" in detail["description"] and "API 키" in detail["description"]
    assert detail["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ImageJobDetail"}
    assert "text" in schema["components"]["schemas"]["WorkflowPrompt"]["required"]


def test_new_storyboard_retains_actual_reference_bindings_and_compositions(setup):
    from test_storyboard import enable
    from test_studio_batch import finish_batch
    client, service, project, _, _, control = setup
    card_ids = enable(setup, 4)
    assert finish_batch(setup)["generation"]["status"] == "ready"
    calls = len(control["calls"])
    listing = client.get(path(project) + "/image-jobs").json()
    sheet = next(j for j in listing["jobs"] if j["mode"] == "storyboard4")
    result = client.get(path(project) + "/image-jobs/" + sheet["jobId"]).json()
    sent = json.loads(submissions(control)[0].content)["workflow"]
    assert result["prompts"][0]["text"] == sent["4"]["inputs"]["prompt"]
    assert [d["cardId"] for d in result["designs"]] == card_ids
    assert all(d["composition"] is not None and d["legacyPrompt"] is None for d in result["designs"])
    assert all(r["purpose"] == "character" and r["partyId"] and r["assetId"] for r in result["references"])
    assert [r["imageNumber"] for r in result["references"]] == [1, 2]
    assert any(s["values"].get("steps") == 50 for s in result["settings"])
    assert len(control["calls"]) == calls


def test_single_reference_prompt_has_frozen_identity_metadata_and_required_design(setup, monkeypatch):
    from test_studio_generation import attach_characters
    client, service, project, _, _, control = setup
    attach_characters(setup)
    assert request_image(setup).status_code == 200
    sent = json.loads(submissions(control)[0].content)["workflow"]
    job_id = client.get(path(project) + "/image-jobs").json()["jobs"][0]["jobId"]
    before = client.get(path(project) + "/image-jobs/" + job_id).json()
    assert before["prompts"][0]["text"] == sent["4"]["inputs"]["prompt"]
    assert before["prompts"][1]["text"] == ""
    assert before["designs"][0]["composition"] is not None
    assert before["designs"][0]["legacyPrompt"] is None
    assert [r["imageNumber"] for r in before["references"]] == [1, 2]
    assert all(r["partyId"] and r["assetId"] for r in before["references"])
    state, version = service.store.get(project["id"], "alice")
    state["document"]["partyNames"].reverse()
    service.store.save(state, "alice", version)
    def must_not_run(*args, **kwargs):
        raise AssertionError("Inspection must not replan/recompile/poll")
    monkeypatch.setattr(service.provider, "call", must_not_run)
    monkeypatch.setattr(service.cloud, "request", must_not_run)
    monkeypatch.setattr("app.studio_generation.compile_reference_image", must_not_run)
    assert client.get(path(project) + "/image-jobs/" + job_id).json() == before
