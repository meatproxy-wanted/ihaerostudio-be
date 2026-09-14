"""Preserve provider transport coverage using the current frontend API."""
import json

import httpx
import pytest

from app import studio_models as wire
from app.config import Config
from app.providers import strict_schema
from app.studio_api import SAMPLE_TEXT
from test_studio import BASE, SETTINGS, client, create, draft, path


@pytest.mark.parametrize("model", [wire.StructureContent, wire.DraftContent, wire.Suggestions,
                                  wire.Sentences, wire.Terms, wire.Explanation])
def test_strict_schema_preserves_frontend_properties(model):
    original = model.model_json_schema()
    schema = strict_schema(model)

    def check(node):
        if isinstance(node, list):
            for child in node:
                check(child)
        elif isinstance(node, dict):
            if node.get("type") == "object":
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            assert not {"default", "minLength", "maxLength"}.intersection(node)
            for key, child in node.items():
                if key in {"properties", "$defs"}:
                    for value in child.values():
                        check(value)
                else:
                    check(child)

    check(schema)
    assert model.model_json_schema() == original
    if model is wire.DraftContent:
        assert "title" in schema["properties"]
        assert {"type": "null"} in schema["$defs"]["Card"]["properties"]["partyId"]["anyOf"]


def use_openai(client):
    provider = client.app.state.provider
    provider.name = "openai"
    provider.config.openai_api_key = "test-secret-never-return"
    provider.config.openai_model = "test-model"


def test_analysis_and_draft_transport_use_frontend_contract(client, monkeypatch):
    fixture = create(client)
    structure = client.get(path(fixture) + "/structure").json()
    document = draft(client, fixture)
    outputs = {
        "StructureContent": {k: structure[k] for k in wire.StructureContent.model_fields},
        "DraftContent": {k: document[k] for k in wire.DraftContent.model_fields},
    }
    outputs["DraftContent"]["title"] = "FE 계약으로 반환한 테스트 초안"
    captured = []

    def fake_post(self, url, **kwargs):
        assert url == "https://api.openai.com/v1/responses"
        body = kwargs["json"]
        captured.append(body)
        assert kwargs["headers"]["Authorization"] == "Bearer test-secret-never-return"
        assert body["stream"] is False and body["store"] is False
        assert body["text"]["format"]["strict"] is True
        assert body["model"] == "test-model"
        payload = json.loads(body["input"][1]["content"])
        assert payload["settings"] == SETTINGS
        assert payload["source"]["paragraphs"][0]["text"] == SAMPLE_TEXT.split("\n\n")[0]
        name = body["text"]["format"]["name"]
        if name == "DraftContent":
            assert payload["structure"]["overview"] == structure["overview"]
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(outputs[name])}]},
        ]}, request=httpx.Request("POST", url))

    use_openai(client)
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    response = client.request("POST", BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": SETTINGS})
    assert response.status_code == 201, response.text
    project = response.json()
    response = client.request("POST", path(project) + "/document/generate")
    assert response.status_code == 200, response.text
    assert response.json()["document"]["title"] == outputs["DraftContent"]["title"]
    assert response.json()["project"]["id"] == project["id"]
    assert [item["text"]["format"]["name"] for item in captured] == ["StructureContent", "DraftContent"]


@pytest.mark.parametrize("phase", ["analysis", "draft"])
@pytest.mark.parametrize("mode,status,code", [
    ("429", 503, "ai_rate_limited"), ("401", 502, "ai_provider_error"),
    ("500", 502, "ai_provider_error"), ("timeout", 504, "ai_timeout"),
    ("refusal", 502, "ai_refusal"), ("incomplete", 502, "ai_incomplete_response"),
    ("empty", 502, "ai_provider_error"), ("malformed", 502, "ai_provider_error"),
])
def test_provider_failures_preserve_saved_frontend_data(client, monkeypatch, phase, mode, status, code):
    project = create(client)
    document = draft(client, project)
    before = client.get(BASE + "/projects").json()

    def fake_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        if mode == "timeout":
            raise httpx.ReadTimeout("test-secret-never-return", request=request)
        if mode.isdigit():
            return httpx.Response(int(mode), json={"error": "test-secret-never-return"}, request=request)
        body = {"status": "completed", "output": []}
        if mode == "refusal":
            body["output"] = [{"type": "message", "content": [{"type": "refusal", "refusal": "test-secret-never-return"}]}]
        elif mode == "incomplete":
            body["status"] = "incomplete"
        elif mode == "malformed":
            body = ["unexpected shape"]
        return httpx.Response(200, json=body, request=request)

    use_openai(client)
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    response = (client.request("POST", BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": SETTINGS})
                if phase == "analysis" else client.request("POST", path(project) + "/document/generate"))
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code
    assert "test-secret-never-return" not in response.text
    assert client.get(BASE + "/projects").json() == before
    assert client.get(path(project) + "/document").json() == document


def test_configuration_validation():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Config(provider="openai", openai_api_key="", openai_model="test-model")
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        Config(provider="openai", openai_api_key="test-secret", openai_model=" ")
    with pytest.raises(ValueError, match="OPENAI_MAX_OUTPUT_TOKENS"):
        Config(openai_max_output_tokens=0)
    with pytest.raises(ValueError, match="Production"):
        Config(environment="production", api_keys={"dev-only-change-me": "local"})
    assert "test-secret" not in repr(Config(provider="openai", openai_api_key="test-secret", openai_model="test-model"))
