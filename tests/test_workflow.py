import io
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.config import Config
from app.main import create_app
from app.models import Document

TEXT = """사건: 가상 임대차보증금 반환 사건 (테스트용)

원고: A씨, 집을 빌린 사람

피고: B씨, 집을 빌려준 사람

원고 주장: A씨는 B씨에게 보증금 1,000만 원을 돌려달라고 했습니다.

피고 주장: B씨는 보증금을 돌려줄 수 없다고 주장했습니다.

법원 판단: 법원은 임대차계약이 끝났다고 판단했습니다.

법원 결정: 법원은 B씨에게 A씨의 보증금 1,000만 원을 돌려주라고 결정했습니다."""


@pytest.fixture
def client(tmp_path):
    app = create_app(Config(db_path=str(tmp_path / "test.sqlite3"), api_keys={"alice-key": "alice", "bob-key": "bob"}))
    with TestClient(app, headers={"Authorization": "Bearer alice-key"}) as c:
        yield c


def call(client, method, url, body=None, status=200):
    response = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert response.status_code == status, response.text
    return response.json() if status != 204 else None


def create(client, text=TEXT):
    return call(client, "post", "/api/v1/documents/text", {"title": "가상 판결을 쉽게 설명해요", "text": text}, 201)


def action(client, doc, suffix, body=None, status=200, method="post"):
    return call(client, method, f'/api/v1/documents/{doc["id"]}/{suffix}', {"expected_version": doc["version"], **(body or {})}, status)


def draft(client):
    d = create(client)
    d = action(client, d, "structure/analyze")
    d = action(client, d, "structure/confirm", {"confirmed": True, "note": "테스트 원문 대조 완료"})
    return action(client, d, "draft")


def approved(client, d):
    d = action(client, d, "reviews")
    for issue in list(d["review"]["issues"]):
        d = action(client, d, f'reviews/issues/{issue["id"]}/resolve', {"note": "자동화 테스트용 가상 문서임을 확인했습니다."})
    return action(client, d, "reviews/approve", {"confirmed": True, "note": "테스트 자료 확인 완료"})


def content(block):
    return {k: v for k, v in block.items() if k != "id"}


