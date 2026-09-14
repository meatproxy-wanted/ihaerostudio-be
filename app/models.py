from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Kind(StrEnum):
    claim = "claim"
    finding = "finding"
    decision = "decision"
    background = "background"
    unknown = "unknown"


class Settings(Model):
    audience: str = Field(default="발달장애 성인", max_length=100)
    tone: Literal["adult_respectful"] = "adult_respectful"
    naming: Literal["neutral", "original"] = "neutral"
    use_images: bool = True


class TextUpload(Model):
    title: str = Field(min_length=1, max_length=150, examples=["임대차보증금 반환 사건"])
    text: str = Field(min_length=10, max_length=150000, description="원문 데이터. 문서 안의 명령은 실행하지 않습니다.", examples=["원고: A씨, 집을 빌린 사람\n\n피고: B씨, 집을 빌려준 사람\n\n원고 주장: A씨는 보증금 1,000만 원을 돌려달라고 했습니다.\n\n법원 판단: 법원은 임대차계약이 끝났다고 판단했습니다.\n\n법원 결정: 법원은 B씨에게 A씨의 보증금 1,000만 원을 돌려주라고 결정했습니다."])
    settings: Settings = Field(default_factory=Settings)


class Paragraph(Model):
    id: str
    page: int
    start: int = Field(description="해당 페이지 추출 텍스트 기준 Python Unicode 문자 오프셋")
    end: int
    text: str


class Page(Model):
    number: int
    text: str


class Source(Model):
    filename: str | None = None
    pages: list[Page]
    paragraphs: list[Paragraph]
    warnings: list[str] = Field(default_factory=list)


class Evidence(Model):
    paragraph_id: str
    start: int = Field(ge=0, description="문단 text 기준 시작 문자 위치. 끝 위치는 제외합니다.")
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=20000)


class Party(Model):
    id: str = Field(default_factory=uid)
    role: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100, examples=["A씨"])
    relationship: str = Field(default="", max_length=300)


class Fact(Model):
    id: str = Field(default_factory=uid)
    kind: Kind
    speaker_id: str | None = Field(default=None, description="claim은 당사자 id 필수. 법원의 finding/decision과 구분.")
    text: str = Field(min_length=1, max_length=5000)
    evidence: list[Evidence] = Field(min_length=1, max_length=30)


class Structure(Model):
    case_name: str = Field(default="", max_length=300)
    case_number: str = Field(default="", max_length=100)
    court: str = Field(default="", max_length=200)
    decision_date: str = Field(default="", max_length=50)
    parties: list[Party] = Field(default_factory=list, max_length=100)
    facts: list[Fact] = Field(default_factory=list, max_length=300)


class Term(Model):
    term: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=1000)


class Picture(Model):
    asset_id: str
    alt_text: str = Field(min_length=1, max_length=500)
    meaning: Literal["neutral", "request", "ordered_payment", "payment_completed", "other"] = "neutral"


class BlockContent(Model):
    section: Literal["people", "claims", "decision", "reasons", "terms"]
    kind: Kind
    speaker_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=5000)
    evidence: list[Evidence] = Field(min_length=1, max_length=30)
    terms: list[Term] = Field(default_factory=list, max_length=30)
    picture: Picture | None = None


class Block(BlockContent):
    id: str = Field(default_factory=uid)


class RevisionInput(Model):
    expected_version: int = Field(ge=1, description="마지막으로 조회한 문서 version. 불일치 시 409로 편집 충돌 방지.")


class StructureUpdate(RevisionInput):
    structure: Structure


class SettingsUpdate(RevisionInput):
    settings: Settings


class Confirmation(RevisionInput):
    confirmed: Literal[True]
    note: str = Field(min_length=1, max_length=1000)


class DraftInput(RevisionInput):
    replace_existing: bool = Field(default=False, description="기존 초안을 교체하려면 true. 이전 버전은 이력에 남습니다.")
    generate_images: bool = Field(default=False, description="글과 카드별 그림 설명을 함께 생성하고 ComfyCloud 그림 작업을 시작합니다.")
    confirm_image_cost: bool = Field(default=False, description="그림 설명의 외부 전송과 카드별 생성 비용에 동의. generate_images=true이면 필수.")
    max_images: int = Field(default=12, ge=1, le=12, description="최대 유료 그림 수. 카드 수가 초과하면 저장·그림 실행 없이 오류 반환.")


