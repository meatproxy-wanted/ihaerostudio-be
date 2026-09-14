"""Maker-only video planning and durable render jobs (no public video sharing)."""
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .models import Model, now, uid


class VideoPlanInput(Model):
    expected_version: int = Field(ge=1, description="현재 문서 version")
    block_ids: list[str] = Field(min_length=1, max_length=8, description="영상으로 설명할 카드 ID. 중복 불가.")
    scenario: str = Field(default="", max_length=6000, description="장면 구성 요청. 원문에 없는 법률 사실을 추가하지 않습니다.")
    visual_style: str = Field(default="성인 독자를 존중하는 차분한 설명용 일러스트", max_length=500)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    max_scenes: int = Field(default=4, ge=1, le=8)


class VideoScene(Model):
    title: str = Field(min_length=1, max_length=150)
    source_block_ids: list[str] = Field(min_length=1, max_length=8)
    narration: str = Field(min_length=1, max_length=1500, description="장면 설명 원고. 1차 버전은 음성 합성을 하지 않습니다.")
    visual_prompt: str = Field(min_length=1, max_length=3000)
    negative_prompt: str = Field(default="text, subtitles, watermark, distorted hands", max_length=1000)
    duration_seconds: Literal[2, 3, 4, 5] = 4


class ScenePlanResult(Model):
    scenes: list[VideoScene] = Field(min_length=1, max_length=8)


class WorkflowNode(Model):
    class_type: str
    inputs: dict[str, Any]


class CompiledScene(Model):
    scene: VideoScene
    seed: int
    width: int
    height: int
    fps: int = 24
    frames: int
    actual_duration_seconds: float
    workflow: dict[str, WorkflowNode]


class VideoPlan(Model):
    id: str = Field(default_factory=uid)
    document_id: str
    source_content_revision: int
    version: int = 1
    request: VideoPlanInput
    preset: Literal["wan22-5b-t2v-v1"] = "wan22-5b-t2v-v1"
    planner: str
    scenes: list[CompiledScene]
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    warning: str = "미검토 영상 계획입니다. 주장·판단·지급 명령을 혼동하지 않는지 직접 확인하세요. 영상은 장면별 무음 클립이며 자동 합본·자막·TTS는 포함하지 않습니다."


class VideoPlanUpdate(Model):
    expected_version: int = Field(ge=1, description="문서가 아닌 영상 계획 version")
    scenes: list[VideoScene] = Field(min_length=1, max_length=8)


class VideoSubmit(Model):
    expected_plan_version: int = Field(ge=1)
    idempotency_key: UUID = Field(description="동일 실행 요청의 재전송에는 같은 UUID를 사용하세요.")
    confirmed: Literal[True] = Field(description="장면·전송 데이터·영상 생성 비용 발생을 확인한 경우만 true")
    note: str = Field(min_length=1, max_length=1000)


class VideoReconcile(Model):
    provider_job_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class VideoOutput(Model):
    asset_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str
    content_type: str
    size_bytes: int = Field(ge=0)
    url: str = Field(description="제작자 전용 만료 URL. 영구 저장 주소가 아닙니다.")
    url_expires_at: str


class VideoJob(Model):
    id: str = Field(default_factory=uid)
    version: int = 1
    document_id: str
    plan_id: str
    plan_version: int
    scene_index: int
    idempotency_key: str
    status: Literal["submitting", "submission_unknown", "submission_failed", "queued", "running", "succeeded", "canceling", "canceled", "failed", "expired"] = "submitting"
    provider_job_id: str | None = None
    poll_url: str | None = None
    cancel_url: str | None = None
    progress: float | None = None
    outputs: list[VideoOutput] = Field(default_factory=list)
    expires_at: str | None = None
    error_code: str | None = None
    review_note: str
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class VideoPreflight(Model):
    compatible: bool
    preset: str
    node_count: int
    issues: list[str]
    note: str = "현재 노드 입력·모델 목록과 대조한 결과이며, 실제 GPU 실행 성공이나 영상 품질을 보장하지 않습니다."