def test_complete_workflow_pdf_and_share(client):
    d = draft(client)
    refs = call(client, "get", f'/api/v1/documents/{d["id"]}/blocks/{d["blocks"][0]["id"]}/sources')
    ref = refs[0]
    assert d["source"]["pages"][0]["text"][ref["page_start"]:ref["page_end"]] == ref["quote"]
    action(client, d, "exports", status=409)
    d = approved(client, d)
    exported = action(client, d, "exports", {"share": True}, 201)
    pdf = client.get(exported["pdf_path"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "공식 판결문" in text and "1,000만 원" in text
    public = client.get(exported["share_path"], headers={"Authorization": ""})
    assert public.status_code == 200 and "원문" in public.text
    assert "paragraph_id" not in public.text and "테스트 자료 확인 완료" not in public.text
    call(client, "delete", f'/api/v1/exports/{exported["id"]}/share', status=204)
    assert client.get(exported["share_path"]).status_code == 404


def test_auth_and_owner_isolation(client):
    d = create(client)
    url = f'/api/v1/documents/{d["id"]}'
    assert client.get(url, headers={"Authorization": ""}).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer bob-key"}).status_code == 404
    assert client.get("/api/v1/documents", headers={"Authorization": "Bearer bob-key"}).json()["items"] == []


def test_version_conflict(client):
    d = create(client)
    action(client, d, "structure/analyze")
    action(client, d, "structure/analyze", status=409)


def test_unknown_cannot_confirm(client):
    d = create(client, "이 문장은 어떤 종류의 판결문인지 알 수 없습니다.")
    d = action(client, d, "structure/analyze")
    assert d["structure"]["facts"][0]["kind"] == "unknown"
    action(client, d, "structure/confirm", {"confirmed": True, "note": "확인"}, status=409)


def test_structure_requires_valid_evidence(client):
    d = create(client)
    d = action(client, d, "structure/analyze")
    s = d["structure"]
    s["facts"][0]["evidence"][0]["quote"] = "없는 원문"
    result = action(client, d, "structure", {"structure": s}, 422, "put")
    assert result["detail"]["code"] == "invalid_evidence"


def test_claim_requires_speaker_and_cannot_be_decision(client):
    d = draft(client)
    block = next(b for b in d["blocks"] if b["kind"] == "claim")
    c = content(block); c["speaker_id"] = None
    action(client, d, f'blocks/{block["id"]}', {"content": c}, 422, "put")
    c = content(block); c["section"] = "decision"
    action(client, d, f'blocks/{block["id"]}', {"content": c}, 422, "put")


def test_proposal_staged_apply_and_stale(client):
    d = draft(client); b = d["blocks"][0]
    c = content(b); c["text"] = "첫 번째 문장입니다. 두 번째 문장입니다."
    d = action(client, d, f'blocks/{b["id"]}', {"content": c}, method="put")
    original_blocks = d["blocks"]
    d = action(client, d, f'blocks/{b["id"]}/proposals', {"action": "split"}, 201)
    assert d["blocks"] == original_blocks
    proposal = d["proposals"][-1]
    assert len(proposal["after"]) == 2
    d = action(client, d, f'proposals/{proposal["id"]}/apply')
    assert len(d["blocks"]) == len(original_blocks) + 1
    action(client, d, f'proposals/{proposal["id"]}/apply', status=409)
    d = action(client, d, f'blocks/{b["id"]}/proposals', {"action": "split"}, 201)
    proposal = d["proposals"][-1]
    c["text"] = "다른 편집입니다."
    d = action(client, d, f'blocks/{b["id"]}', {"content": c}, method="put")
    action(client, d, f'proposals/{proposal["id"]}/apply', status=409)


def test_demo_ai_not_pretended(client):
    d = draft(client)
    result = action(client, d, f'blocks/{d["blocks"][0]["id"]}/proposals', {"action": "simplify"}, 503)
    assert result["detail"]["code"] == "ai_not_configured"


def test_approval_requires_all_checks(client):
    d = action(client, draft(client), "reviews")
    action(client, d, "reviews/approve", {"confirmed": True, "note": "확인"}, 409)


def test_edit_invalidates_review_and_export_stays_immutable(client):
    d = approved(client, draft(client)); exported = action(client, d, "exports", {"share": True}, 201)
    original = client.get(exported["pdf_path"]).content
    b = d["blocks"][0]; c = content(b); c["text"] = "새로 편집한 내용입니다."
    d = action(client, d, f'blocks/{b["id"]}', {"content": c}, method="put")
    assert d["review"] is None and d["status"] == "editing"
    action(client, d, "exports", status=409)
    assert client.get(exported["pdf_path"]).content == original
    assert "새로 편집한 내용" not in client.get(exported["share_path"]).text


def test_amount_and_image_time_checks(client):
    d = draft(client); b = next(b for b in d["blocks"] if b["kind"] == "decision")
    image = io.BytesIO(); Image.new("RGB", (24, 24), "white").save(image, "PNG")
    result = client.post(f'/api/v1/documents/{d["id"]}/assets', files={"file": ("pic.png", image.getvalue(), "image/png")})
    assert result.status_code == 201
    c = content(b); c["text"] = "법원은 2,000만 원을 지급하라고 결정했습니다."
    c["picture"] = {"asset_id": result.json()["id"], "alt_text": "돈을 받은 장면", "meaning": "payment_completed"}
    d = action(client, d, f'blocks/{b["id"]}', {"content": c}, method="put")
    d = action(client, d, "reviews")
    codes = {i["code"] for i in d["review"]["issues"]}
    assert {"amount_mismatch", "image_time_mismatch", "image_human_check"} <= codes


def test_html_escapes_injected_content(client):
    d = draft(client); b = d["blocks"][0]; c = content(b)
    c["text"] = '<script>alert("x")</script>'
    d = action(client, d, f'blocks/{b["id"]}', {"content": c}, method="put")
    response = client.get(f'/api/v1/documents/{d["id"]}/reader')
    assert "<script>" not in response.text and "&lt;script&gt;" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_invalid_pdf_and_ocr(client):
    result = client.post("/api/v1/documents/pdf", data={"title": "테스트"}, files={"file": ("x.pdf", b"not pdf", "application/pdf")})
    assert result.status_code == 422
    writer = PdfWriter(); writer.add_blank_page(200, 200); buf = io.BytesIO(); writer.write(buf)
    result = client.post("/api/v1/documents/pdf", data={"title": "테스트"}, files={"file": ("x.pdf", buf.getvalue(), "application/pdf")})
    assert result.status_code == 422 and result.json()["detail"]["code"] == "ocr_required"


def test_pdf_upload_original(client):
    d = draft(client)
    pdf = client.get(f'/api/v1/documents/{d["id"]}/preview.pdf').content
    result = client.post("/api/v1/documents/pdf", data={"title": "PDF 업로드", "settings_json": '{"use_images":false}'}, files={"file": ("../sample.pdf", pdf, "application/pdf")})
    assert result.status_code == 201, result.text
    loaded = result.json()
    assert loaded["source"]["filename"] == "sample.pdf"
    assert client.get(f'/api/v1/documents/{loaded["id"]}/source.pdf').content == pdf


def test_image_cannot_cross_document_boundary(client):
    one, two = draft(client), draft(client)
    image = io.BytesIO(); Image.new("RGB", (8, 8)).save(image, "PNG")
    asset = client.post(f'/api/v1/documents/{one["id"]}/assets', files={"file": ("x.png", image.getvalue(), "image/png")}).json()
    b = two["blocks"][0]; c = content(b)
    c["picture"] = {"asset_id": asset["id"], "alt_text": "설명"}
    action(client, two, f'blocks/{b["id"]}', {"content": c}, 404, "put")


def test_history_and_persistence(client):
    d = draft(client)
    history = client.get(f'/api/v1/documents/{d["id"]}/history').json()
    assert [h["version"] for h in history] == list(range(d["version"], 0, -1))
    assert client.get(f'/api/v1/documents/{d["id"]}/history/1').json()["status"] == "uploaded"
    with TestClient(create_app(client.app.state.config), headers=client.headers) as reopened:
        assert reopened.get(f'/api/v1/documents/{d["id"]}').json() == d


def test_openapi_docs(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "이해로 스튜디오 API"
    assert "HTTPBearer" in schema["components"]["securitySchemes"]
    assert schema["paths"]["/api/v1/documents/text"]["post"]["security"]
    assert not schema["paths"]["/share/{token}"]["get"].get("security")
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_openai_adapter_schema_and_error(client, monkeypatch):
    from app.providers import Provider
    provider = Provider(Config(provider="openai", openai_api_key="test-secret", openai_model="test-gpt-model"))
    d = Document.model_validate(create(client))
    demo_result = client.app.state.provider.analyze(d)
    captured = []
    def fake_post(self, url, **kwargs):
        assert url == "https://api.openai.com/v1/responses"
        assert kwargs["headers"]["Authorization"] == "Bearer test-secret"
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": demo_result.model_dump_json()}]},
        ]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = provider.analyze(d)
    assert result == demo_result and captured[0]["text"]["format"]["schema"]["type"] == "object"
    assert captured[0]["stream"] is False
    assert captured[0]["store"] is False
    assert captured[0]["model"] == "test-gpt-model"
    assert captured[0]["max_output_tokens"] == 16384
    assert captured[0]["text"]["format"]["strict"] is True
    assert json.loads(captured[0]["input"][1]["content"])["source"] == d.source.model_dump()
    def bad(self, url, **kwargs):
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "broken"}]}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.Client, "post", bad)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        provider.analyze(d)
    assert error.value.status_code == 502


