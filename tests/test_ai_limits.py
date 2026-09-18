from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.ai_limits import AiCallLimiter, consume_ai_call
from app.config import Config
from app.main import create_app
from app.store import Store
from test_auth_modes import ALICE, BOB
from test_studio import BASE, SETTINGS
from app.studio_api import SAMPLE_TEXT


def test_limits_are_atomic_across_workers_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("app.ai_limits.time.time", lambda: 3601)
    config = Config(ai_calls_per_hour=2, ai_calls_global_per_hour=3)
    store = Store(str(tmp_path / "limits.sqlite3"))
    limiter = AiCallLimiter(store, config)
    def attempt(_):
        try:
            limiter.consume("alice")
            return True
        except HTTPException as error:
            assert error.status_code == 429 and error.headers["Retry-After"] == "3599"
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(8))) == 2
    restarted = AiCallLimiter(store, config)
    restarted.consume("bob")
    with pytest.raises(HTTPException):
        restarted.consume("charlie")
    with store.connect() as db:
        assert db.execute("SELECT calls FROM studio_ai_usage WHERE scope='global'").fetchone()["calls"] == 3
        assert db.execute("SELECT calls FROM studio_ai_usage WHERE scope='owner:charlie'").fetchone() is None
    monkeypatch.setattr("app.ai_limits.time.time", lambda: 7201)
    restarted.consume("alice")


def test_authenticated_http_context_reaches_threadpool_without_blocking_reads(tmp_path, monkeypatch):
    from app import studio_provider
    original = studio_provider.analyze
    def analyze(*args):
        consume_ai_call()
        return original(*args)
    monkeypatch.setattr(studio_provider, "analyze", analyze)
    app = create_app(Config(db_path=str(tmp_path / "http.sqlite3"), ai_calls_per_hour=1))
    with TestClient(app) as client:
        payload = {"text": SAMPLE_TEXT, "settings": SETTINGS}
        assert client.post(BASE + "/projects/text", json=payload, headers=ALICE).status_code == 201
        denied = client.post(BASE + "/projects/text", json=payload, headers=ALICE)
        assert denied.status_code == 429 and denied.json()["detail"]["code"] == "ai_call_limit"
        assert "Retry-After" in denied.headers
        assert client.get(BASE + "/projects", headers=ALICE).status_code == 200
        assert client.post(BASE + "/projects/text", json=payload, headers=BOB).status_code == 201


def test_disabled_limits_do_not_create_usage_storage(tmp_path):
    store = Store(str(tmp_path / "disabled.sqlite3"))
    AiCallLimiter(store, Config()).consume("maker")
    with store.connect() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='studio_ai_usage'").fetchone() is None


@pytest.mark.parametrize("field", ["ai_calls_per_hour", "ai_calls_global_per_hour"])
def test_negative_limit_is_rejected(field):
    with pytest.raises(ValueError, match="nonnegative"):
        Config(**{field: -1})
