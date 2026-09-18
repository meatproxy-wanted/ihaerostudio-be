import copy
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Config
from app.main import create_app
from app.studio_api import SAMPLE_TEXT
from app.studio_domain import anchor_text

SETTINGS = {"tone": "haeyo", "naming": "initial", "illustrations": "none"}
BASE = "/api/studio"
AUTH = {"Authorization": "Bearer studio-test-token"}


@pytest.fixture
def client(tmp_path):
    app = create_app(Config(db_path=str(tmp_path / "test.sqlite3"), provider="demo",
                            api_keys={"studio-test-token": "alice", "other-test-token": "bob"}))
    with TestClient(app, headers=AUTH) as client:
        yield client


def create(client, settings=None):
    response = client.post(BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": settings or SETTINGS})
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_conflict_leaves_no_orphan_bytes(client, monkeypatch):
    from app import studio_api
    project = create(client)
    store = client.app.state.studio
    original = studio_api.cleaned_upload
    def concurrent_edit(*args):
        cleaned = original(*args)
        state, version = store.get(project["id"], "alice")
        state["project"]["title"] = "동시 수정"
        store.save(state, "alice", version)
        return cleaned
    monkeypatch.setattr(studio_api, "cleaned_upload", concurrent_edit)
    out = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(out, "PNG")
    response = client.post(path(project) + "/assist/upload-image", files={"file": ("test.png", out.getvalue(), "image/png")})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "version_conflict"
    state, _ = store.get(project["id"], "alice")
    assert state["project"]["title"] == "동시 수정" and state["assets"] == []
    with store.store.connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM studio_assets").fetchone()["n"] == 0


def test_upload_insert_failure_rolls_back_project(client, monkeypatch):
    from app.studio_store import StudioStore
    project = create(client)
    before = client.app.state.studio.get(project["id"], "alice")
    def broken_insert(*args):
        raise RuntimeError("simulated asset insert failure")
    monkeypatch.setattr(StudioStore, "insert_asset", staticmethod(broken_insert))
    out = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(out, "PNG")
    with pytest.raises(RuntimeError, match="simulated asset"):
        client.post(path(project) + "/assist/upload-image", files={"file": ("test.png", out.getvalue(), "image/png")})
    assert client.app.state.studio.get(project["id"], "alice") == before


def path(project):
    return BASE + "/projects/" + project["id"]


def draft(client, project):
    response = client.post(path(project) + "/document/generate")
    assert response.status_code == 200, response.text
    return response.json()["document"]


def complete(client, project):
    run = client.post(path(project) + "/review/run").json()["run"]
    for item in run["items"]:
        if item["level"] == "required" and not item["dismissal"]:
            response = client.post(path(project) + "/review/dismiss", json={"key": item["key"], "memo": "가상 예제에 대한 테스트 확인"})
            assert response.status_code == 200
    checklist = ["numbers", "relations", "claims"]
    if project["settings"]["illustrations"] == "with":
        checklist.append("images")
    response = client.post(path(project) + "/review/complete", json={"checklist": checklist})
    assert response.status_code == 200, response.text
    return response.json()


def test_full_workflow_and_fe_contract(client):
    contract = {}
    project = create(client)
    contract["project"] = project
    prefix = path(project)
    contract["projects"] = client.get(BASE + "/projects").json()
    contract["source"] = client.get(prefix + "/source").json()
    assert SAMPLE_TEXT.split("\n\n")[0] == contract["source"]["paragraphs"][0]["text"]
    contract["structure"] = client.get(prefix + "/structure").json()
    contract["structure"]["overview"]["court"] = "가상 법원"
    contract["structureResult"] = client.put(prefix + "/structure", json=contract["structure"]).json()
    doc = draft(client, project)
    assert not any(s["verified"] for sec in doc["sections"] for c in sec["cards"] for s in c["sentences"])
    contract["document"] = doc
    doc["sections"][0]["cards"][0]["sentences"][0]["verified"] = True
    contract["documentResult"] = client.put(prefix + "/document", json=doc).json()
    assert contract["documentResult"]["document"]["contentRevision"] == doc["contentRevision"]
    contract["runResult"] = client.post(prefix + "/review/run").json()
    assert contract["runResult"]["run"]["items"]
    contract["completionResult"] = complete(client, project)
    contract["run"] = client.get(prefix + "/review").json()
    contract["publishResult"] = client.post(prefix + "/publications").json()
    publication_id = contract["publishResult"]["publication"]["id"]
    assert contract["publishResult"]["publication"]["reviewed"] is True
    contract["publications"] = client.get(prefix + "/publications").json()
    contract["publication"] = client.get(prefix + "/publications/" + publication_id).json()
    repeated = client.post(prefix + "/publications").json()
    assert repeated["publication"]["id"] == publication_id
    assert client.put(prefix + "/public", json={"publicationId": publication_id}).status_code == 200
    public = client.get(BASE + "/reader/" + project["id"], headers={"Authorization": ""}).json()
    contract["publicReading"] = public
    serialized = json.dumps(public)
    for forbidden in ["anchors", "verified", "origin", "sourceLabel", "dismissal", "memo"]:
        assert f'"{forbidden}"' not in serialized
    assert client.put(prefix + "/public", json={"publicationId": None}).status_code == 200
    assert client.get(BASE + "/reader/" + project["id"]).json() == {"status": "unavailable"}
    if target := os.getenv("STUDIO_CONTRACT_OUT"):
        with open(target, "w") as file:
            json.dump(contract, file, ensure_ascii=False)


def test_ownership_and_authentication(client):
    project = create(client)
    for suffix in ["", "/source", "/structure", "/document", "/review", "/publications"]:
        assert client.get(path(project) + suffix, headers={"Authorization": "Bearer other-test-token"}).status_code == 404
        assert client.get(path(project) + suffix, headers={"Authorization": ""}).status_code == 401
    assert client.delete(path(project), headers={"Authorization": "Bearer other-test-token"}).status_code == 404
    assert client.get(BASE + "/projects", headers={"Authorization": "Bearer other-test-token"}).json() == []


def test_autosave_conflict_and_invalid_references(client):
    project = create(client)
    doc = draft(client, project)
    stale = copy.deepcopy(doc)
    first = client.put(path(project) + "/document", json=doc)
    assert first.status_code == 200
    assert client.put(path(project) + "/document", json=stale).status_code == 409
    new = first.json()["document"]
    new["sections"][0]["cards"][0]["sentences"][0]["anchors"][0]["paragraphId"] = "someone-elses-paragraph"
    assert client.put(path(project) + "/document", json=new).status_code == 422
    structure = client.get(path(project) + "/structure").json()
    structure["overview"]["caseName"] = "변경"
    assert client.put(path(project) + "/structure", json=structure).status_code == 200
    assert client.put(path(project) + "/structure", json=structure).status_code == 409


def test_review_cannot_be_reused_after_content_or_evidence_edits(client):
    project = create(client)
    draft(client, project)
    complete(client, project)
    doc = client.get(path(project) + "/document").json()
    original_revision = doc["contentRevision"]
    doc["sections"][0]["cards"][0]["sentences"][0]["anchors"] = []
    response = client.put(path(project) + "/document", json=doc).json()
    assert response["document"]["contentRevision"] == original_revision
    assert response["project"]["review"]["completedContentRevision"] is None
    assert client.post(path(project) + "/review/complete", json={"checklist": ["numbers", "relations", "claims"]}).status_code == 409
    run = client.post(path(project) + "/review/run").json()["run"]
    assert any(i["category"] == "no-anchor" and i["dismissal"] is None for i in run["items"])


def test_publication_is_immutable_and_unreviewed_copy_is_private(client):
    project = create(client)
    draft(client, project)
    unreviewed = client.post(path(project) + "/publications").json()["publication"]
    assert client.put(path(project) + "/public", json={"publicationId": unreviewed["id"]}).status_code == 409
    complete(client, project)
    reviewed = client.post(path(project) + "/publications").json()["publication"]
    client.put(path(project) + "/public", json={"publicationId": reviewed["id"]})
    snapshot = client.get(BASE + "/reader/" + project["id"]).json()
    doc = client.get(path(project) + "/document").json()
    doc["title"] = "바뀐 비공개 작업 제목"
    assert client.put(path(project) + "/document", json=doc).status_code == 200
    assert client.get(BASE + "/reader/" + project["id"]).json() == snapshot
    assert client.delete(path(project)).status_code == 204
    assert client.get(BASE + "/reader/" + project["id"]).json() == {"status": "unavailable"}
    assert client.get(path(project)).status_code == 404


def test_settings_invalidate_and_forged_revisions_are_ignored(client):
    project = create(client)
    draft(client, project)
    complete(client, project)
    result = client.put(path(project) + "/settings", json={**SETTINGS, "tone": "hamnida"}).json()
    assert result["settingsRevision"] == 1
    assert result["review"]["completedAt"] is None
    doc = client.get(path(project) + "/document").json()
    doc.update(contentRevision=999, basedOnSettingsRevision=999, basedOnStructureRevision=999)
    saved = client.put(path(project) + "/document", json=doc).json()["document"]
    assert saved["contentRevision"] != 999
    assert saved["basedOnSettingsRevision"] == 0


def test_naming_setting_decides_party_names_from_the_start(client):
    def parties(settings):
        return client.get(path(create(client, settings)) + "/structure").json()["parties"]
    by_role = parties({**SETTINGS, "naming": "role"})
    assert [p["displayName"] for p in by_role] == [p["easyRole"] or p["legalStatus"] for p in by_role]
    assert "A씨" not in {p["displayName"] for p in by_role}
    assert [p["displayName"] for p in parties({**SETTINGS, "naming": "legal"})] == ["원고", "피고"]
    assert [p["displayName"] for p in parties(SETTINGS)] == ["A씨", "B씨"]


def test_dismiss_all_records_every_key_in_one_save(client):
    project = create(client)
    draft(client, project)
    run = client.post(path(project) + "/review/run").json()["run"]
    keys = [item["key"] for item in run["items"]]
    assert len(keys) >= 2
    missing = client.post(path(project) + "/review/dismiss-all", json={"keys": keys + ["nope"], "memo": ""})
    assert missing.status_code == 404
    assert all(i["dismissal"] is None for i in client.get(path(project) + "/review").json()["items"])
    response = client.post(path(project) + "/review/dismiss-all", json={"keys": keys + keys[:1], "memo": " 전부 대조했어요 "})
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(i["dismissal"]["memo"] == "전부 대조했어요" for i in result["run"]["items"])
    assert result["project"]["review"]["openRequiredCount"] == 0
    assert client.post(path(project) + "/review/dismiss-all", json={"keys": [], "memo": ""}).status_code == 422


def test_png_upload_and_external_asset_rejection(client):
    project = create(client, {**SETTINGS, "illustrations": "with"})
    doc = draft(client, project)
    out = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(out, "PNG")
    response = client.post(path(project) + "/assist/upload-image", files={"file": ("test.png", out.getvalue(), "image/png")},
                           data={"alt": "파란색 사각형", "meaning": "테스트용 이미지"})
    assert response.status_code == 200, response.text
    image = response.json()["image"]
    assert image["src"] == "http://127.0.0.1:8100/api/studio/assets/" + image["id"]
    served = client.get("/api/studio/assets/" + image["id"], headers={"Authorization": ""})
    assert served.status_code == 200 and served.headers["content-type"].startswith("image/png")
    assert served.content.startswith(b"\x89PNG") and "immutable" in served.headers["cache-control"]
    assert client.get("/api/studio/assets/missing").status_code == 404
    doc["images"] = [image]
    doc["sections"][0]["cards"][0]["imageId"] = image["id"]
    response = client.put(path(project) + "/document", json=doc)
    assert response.status_code == 200, response.text
    doc = response.json()["document"]
    doc["images"][0]["src"] = "https://attacker.invalid/track"
    assert client.put(path(project) + "/document", json=doc).status_code == 422
    svg = client.post(path(project) + "/assist/upload-image", files={"file": ("bad.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")})
    assert svg.status_code == 422


def test_demo_does_not_fake_ai_and_failed_analysis_leaves_no_project(client, monkeypatch):
    project = create(client)
    doc = draft(client, project)
    sentence = doc["sections"][0]["cards"][0]["sentences"][0]
    assert client.post(path(project) + "/assist/simplify", json={"sentenceId": sentence["id"], "text": sentence["text"]}).status_code == 503
    assert client.post(path(project) + "/assist/split", json={"sentenceId": sentence["id"], "text": "첫 문장입니다. 다음 문장입니다."}).json() == {"sentences": ["첫 문장입니다.", "다음 문장입니다."]}
    from app.store import fail
    def failure(*args):
        fail(502, "ai_provider_error", "AI 실패")
    monkeypatch.setattr("app.studio_provider.analyze", failure)
    before = client.get(BASE + "/projects").json()
    assert client.post(BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": SETTINGS}).status_code == 502
    assert client.get(BASE + "/projects").json() == before


def test_utf16_anchor_offsets():
    source = {"paragraphs": [{"id": "p1", "text": "😀원고"}]}
    assert anchor_text(source, {"paragraphId": "p1", "start": 2, "end": 4}) == "원고"
    with pytest.raises(Exception) as exc:
        anchor_text(source, {"paragraphId": "p1", "start": 1, "end": 2})
    assert exc.value.status_code == 422


def test_atomic_concurrent_edits(client):
    project = create(client)
    doc = draft(client, project)
    def save(title):
        return client.put(path(project) + "/document", json={**doc, "title": title}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["첫 번째", "두 번째"]))
    assert sorted(results) == [200, 409]


def test_reset_forbidden_in_production(tmp_path):
    token = "production-random-looking-test-token-32chars"
    app = create_app(Config(db_path=str(tmp_path / "production.db"), environment="production", provider="demo", api_keys={token: "test"}))
    with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
        assert client.post(BASE + "/demo/reset").status_code == 403


def test_safe_svg_is_supported_without_active_content(client):
    project = create(client)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="blue"/></svg>'
    response = client.post(path(project) + "/assist/upload-image", files={"file": ("safe.svg", svg, "image/svg+xml")})
    assert response.status_code == 200, response.text
    served = client.get("/api/studio/assets/" + response.json()["image"]["id"])
    assert served.headers["content-type"].startswith("image/svg+xml") and b"<svg" in served.content
    for unsafe in [b'<svg><script>alert(1)</script></svg>', b'<svg><image href="https://example.com"/></svg>',
                   b'<!DOCTYPE svg [<!ENTITY e SYSTEM "file:///etc/passwd">]><svg>&e;</svg>',
                   b'<svg><rect fill="url(https://example.com)"/></svg>']:
        response = client.post(path(project) + "/assist/upload-image", files={"file": ("unsafe.svg", unsafe, "image/svg+xml")})
        assert response.status_code == 422


def test_pdf_upload_uses_real_extracted_source(client):
    from reportlab.pdfgen import canvas
    out = io.BytesIO()
    pdf = canvas.Canvas(out)
    pdf.drawString(72, 720, "Fictional judgment: a unique source for PDF upload testing.")
    pdf.save()
    response = client.post(BASE + "/projects/pdf", files={"file": ("court.pdf", out.getvalue(), "application/pdf")},
                           data={"settings": json.dumps(SETTINGS)})
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["source"]["kind"] == "pdf"
    assert project["source"]["byteSize"] == len(out.getvalue())
    source = client.get(path(project) + "/source").json()
    assert "unique source" in source["paragraphs"][0]["text"]
    assert source["paragraphs"][0]["page"] == 1
    # Only the text survives: the uploaded file is not kept anywhere.
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT original FROM studio_projects WHERE id=?", (project["id"],)).fetchone()[0] is None


def test_fe_optional_memo_and_flag_only_save(client):
    project = create(client)
    draft(client, project)
    structure = client.get(path(project) + "/structure").json()
    before = client.get(path(project) + "/document").json()
    structure["keyFacts"][0]["flags"] = []
    result = client.put(path(project) + "/structure", json=structure).json()
    assert result["structure"]["revision"] == structure["revision"]
    assert client.get(path(project) + "/document").json() == before
    run = client.post(path(project) + "/review/run").json()["run"]
    response = client.post(path(project) + "/review/dismiss", json={"key": run["items"][0]["key"], "memo": ""})
    assert response.status_code == 200
    assert response.json()["run"]["items"][0]["dismissal"]["memo"] == ""
