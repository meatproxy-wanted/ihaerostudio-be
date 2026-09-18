"""One-call scene staging, separate from solo character portrait design."""
from pydantic import Field

from .studio_models import Wire
from .image_workflows import PLANNING_STYLE_INSTRUCTION
from .easy_read import VISUAL_VERSION, VISUAL_TASK_RULES

SCENE_VERSION = VISUAL_VERSION


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

    def rendered_prompt(self):
        # Planning fields remain for stored-plan compatibility, not image input.
        # GPT integrates them into this one description; never repeat them.
        return self.prompt.strip()


SCENE_TASK = PLANNING_STYLE_INSTRUCTION + """\n카드 하나의 설명 삽화를 설계하세요.
prompt는 영어 60단어 이내로, 인물별 참조 번호(Picture 1 등)·행동·배치·핵심 사물을 한 번씩만 적으세요.
공통 화풍·인물 외형 목록·금지 규칙은 서버에서 붙이므로 prompt에 반복하지 마세요.
focalAction, staging, objectsAndSetting, semanticBoundary는 영어 설계 기록이며 이미지 모델에는 별도로 보내지 않습니다.
alt와 meaning은 한국어 설계 초안이며 실제 결과를 검수했다는 표현은 쓰지 마세요.
""" + VISUAL_TASK_RULES
