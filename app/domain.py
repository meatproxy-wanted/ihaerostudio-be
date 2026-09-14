import re

from .models import Block, BlockContent, EvidenceLocation, Issue, Kind, ReaderCard, ReaderView, Review
from .store import fail

DISCLAIMER = "이 자료는 판결 내용을 쉽게 설명하기 위한 자료이며, 공식 판결문을 대신하지 않습니다. 정확한 내용은 원문을 확인해주세요."


def expect(doc, version):
    if doc.version != version:
        fail(409, "version_conflict", "문서 버전이 달라졌습니다. 다시 조회 후 작업하세요.")


def changed(doc):
    doc.content_revision += 1
    doc.review = None
    doc.status = "editing" if doc.blocks else "structure_pending"


def block_for(doc, block_id):
    for b in doc.blocks:
        if b.id == block_id:
            return b
    fail(404, "block_not_found", "문장을 찾을 수 없습니다.")


def evidence_locations(doc, refs):
    lookup = {p.id: p for p in doc.source.paragraphs}
    result = []
    for ref in refs:
        p = lookup.get(ref.paragraph_id)
        if p is None or ref.end <= ref.start or ref.end > len(p.text) or p.text[ref.start:ref.end] != ref.quote:
            fail(422, "invalid_evidence", "원문 인용문과 문자 위치가 일치하지 않습니다.")
        result.append(EvidenceLocation(**ref.model_dump(), page=p.page, page_start=p.start + ref.start, page_end=p.start + ref.end))
    return result


def validate_structure(doc, structure):
    ids = [p.id for p in structure.parties]
    if len(ids) != len(set(ids)) or len({f.id for f in structure.facts}) != len(structure.facts):
        fail(422, "duplicate_id", "당사자·사건 항목 id는 중복될 수 없습니다.")
    for fact in structure.facts:
        evidence_locations(doc, fact.evidence)
        validate_speaker(fact, ids)


def validate_speaker(item, ids):
    if item.kind == Kind.claim and item.speaker_id not in ids:
        fail(422, "speaker_required", "주장에는 실제 등록된 당사자 speaker_id가 필요합니다.")
    if item.speaker_id is not None and item.speaker_id not in ids:
        fail(422, "invalid_speaker", "등록되지 않은 당사자입니다.")
    if item.kind in {Kind.finding, Kind.decision} and item.speaker_id is not None:
        fail(422, "court_speaker", "법원 판단·결정에 당사자를 발화자로 지정할 수 없습니다.")


def validate_block(doc, content, store):
    evidence_locations(doc, content.evidence)
    validate_speaker(content, [p.id for p in doc.structure.parties] if doc.structure else [])
    if content.kind == Kind.claim and content.section != "claims":
        fail(422, "claim_section", "당사자 주장은 claims 영역에 배치해야 합니다.")
    if content.kind == Kind.decision and content.section != "decision":
        fail(422, "decision_section", "법원 결정은 decision 영역에 배치해야 합니다.")
    if content.picture:
        if not doc.settings.use_images:
            fail(422, "images_disabled", "이 문서는 그림 사용이 꺼져 있습니다.")
        store.asset(doc.id, content.picture.asset_id)


