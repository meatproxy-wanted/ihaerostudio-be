"""Use the existing provider transport with the FE's richer structure/draft schema."""
from . import studio_models as wire
from .models import Document, Settings, uid
from .studio_domain import apply_naming, resolve_draft_quotes, resolve_structure_quotes, utf16_length

STUDIO_INSTRUCTIONS = """
이번 작업의 settings는 FE 형식입니다. tone=haeyo는 해요체, hamnida는 합니다체입니다.
naming=initial은 A씨·B씨, role은 쉬운 역할, legal은 원고·피고와 쉬운 설명입니다.
출력 스키마의 anchors에는 paragraphId와, 그 문단에서 글자 그대로 복사한 짧은 quote를 쓰세요.
위치 숫자는 쓰지 않습니다. quote는 원문의 한 문장 또는 그 일부를 한 글자도 바꾸지 말고 옮기고,
한 항목에 근거를 여러 개 붙일 수 있습니다. 원문에 없는 말을 quote에 넣지 마세요.
원문의 개인정보를 추가하지 마세요. 구조의 주장과 판단 및 결정은 분리하고 근거를 붙이세요.
누락된 정보는 빈 문자열, 불확실한 항목은 flags로 남기세요. 모든 id는 서로 달라야 합니다.
작성 지침의 <이 판결의 결론>은 decision 구획, <이런 결론을 내린 이유>는 reasons 구획, 등장인물은 people 구획입니다.
reasons 구획에서는 카드의 첫 문장에 결론을 쓰고, claim 카드에는 당사자가 말한 내용을, finding 카드에는 법원의 판단과 그 이유를 나눠 담으세요.
"누구에게 무엇을 설명하는 자료인지"는 subtitle 한 문장으로 쓰세요. 어려운 말은 그 문장 바로 옆에 짧게 설명하세요.
"""


def source_projection(project_id, source, is_pdf):
    block = "header"
    paragraphs = []
    headings = {"주문": "order", "청구취지": "claim-purpose", "이유": "reasons"}
    for p in source.paragraphs:
        compact = p.text.replace(" ", "")
        heading = compact in headings
        if heading:
            block = headings[compact]
        elif p.text.startswith(("법원 판단:", "원고 주장:", "피고 주장:")):
            block = "reasons"
        elif p.text.startswith("법원 결정:"):
            block = "order"
        paragraphs.append({"id": p.id, "block": block, "kind": "heading" if heading else "body",
                           "level": 1 if heading else None, "page": p.page if is_pdf else None, "text": p.text})
    return {"projectId": project_id, "paragraphs": paragraphs}


def analyze(provider, source, projected, settings):
    # The naming setting is applied here rather than left to the model, which tends to answer A씨·B씨 regardless.
    return apply_naming(extract_structure(provider, source, projected, settings), settings["naming"])


def extract_structure(provider, source, projected, settings):
    if provider.name != "demo":
        quoted = provider.call(STUDIO_INSTRUCTIONS + "원문에서 사건 개요, 당사자, 핵심 사실, 주장, 판단, 결정을 추출하세요.",
                               {"source": projected, "settings": settings}, wire.AiStructureContent).model_dump()
        return wire.StructureContent.model_validate(resolve_structure_quotes(quoted, projected)).model_dump()
    legacy = provider.analyze(Document(title="데모 원문 대조 자료", source=source, settings=Settings()))
    parties = [{"id": p.id, "sourceLabel": p.role, "legalStatus": p.role, "displayName": p.label,
                "easyRole": p.relationship, "anchors": [], "flags": []} for p in legacy.parties]
    result = {"overview": {"caseName": legacy.case_name, "caseNumber": "", "court": "", "decisionDate": ""},
              "parties": parties, "keyFacts": [], "claims": [], "findings": [], "decisions": []}
    paragraph_map = {p.id: p.text for p in source.paragraphs}
    for fact in legacy.facts:
        anchors = [{"paragraphId": e.paragraph_id,
                    "start": utf16_length(paragraph_map[e.paragraph_id][:e.start]),
                    "end": utf16_length(paragraph_map[e.paragraph_id][:e.end])} for e in fact.evidence]
        item = {"id": fact.id, "anchors": anchors, "flags": []}
        if fact.kind == "claim":
            result["claims"].append({**item, "text": fact.text, "partyId": fact.speaker_id})
        elif fact.kind == "finding":
            result["findings"].append({**item, "text": fact.text, "claimIds": [], "stance": "none"})
        elif fact.kind == "decision":
            result["decisions"].append({**item, "text": fact.text})
        else:
            item["flags"] = [{"code": "demo-unclassified", "message": "데모는 원문을 복사합니다. 사건 구조를 직접 분류해 주세요."}]
            result["keyFacts"].append({**item, "kind": "other", "label": "원문 확인", "value": fact.text})
    return wire.StructureContent.model_validate(result).model_dump()


def draft(provider, state):
    structure, project = state["structure"], state["project"]
    if provider.name != "demo":
        quoted = provider.call(STUDIO_INSTRUCTIONS + "쉬운 설명자료 초안을 만드세요. "
            "sections는 people, decision, reasons, glossary 순서이고 glossary의 cards는 빈 배열입니다. "
            "people 카드 role은 person/background, decision은 decision, reasons는 background/claim/finding입니다. "
            "카드별 문장을 나누고 origin=ai-draft, verified=false로 설정하세요. "
            "문장마다 anchors에 그 문장의 바탕이 된 원문 quote를 붙이세요. "
            "images=[], imageId=null로 두세요. 인물·주장 카드의 partyId는 partyNames와 연결하세요. "
            "structure.parties를 공통 인물 기준으로 삼아 partyNames의 partyId와 displayName을 그대로 유지하세요. "
            "등장인물마다 role=person 카드를 만들고 정확한 partyId와 원문에 근거한 역할·관계를 설명하세요. "
            "결론과 이유에서도 같은 인물을 같은 이름으로 부르고, 누가 누구에게 무엇을 해야 하는지 주체·대상을 명확히 쓰세요. "
            "원문에 없는 인물이나 관계를 새로 만들거나 주장 속 관계를 인정된 사실로 바꾸지 마세요. "
            "중요한 주장·판단·결정을 빠짐없이 보존하세요.",
            {"source": state["source"], "structure": structure, "settings": project["settings"]}, wire.AiDraftContent).model_dump()
        return wire.DraftContent.model_validate(resolve_draft_quotes(quoted, state["source"])).model_dump()
    sections = [{"kind": kind, "title": title, "cards": []} for kind, title in
                [("people", "등장인물"), ("decision", "법원의 결정"), ("reasons", "사건의 내용과 이유"), ("glossary", "어려운 말 풀이")]]
    for group, index, role, field in [("keyFacts", 0, "background", "value"), ("claims", 2, "claim", "text"),
                                     ("findings", 2, "finding", "text"), ("decisions", 1, "decision", "text")]:
        for item in structure[group]:
            sections[index]["cards"].append({"id": uid(), "role": role, "partyId": item.get("partyId"), "imageId": None,
                "sentences": [{"id": uid(), "text": item[field], "anchors": item["anchors"], "origin": "ai-draft", "verified": False}]})
    return {"title": project["title"], "subtitle": "데모: 원문을 복사한 자료입니다. 쉬운 글 변환은 실제 AI 설정이 필요합니다.",
            "partyNames": [{"partyId": p["id"], "displayName": p["displayName"]} for p in structure["parties"]],
            "sections": sections, "glossary": [], "images": []}