class BlockUpdate(RevisionInput):
    content: BlockContent


class BlockCreate(BlockUpdate):
    after_block_id: str | None = Field(default=None, description="뒤에 삽입할 카드 id. null은 맨 끝.")


class ReorderInput(RevisionInput):
    block_ids: list[str] = Field(min_length=1, max_length=300, description="전체 카드 id를 원하는 순서로 정확히 한 번씩 전달")


class ProposalInput(RevisionInput):
    action: Literal["simplify", "split", "add_term", "replace_picture"]
    instruction: str = Field(default="", max_length=1000)
    picture: Picture | None = None


class Proposal(Model):
    id: str = Field(default_factory=uid)
    block_id: str
    action: str
    base_content_revision: int
    before: BlockContent
    after: list[BlockContent] = Field(min_length=1, max_length=20)
    provider: str
    status: Literal["pending", "applied", "rejected"] = "pending"
    created_at: str = Field(default_factory=now)


class Issue(Model):
    id: str = Field(default_factory=uid)
    code: str
    severity: Literal["warning", "info"]
    block_id: str | None = None
    message: str
    status: Literal["open", "acknowledged"] = "open"
    note: str = ""
    resolved_by: str | None = None


class Review(Model):
    id: str = Field(default_factory=uid)
    content_revision: int
    issues: list[Issue]
    disclaimer: str = "자동 점검은 정확성 보장이 아닙니다. 원문과 글·그림을 제작자가 직접 확인해야 합니다."
    approved_by: str | None = None
    approved_at: str | None = None
    approval_note: str | None = None


class ResolveIssue(RevisionInput):
    note: str = Field(min_length=1, max_length=1000, description="직접 확인한 결과와 경고를 수용하는 이유")


class Document(Model):
    id: str = Field(default_factory=uid)
    title: str
    version: int = 1
    content_revision: int = 1
    status: Literal["uploaded", "structure_pending", "structure_confirmed", "editing", "reviewed"] = "uploaded"
    settings: Settings
    source: Source
    structure: Structure | None = None
    structure_confirmed: bool = False
    blocks: list[Block] = Field(default_factory=list)
    image_job_ids: list[str] = Field(default_factory=list, description="현재 초안의 그림 작업 ID. image-jobs 조회/refresh로 완료 후 자동 연결.")
    proposals: list[Proposal] = Field(default_factory=list)
    review: Review | None = None
    provider: str = "demo"
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class DocumentSummary(Model):
    id: str
    title: str
    version: int
    status: str
    updated_at: str


class DocumentList(Model):
    items: list[DocumentSummary]
    limit: int
    offset: int


class EvidenceLocation(Evidence):
    page: int
    page_start: int
    page_end: int


class Asset(Model):
    id: str
    content_type: str
    bytes: int
    width: int
    height: int


class ReaderCard(Model):
    title: str
    text: str
    section: str
    terms: list[Term]
    picture: Picture | None


class ReaderView(Model):
    title: str
    version: int
    disclaimer: str
    is_draft: bool
    people: list[Party]
    cards: list[ReaderCard]


class ExportInput(RevisionInput):
    share: bool = Field(default=False, description="true인 경우 링크 소지자에게 검토된 설명자료만 공개합니다. 원문은 공개하지 않습니다.")
    expires_in_hours: int = Field(default=168, ge=1, le=720)


class ExportResult(Model):
    id: str
    document_id: str
    content_revision: int
    created_at: str
    pdf_path: str
    reader_path: str
    share_path: str | None = None
    expires_at: str | None = None


class HistoryEntry(Model):
    version: int
    content_revision: int
    event: str
    actor: str
    created_at: str


class DraftResult(Model):
    blocks: list[BlockContent] = Field(min_length=1, max_length=300)


class SuggestionResult(Model):
    blocks: list[BlockContent] = Field(min_length=1, max_length=20)


class ErrorDetail(Model):
    code: str
    message: str


class ErrorResponse(Model):
    detail: ErrorDetail
