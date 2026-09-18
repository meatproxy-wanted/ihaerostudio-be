import copy
import json

import pytest
import httpx
from pydantic import ValidationError

from app.easy_read import TEXT_RULES, VISUAL_COMPOSITION, VISUAL_TASK_RULES, VISUAL_VERSION, text_hints
from app.providers import strict_schema, system_prompt
from app.providers import Provider
from app.config import Config
from app.studio_domain import review_items
from app.studio_scene import SCENE_TASK, SceneIllustrationPlan
from app.studio_storyboard import TASK, StoryboardPlan, Panel, compile_storyboard
from test_review_rules import state_with, sentence
from test_studio_generation import client, setup, submissions
from test_studio_batch import preparation
from test_storyboard import enable


def test_text_rule_priority_and_source_quotes_are_explicit():
    prompt = system_prompt("쉬운 글 초안")
    assert prompt.index("</작성 지침>") < prompt.index(TEXT_RULES) < prompt.rindex("작업:")
    for rule in ("한 문장", "같은 이름", "바로 다음 문장", "반올림", "quote", "기한", "부정", "role=claim/finding/decision"):
        assert rule in TEXT_RULES
    assert "법적 의미·수치·부정·조건 보존" in prompt


def test_concrete_icons_and_one_message_are_shared_by_both_scene_paths():
    for task in (SCENE_TASK, TASK):
        assert VISUAL_TASK_RULES in task
        for rule in ("mainMessage", "keyTerms", "아이콘", "이미지 밖", "지급 명령은 실제 지급 장면이 아닙니다", "근거가 있는 대상"):
            assert rule in task
    assert "concrete pictograms" in VISUAL_COMPOSITION
    assert "include only supported objects" in VISUAL_COMPOSITION
    assert "without people" in VISUAL_COMPOSITION


def plan():
    return dict(mainMessage="The document describes an order, not a completed payment.", keyTerms=["문서"],
                prompt="A document explaining the supplied court order.", focalAction="A document remains on a plain surface.",
                staging="The document is centered with generous space.", objectsAndSetting="One large document pictogram with no lettering.",
                semanticBoundary="An order is not a completed transfer of money.", alt="결정 내용을 설명하는 문서 그림", meaning="법원이 내린 결정이에요.")


def test_private_plan_fields_are_required_in_new_openai_schema_but_legacy_plans_load():
    value = SceneIllustrationPlan(**plan())
    assert value.rendered_prompt() == value.prompt
    assert VISUAL_COMPOSITION not in value.rendered_prompt()
    for schema in (strict_schema(SceneIllustrationPlan), strict_schema(StoryboardPlan)["$defs"]["Panel"]):
        assert {"mainMessage", "keyTerms"} <= set(schema["required"])
    old = {k: v for k, v in plan().items() if k not in {"mainMessage", "keyTerms"}}
    assert SceneIllustrationPlan(**old).mainMessage == ""  # already paid historical plans
    with pytest.raises(ValidationError):
        SceneIllustrationPlan(**{**plan(), "mainMessage": ""})
    with pytest.raises(ValidationError):
        SceneIllustrationPlan(**{**plan(), "keyTerms": ["집", "돈", "문서", "법원"]})


def test_storyboard_sends_each_scene_once_and_common_rules_once():
    value = StoryboardPlan(panels=[Panel(cardId=f"card-{i}", **{**plan(),
        "prompt": f"Picture 1 reviews document numberless scene {i}."}) for i in range(4)])
    graph = compile_storyboard(value, ["finding"] * 4, 1, "test", ["reference.png"], [])
    prompt = graph["4"].inputs["prompt"]
    assert prompt.count(VISUAL_COMPOSITION) == 1
    assert prompt.count("Simple animation-style image or illustration.") == 1
    for panel in value.panels:
        assert prompt.count(panel.prompt) == 1
        assert panel.mainMessage not in prompt
        assert panel.focalAction not in prompt
        assert panel.semanticBoundary not in prompt
    assert len(prompt) < 1500
    assert graph["4"].inputs["image1"] == ["30", 0]
    assert graph["11"].inputs["steps"] == 50


def test_storyboard_does_not_block_long_input_or_add_visual_quality_gate():
    value = StoryboardPlan(panels=[Panel(cardId=f"card-{i}", **{**plan(),
        "prompt": "A document lies on the table. " * 70}) for i in range(4)])
    graph = compile_storyboard(value, ["finding"] * 4, 1, "test", ["reference.png"], [])
    prompt = graph["4"].inputs["prompt"]
    assert len(prompt) > 6000
    for panel in value.panels:
        assert panel.prompt.strip() in prompt