@pytest.mark.parametrize("model_name", ["Structure", "DraftResult", "SuggestionResult"])
def test_openai_strict_schema(model_name):
    from app import models
    from app.providers import strict_schema
    model = getattr(models, model_name)
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
    assert schema["$defs"]["Fact" if model_name == "Structure" else "BlockContent"]["properties"]["speaker_id"]["anyOf"][-1] == {"type": "null"}


@pytest.mark.parametrize("mode,status,code", [
    ("429", 503, "ai_rate_limited"), ("401", 502, "ai_provider_error"),
    ("500", 502, "ai_provider_error"), ("timeout", 504, "ai_timeout"),
    ("refusal", 502, "ai_refusal"), ("incomplete", 502, "ai_incomplete_response"),
    ("empty", 502, "ai_provider_error"), ("malformed", 502, "ai_provider_error"),
])
def test_openai_failures_preserve_document(client, monkeypatch, mode, status, code):
    provider = client.app.state.provider
    provider.name = "openai"
    provider.config.openai_api_key = "test-secret-never-return"
    provider.config.openai_model = "test-gpt-model"
    d = create(client)
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
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    # TestClient also inherits httpx.Client: issue the app request via .request().
    r = client.request("POST", f'/api/v1/documents/{d["id"]}/structure/analyze', json={"expected_version": d["version"]})
    assert r.status_code == status and r.json()["detail"]["code"] == code
    assert "test-secret-never-return" not in r.text
    assert client.get(f'/api/v1/documents/{d["id"]}').json() == d
    assert len(client.get(f'/api/v1/documents/{d["id"]}/history').json()) == 1


