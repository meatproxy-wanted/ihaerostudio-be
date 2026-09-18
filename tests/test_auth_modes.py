"""Anonymous mode: every browser gets its own workspace without accounts; keys mode stays strict."""
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.studio_api import SAMPLE_TEXT
from test_studio import BASE, SETTINGS

ALICE = {"Authorization": "Bearer visitor-0f2c7f3a-1111-4d1f-9d3e-aaaaaaaaaaaa"}
BOB = {"Authorization": "Bearer visitor-0f2c7f3a-2222-4d1f-9d3e-bbbbbbbbbbbb"}


def make(tmp_path, **overrides):
    return create_app(Config(db_path=str(tmp_path / "auth.sqlite3"), provider="demo", **overrides))


def test_anonymous_mode_gives_each_browser_its_own_workspace(tmp_path):
    app = make(tmp_path)
    with TestClient(app) as client:
        created = client.post(BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": SETTINGS}, headers=ALICE)
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        assert [p["id"] for p in client.get(BASE + "/projects", headers=ALICE).json()] == [project_id]
        assert client.get(BASE + "/projects", headers=BOB).json() == []
        assert client.get(BASE + "/projects/" + project_id, headers=BOB).status_code == 404
        with app.state.store.connect() as db:
            owners = [row["owner"] for row in db.execute("SELECT owner FROM studio_projects")]
        assert owners and all(owner.startswith("anon-") and "visitor" not in owner for owner in owners)


def test_anonymous_mode_still_rejects_missing_or_malformed_tokens(tmp_path):
    with TestClient(make(tmp_path)) as client:
        assert client.get(BASE + "/projects").status_code == 401
        for bad in ["short", "has spaces in it 1234", "bad/slash-token-1234", "x" * 201]:
            assert client.get(BASE + "/projects", headers={"Authorization": "Bearer " + bad}).status_code == 401, bad


def test_registered_tokens_keep_their_maker_id_in_anonymous_mode(tmp_path):
    app = make(tmp_path, api_keys={"registered-token-for-tests": "maker-01"})
    with TestClient(app) as client:
        response = client.post(BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": SETTINGS},
                               headers={"Authorization": "Bearer registered-token-for-tests"})
        assert response.status_code == 201
        with app.state.store.connect() as db:
            assert [row["owner"] for row in db.execute("SELECT owner FROM studio_projects")] == ["maker-01"]


def test_keys_mode_only_accepts_registered_tokens(tmp_path):
    with TestClient(make(tmp_path, auth_mode="keys", api_keys={"registered-token-for-tests": "maker"})) as client:
        assert client.get(BASE + "/projects", headers={"Authorization": "Bearer registered-token-for-tests"}).status_code == 200
        assert client.get(BASE + "/projects", headers=ALICE).status_code == 401


def test_production_validates_registered_keys_in_both_modes(monkeypatch):
    Config(environment="production", provider="demo")
    with pytest.raises(ValueError, match="API_KEYS"):
        Config(environment="production", provider="demo", auth_mode="keys")
    for mode in ["anonymous", "keys"]:
        with pytest.raises(ValueError, match="Production"):
            Config(environment="production", auth_mode=mode, api_keys={"short-key": "maker"})
        Config(environment="production", auth_mode=mode, api_keys={"x" * 32: "maker"})
    with pytest.raises(ValueError, match="AUTH_MODE"):
        Config(auth_mode="open")
    monkeypatch.setenv("API_KEYS", "")
    assert Config().api_keys == {}


def test_public_development_token_never_maps_to_shared_workspace(tmp_path):
    with TestClient(make(tmp_path)) as client:
        created = client.post(BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": SETTINGS},
                              headers={"Authorization": "Bearer dev-only-change-me"})
        assert created.status_code == 401
        with client.app.state.store.connect() as db:
            assert list(db.execute("SELECT owner FROM studio_projects")) == []
    with pytest.raises(ValueError, match="public development"):
        Config(api_keys={"dev-only-change-me": "maker-local"})
