"""FE reference integrity, reader projection and conservative producer checks."""
import hashlib
import json
import re

from .models import now
from .store import fail


def timestamp():
    return now().replace("+00:00", "Z")


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def anchor_text(source, anchor):
    paragraph = next((p for p in source["paragraphs"] if p["id"] == anchor["paragraphId"]), None)
    if paragraph is None:
        fail(422, "invalid_anchor", "존재하지 않는 원문 문단입니다.")
    encoded = paragraph["text"].encode("utf-16-le")
    if not 0 <= anchor["start"] < anchor["end"] <= len(encoded) // 2:
        fail(422, "invalid_anchor", "원문 근거 범위가 문단을 벗어났어요.")
    try:
        return encoded[anchor["start"] * 2:anchor["end"] * 2].decode("utf-16-le")
    except UnicodeDecodeError:
        fail(422, "invalid_anchor", "문자 중간에서 근거 범위를 나눌 수 없어요.")


def unique(items, field="id"):
    values = [item[field] for item in items]
    if len(set(values)) != len(values):
        fail(422, "duplicate_id", "중복된 항목 ID가 있어요.")


def validate_structure(structure, source):
    all_items = [item for group in ("parties", "keyFacts", "claims", "findings", "decisions") for item in structure[group]]
    unique(all_items)
    party_ids = {p["id"] for p in structure["parties"]}
    claim_ids = {c["id"] for c in structure["claims"]}
    if any(c["partyId"] not in party_ids for c in structure["claims"]):
        fail(422, "invalid_party", "주장의 당사자를 확인해 주세요.")
    if any(set(f["claimIds"]) - claim_ids for f in structure["findings"]):
        fail(422, "invalid_claim", "판단에 연결된 주장을 확인해 주세요.")
    for item in all_items:
        for anchor in item["anchors"]:
            anchor_text(source, anchor)


def structure_content(structure):
    return {"overview": structure["overview"], **{
        group: [{key: value for key, value in item.items() if key != "flags"} for item in structure[group]]
        for group in ("parties", "keyFacts", "claims", "findings", "decisions")}}


def cards(document):
    return [card for section in document["sections"] for card in section["cards"]]


def sentences(document):
    return [sentence for card in cards(document) for sentence in card["sentences"]]


def validate_document(document, state):
    all_cards = cards(document)
    all_sentences = sentences(document)
    if len(all_cards) > 300 or len(all_sentences) > 1500:
        fail(413, "document_too_large", "카드 300개, 문장 1,500개까지 저장할 수 있어요.")
    for group in (all_cards, all_sentences, document["images"], document["glossary"]):
        unique(group)
    unique(document["partyNames"], "partyId")
    names = {n["partyId"] for n in document["partyNames"]}
    image_ids = {image["id"] for image in document["images"]}
    allowed_sources = {image["src"] for image in state["assets"]}
    for image in document["images"]:
        if image["src"] not in allowed_sources:
            fail(422, "invalid_image", "이 자료에서 생성하거나 업로드한 그림만 연결할 수 있어요.")
    for card in all_cards:
        if card["partyId"] is not None and card["partyId"] not in names:
            fail(422, "invalid_party", "카드의 당사자 이름이 없어요.")
        if card["imageId"] is not None and card["imageId"] not in image_ids:
            fail(422, "invalid_image", "카드에 연결한 그림이 없어요.")
    for sentence in all_sentences:
        for anchor in sentence["anchors"]:
            anchor_text(state["source"], anchor)


def summarize_document(document):
    return {**{k: document[k] for k in ("saveRevision", "contentRevision", "basedOnStructureRevision", "basedOnSettingsRevision")},
            "sentenceCount": len(sentences(document)), "verifiedCount": sum(s["verified"] for s in sentences(document))}


def reader_content(document, state):
    settings = state["project"]["settings"]
    names = {n["partyId"]: n["displayName"] for n in document["partyNames"]}
    images = {image["id"]: image for image in document["images"]}
    sections = []
    for section in document["sections"]:
        projected = []
        for card in section["cards"]:
            image = images.get(card["imageId"]) if settings["illustrations"] == "with" else None
            projected.append({"id": card["id"], "role": card["role"],
                "partyName": names.get(card["partyId"]) if card["role"] in {"claim", "person"} else None,
                "image": {"src": image["src"], "alt": image["alt"]} if image else None,
                "sentences": [{"id": s["id"], "text": s["text"]} for s in card["sentences"]]})
        sections.append({"kind": section["kind"], "title": section["title"], "cards": projected})
    # Longest overlapping glossary terms take precedence, matching the FE reader.
    terms = sorted(enumerate(document["glossary"]), key=lambda pair: (-len(pair[1]["term"]), pair[0]))
    first_use, cursor = {}, 0
    for sentence in sentences(document):
        occupied = set()
        for _, term in terms:
            for match in re.finditer(re.escape(term["term"]), sentence["text"]):
                span = set(range(*match.span()))
                if not span & occupied:
                    occupied.update(span)
                    first_use.setdefault(term["id"], cursor + match.start())
        cursor += len(sentence["text"]) + 1
    glossary = sorted(document["glossary"], key=lambda term: first_use.get(term["id"], float("inf")))
    return {"title": document["title"], "subtitle": document["subtitle"], "tone": settings["tone"],
            "overview": state["structure"]["overview"], "sections": sections, "glossary": glossary}


