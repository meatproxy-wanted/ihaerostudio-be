from typing import Literal

from pydantic import Field

from .models import BlockContent, Model, now, uid
from .video_models import WorkflowNode


class Illustration(Model):
    prompt: str = Field(min_length=10, max_length=2500, description="영문 장면 설명. 원문 개인정보·URL·실행 명령 없이 생성 의도만 포함.")
    alt_text: str = Field(min_length=1, max_length=500, description="제작 의도에 따른 한국어 대체 텍스트. 실제 그림과 제작자가 대조해야 합니다.")


class IllustratedBlock(BlockContent):
    illustration: Illustration


class IllustratedDraft(Model):
    blocks: list[IllustratedBlock] = Field(min_length=1, max_length=12)


class ImageJob(Model):
    id: str = Field(default_factory=uid)
    document_id: str
    block_id: str
    version: int = 1
    status: Literal["pending", "submitting", "submission_unknown", "submission_failed", "queued", "running", "succeeded", "failed", "expired", "canceling", "canceled", "attached", "stale"] = "pending"
    illustration: Illustration
    workflow: dict[str, WorkflowNode]
    target_fingerprint: str
    provider_job_id: str | None = None
    poll_url: str | None = None
    progress: float | None = None
    asset_id: str | None = None
    error_code: str | None = None
    retry_of: str | None = None
    retry_job_id: str | None = None
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class ImageRetry(Model):
    expected_job_version: int = Field(ge=1)
    confirmed: Literal[True] = Field(description="이 그림의 재생성 외부 전송·비용 확인")


class ImageReconcile(Model):
    expected_job_version: int = Field(ge=1)
    provider_job_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
