"""The review keeps three rules: missing evidence, unknown numbers, long sentences."""
from app.studio_domain import review_items

SOURCE = {"paragraphs": [{"id": "p1", "text": "피고는 원고에게 1,000만 원을 지급하라."}]}
ANCHOR = {"paragraphId": "p1", "start": 0, "end": 5}


def state_with(sentences):
    cards = [{"id": f"c{i}", "role": "claim", "partyId": None, "imageId": None, "sentences": [s]} for i, s in enumerate(sentences)]
    document = {"title": "t", "subtitle": "", "partyNames": [], "glossary": [], "images": [],
                "saveRevision": 0, "contentRevision": 0, "basedOnStructureRevision": 0, "basedOnSettingsRevision": 5,
                "sections": [{"kind": "people", "title": "", "cards": []}, {"kind": "decision", "title": "", "cards": []},
                             {"kind": "reasons", "title": "", "cards": cards}, {"kind": "glossary", "title": "", "cards": []}]}
    return {"document": document, "source": SOURCE, "dismissals": {},
            "project": {"structureRevision": 3, "settingsRevision": 0, "settings": {"illustrations": "with"}},
            "structure": {"claims": [], "findings": [], "decisions": []}}


def sentence(text, anchors=(ANCHOR,), verified=False):
    return {"id": "s-" + text[:4], "text": text, "anchors": list(anchors), "origin": "ai-draft", "verified": verified}


def test_only_the_three_rules_fire():
    items = review_items(state_with([
        sentence("피고는 돈을 줘야 해요."),                       # claim card, unverified, no party: no item
        sentence("근거 없는 문장이에요.", anchors=()),           # no-anchor
        sentence("피고는 2,000만 원을 줘야 해요."),               # numbers (2000 not in source)
        sentence("이 문장은 일부러 마흔다섯 글자보다 길게 써서 살펴보기 항목이 생기는지 확인하는 문장이에요."),  # long
    ]))
    assert [(i["category"], i["level"]) for i in items] == [
        ("no-anchor", "required"), ("numbers", "required"), ("long-sentence", "suggested")]
    assert all(i["target"]["type"] == "sentence" for i in items)


def test_keys_ignore_structure_and_settings_revisions():
    state = state_with([sentence("근거 없는 문장이에요.", anchors=())])
    before = review_items(state)[0]["key"]
    state["project"]["structureRevision"] += 1
    state["project"]["settingsRevision"] += 1
    assert review_items(state)[0]["key"] == before
    state["dismissals"][before] = {"memo": "", "at": "2026-09-15T00:00:00Z"}
    assert review_items(state)[0]["dismissal"] == {"memo": "", "at": "2026-09-15T00:00:00Z"}
