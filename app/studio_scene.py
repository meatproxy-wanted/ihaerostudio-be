"""One-call scene staging, separate from solo character portrait design."""
from pydantic import Field

from .studio_models import Wire
from .studio_models import Id
from .image_workflows import PLANNING_STYLE_INSTRUCTION
from .easy_read import VISUAL_VERSION, VISUAL_TASK_RULES

SCENE_VERSION = VISUAL_VERSION


class SceneCharacter(Wire):
    partyId: Id | None = Field(description="Exact input partyId for a case party; null only for a source-supported generic figure such as a judge.")
    role: str = Field(min_length=1, max_length=60, description="English role, not appearance.")
    expression: str = Field(min_length=1, max_length=80, description="English visible expression; neutral unless a stronger expression is supported. Not actual intent or personality.")
    position: str = Field(min_length=1, max_length=100, description="English position inside this scene, e.g. left or right.")
    action: str = Field(min_length=1, max_length=200, description="English visible action consistent with the legal status of the event.")


class SceneObject(Wire):
    name: str = Field(min_length=1, max_length=60, description="English name of a concrete object or source-supported pictogram.")
    stateAndPosition: str = Field(min_length=1, max_length=140, description="English visible state and position; do not turn an ordered payment into a completed transfer.")


class SceneComposition(Wire):
    characters: list[SceneCharacter] = Field(max_length=6, description="Required classification of who must appear and their expressions. Empty only for an object-only scene.")
    situation: str = Field(min_length=10, max_length=500, description="Required English description of the situation, relationship and request/finding/order/completion distinction.")
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
            index = len(people) + 1
            number = numbers.get(character.partyId)
            identity = f"the person from Picture {number}" if number is not None else character.role
            people.append(f"Person {index} = {identity}; position: {character.position}; expression: {character.expression}; action: {character.action}.")
        noun = "person" if len(people) == 1 else "people"
        characters = (f"Exactly {len(people)} {noun}. " + " ".join(people) +
                      " Each listed person appears once as a distinct individual and retains their own reference identity."
                      if people else "No people in this scene.")
        objects = "; ".join(f"{o.name}: {o.stateAndPosition}" for o in self.objects)
        return ("Characters (expressions): " + characters + "\nSituation: " + self.situation +
                "\nObjects: " + (objects + ". Show every listed object clearly." if objects else "None required; omit props.") +
                "\nDepict all specified characters, expressions, situation and objects together in this scene.")


class SceneCompositionPlan(SceneComposition):
    """Strict new LLM contract; the legacy free-text plan is never requested."""
    mainMessage: str = Field(min_length=10, max_length=300)
    keyTerms: list[str] = Field(max_length=3)
    semanticBoundary: str = Field(min_length=10, max_length=500)
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


SCENE_TASK = PLANNING_STYLE_INSTRUCTION + """\n카드 하나의 설명 삽화를 설계하세요.
컷마다 characters(인물·표정), situation(상황), objects(오브젝트)를 반드시 분류하세요. 세 필드는 생략할 수 없습니다.
분류와 인물 선택·표정·위치·행동·사물 상태는 입력 글과 원문 근거를 바탕으로 판단하세요. 장면 설명은 영어입니다.
characters의 당사자는 입력의 partyId를 그대로 사용하세요. 서버가 partyId를 실제 레퍼런스 Picture 번호에 연결합니다.
서로 다른 당사자는 서로 다른 characters 항목으로 나누며 각 인물을 한 번씩만 지정하세요. 참조 이미지는 등장인물이 아니라 입력 자료이므로 모든 참조 인물을 무조건 출연시키지 마세요.
role에는 역할만, expression에는 표정만, position에는 컷 내부 위치만, action에는 행동만 적으세요. 두 인물의 위치와 행동을 각각 명시하세요.
입력에 근거가 있는 일반 인물(예: 판사)은 partyId=null로 두세요. 없는 인물은 추가하지 마세요.
인물 외형은 레퍼런스를 참고합니다. 외형을 텍스트로 묘사하지 마세요.
실제 감정·성격·의도를 추정하지 마세요. 표정 근거가 없으면 neutral 등 과장 없는 표정을 선택하세요.
situation에는 누가 무엇을 하는지와 주장/판단/명령/완료의 차이를 짧게 적으세요.
objects에는 근거 있는 핵심 사물·익숙한 아이콘의 이름과 상태·위치를 지정하세요. 항목을 채우려고 원문에 없는 물건을 발명하지 마세요.
인물이 필요 없는 컷은 characters=[], 사물이 필요 없는 컷은 objects=[]로 명시하세요. situation은 항상 지정하세요.
서버가 이 세 항목을 모두 필수 시각 요소로 조립하므로 별도의 자유 prompt를 만들거나 같은 지시를 반복하지 마세요.
공통 화풍·금지 규칙은 서버에서 붙입니다. semanticBoundary는 영어 법적 의미 보존 기록입니다.
alt와 meaning은 한국어 설계 초안이며 실제 결과를 검수했다는 표현은 쓰지 마세요.
""" + VISUAL_TASK_RULES
