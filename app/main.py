import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .config import Config
from .domain import (DISCLAIMER, block_for, changed, evidence_locations, expect, reader_view,
                     require_reviewed, run_review, validate_block, validate_structure)
from .models import (Asset, Block, BlockContent, BlockCreate, BlockUpdate, Confirmation, Document, DocumentList,
                     DocumentSummary, DraftInput, ErrorResponse, EvidenceLocation, ExportInput,
                     ExportResult, HistoryEntry, Proposal, ProposalInput, ReaderView, ResolveIssue,
                     RevisionInput, ReorderInput, Settings, SettingsUpdate, Source, StructureUpdate, TextUpload, now, uid)
from .providers import Provider
from .render import render_html, render_pdf
from .sources import MAX_IMAGE_BYTES, MAX_PDF_BYTES, clean_image, extract_pdf, source_from_pages
from .store import Store, fail


TAGS = [
    {"name": "01 문서 업로드", "description": "PDF 또는 텍스트 등록. 원문은 보존되며 작성자별로 분리됩니다."},
    {"name": "02 사건 구조", "description": "주장/판단/결정 분리 → 원문 대조 → 제작자 확인."},
    {"name": "03 글그림 편집", "description": "근거 위치 조회, 직접 수정, AI 수정 제안과 명시적 적용."},
    {"name": "04 검토", "description": "자동 확인 항목은 정확성을 보장하지 않습니다. 모든 항목을 제작자가 확인합니다."},
    {"name": "05 미리보기", "description": "편집 정보를 숨긴 독자용 JSON/HTML 및 검토 전 PDF."},
    {"name": "06 내보내기", "description": "현재 검토 완료 버전의 불변 PDF/독자 화면. 공유는 선택 사항이며 해제 가능합니다."},
    {"name": "이력", "description": "편집·승인한 작성자와 전체 버전 스냅샷."},
]