def test_openai_config_requires_explicit_credentials():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Config(provider="openai", openai_api_key="", openai_model="test-model")
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        Config(provider="openai", openai_api_key="test-secret", openai_model=" ")
    with pytest.raises(ValueError, match="OPENAI_MAX_OUTPUT_TOKENS"):
        Config(openai_max_output_tokens=0)
    assert "test-secret" not in repr(Config(provider="openai", openai_api_key="test-secret", openai_model="test-model"))


@pytest.mark.parametrize("edit_action", ["simplify", "split", "add_term"])
def test_openai_draft_and_proposal_contract(client, monkeypatch, edit_action):
    d = draft(client)
    provider = client.app.state.provider
    provider.name = "openai"
    provider.config.openai_api_key = "test-secret"
    provider.config.openai_model = "test-gpt-model"
    captured = []
    def fake_post(self, url, **kwargs):
        body = kwargs["json"]
        captured.append(body)
        payload = json.loads(body["input"][1]["content"])
        if body["text"]["format"]["name"] == "DraftResult":
            assert payload["structure"] == d["structure"]
            blocks = [content(b) for b in d["blocks"]]
        else:
            blocks = [{**payload["block"], "title": "검토할 수정 제안"}]
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({"blocks": blocks})}]}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    response = client.request("POST", f'/api/v1/documents/{d["id"]}/draft', json={"expected_version": d["version"], "replace_existing": True})
    assert response.status_code == 200
    d = response.json()
    block = d["blocks"][0]
    response = client.request("POST", f'/api/v1/documents/{d["id"]}/blocks/{block["id"]}/proposals', json={"expected_version": d["version"], "action": edit_action})
    assert response.status_code == 201
    proposed = response.json()
    assert proposed["blocks"] == d["blocks"]
    assert proposed["proposals"][-1]["provider"] == "openai"
    assert proposed["proposals"][-1]["after"][0]["title"] == "검토할 수정 제안"
    assert [c["text"]["format"]["name"] for c in captured] == ["DraftResult", "SuggestionResult"]


def test_atomic_concurrent_edit(client):
    d = draft(client); block = d["blocks"][0]; c = content(block)
    url = f'/api/v1/documents/{d["id"]}/blocks/{block["id"]}'
    def edit(n):
        return client.put(url, json={"expected_version": d["version"], "content": {**c, "text": f"편집 {n}"}}).status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(edit, [1, 2])) == [200, 409]


def test_share_expiry(client):
    d = approved(client, draft(client)); exp = action(client, d, "exports", {"share": True}, 201)
    with client.app.state.store.connect() as db:
        db.execute("UPDATE exports SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", exp["id"]))
    assert client.get(exp["share_path"]).status_code == 404


