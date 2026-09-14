"""Fictional, internally consistent Swagger examples; never loaded from user data."""
from copy import deepcopy

from .studio_domain import reader_content, summarize_document

AT = "2026-09-14T09:00:00Z"
TEXT = ("이 자료는 API 사용법을 설명하기 위한 가상 사건이며 실제 판결문이 아닙니다. "
        "A씨는 B씨에게 집을 빌렸습니다. 계약이 끝난 뒤 A씨는 보증금 1,000만 원을 돌려달라고 했습니다. "
        "B씨는 수리비가 남았다고 주장했습니다. 법원은 B씨에게 A씨의 보증금 1,000만 원을 돌려주라고 결정했습니다.")
SETTINGS = {"tone": "haeyo", "naming": "initial", "illustrations": "with"}
SOURCE = {"projectId": "project-example", "paragraphs": [
    {"id": "p1", "block": "header", "kind": "body", "level": None, "page": None, "text": TEXT}]}
ANCHOR = {"paragraphId": "p1", "start": TEXT.index("법원은"), "end": len(TEXT)}
STRUCTURE = {
    "projectId": "project-example", "revision": 0,
    "overview": {"caseName": "가상 임대차보증금 반환 사건", "caseNumber": "", "court": "", "decisionDate": ""},
    "parties": [
        {"id": "party-a", "sourceLabel": "A씨", "legalStatus": "원고", "displayName": "A씨", "easyRole": "집을 빌린 사람", "anchors": [], "flags": []},
        {"id": "party-b", "sourceLabel": "B씨", "legalStatus": "피고", "displayName": "B씨", "easyRole": "집을 빌려준 사람", "anchors": [], "flags": []}],
    "keyFacts": [], "claims": [], "findings": [],
    "decisions": [{"id": "decision-1", "text": TEXT[ANCHOR["start"]:], "anchors": [ANCHOR], "flags": []}],
}
DOCUMENT = {
    "projectId": "project-example", "saveRevision": 0, "contentRevision": 0,
    "basedOnStructureRevision": 0, "basedOnSettingsRevision": 0,
    "title": "보증금을 돌려주라는 법원의 결정", "subtitle": "API 설명을 위한 가상 자료입니다.",
    "partyNames": [{"partyId": "party-a", "displayName": "A씨"}, {"partyId": "party-b", "displayName": "B씨"}],
    "sections": [
        {"kind": "people", "title": "등장인물", "cards": []},
        {"kind": "decision", "title": "법원이 정한 것", "cards": [
            {"id": "card-1", "role": "decision", "partyId": None, "imageId": None, "sentences": [
                {"id": "sentence-1", "text": "법원은 B씨에게 A씨의 보증금 1,000만 원을 돌려주라고 결정했어요.",
                 "anchors": [ANCHOR], "origin": "ai-draft", "verified": False}]}]},
        {"kind": "reasons", "title": "사건의 내용과 이유", "cards": []},
        {"kind": "glossary", "title": "어려운 말 풀이", "cards": []}],
    "glossary": [], "images": [],
}
PROJECT = {
    "id": "project-example", "title": "가상 임대차보증금 반환 사건 쉬운 설명자료", "createdAt": AT, "updatedAt": AT,
    "settings": SETTINGS, "settingsRevision": 0,
    "source": {"kind": "text", "fileName": None, "byteSize": None, "charCount": len(TEXT)},
    "caseNumber": None, "structureRevision": 0, "document": None,
    "review": {"checkedContentRevision": None, "openRequiredCount": None, "completedContentRevision": None, "completedAt": None},
    "publication": {"latestVersion": None, "latestContentRevision": None, "publicPublicationId": None, "publicVersion": None},
}
DRAFT_PROJECT = deepcopy(PROJECT)
DRAFT_PROJECT["document"] = summarize_document(DOCUMENT)
REVIEW = {"projectId": PROJECT["id"], "contentRevision": 0, "ranAt": AT, "items": [
    {"key": "relations:example", "category": "relations", "level": "required", "title": "원문 대조가 필요해요",
     "detail": "누가 누구에게 무엇을 하는지 원문과 비교해 주세요.",
     "target": {"type": "sentence", "cardId": "card-1", "sentenceId": "sentence-1"},
     "evidence": {"text": DOCUMENT["sections"][1]["cards"][0]["sentences"][0]["text"], "anchors": [ANCHOR], "structureValue": None, "imageId": None},
     "suggestion": None, "dismissal": None}]}
REVIEW_PROJECT = deepcopy(DRAFT_PROJECT)
REVIEW_PROJECT["review"].update(checkedContentRevision=0, openRequiredCount=1)
COMPLETION = {"contentRevision": 0, "completedAt": AT, "checklist": ["numbers", "relations", "images", "claims"]}
COMPLETE_PROJECT = deepcopy(REVIEW_PROJECT)
COMPLETE_PROJECT["review"].update(openRequiredCount=0, completedContentRevision=0, completedAt=AT)
PUBLICATION = {"id": "publication-example", "projectId": PROJECT["id"], "version": 1, "createdAt": AT,
               "contentRevision": 0, "reviewed": True,
               "content": reader_content(DOCUMENT, {"project": PROJECT, "structure": STRUCTURE})}
PUBLICATION_SUMMARY = {k: v for k, v in PUBLICATION.items() if k != "content"}
PUBLISHED_PROJECT = deepcopy(COMPLETE_PROJECT)
PUBLISHED_PROJECT["publication"].update(latestVersion=1, latestContentRevision=0)
PUBLIC_PROJECT = deepcopy(PUBLISHED_PROJECT)
PUBLIC_PROJECT["publication"].update(publicPublicationId=PUBLICATION["id"], publicVersion=1)
# A tiny inert PNG for the wire-format example, not an example of generated artwork.
IMAGE = {"id": "image-example", "source": "upload",
         "src": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC",
         "alt": "API 형식을 설명하기 위한 작은 예시 그림", "meaning": "실제 생성 결과 대신 응답의 데이터 형식만 보여주는 예시입니다."}
IMAGE_CANDIDATES = {"candidates": [{k: IMAGE[k] for k in ("src", "alt", "meaning")}]}
