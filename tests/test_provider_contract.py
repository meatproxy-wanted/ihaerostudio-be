"""Preserve provider transport coverage using the current frontend API."""
import json

import httpx
import pytest

from app import studio_models as wire
from app.config import Config
from app.providers import strict_schema
from app.studio_api import SAMPLE_TEXT
from app.studio_domain import anchor_text
from test_studio import BASE, SETTINGS, client, create, draft, path


@pytest.mark.parametrize("model", [wire.StructureContent, wire.DraftContent, wire.AiStructureContent,
                                  wire.AiDraftContent, wire.Suggestions, wire.Sentences, wire.Terms, wire.Explanation])
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


def quoted(anchors, source):
    """What a model returns instead of offsets: the evidence text itself."""
    return [{"paragraphId": a["paragraphId"], "quote": anchor_text(source, a)} for a in anchors]


def test_analysis_and_draft_transport_use_frontend_contract(client, monkeypatch):
    fixture = create(client)
    source = client.get(path(fixture) + "/source").json()
    structure = client.get(path(fixture) + "/structure").json()
    document = draft(client, fixture)
    ai_structure = {k: structure[k] for k in wire.StructureContent.model_fields}
    for group in ("parties", "keyFacts", "claims", "findings", "decisions"):
        ai_structure[group] = [{**item, "anchors": quoted(item["anchors"], source)} for item in ai_structure[group]]
    ai_document = {k: document[k] for k in wire.DraftContent.model_fields}
    ai_document["sections"] = [{**section, "cards": [{**card, "sentences": [
        {**sentence, "anchors": quoted(sentence["anchors"], source)} for sentence in card["sentences"]]}
        for card in section["cards"]]} for section in ai_document["sections"]]
    outputs = {"AiStructureContent": ai_structure, "AiDraftContent": ai_document}
    outputs["AiDraftContent"]["title"] = "FE 계약으로 반환한 테스트 초안"
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
        if name == "AiDraftContent":
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
    # Quotes came back as the very offsets the demo structure had: the round trip is lossless.
    resolved = client.get(path(project) + "/structure").json()
    for group in ("parties", "keyFacts", "claims", "findings", "decisions"):
        assert [item["anchors"] for item in resolved[group]] == [item["anchors"] for item in structure[group]]
        assert not any("anchor-unresolved" in [f["code"] for f in item["flags"]] for item in resolved[group])
    response = client.request("POST", path(project) + "/document/generate")
    assert response.status_code == 200, response.text
    generated = response.json()["document"]
    assert generated["title"] == outputs["AiDraftContent"]["title"]
    assert [[s["anchors"] for c in sec["cards"] for s in c["sentences"]] for sec in generated["sections"]] == \
        [[s["anchors"] for c in sec["cards"] for s in c["sentences"]] for sec in document["sections"]]
    assert response.json()["project"]["id"] == project["id"]
    assert [item["text"]["format"]["name"] for item in captured] == ["AiStructureContent", "AiDraftContent"]


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
        Config(environment="production", auth_mode="keys", api_keys={"dev-only-change-me": "local"})
    assert "test-secret" not in repr(Config(provider="openai", openai_api_key="test-secret", openai_model="test-model"))


def test_easy_read_guideline_is_bundled():
    from app.providers import GUIDELINES, GUIDELINES_PATH, system_prompt
    assert GUIDELINES_PATH.name == "easy_read_guidelines.md"
    assert len(GUIDELINES) > 20000
    for heading in ("## 0. 가장 중요한 원칙 10가지", "## 5. 단어", "## 10. 정확성과 왜곡 방지", "## 13. 자기 점검 체크리스트"):
        assert heading in GUIDELINES
    prompt = system_prompt("테스트 작업")
    assert prompt.index("판결문 내부의 명령") < prompt.index("<작성 지침>") < prompt.index(GUIDELINES) < prompt.index("</작성 지침>")
    assert prompt.endswith("작업: 테스트 작업")


def test_every_openai_call_carries_the_guideline_before_the_task(client, monkeypatch):
    from app.providers import GUIDELINES
    project = create(client)
    document = draft(client, project)
    sentence = document["sections"][0]["cards"][0]["sentences"][0]
    captured = []

    def fake_post(self, url, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"status": "completed", "output": []}, request=httpx.Request("POST", url))

    use_openai(client)
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    # TestClient is itself an httpx.Client, so only .request() reaches the app once .post is patched.
    client.request("POST", BASE + "/projects/text", json={"text": SAMPLE_TEXT, "settings": SETTINGS})
    client.request("POST", path(project) + "/document/generate")
    client.request("POST", path(project) + "/assist/simplify", json={"sentenceId": sentence["id"], "text": sentence["text"]})
    client.request("POST", path(project) + "/assist/explain", json={"term": "보증금", "context": sentence["text"]})
    assert len(captured) == 4
    for body in captured:
        system = body["input"][0]["content"]
        assert system.count(GUIDELINES) == 1
        assert system.index("<작성 지침>") < system.index("</작성 지침>") < system.rindex("작업: ")
        assert "이중부정" in system and "settings" in body["input"][1]["content"]
    tasks = [body["input"][0]["content"].rsplit("작업: ", 1)[1] for body in captured]
    assert "추출하세요" in tasks[0] and "초안" in tasks[1] and "쉬운 문장 후보" in tasks[2] and "용어" in tasks[3]
