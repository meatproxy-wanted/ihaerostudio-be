"""FE reference integrity, reader projection and conservative producer checks."""
import difflib
import hashlib
import json
import re

from .models import now
from .store import fail


def timestamp():
    return now().replace("+00:00", "Z")


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def display_name(party, naming, index):
    """The party name the maker's naming setting asks for; the model's own choice is not trusted."""
    if naming == "legal":
        return party["legalStatus"]
    if naming == "role":
        return party["easyRole"] or party["legalStatus"]
    return f"{chr(65 + index)}씨" if index < 26 else f"인물 {index + 1}"


def apply_naming(structure, naming):
    for index, party in enumerate(structure["parties"]):
        party["displayName"] = display_name(party, naming, index)
    return structure


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


UNRESOLVED_FLAG = {"code": "anchor-unresolved",
                   "message": "AI가 적은 근거를 원문에서 찾지 못했어요. 원문과 직접 비교해 주세요."}
MAX_ANCHORS = 30


def _collapse(text):
    """A whitespace-insensitive view of text with a map back to original indexes."""
    chars, positions = [], []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                positions.append(index)
            continue
        chars.append(char)
        positions.append(index)
    if chars and chars[-1] == " ":
        chars.pop()
        positions.pop()
    return "".join(chars), positions


def _locate(text, quote, fuzzy):
    """Python [start, end) of a quote inside a paragraph, or None."""
    if not fuzzy:
        index = text.find(quote)
        if index >= 0:
            return index, index + len(quote)
    collapsed, positions = _collapse(text)
    needle, _ = _collapse(quote)
    if not needle:
        return None
    if not fuzzy:
        index = collapsed.find(needle)
        if index >= 0:
            return positions[index], positions[index + len(needle) - 1] + 1
        return None
    # The model rewrote a little: keep the longest run it did copy, if that is most of the quote.
    match = difflib.SequenceMatcher(None, collapsed, needle, autojunk=False).find_longest_match(
        0, len(collapsed), 0, len(needle))
    if match.size >= max(10, int(len(needle) * 0.6)):
        return positions[match.a], positions[match.a + match.size - 1] + 1
    return None


def resolve_quotes(quotes, source):
    """Turns model-written quotes into UTF-16 anchors. Returns (anchors, unresolved count).

    Exact and whitespace-insensitive matches are tried in the named paragraph first, then in
    every other paragraph (models mix up ids more often than text); a fuzzy match is accepted
    only inside the named paragraph. Quotes that cannot be found are dropped, never guessed.
    """
    paragraphs = source["paragraphs"]
    by_id = {p["id"]: p for p in paragraphs}
    anchors, seen, unresolved = [], set(), 0
    for item in quotes:
        quote = item["quote"].strip()
        named = by_id.get(item["paragraphId"])
        ordered = ([named] if named else []) + [p for p in paragraphs if p is not named]
        located = None
        for fuzzy, candidates in ((False, ordered), (True, [named] if named else [])):
            for paragraph in candidates:
                span = _locate(paragraph["text"], quote, fuzzy) if quote else None
                if span:
                    located = (paragraph, span)
                    break
            if located:
                break
        if not located:
            unresolved += 1
            continue
        paragraph, (start, end) = located
        anchor = {"paragraphId": paragraph["id"],
                  "start": utf16_length(paragraph["text"][:start]), "end": utf16_length(paragraph["text"][:end])}
        key = (anchor["paragraphId"], anchor["start"], anchor["end"])
        if key not in seen and len(anchors) < MAX_ANCHORS:
            seen.add(key)
            anchors.append(anchor)
    return anchors, unresolved


def resolve_structure_quotes(structure, source):
    """Structure as the model returned it (quotes) -> the wire structure (anchors)."""
    resolved = {"overview": structure["overview"]}
    for group in ("parties", "keyFacts", "claims", "findings", "decisions"):
        items = []
        for item in structure[group]:
            anchors, unresolved = resolve_quotes(item["anchors"], source)
            flags = list(item["flags"]) + ([UNRESOLVED_FLAG] if unresolved else [])
            items.append({**item, "anchors": anchors, "flags": flags})
        resolved[group] = items
    return resolved


def resolve_draft_quotes(draft, source):
    """Draft as the model returned it (quotes) -> the wire draft (anchors). Unfound quotes leave a sentence without evidence, which the review flags."""
    sections = []
    for section in draft["sections"]:
        cards = []
        for card in section["cards"]:
            sentences = [{**sentence, "anchors": resolve_quotes(sentence["anchors"], source)[0]}
                         for sentence in card["sentences"]]
            cards.append({**card, "sentences": sentences})
        sections.append({**section, "cards": cards})
    return {**draft, "sections": sections}


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


LONG_SENTENCE = 60


def review_items(state):
    """Rule checks a producer asked for: missing evidence, numbers the source does not have, long sentences.

    Only missing evidence blocks completion; numbers and length are pointers for the maker to weigh.

    Deliberately narrow. Everything else (who said what, claims vs findings, picture meaning)
    is the producer's own comparison, guided by the final checklist rather than by items.
    """
    document = require_document(state)
    items = []

    def add(category, title, detail, target, text, anchors, level="required"):
        evidence = {"text": text, "anchors": anchors, "structureValue": None, "imageId": None}
        # The key follows the sentence's content and evidence, so an acknowledgement survives
        # re-checks until the sentence itself changes.
        digest = hashlib.sha256(json.dumps([category, target, evidence], sort_keys=True).encode()).hexdigest()[:24]
        key = f"{category}:{digest}"
        items.append({"key": key, "category": category, "level": level, "title": title, "detail": detail,
                      "target": target, "evidence": evidence, "suggestion": None,
                      "dismissal": state["dismissals"].get(key)})

    source_text = "\n".join(p["text"] for p in state["source"]["paragraphs"])
    known_numbers = set(re.findall(r"\d[\d,]*(?:\.\d+)?", source_text.replace(",", "")))
    for card in cards(document):
        for sentence in card["sentences"]:
            target = {"type": "sentence", "cardId": card["id"], "sentenceId": sentence["id"]}
            text, anchors = sentence["text"], sentence["anchors"]
            if not anchors:
                add("no-anchor", "원문 근거가 없는 문장이에요", "근거를 연결하거나 원문과 직접 비교해 주세요.", target, text, anchors)
            if set(re.findall(r"\d[\d,]*(?:\.\d+)?", text.replace(",", ""))) - known_numbers:
                add("numbers", "숫자를 원문과 비교해 주세요", "표기가 바뀐 금액·날짜·기간일 수 있어요. 값과 단위를 확인해 주세요.", target, text, anchors, level="suggested")
            if len(text) > LONG_SENTENCE:
                add("long-sentence", "문장이 길어요", "한 문장에 한 가지 내용을 담아 주세요.", target, text, anchors, level="suggested")
    return items


def sync_review(state):
    run = state["review"]
    state["project"]["review"].update(checkedContentRevision=run["contentRevision"],
        openRequiredCount=sum(i["level"] == "required" and i["dismissal"] is None for i in run["items"]))