def invalidate_review(state):
    state["project"]["review"].update(completedContentRevision=None, completedAt=None)
    state["completion"] = None


def require_document(state):
    if state["document"] is None:
        fail(404, "not_found", "아직 초안을 만들지 않았어요.")
    return state["document"]


def review_items(state):
    document = require_document(state)
    project, structure = state["project"], state["structure"]
    items = []
    revision_context = [project["structureRevision"], project["settingsRevision"]]

    def add(category, title, detail, target, text=None, anchors=None, image_id=None, level="required", extra=None):
        evidence = {"text": text, "anchors": anchors or [], "structureValue": None, "imageId": image_id}
        # Source/settings/anchors are part of the key so old acknowledgements cannot certify new evidence.
        digest = hashlib.sha256(json.dumps([category, title, target, evidence, revision_context, extra], sort_keys=True).encode()).hexdigest()[:24]
        key = f"{category}:{digest}"
        items.append({"key": key, "category": category, "level": level, "title": title, "detail": detail,
                      "target": target, "evidence": evidence, "suggestion": None,
                      "dismissal": state["dismissals"].get(key)})

    if (document["basedOnStructureRevision"] != project["structureRevision"] or
            document["basedOnSettingsRevision"] != project["settingsRevision"]):
        add("structure-changed", "초안 생성 뒤 구조·설정이 바뀌었어요", "바뀐 내용을 직접 반영했는지 확인하거나 초안을 다시 만들어 주세요.", {"type": "document"})
    if not sentences(document):
        add("no-anchor", "설명 문장이 없어요", "초안을 작성해 주세요.", {"type": "document"})
    source_text = "\n".join(p["text"] for p in state["source"]["paragraphs"])
    known_numbers = set(re.findall(r"\d[\d,]*(?:\.\d+)?", source_text.replace(",", "")))
    images = {i["id"]: i for i in document["images"]}
    for card in cards(document):
        if card["role"] in {"claim", "person"} and card["partyId"] is None:
            add("relations", "카드의 당사자가 없어요", "인물·주장 카드가 누구의 내용인지 선택해 주세요.", {"type": "card", "cardId": card["id"]})
        for sentence in card["sentences"]:
            target = {"type": "sentence", "cardId": card["id"], "sentenceId": sentence["id"]}
            text, anchors = sentence["text"], sentence["anchors"]
            if not anchors:
                add("no-anchor", "원문 근거가 없는 문장이에요", "근거를 연결하거나 원문과 직접 비교해 주세요.", target, text, anchors)
            if not sentence["verified"]:
                add("relations", "원문 대조가 필요해요", "누가 누구에게 무엇을 하는지, 부정 표현과 조건을 원문과 비교해 주세요.", target, text, anchors)
            if set(re.findall(r"\d[\d,]*(?:\.\d+)?", text.replace(",", ""))) - known_numbers:
                add("numbers", "숫자를 원문과 비교해 주세요", "표기가 바뀐 금액·날짜·기간일 수 있어요. 값과 단위를 확인해 주세요.", target, text, anchors)
            if card["role"] in {"claim", "finding", "decision"}:
                add("claim-mix", "주장과 법원의 판단을 대조해 주세요", "카드 분류와 말한 사람이 원문과 같은지 확인해 주세요.", target, text, anchors)
            if len(text) > 45:
                add("long-sentence", "문장이 길어요", "한 문장에 한 가지 내용을 담아 주세요.", target, text, anchors, level="suggested")
        image = images.get(card["imageId"]) if project["settings"]["illustrations"] == "with" else None
        if image:
            target = {"type": "image", "cardId": card["id"], "imageId": image["id"]}
            add("image-meaning", "그림의 뜻을 확인해 주세요", "자동 점검은 그림을 해석하지 않아요. 그림이 글과 같은 뜻인지 직접 확인해 주세요.",
                target, " ".join(s["text"] for s in card["sentences"]) + " / " + image["meaning"], image_id=image["id"],
                extra=hashlib.sha256((image["src"] + image["alt"]).encode()).hexdigest())
            if not image["alt"].strip():
                add("alt-text", "대체텍스트가 없어요", "그림 내용을 짧게 설명해 주세요.", target, image_id=image["id"])
    # Important structure items must be represented, even if all remaining sentences were verified.
    covered = [a for s in sentences(document) for a in s["anchors"]]
    for group in ("claims", "findings", "decisions"):
        for item in structure[group]:
            if not any(a["paragraphId"] == b["paragraphId"] and a["start"] < b["end"] and b["start"] < a["end"]
                       for a in item["anchors"] for b in covered):
                add("no-anchor", "사건 구조의 내용이 빠졌을 수 있어요", "중요한 주장·판단·결정이 설명자료에 포함됐는지 확인해 주세요.",
                    {"type": "document"}, item["text"], item["anchors"])
    return items


def sync_review(state):
    run = state["review"]
    state["project"]["review"].update(checkedContentRevision=run["contentRevision"],
        openRequiredCount=sum(i["level"] == "required" and i["dismissal"] is None for i in run["items"]))
