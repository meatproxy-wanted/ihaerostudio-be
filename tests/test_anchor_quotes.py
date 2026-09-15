"""Model-written quotes become offsets on the server; nothing is guessed."""
from app.studio_domain import (UNRESOLVED_FLAG, anchor_text, resolve_draft_quotes, resolve_quotes,
                               resolve_structure_quotes)

SOURCE = {"paragraphs": [
    {"id": "p1", "text": "😀 원고는 2022. 3. 1. 피고와  임대차계약을\n체결하였다."},
    {"id": "p2", "text": "피고는 원고에게 98,500,000원을 지급하라."},
]}


def resolved_text(anchor):
    return anchor_text(SOURCE, anchor)


def test_exact_quote_gets_utf16_offsets():
    anchors, unresolved = resolve_quotes([{"paragraphId": "p2", "quote": "98,500,000원을 지급하라"}], SOURCE)
    assert unresolved == 0 and resolved_text(anchors[0]) == "98,500,000원을 지급하라"
    anchors, _ = resolve_quotes([{"paragraphId": "p1", "quote": "원고는"}], SOURCE)
    assert anchors == [{"paragraphId": "p1", "start": 3, "end": 6}]  # the emoji counts as two units


def test_whitespace_differences_are_forgiven():
    anchors, unresolved = resolve_quotes([{"paragraphId": "p1", "quote": "피고와 임대차계약을 체결하였다."}], SOURCE)
    assert unresolved == 0
    assert resolved_text(anchors[0]) == "피고와  임대차계약을\n체결하였다."


def test_wrong_paragraph_id_is_searched_elsewhere():
    anchors, unresolved = resolve_quotes([{"paragraphId": "p1", "quote": "원고에게 98,500,000원을"}], SOURCE)
    assert unresolved == 0 and anchors[0]["paragraphId"] == "p2"
    anchors, unresolved = resolve_quotes([{"paragraphId": "missing", "quote": "지급하라."}], SOURCE)
    assert unresolved == 0 and anchors[0]["paragraphId"] == "p2"


def test_small_rewrites_keep_the_copied_part_only_in_the_named_paragraph():
    anchors, unresolved = resolve_quotes([{"paragraphId": "p2", "quote": "피고는 원고에게 98,500,000원을 지급해야 한다"}], SOURCE)
    assert unresolved == 0 and resolved_text(anchors[0]) == "피고는 원고에게 98,500,000원을 지급"
    anchors, unresolved = resolve_quotes([{"paragraphId": "p1", "quote": "피고는 원고에게 98,500,000원을 지급해야 한다"}], SOURCE)
    assert anchors == [] and unresolved == 1


def test_unfound_quotes_are_dropped_and_flagged():
    anchors, unresolved = resolve_quotes([{"paragraphId": "p2", "quote": "법원은 청구를 기각한다"},
                                          {"paragraphId": "p2", "quote": "   "}], SOURCE)
    assert anchors == [] and unresolved == 2
    structure = {"overview": {}, "parties": [], "keyFacts": [], "claims": [], "findings": [],
                 "decisions": [{"id": "d1", "text": "기각", "flags": [], "anchors": [
                     {"paragraphId": "p2", "quote": "없는 문장"}, {"paragraphId": "p2", "quote": "지급하라"}]}]}
    resolved = resolve_structure_quotes(structure, SOURCE)
    decision = resolved["decisions"][0]
    assert decision["flags"] == [UNRESOLVED_FLAG] and resolved_text(decision["anchors"][0]) == "지급하라"
    draft = {"title": "t", "sections": [{"kind": "decision", "title": "s", "cards": [
        {"id": "c1", "role": "decision", "sentences": [{"id": "s1", "text": "기각", "anchors": [{"paragraphId": "p2", "quote": "없는 문장"}]}]}]}]}
    assert resolve_draft_quotes(draft, SOURCE)["sections"][0]["cards"][0]["sentences"][0]["anchors"] == []


def test_duplicate_quotes_collapse_into_one_anchor():
    anchors, _ = resolve_quotes([{"paragraphId": "p2", "quote": "지급하라"}, {"paragraphId": "p2", "quote": "지급하라"}], SOURCE)
    assert len(anchors) == 1