def run_review(doc):
    issues = [Issue(code="human_semantic_check", severity="warning", message="원문과 대조해 금액·인물 관계·주장과 판단·누락된 조건을 직접 확인하세요. 자동 점검으로 의미의 정확성을 보장할 수 없습니다.")]
    if doc.provider == "demo":
        issues.append(Issue(code="demo_content", severity="warning", message="데모는 원문을 복사한 초안입니다. 쉬운 글 작성과 사실 대조를 제작자가 직접 완료했는지 확인하세요."))
    if doc.source.warnings:
        issues.append(Issue(code="source_pages_missing", severity="warning", message="텍스트가 없는 페이지가 있습니다. 해당 페이지의 누락 내용을 직접 확인하세요."))
    covered = {e.paragraph_id for b in doc.blocks for e in b.evidence}
    important = {e.paragraph_id for f in doc.structure.facts if f.kind in {Kind.claim, Kind.finding, Kind.decision} for e in f.evidence}
    if important - covered:
        issues.append(Issue(code="missing_source_coverage", severity="warning", message="사건 구조의 중요 주장·판단·결정 중 초안에서 참조하지 않은 근거 문단이 있습니다."))
    for b in doc.blocks:
        source = " ".join(e.quote for e in b.evidence)
        linked_paragraphs = {e.paragraph_id for e in b.evidence}
        linked_kinds = {f.kind for f in doc.structure.facts if any(e.paragraph_id in linked_paragraphs for e in f.evidence)}
        if linked_kinds and b.kind not in linked_kinds:
            issues.append(Issue(code="source_kind_mismatch", severity="warning", block_id=b.id, message="카드의 주장·판단·결정 분류가 같은 근거를 연결한 사건 구조 항목과 다릅니다."))
        # Conservative exact-token comparison; normalization is intentionally not a legal inference.
        amounts = set(re.findall(r"\d[\d,]*(?:\.\d+)?\s*(?:억|만|천)?\s*원", b.text))
        if any(a not in source for a in amounts):
            issues.append(Issue(code="amount_mismatch", severity="warning", block_id=b.id, message="설명자료의 금액 표기가 연결된 원문과 다릅니다. 단위 변환을 포함해 직접 확인하세요."))
        if b.kind == Kind.unknown:
            issues.append(Issue(code="unclassified", severity="warning", block_id=b.id, message="주장인지 법원 판단인지 분류가 필요합니다."))
        if b.kind == Kind.claim and any(w in b.text for w in ["법원은", "법원이", "인정했다", "인정했습니다"]):
            issues.append(Issue(code="claim_finding_mix", severity="warning", block_id=b.id, message="당사자 주장 문장에 법원 판단을 나타내는 표현이 있습니다."))
        if b.kind == Kind.decision and any(w in b.text for w in ["받았습니다", "받았다", "지급했습니다", "지급했다"]):
            issues.append(Issue(code="ordered_vs_completed", severity="warning", block_id=b.id, message="지급 결정이 이미 이행된 일처럼 표현되지 않았는지 확인하세요."))
        if len(b.text) > 100:
            issues.append(Issue(code="long_sentence", severity="info", block_id=b.id, message="긴 설명입니다. 한 문장에 한 내용을 담도록 나누는 것을 검토하세요."))
        for term in ["임대차계약", "보증금", "기각", "인용", "소송비용", "가집행"]:
            if term in b.text and term not in {t.term for t in b.terms}:
                issues.append(Issue(code="undefined_term", severity="info", block_id=b.id, message=f"'{term}'의 쉬운 뜻 설명이 없습니다."))
        if b.picture:
            issues.append(Issue(code="image_human_check", severity="warning", block_id=b.id, message="실제 그림을 보고 글의 인물·행동·시점과 맞는지 확인하세요. 그림 픽셀의 의미는 자동 판독하지 않습니다."))
            if b.kind == Kind.decision and b.picture.meaning == "payment_completed":
                issues.append(Issue(code="image_time_mismatch", severity="warning", block_id=b.id, message="법원의 지급 결정에 '지급 완료' 그림이 연결되어 있습니다. 명령과 이행을 구분하세요."))
    return Review(content_revision=doc.content_revision, issues=issues)


def require_reviewed(doc):
    if not doc.structure_confirmed or not doc.blocks or not doc.review or not doc.review.approved_at or doc.review.content_revision != doc.content_revision or doc.status != "reviewed":
        fail(409, "review_required", "현재 편집본을 검토·승인한 뒤 내보낼 수 있습니다.")


def reader_view(doc):
    order = {"people": 0, "decision": 1, "claims": 2, "reasons": 3, "terms": 4}
    # Claims remain explicitly labelled; reader sees no evidence/review/editor metadata.
    cards = []
    for b in sorted(doc.blocks, key=lambda b: order[b.section]):
        title = b.title
        if b.kind == Kind.claim:
            speaker = next(p.label for p in doc.structure.parties if p.id == b.speaker_id)
            title = f"{speaker}의 주장 - {title}"
        cards.append(ReaderCard(title=title, text=b.text, section=b.section, terms=b.terms, picture=b.picture if doc.settings.use_images else None))
    return ReaderView(title=doc.title, version=doc.content_revision, disclaimer=DISCLAIMER, is_draft=doc.status != "reviewed", people=doc.structure.parties if doc.structure else [], cards=cards)