def test_add_reorder_delete_and_coverage_review(client):
    d = draft(client)
    c = content(d["blocks"][0])
    d = action(client, d, "blocks", {"content": c}, 201)
    ids = [b["id"] for b in reversed(d["blocks"])]
    d = action(client, d, "block-order", {"block_ids": ids}, method="put")
    assert [b["id"] for b in d["blocks"]] == ids
    action(client, d, "block-order", {"block_ids": [ids[0]] * len(ids)}, 422, "put")
    target = next(b for b in d["blocks"] if b["kind"] == "decision")
    # TestClient.delete doesn't expose json in every supported httpx version.
    r = client.request("DELETE", f'/api/v1/documents/{d["id"]}/blocks/{target["id"]}', json={"expected_version": d["version"]})
    assert r.status_code == 200
    d = action(client, r.json(), "reviews")
    assert "missing_source_coverage" in {i["code"] for i in d["review"]["issues"]}


def test_settings_and_structure_edits_invalidate_confirmation(client):
    d = approved(client, draft(client))
    d = action(client, d, "settings", {"settings": {"use_images": False}}, method="patch")
    assert d["review"] is None and not d["structure_confirmed"]
    action(client, d, "draft", status=409)
    action(client, d, "exports", status=409)


def test_ai_failure_does_not_save_partial_work(client, monkeypatch):
    from app.store import fail
    d = create(client)
    def broken(_):
        fail(502, "ai_provider_error", "test")
    monkeypatch.setattr(client.app.state.provider, "analyze", broken)
    action(client, d, "structure/analyze", status=502)
    saved = client.get(f'/api/v1/documents/{d["id"]}').json()
    assert saved == d


def test_generated_invalid_evidence_never_saved(client, monkeypatch):
    from app.models import Structure, Fact, Evidence
    d = create(client)
    generated = Structure(facts=[Fact(kind="decision", text="판결", evidence=[Evidence(paragraph_id="missing", start=0, end=2, quote="판결")])])
    monkeypatch.setattr(client.app.state.provider, "analyze", lambda _: generated)
    action(client, d, "structure/analyze", status=422)
    assert client.get(f'/api/v1/documents/{d["id"]}').json()["structure"] is None


def test_pdf_encrypted_and_size_limit(client):
    writer = PdfWriter(); writer.add_blank_page(200, 200); writer.encrypt("password")
    blob = io.BytesIO(); writer.write(blob)
    r = client.post("/api/v1/documents/pdf", data={"title": "보호 문서"}, files={"file": ("x.pdf", blob.getvalue(), "application/pdf")})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "encrypted_pdf"
    r = client.post("/api/v1/documents/pdf", data={"title": "대용량"}, files={"file": ("big.pdf", b"%PDF-" + b"x" * (20 * 1024 * 1024), "application/pdf")})
    assert r.status_code == 413


def test_pdf_missing_font_is_explicit(client):
    client.app.state.config.font_path = "/missing/korean.ttf"
    d = draft(client)
    response = client.get(f'/api/v1/documents/{d["id"]}/preview.pdf')
    assert response.status_code == 503 and response.json()["detail"]["code"] == "pdf_font_missing"


def test_public_without_share_and_export_isolation(client):
    d = approved(client, draft(client)); exp = action(client, d, "exports", status=201)
    assert exp["share_path"] is None
    assert client.get(exp["pdf_path"], headers={"Authorization": "Bearer bob-key"}).status_code == 404
    assert client.get("/share/unissued-token").status_code == 404


def test_production_rejects_default_key():
    with pytest.raises(ValueError, match="Production"):
        Config(environment="production")


def test_source_offsets_for_multiple_pages():
    from app.sources import source_from_pages
    s = source_from_pages(["  첫 문단입니다.\n\n두 번째 문단입니다.  ", "다음 쪽입니다.\n\n끝입니다."])
    assert len(s.paragraphs) == 4
    for p in s.paragraphs:
        assert s.pages[p.page - 1].text[p.start:p.end] == p.text
