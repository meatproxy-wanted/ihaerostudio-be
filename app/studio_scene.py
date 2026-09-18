"""One-call scene staging, separate from solo character portrait design."""
from typing import Literal

from pydantic import Field, create_model

from .studio_models import Id, Wire
from .image_workflows import PLANNING_STYLE_INSTRUCTION, wrap_picture_references
from .easy_read import VISUAL_VERSION, VISUAL_TASK_RULES

SCENE_VERSION = VISUAL_VERSION


class SceneCharacter(Wire):
    partyId: Id | None = Field(description="Exact input partyId for a case party; null only for a source-supported generic figure such as a judge.")
    role: str = Field(min_length=1, max_length=60, description="Short English role for a visibly necessary figure, not appearance or a reason to include a party.")
    expression: str = Field(min_length=1, max_length=80, description="Short observable expression, preferably 1-4 English words. Neutral unless supported.")
    position: str = Field(min_length=1, max_length=100, description="Short visible position within this cut, preferably 1-6 English words.")
    action: str = Field(min_length=1, max_length=200, description="One drawable physical action, preferably up to 12 English words. Not a legal conclusion, duration or personal circumstance.")


class SceneObject(Wire):
    name: str = Field(min_length=1, max_length=60, description="English name of a concrete object or source-supported pictogram.")
    stateAndPosition: str = Field(min_length=1, max_length=140, description="Short observable state and location, preferably up to 10 English words. Not a duration or legal explanation.")


class SceneComposition(Wire):
    characters: list[SceneCharacter] = Field(max_length=6, description="Required classification of who must appear and their expressions. Empty only for an object-only scene.")
    situation: str = Field(min_length=10, max_length=500, description="Only the visible scene/action/layout to draw, preferably up to 25 English words. No court findings, legal explanations, dates, counts, durations or nonvisual facts.")
    objects: list[SceneObject] = Field(max_length=3, description="Required classification of visible objects. Empty if no source-supported object is needed; never invent a prop to fill this field.")

    def rendered_prompt(self, references):
        numbers = {r["partyId"]: r["imageNumber"] for r in references}
        people, seen = [], set()
        for character in self.characters:
            # Canonicalize repeated party records without rejecting the scene or
            # requesting another paid plan. One party is one visible person.
            if character.partyId is not None:
                if character.partyId in seen:
                    continue
                seen.add(character.partyId)
            number = numbers.get(character.partyId)
            identity = f"Picture {number}" if number is not None else character.role
            people.append(f"{identity} (expression: {character.expression}; position: {character.position}; action: {character.action}).")
        noun = "person" if len(people) == 1 else "people"
        characters = (f"Exactly {len(people)} {noun}. " + " ".join(people)
                      if people else "No people in this scene.")
        objects = "; ".join(f"{o.name}: {o.stateAndPosition}" for o in self.objects)
        return wrap_picture_references("Characters (expressions): " + characters + "\nSituation: " + self.situation +
                "\nObjects: " + (objects + "." if objects else "None required; omit props."))


def bound_scene_schema(base, context):
    """Constrain new LLM identity choices, without inspecting/rejecting image outputs."""
    refs = context.get("characterReferences", [])
    ids = tuple(dict.fromkeys(r["partyId"] for r in refs))
    if not ids:
        ids = tuple(dict.fromkeys(c["partyId"] for c in context.get("characters", [])))
    if not ids:
        return base
    actor = create_model("BoundSceneCharacter", __base__=SceneCharacter,
        partyId=(Literal[ids] | None, Field(description="Choose the exact matching input partyId; null only for a necessary source-supported generic figure, never to bypass a reference.")))
    return create_model(base.__name__, __base__=base,
        characters=(list[actor], Field(max_length=6, description="Only physically present/visually necessary figures. A shop owner is not automatically present in the shop.")))


class SceneCompositionPlan(SceneComposition):
    """Strict new LLM contract; the legacy free-text plan is never requested."""
    mainMessage: str = Field(min_length=10, max_length=300, description="Internal meaning record, not image input.")
    keyTerms: list[str] = Field(max_length=3)
    semanticBoundary: str = Field(min_length=10, max_length=500, description="Internal legal meaning/status/conditions to preserve when choosing a scene, never copied into image fields.")
    alt: str = Field(min_length=1, max_length=500)
    meaning: str = Field(min_length=1, max_length=500)

    def stored_plan(self):
        # Compatibility storage: historical paid plans can still load unchanged.
        return SceneIllustrationPlan(mainMessage=self.mainMessage, keyTerms=self.keyTerms,
            prompt="Structured scene; render the required composition fields.", focalAction=self.situation,
            staging="Use the positions assigned to each listed character.", objectsAndSetting="Use only the classified source-supported objects.",
            semanticBoundary=self.semanticBoundary, alt=self.alt, meaning=self.meaning,
            composition=SceneComposition(characters=self.characters, situation=self.situation, objects=self.objects))