@pytest.mark.parametrize("schema,task", [(SceneIllustrationPlan, SCENE_TASK), (StoryboardPlan, TASK)])
def test_new_plan_fields_round_trip_through_openai_transport(monkeypatch, schema, task):
    expected = plan() if schema is SceneIllustrationPlan else {"panels": [{"cardId": "card-1", **plan()}]}

    def post(self, url, **kwargs):
        body = kwargs["json"]
        assert TEXT_RULES in body["input"][0]["content"]
        assert VISUAL_TASK_RULES in body["input"][0]["content"]
        assert body["text"]["format"]["strict"] is True
        return httpx.Response(200, request=httpx.Request("POST", url), json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(expected)}]}]})

    monkeypatch.setattr(httpx.Client, "post", post)
    result = Provider(Config(provider="openai", openai_api_key="test", openai_model="test-model")).call(task, {}, schema)
    focus = result if schema is SceneIllustrationPlan else result.panels[0]
    assert focus.keyTerms == ["문서"] and focus.mainMessage == plan()["mainMessage"]


@pytest.mark.parametrize("text,category", [
    ("A씨는 돈을 요청했어요. B씨는 거절했어요.", "long-sentence"),
    ("법원은 거절하지 않을 수 없다고 판단했어요.", "hard-term"),
    ("그것을 돌려줘야 해요.", "relations"),
    ("앞서 본 것과 같아요.", "relations"),
])
def test_easy_read_hints_are_advisory_and_do_not_change_sentences(text, category):
    state = state_with([sentence(text)])
    before = copy.deepcopy(state)
    items = review_items(state)
    assert any(i["category"] == category and i["level"] == "suggested" for i in items)
    assert state == before


@pytest.mark.parametrize("text", [
    "법원은 돈을 지급하지 않아도 된다고 결정했어요.",
    "A씨는 1.5퍼센트의 이자를 요청했어요.",
    "그림은 문서 옆에 있어요.",
    "A씨는 1,000만 원을 돌려달라고 요청했어요.",
])
def test_necessary_negation_decimals_and_concrete_text_are_not_flagged(text):
    assert text_hints(text) == []


def test_glossary_hint_requires_nearby_explanation_and_resets_acknowledgement():
    state = state_with([sentence("A씨는 보증금을 요청했어요.")])
    state["document"]["glossary"] = [{"id": "term", "term": "보증금", "explanation": "집을 빌릴 때 맡긴 돈이에요."}]
    item = next(i for i in review_items(state) if i["category"] == "hard-term")
    state["dismissals"][item["key"]] = {"memo": "확인", "at": "now"}
    assert next(i for i in review_items(state) if i["category"] == "hard-term")["dismissal"]
    state["document"]["glossary"][0]["explanation"] = "집을 빌리는 동안 맡겨 둔 돈이에요."
    changed = next(i for i in review_items(state) if i["category"] == "hard-term")
    assert changed["key"] != item["key"] and changed["dismissal"] is None
    state["document"]["glossary"][0]["explanation"] = "집을 빌릴 때 맡긴 돈이에요."
    state["document"]["sections"][2]["cards"][0]["sentences"].append(sentence("집을 빌릴 때 맡긴 돈이에요."))
    assert not any(i["category"] == "hard-term" for i in review_items(state))


def test_hints_have_unique_keys_when_multiple_rules_share_a_category():
    state = state_with([sentence("그것은 없지 않아요. 다시 확인해요.")])
    state["document"]["glossary"] = [{"id": "term", "term": "그것", "explanation": "실제 대상의 이름"}]
    items = review_items(state)
    assert len(items) == len({i["key"] for i in items})
    detail = next(i["detail"] for i in items if i["category"] == "hard-term")
    assert "부정" in detail and "용어집" in detail


@pytest.mark.parametrize("status", ["prepared", "running", "submission_unknown"])
def test_easy_read_upgrade_replans_only_definitely_unsubmitted_storyboards(setup, monkeypatch, status):
    _, service, project, _, _, control = setup
    enable(setup)
    monkeypatch.setattr("app.studio_storyboard.WAIT_SECONDS", 0)
    control["status"] = "running"
    control["submit_error"] = 400 if status == "prepared" else "timeout" if status == "submission_unknown" else None
    assert preparation(setup).status_code == (200 if status == "running" else 503)
    with service.store.store.connect() as db:
        row = db.execute("SELECT id,body FROM studio_image_jobs WHERE project_id=? AND card_id LIKE 'storyboard4:%'",
                         (project["id"],)).fetchone()
        job = json.loads(row["body"])
        job.pop("easy_read_revision")
        original_workflow = copy.deepcopy(job["workflow"])
        db.execute("UPDATE studio_image_jobs SET body=? WHERE id=?", (json.dumps(job), row["id"]))
    control["submit_error"], control["status"] = None, "succeeded"
    response = preparation(setup)
    assert response.status_code == (503 if status == "submission_unknown" else 200), response.text
    assert service.provider.storyboard_calls == (2 if status == "prepared" else 1)
    assert len(submissions(control)) == (2 if status == "prepared" else 1)
    with service.store.store.connect() as db:
        updated = json.loads(db.execute("SELECT body FROM studio_image_jobs WHERE id=?", (row["id"],)).fetchone()["body"])
    if status == "prepared":
        assert updated["easy_read_revision"] == VISUAL_VERSION
        assert VISUAL_COMPOSITION in updated["workflow"]["4"]["inputs"]["prompt"]
    else:
        assert updated["workflow"] == original_workflow
