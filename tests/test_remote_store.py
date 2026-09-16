"""The Turso path: the same routes and SQL, sent over HTTP to a remote SQLite."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Config
from app.main import create_app
from app.studio_api import SAMPLE_TEXT
from fake_hrana import FakeHrana
from test_studio import BASE, SETTINGS, complete, draft, path

AUTH = {"Authorization": "Bearer studio-test-token"}


@pytest.fixture
def remote(tmp_path):
    server = FakeHrana(str(tmp_path / "remote.sqlite3")).start()
    config = Config(db_path=str(tmp_path / "never-created.sqlite3"), provider="demo", turso_url=server.url,
                    turso_token="remote-test-token", api_keys={"studio-test-token": "alice"})
    with TestClient(create_app(config), headers=AUTH) as client:
        yield client, server, tmp_path
    server.stop()


def test_whole_workflow_runs_against_the_remote_database(remote):
    client, server, tmp_path = remote
    assert client.get("/health").json()["storage"] == "turso"
    assert not (tmp_path / "never-created.sqlite3").exists()
    assert all(auth == "Bearer remote-test-token" for auth in server.requests)

    response = client.post(BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": {**SETTINGS, "illustrations": "with"}})
    assert response.status_code == 201, response.text
    project = response.json()
    structure = client.get(path(project) + "/structure").json()
    structure["overview"]["court"] = "가상 법원"
    assert client.put(path(project) + "/structure", json=structure).status_code == 200
    assert client.put(path(project) + "/structure", json=structure).status_code == 409  # stale revision

    doc = draft(client, project)
    doc["title"] = "원격 저장소에 저장한 제목"
    first = client.put(path(project) + "/document", json=doc)
    assert first.status_code == 200, first.text
    assert client.put(path(project) + "/document", json=doc).status_code == 409  # stale saveRevision
    assert client.get(path(project) + "/document").json()["title"] == doc["title"]

    png = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(png, "PNG")
    upload = client.post(path(project) + "/assist/upload-image", files={"file": ("green.png", png.getvalue(), "image/png")},
                         data={"alt": "초록 사각형", "meaning": "테스트"})
    assert upload.status_code == 200, upload.text
    image = upload.json()["image"]
    served = client.get("/api/studio/assets/" + image["id"], headers={"Authorization": ""})
    assert served.status_code == 200 and served.content.startswith(b"\x89PNG")
    latest = client.get(path(project) + "/document").json()
    latest["images"] = [image]
    latest["sections"][0]["cards"][0]["imageId"] = image["id"]
    assert client.put(path(project) + "/document", json=latest).status_code == 200

    complete(client, project)
    publication = client.post(path(project) + "/publications").json()["publication"]
    assert client.put(path(project) + "/public", json={"publicationId": publication["id"]}).status_code == 200
    reading = client.get(BASE + "/reader/" + project["id"], headers={"Authorization": ""}).json()
    assert reading["status"] == "available"
    assert reading["publication"]["content"]["sections"][0]["cards"][0]["image"]["src"] == image["src"]

    # The picture-job lock takes an explicit BEGIN IMMEDIATE over the wire and holds until released.
    generation = client.app.state.studio_generation
    card = latest["sections"][0]["cards"][0]
    job = generation.claim(project["id"], "alice", card["id"], "digest-1")
    assert job["status"] == "pending"
    with pytest.raises(Exception) as busy:
        generation.claim(project["id"], "alice", card["id"], "digest-1")
    assert busy.value.detail["code"] == "image_in_progress"

    with TestClient(create_app(client.app.state.config), headers=AUTH) as restarted:
        assert restarted.get(path(project) + "/document").json()["title"] == doc["title"]
        assert [p["id"] for p in restarted.get(BASE + "/projects").json()] == [project["id"]]