class UploadLimitMiddleware:
    """Bound streamed request bodies too, before multipart parsers allocate files."""
    def __init__(self, app, limit=22 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                response = Response(json.dumps({"detail": {"code": "request_too_large", "message": "요청은 최대 22MB입니다."}}), status_code=413, media_type="application/json")
                return await response(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        index = 0
        async def replay():
            nonlocal index
            if index < len(chunks):
                message = chunks[index]
                index += 1
                return message
            return await receive()
        await self.app(scope, replay, send)


def create_app(config: Config | None = None):
    config = config or Config()
    store, provider = Store(config.db_path), Provider(config)
    api = FastAPI(title="이해로 스튜디오 API", version="0.1.0", description="""
판결문 원문과 쉬운 글·그림을 대조하고 편집·검토·출력하는 제작자용 백엔드.

**시작:** Authorize에 API_KEYS에 등록한 Bearer 토큰 입력.
로컬 개발 기본값: `dev-only-change-me` (실서비스 사용 금지).

**6단계:** documents/text 또는 documents/pdf → structure/analyze → structure 수정·confirm
→ draft → blocks 편집·proposals 적용 → reviews 및 항목 확인·approve → exports.

쓰기 요청의 `expected_version`에는 직전 응답의 `version`을 넣으세요.
편집하면 검토 승인이 해제됩니다. AI 제안은 apply 전까지 본문을 바꾸지 않습니다.
demo는 실제 LLM이 아닙니다. 실제 구조 분석·쉬운 표현·용어 설명은 Ollama 설정이 필요합니다.
법적 정확성·그림 의미를 보장하지 않습니다. 원문 내 명령은 입력 데이터로 취급합니다.
""", openapi_tags=TAGS, responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
    api.state.store, api.state.provider, api.state.config = store, provider, config
    api.add_middleware(UploadLimitMiddleware)
    api.add_middleware(CORSMiddleware, allow_origins=config.cors_origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @api.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/share/") or "reader" in request.url.path:
            response.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        return response

    bearer = HTTPBearer(auto_error=False, description="제작자 API 토큰. 개발 환경: dev-only-change-me")
    def owner(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials:
            for token, maker in config.api_keys.items():
                if hmac.compare_digest(credentials.credentials, token):
                    return maker
        fail(401, "unauthorized", "유효한 Bearer 토큰이 필요합니다.")
    Owner = Annotated[str, Depends(owner)]

    @api.get("/health", tags=["운영"], summary="서버 상태")
    def health() -> dict[str, str]:
        return {"status": "ok", "ai_provider": config.provider, "environment": config.environment}

    @api.post("/api/v1/documents/text", response_model=Document, status_code=201, tags=[TAGS[0]["name"]], summary="판결문 텍스트 등록")
    def upload_text(body: TextUpload, maker: Owner):
        source = source_from_pages([body.text])
        return store.create(maker, Document(title=body.title, settings=body.settings, source=source, provider=provider.name))

    @api.post("/api/v1/documents/pdf", response_model=Document, status_code=201, tags=[TAGS[0]["name"]], summary="판결문 PDF 등록", description="multipart/form-data. PDF 20MB/100쪽/추출 15만 자 제한. 스캔본 OCR은 별도로 처리해야 합니다. settings_json은 JSON 문자열입니다.")
    async def upload_pdf(maker: Owner, file: Annotated[UploadFile, File()], title: Annotated[str, Form(min_length=1, max_length=150)], settings_json: Annotated[str, Form()] = "{}"):
        try:
            settings = Settings.model_validate_json(settings_json)
        except ValidationError:
            fail(422, "invalid_settings", "settings_json 형식이 올바르지 않습니다.")
        data = await file.read(MAX_PDF_BYTES + 1)
        await file.close()
        source = await run_in_threadpool(extract_pdf, data, (file.filename or "judgment.pdf").replace("\\", "/").split("/")[-1])
        doc = Document(title=title, settings=settings, source=source, provider=provider.name)
        return store.create(maker, doc, data)

    @api.get("/api/v1/documents", response_model=DocumentList, tags=[TAGS[0]["name"]], summary="내 문서 목록")
    def documents(maker: Owner, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        return DocumentList(items=[DocumentSummary(**{k: getattr(d, k) for k in DocumentSummary.model_fields}) for d in store.listing(maker, limit, offset)], limit=limit, offset=offset)

    @api.get("/api/v1/documents/{doc_id}", response_model=Document, tags=[TAGS[0]["name"]], summary="제작자 편집 상태 조회")
    def document(doc_id: str, maker: Owner):
        return store.get(doc_id, maker)

    @api.get("/api/v1/documents/{doc_id}/source", response_model=Source, tags=[TAGS[0]["name"]], summary="원문 페이지·문단 및 오프셋 조회")
    def source(doc_id: str, maker: Owner):
        return store.get(doc_id, maker).source

    @api.get("/api/v1/documents/{doc_id}/source.pdf", tags=[TAGS[0]["name"]], summary="업로드 원본 PDF", responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}}, response_class=Response)
    def original(doc_id: str, maker: Owner):
        return Response(store.original(doc_id, maker), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="original.pdf"'})

    @api.patch("/api/v1/documents/{doc_id}/settings", response_model=Document, tags=[TAGS[0]["name"]], summary="제작 설정 변경", description="사건 구조 확인과 최종 검토를 해제합니다. 기존 글·그림은 자동 재작성하지 않습니다.")
    def settings(doc_id: str, body: SettingsUpdate, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        doc.settings = body.settings; doc.structure_confirmed = False; changed(doc)
        return store.save(doc, maker, body.expected_version, "settings_changed")

    @api.post("/api/v1/documents/{doc_id}/structure/analyze", response_model=Document, tags=[TAGS[1]["name"]], summary="AI 사건 구조 초안 분석", description="최대 120초 동기 실행. 이미 존재하는 구조는 덮어쓰지 않습니다. 결과는 제작자 확인 전 초안입니다.")
    def analyze(doc_id: str, body: RevisionInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        if doc.structure is not None:
            fail(409, "structure_exists", "기존 구조는 PUT으로 직접 수정하세요.")
        result = provider.analyze(doc); validate_structure(doc, result)
        doc.structure = result; changed(doc)
        return store.save(doc, maker, body.expected_version, "structure_analyzed")

    @api.put("/api/v1/documents/{doc_id}/structure", response_model=Document, tags=[TAGS[1]["name"]], summary="사건 구조 직접 수정")
    def update_structure(doc_id: str, body: StructureUpdate, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        validate_structure(doc, body.structure)
        # Existing block speakers must remain resolvable for previews and exports.
        doc.structure = body.structure
        for block in doc.blocks:
            validate_block(doc, block, store)
        doc.structure_confirmed = False; changed(doc)
        return store.save(doc, maker, body.expected_version, "structure_edited")

    @api.post("/api/v1/documents/{doc_id}/structure/confirm", response_model=Document, tags=[TAGS[1]["name"]], summary="원문 대조 후 사건 구조 확인")
    def confirm_structure(doc_id: str, body: Confirmation, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        if not doc.structure or not doc.structure.facts or any(f.kind == "unknown" for f in doc.structure.facts):
            fail(409, "structure_incomplete", "사건 항목이 없거나 unknown 분류가 남아 있습니다. 직접 수정해주세요.")
        doc.structure_confirmed = True
        doc.status = "editing" if doc.blocks else "structure_confirmed"
        return store.save(doc, maker, body.expected_version, "structure_confirmed: " + body.note)

    @api.post("/api/v1/documents/{doc_id}/draft", response_model=Document, tags=[TAGS[2]["name"]], summary="확인된 구조로 글그림 초안 생성")
    def draft(doc_id: str, body: DraftInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        if not doc.structure_confirmed:
            fail(409, "structure_confirmation_required", "먼저 사건 구조를 원문과 대조하고 확인해주세요.")
        if doc.blocks and not body.replace_existing:
            fail(409, "draft_exists", "기존 초안을 교체하려면 replace_existing=true가 필요합니다.")
        blocks = provider.draft(doc)
        for b in blocks:
            validate_block(doc, b, store)
        doc.blocks = [Block(**b.model_dump()) for b in blocks]; changed(doc)
        return store.save(doc, maker, body.expected_version, "draft_generated")

    @api.get("/api/v1/documents/{doc_id}/blocks/{block_id}/sources", response_model=list[EvidenceLocation], tags=[TAGS[2]["name"]], summary="선택 문장의 원문 강조 위치")
    def block_sources(doc_id: str, block_id: str, maker: Owner):
        doc = store.get(doc_id, maker)
        return evidence_locations(doc, block_for(doc, block_id).evidence)

    @api.post("/api/v1/documents/{doc_id}/blocks", response_model=Document, status_code=201, tags=[TAGS[2]["name"]], summary="근거가 연결된 카드 직접 추가")
    def add_block(doc_id: str, body: BlockCreate, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        if not doc.structure_confirmed:
            fail(409, "structure_confirmation_required", "사건 구조를 먼저 확인해주세요.")
        if len(doc.blocks) >= 300:
            fail(409, "block_limit", "카드는 최대 300개입니다.")
        validate_block(doc, body.content, store)
        index = doc.blocks.index(block_for(doc, body.after_block_id)) + 1 if body.after_block_id else len(doc.blocks)
        doc.blocks.insert(index, Block(**body.content.model_dump())); changed(doc)
        return store.save(doc, maker, body.expected_version, "block_added")

    @api.put("/api/v1/documents/{doc_id}/block-order", response_model=Document, tags=[TAGS[2]["name"]], summary="카드 순서 변경", description="편집 순서를 저장합니다. 독자 화면은 people → decision → claims → reasons → terms로 그룹화하며 그룹 안에서는 이 순서를 유지합니다.")
    def reorder(doc_id: str, body: ReorderInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        lookup = {b.id: b for b in doc.blocks}
        if len(body.block_ids) != len(lookup) or set(body.block_ids) != set(lookup):
            fail(422, "invalid_order", "모든 카드 id를 중복 없이 전달해주세요.")
        doc.blocks = [lookup[i] for i in body.block_ids]; changed(doc)
        return store.save(doc, maker, body.expected_version, "blocks_reordered")

    @api.delete("/api/v1/documents/{doc_id}/blocks/{block_id}", response_model=Document, tags=[TAGS[2]["name"]], summary="카드 삭제", description="이전 버전은 이력에 남습니다. 제거된 중요 내용은 재검토 때 확인해야 합니다.")
    def delete_block(doc_id: str, block_id: str, body: RevisionInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        doc.blocks.remove(block_for(doc, block_id)); changed(doc)
        return store.save(doc, maker, body.expected_version, "block_deleted: " + block_id)

    @api.put("/api/v1/documents/{doc_id}/blocks/{block_id}", response_model=Document, tags=[TAGS[2]["name"]], summary="글·용어·그림 직접 수정", description="카드 전체 content를 보냅니다. 원문 근거는 필수이며 그림 제거는 picture=null. 편집 후 재검토가 필요합니다.")
    def edit_block(doc_id: str, block_id: str, body: BlockUpdate, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        old = block_for(doc, block_id); validate_block(doc, body.content, store)
        doc.blocks[doc.blocks.index(old)] = Block(id=old.id, **body.content.model_dump()); changed(doc)
        return store.save(doc, maker, body.expected_version, "block_edited: " + block_id)

    @api.post("/api/v1/documents/{doc_id}/blocks/{block_id}/proposals", response_model=Document, status_code=201, tags=[TAGS[2]["name"]], summary="수정 전후를 비교할 AI 제안 생성", description="응답 proposals[-1].before/after를 비교하세요. 생성만으로 글은 변경되지 않습니다. 그림 교체는 asset 업로드 후 picture를 전달합니다.")
    def propose(doc_id: str, block_id: str, body: ProposalInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        if len(doc.proposals) >= 200:
            fail(409, "proposal_limit", "문서당 제안은 최대 200개입니다.")
        block = block_for(doc, block_id)
        alternatives = provider.propose(doc, block, body)
        for item in alternatives:
            validate_block(doc, item, store)
            if (item.kind, item.speaker_id, item.evidence, item.section) != (block.kind, block.speaker_id, block.evidence, block.section):
                fail(502, "unsafe_ai_change", "AI가 사건 분류나 원문 근거를 바꾸어 제안을 거부했습니다. 직접 수정해주세요.")
        doc.proposals.append(Proposal(block_id=block.id, action=body.action, base_content_revision=doc.content_revision, before=BlockContent(**block.model_dump(exclude={"id"})), after=alternatives, provider=provider.name))
        return store.save(doc, maker, body.expected_version, "proposal_created")

    @api.post("/api/v1/documents/{doc_id}/proposals/{proposal_id}/apply", response_model=Document, tags=[TAGS[2]["name"]], summary="비교 후 수정안 적용", description="다른 내용 편집 후에는 제안이 만료됩니다. 새로 생성해주세요. split은 카드를 여러 개로 나눕니다.")
    def apply_proposal(doc_id: str, proposal_id: str, body: RevisionInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        proposal = next((p for p in doc.proposals if p.id == proposal_id), None)
        if not proposal:
            fail(404, "not_found", "제안이 없습니다.")
        if proposal.status != "pending" or proposal.base_content_revision != doc.content_revision:
            fail(409, "stale_proposal", "이미 처리했거나 편집으로 만료된 제안입니다.")
        block = block_for(doc, proposal.block_id)
        for item in proposal.after:
            validate_block(doc, item, store)
        if len(doc.blocks) - 1 + len(proposal.after) > 300:
            fail(409, "block_limit", "카드는 최대 300개입니다.")
        replacements = [Block(id=block.id if i == 0 else uid(), **b.model_dump()) for i, b in enumerate(proposal.after)]
        index = doc.blocks.index(block); doc.blocks[index:index + 1] = replacements
        proposal.status = "applied"; changed(doc)
        return store.save(doc, maker, body.expected_version, "proposal_applied: " + proposal.id)

    @api.post("/api/v1/documents/{doc_id}/proposals/{proposal_id}/reject", response_model=Document, tags=[TAGS[2]["name"]], summary="수정안 거절")
    def reject_proposal(doc_id: str, proposal_id: str, body: RevisionInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        proposal = next((p for p in doc.proposals if p.id == proposal_id), None)
        if not proposal:
            fail(404, "not_found", "제안이 없습니다.")
        if proposal.status != "pending":
            fail(409, "proposal_processed", "이미 처리된 제안입니다.")
        proposal.status = "rejected"
        return store.save(doc, maker, body.expected_version, "proposal_rejected: " + proposal.id)

    @api.post("/api/v1/documents/{doc_id}/assets", response_model=Asset, status_code=201, tags=[TAGS[2]["name"]], summary="교체할 그림 업로드", description="PNG/JPEG/WebP 5MB까지. 메타데이터를 제거하고 PNG로 다시 저장합니다. 업로드만으로 본문에 연결되지 않습니다.")
    async def upload_asset(doc_id: str, maker: Owner, file: Annotated[UploadFile, File()]):
        store.get(doc_id, maker)
        data = await file.read(MAX_IMAGE_BYTES + 1); await file.close()
        blob, width, height = await run_in_threadpool(clean_image, data)
        asset_id = uid(); store.put_asset(doc_id, maker, asset_id, blob, width, height)
        return Asset(id=asset_id, content_type="image/png", bytes=len(blob), width=width, height=height)

    @api.get("/api/v1/documents/{doc_id}/assets/{asset_id}", response_class=Response, tags=[TAGS[2]["name"]], summary="문서 그림 조회", responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}})
    def asset(doc_id: str, asset_id: str, maker: Owner):
        store.get(doc_id, maker)
        return Response(store.asset(doc_id, asset_id), media_type="image/png")

    @api.post("/api/v1/documents/{doc_id}/reviews", response_model=Document, tags=[TAGS[3]["name"]], summary="현재 버전 자동 확인 항목 생성")
    def review(doc_id: str, body: RevisionInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        if not doc.blocks or not doc.structure_confirmed:
            fail(409, "draft_required", "확인된 사건 구조와 초안이 필요합니다.")
        doc.review = run_review(doc); doc.status = "editing"
        return store.save(doc, maker, body.expected_version, "review_created")

    @api.post("/api/v1/documents/{doc_id}/reviews/issues/{issue_id}/resolve", response_model=Document, tags=[TAGS[3]["name"]], summary="제작자가 확인 항목 처리", description="경고가 틀렸다고 자동 판정하지 않습니다. 제작자가 직접 확인한 이유를 기록합니다. 실제 수정이 필요하면 카드 수정 후 다시 검토하세요.")
    def resolve(doc_id: str, issue_id: str, body: ResolveIssue, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        if not doc.review or doc.review.content_revision != doc.content_revision or doc.review.approved_at:
            fail(409, "review_required", "처리할 현재 검토가 없습니다.")
        issue = next((i for i in doc.review.issues if i.id == issue_id), None)
        if not issue:
            fail(404, "not_found", "확인 항목이 없습니다.")
        issue.status, issue.note, issue.resolved_by = "acknowledged", body.note, maker
        return store.save(doc, maker, body.expected_version, "issue_acknowledged: " + issue.id)

    @api.post("/api/v1/documents/{doc_id}/reviews/approve", response_model=Document, tags=[TAGS[3]["name"]], summary="제작자 최종 검토 승인")
    def approve(doc_id: str, body: Confirmation, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version)
        if not doc.structure_confirmed or not doc.review or doc.review.content_revision != doc.content_revision:
            fail(409, "review_required", "현재 버전의 검토를 먼저 실행하세요.")
        if any(i.status == "open" for i in doc.review.issues) or any(b.kind == "unknown" for b in doc.blocks):
            fail(409, "unresolved_issues", "모든 항목을 직접 확인하고 unknown 분류를 수정하세요.")
        doc.review.approved_at = now(); doc.review.approved_by = maker; doc.review.approval_note = body.note; doc.status = "reviewed"
        return store.save(doc, maker, body.expected_version, "review_approved")

    @api.get("/api/v1/documents/{doc_id}/preview", response_model=ReaderView, tags=[TAGS[4]["name"]], summary="독자 화면용 JSON", description="원문·검토 이력 제외. 그림 bytes는 인증된 asset API에서 조회하세요.")
    def preview(doc_id: str, maker: Owner):
        return reader_view(store.get(doc_id, maker))

    @api.get("/api/v1/documents/{doc_id}/reader", response_class=HTMLResponse, tags=[TAGS[4]["name"]], summary="독자용 HTML 미리보기")
    def reader(doc_id: str, maker: Owner):
        return HTMLResponse(render_html(store.get(doc_id, maker), store))

    @api.get("/api/v1/documents/{doc_id}/preview.pdf", response_class=Response, tags=[TAGS[4]["name"]], summary="인쇄용 PDF 미리보기", responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
    def preview_pdf(doc_id: str, maker: Owner):
        return Response(render_pdf(store.get(doc_id, maker), store, config.font_path), media_type="application/pdf")

    @api.post("/api/v1/documents/{doc_id}/exports", response_model=ExportResult, status_code=201, tags=[TAGS[5]["name"]], summary="검토한 버전 내보내기·선택적 공유", description="PDF를 즉시 생성하고 불변 스냅샷으로 저장합니다. 이후 원본 편집은 기존 출력물에 반영되지 않습니다. 공개 링크는 이 응답에만 반환하므로 보관하세요.")
    def export(doc_id: str, body: ExportInput, maker: Owner):
        doc = store.get(doc_id, maker); expect(doc, body.expected_version); require_reviewed(doc)
        export_id, token = uid(), secrets.token_urlsafe(32) if body.share else None
        result = ExportResult(id=export_id, document_id=doc_id, content_revision=doc.content_revision, created_at=now(), pdf_path=f"/api/v1/exports/{export_id}/pdf", reader_path=f"/api/v1/exports/{export_id}/reader", share_path=f"/share/{token}" if token else None, expires_at=(datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)).isoformat() if token else None)
        pdf = render_pdf(doc, store, config.font_path)
        store.put_export(maker, doc, result, pdf, token)
        return result

    @api.get("/api/v1/exports/{export_id}/pdf", response_class=Response, tags=[TAGS[5]["name"]], summary="검토 완료 PDF 다운로드", responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
    def export_pdf(export_id: str, maker: Owner):
        row = store.get_export(export_id, maker)
        return Response(row["pdf"], media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="ihaero-{export_id}.pdf"'})

    @api.get("/api/v1/exports/{export_id}/reader", response_class=HTMLResponse, tags=[TAGS[5]["name"]], summary="내보낸 고정 버전의 독자 화면")
    def export_reader(export_id: str, maker: Owner):
        row = store.get_export(export_id, maker)
        return HTMLResponse(render_html(Document.model_validate_json(row["snapshot"]), store))

    @api.delete("/api/v1/exports/{export_id}/share", status_code=204, tags=[TAGS[5]["name"]], summary="공유 링크 해제", description="이미 내려받은 파일은 회수되지 않습니다. 원래 제작자는 인증 후 출력물을 계속 조회할 수 있습니다.")
    def revoke(export_id: str, maker: Owner):
        store.revoke(export_id, maker)
        return Response(status_code=204)

    @api.get("/share/{token}", response_class=HTMLResponse, tags=[TAGS[5]["name"]], summary="공유받은 독자 화면", description="인증 없이 토큰으로 조회. 원문, 개인정보가 포함될 수 있는 근거 인용, 검토·편집 이력은 제공하지 않습니다.")
    def public_reader(token: str):
        row = store.shared(token)
        return HTMLResponse(render_html(Document.model_validate_json(row["snapshot"]), store))

    @api.get("/api/v1/documents/{doc_id}/history", response_model=list[HistoryEntry], tags=["이력"], summary="변경·승인 이력 목록")
    def history(doc_id: str, maker: Owner):
        return store.history(doc_id, maker)

    @api.get("/api/v1/documents/{doc_id}/history/{version}", response_model=Document, tags=["이력"], summary="이전 버전 전체 조회")
    def history_snapshot(doc_id: str, version: int, maker: Owner):
        return store.snapshot(doc_id, maker, version)

    return api


app = create_app()