class SceneIllustrationPlan(Wire):
    # Defaults preserve already paid plans saved before these private fields
    # existed. Structured Outputs makes both fields required for new GPT calls.
    mainMessage: str = Field(default="", min_length=10, max_length=300,
                             description="English: the single core point this scene helps explain; not an image caption.")
    keyTerms: list[str] = Field(default_factory=list, max_length=3,
                                description="Up to three Korean concrete terms actually present in the card text/evidence, illustrated with familiar pictograms.")
    prompt: str = Field(min_length=10, max_length=2500)
    focalAction: str = Field(min_length=10, max_length=500)
    staging: str = Field(min_length=10, max_length=700)
    objectsAndSetting: str = Field(min_length=10, max_length=500)
    semanticBoundary: str = Field(min_length=10, max_length=500)
    alt: str = Field(min_length=1, max_length=500)
    meaning: str = Field(min_length=1, max_length=500)
    composition: SceneComposition | None = None

    def rendered_prompt(self, references=()):
        if self.composition is not None:
            return self.composition.rendered_prompt(references)
        # Historical plans have no composition and keep their original text.
        return self.prompt.strip()


SCENE_TASK = PLANNING_STYLE_INSTRUCTION + """\n카드 하나의 설명 삽화에 실제로 그릴 요소를 구조화하세요. 판결 내용을 요약하는 작업이 아닙니다.
컷마다 characters(인물·표정), situation(상황), objects(오브젝트)를 반드시 분류하세요. 세 필드는 생략할 수 없습니다.
세 항목은 이미지에 보이는 내용만 짧은 영어로 적으세요. 법적 의미와 조건은 mainMessage/semanticBoundary에만 기록하며 이미지 모델에는 전달하지 않습니다.
characters: 당사자는 입력의 정확한 partyId를 사용하고 서버가 Picture 번호에 연결합니다. 인물별 표정·위치·물리적 행동만 지정하세요.
이미지용 문장에서 레퍼런스를 언급할 때는 <Picture 1>, <Picture 2> 형식으로 쓰세요. 컷 안의 별도 Person 번호를 만들지 마세요.
role은 짧은 역할명이며 외형은 레퍼런스만 참고합니다. 표정 근거가 없으면 neutral을 사용하세요.
situation: 한 컷에 보이는 장소·행동·배치만, 가능하면 25단어 이내로 작성하세요. '법원이 인정했다/고려했다', 횟수·기간·가족관계·결정과 완료의 설명을 넣지 마세요.
objects: 실제로 보여야 할 사물·익숙한 아이콘과 보이는 상태·위치만 지정하세요. 각 상태·위치는 가능하면 10단어 이내입니다.
행동은 가능하면 12단어 이내로 구체적으로 적으세요. '물건을 가져간 사실'은 물건을 선반에서 집는 모습처럼 표현하며 가게 근처에 서 있는 모습으로 대체하지 마세요.
현장에 필요한 인물만 포함하세요. 가게 소유자·피해자·당사자라는 이유만으로 현장에 출연시키지 마세요. 무인점포 범행에 소유자의 현장 존재 근거가 없으면 소유자는 제외합니다.
일반 인물(예: 판사)은 근거 있고 실제 장면에 필요할 때만 partyId=null로 지정하세요. 입력 당사자를 null로 바꾸어 레퍼런스를 우회하지 마세요.
징역·집행유예·보호관찰은 막연히 문서 옆에 선 인물을 반복하지 말고, 각각 설명을 돕는 구체적인 시각 요소를 선택하세요.
숫자·기간·금액은 설명 글이 전달합니다. '글자 없는 2년 달력/6개월 표시'처럼 읽을 수 없는 수치를 이미지에 표현하라고 하지 마세요.
명령을 이행하거나 복역을 완료한 것처럼 그리지 마세요. 형 선고와 집행유예를 함께 읽고 현재 수감 여부를 발명하지 마세요. 이 구분은 장면 선택에 적용하며 이미지용 필드에는 법적 해설을 복사하지 마세요.
인물이 필요 없는 컷은 characters=[], 사물이 필요 없는 컷은 objects=[]로 명시하세요. situation은 항상 지정하세요.
세 항목은 중복 설명 없이 조립됩니다. 별도 자유 prompt, 화풍 지시, 'not a completed payment' 같은 상투적인 금지 문구를 쓰지 마세요.
alt와 meaning은 한국어 설계 초안이며 실제 결과를 검수했다는 표현은 쓰지 마세요.
""" + VISUAL_TASK_RULES
