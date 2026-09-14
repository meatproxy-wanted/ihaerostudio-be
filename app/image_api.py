"""Durable per-card image jobs. Refresh finishes import; no in-memory-only queue.

Paid submissions are reserved before I/O. Unknown submissions are never retried
automatically. Document + jobs and image + attachment are atomic transactions.
"""
import hashlib
import ipaddress
import json
import secrets
from typing import Annotated
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import BackgroundTasks, Depends, HTTPException

from .comfy import ComfyFailure, ORIGIN
from .domain import changed, validate_block
from .image_models import ImageJob, ImageReconcile, ImageRetry, Illustration
from .image_workflows import ALLOWED, PRESET, compile_image
from .models import Block, BlockContent, Document, Picture, now, uid
from .sources import MAX_IMAGE_BYTES, clean_image
from .store import fail
from .video_models import VideoOutput


def fingerprint(doc, block):
    data = {"block": block.model_dump(), "settings": doc.settings.model_dump(),
            "structure": doc.structure.model_dump() if doc.structure else None,
            "confirmed": doc.structure_confirmed}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ImageService:
    def __init__(self, store, provider, comfy):
        self.store, self.provider, self.comfy = store, provider, comfy
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS image_jobs (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL, owner TEXT NOT NULL,
                version INTEGER NOT NULL, body TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS image_document ON image_jobs(document_id, owner)")

    def get(self, job_id, maker):
        with self.store.connect() as db:
            row = db.execute("SELECT body FROM image_jobs WHERE id=? AND owner=?", (job_id, maker)).fetchone()
        if not row:
            fail(404, "image_job_not_found", "그림 작업을 찾을 수 없습니다.")
        return ImageJob.model_validate_json(row["body"])

    def listing(self, doc_id, maker):
        self.store.get(doc_id, maker)
        with self.store.connect() as db:
            rows = db.execute("SELECT body FROM image_jobs WHERE document_id=? AND owner=? ORDER BY rowid", (doc_id, maker)).fetchall()
        return [ImageJob.model_validate_json(r["body"]) for r in rows]

    def save(self, job, maker, db=None):
        if db is None:
            with self.store.connect() as connection:
                return self.save(job, maker, connection)
        expected = job.version
        job.version += 1
        job.updated_at = now()
        result = db.execute("UPDATE image_jobs SET version=?,body=? WHERE id=? AND owner=? AND version=?",
                            (job.version, job.model_dump_json(), job.id, maker, expected))
        if result.rowcount != 1:
            fail(409, "image_job_conflict", "작업 상태가 바뀌었습니다. 다시 조회하세요.")
        return job

    def insert(self, db, job, maker):
        db.execute("INSERT INTO image_jobs VALUES(?,?,?,?,?)", (job.id, job.document_id, maker, job.version, job.model_dump_json()))

    def save_document(self, db, doc, maker, expected, event):
        doc.version = expected + 1
        doc.updated_at = now()
        result = db.execute("UPDATE documents SET version=?,body=? WHERE id=? AND owner=? AND version=?",
                            (doc.version, doc.model_dump_json(), doc.id, maker, expected))
        if result.rowcount != 1:
            fail(409, "version_conflict", "다른 편집이 저장되었습니다. 문서를 다시 조회하세요.")
        self.store._history(db, doc, event, maker)

    def prepare(self, doc, body, maker):
        if not body.confirm_image_cost:
            fail(422, "image_cost_confirmation_required", "그림 설명의 외부 전송·카드별 생성 비용 확인이 필요합니다.")
        if not doc.settings.use_images:
            fail(422, "images_disabled", "문서 설정의 use_images를 먼저 켜세요.")
        self.comfy.require_key()
        try:
            probe = compile_image(Illustration(prompt="An anonymous adult reading a document.", alt_text="가용성 확인"), 1, "ihaero-probe")
            check = self.comfy.preflight(probe, allowed=ALLOWED, preset=PRESET)
        except ComfyFailure as error:
            fail(502, error.code, "그림 모델·노드 가용성 조회에 실패했습니다. 문서는 변경되지 않았습니다.")
        if not check.compatible:
            fail(422, "image_workflow_unavailable", "그림 프리셋이 현재 Cloud에서 지원되지 않습니다: " + "; ".join(check.issues))
        illustrated = self.provider.illustrated_draft(doc, body.max_images)
        if not 1 <= len(illustrated) <= body.max_images:
            fail(422, "image_limit", "카드 수가 max_images를 초과했습니다. 글 전용으로 생성하거나 문서를 나누세요. 그림은 실행되지 않았습니다.")
        blocks, jobs = [], []
        for item in illustrated:
            content = BlockContent.model_validate(item.model_dump(exclude={"illustration"}))
            if content.picture is not None:
                fail(502, "invalid_generated_picture", "초안 응답에 기존 그림 참조를 넣을 수 없습니다.")
            validate_block(doc, content, self.store)
            block = Block(**content.model_dump())
            job_id = uid()
            jobs.append(ImageJob(id=job_id, document_id=doc.id, block_id=block.id,
                                 target_fingerprint=fingerprint(doc, block), illustration=item.illustration,
                                 workflow=compile_image(item.illustration, secrets.randbits(48), "ihaero-" + job_id)))
            blocks.append(block)
        doc.blocks, doc.image_job_ids = blocks, [j.id for j in jobs]
        changed(doc)
        with self.store.connect() as db:
            self.save_document(db, doc, maker, body.expected_version, "illustrated_draft_generated")
            for job in jobs:
                self.insert(db, job, maker)
        return doc

    def target_current(self, job, maker):
        doc = self.store.get(job.document_id, maker)
        block = next((b for b in doc.blocks if b.id == job.block_id), None)
        return block is not None and fingerprint(doc, block) == job.target_fingerprint

    def start_many(self, ids, maker):
        for job_id in ids:
            try:
                self.start(job_id, maker)
            except HTTPException:
                # Another request may already have reserved it. Durable state is authoritative.
                continue

    def start(self, job_id, maker):
        job = self.get(job_id, maker)
        if job.status != "pending":
            return job
        if not self.target_current(job, maker):
            job.status, job.error_code = "stale", "image_target_changed"
            return self.save(job, maker)
        self.comfy.require_key()
        job.status = "submitting"
        self.save(job, maker)  # CAS reservation precedes paid I/O, including crash windows.
        try:
            parsed = self.comfy.parse_job(self.comfy.submit(job.workflow, job.id), media_type="image")
            self.apply_remote(job, parsed)
        except ComfyFailure as error:
            job.status = "submission_unknown" if error.uncertain else "submission_failed"
            job.error_code = error.code
        return self.save(job, maker)

    @staticmethod
    def apply_remote(job, parsed):
        for key in ("provider_job_id", "poll_url", "status", "progress", "error_code"):
            setattr(job, key, parsed[key])

    def download(self, output):
        try:
            url = urlsplit(output.url)
            port = url.port
        except ValueError:
            raise ComfyFailure("image_download_host_not_allowed") from None
        hosts = {h.strip().lower() for h in self.comfy.config.comfy_asset_allowed_hosts if h.strip()}
        try:
            address = ipaddress.ip_address(url.hostname or "")
        except ValueError:
            address = None
        if (url.scheme != "https" or url.hostname not in hosts or port not in {None, 443}
                or url.username or url.password or url.fragment or address is not None
                or url.hostname == "localhost" or (url.hostname or "").endswith(".localhost")):
            raise ComfyFailure("image_download_host_not_allowed")
        if output.size_bytes > MAX_IMAGE_BYTES:
            raise ComfyFailure("image_download_too_large")
        try:
            # Never forward Comfy credentials to a signed asset URL. Redirects disabled.
            with httpx.Client(timeout=httpx.Timeout(45, connect=5), trust_env=False, follow_redirects=False) as client:
                with client.stream("GET", output.url) as response:
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ComfyFailure("image_download_failed")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes(65536):
                        size += len(chunk)
                        if size > MAX_IMAGE_BYTES:
                            raise ComfyFailure("image_download_too_large")
                        chunks.append(chunk)
            return clean_image(b"".join(chunks))
        except (httpx.HTTPError, HTTPException):
            raise ComfyFailure("image_download_invalid_or_failed") from None

    def attach(self, job, maker, image):
        data, width, height = image
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM documents WHERE id=? AND owner=?", (job.document_id, maker)).fetchone()
            if not row:
                fail(404, "not_found", "문서를 찾을 수 없습니다.")
            doc = Document.model_validate_json(row["body"])
            block = next((b for b in doc.blocks if b.id == job.block_id), None)
            job.asset_id = uid()
            db.execute("INSERT INTO assets VALUES(?,?,?,?,?)", (job.asset_id, doc.id, data, width, height))
            if block and fingerprint(doc, block) == job.target_fingerprint:
                # 'neutral' is deliberately not an assertion about visual/legal correctness.
                block.picture = Picture(asset_id=job.asset_id, alt_text=job.illustration.alt_text, meaning="neutral")
                changed(doc)
                self.save_document(db, doc, maker, doc.version, "generated_image_attached")
                job.status, job.error_code = "attached", None
            else:
                job.status, job.error_code = "stale", "image_target_changed"
            self.save(job, maker, db)
        return job

    def refresh(self, job_id, maker):
        job = self.get(job_id, maker)
        if job.status == "pending":
            job = self.start(job.id, maker)
        if job.status not in {"queued", "running", "succeeded", "canceling"}:
            return job
        try:
            parsed = self.comfy.parse_job(self.comfy.request("GET", job.poll_url), job.provider_job_id, media_type="image")
            self.apply_remote(job, parsed)
            if job.status == "succeeded":
                if len(parsed["outputs"]) != 1:
                    raise ComfyFailure("image_output_count_invalid")
                output = parsed["outputs"][0]
                # Refresh expiring signed links without starting a new generation.
                asset = self.comfy.request("GET", ORIGIN + "/api/v2/assets/" + output.asset_id)
                if asset.get("id") != output.asset_id or asset.get("job_id") not in {None, job.provider_job_id}:
                    raise ComfyFailure("image_asset_mismatch")
                try:
                    output = VideoOutput.model_validate({**output.model_dump(), **{
                        k: asset[k] for k in ("url", "url_expires_at", "content_type", "size_bytes")}})
                except (KeyError, ValueError, TypeError):
                    raise ComfyFailure("image_asset_invalid") from None
                if not output.content_type.startswith("image/"):
                    raise ComfyFailure("image_asset_invalid")
                output.url = urljoin(ORIGIN, output.url)
                return self.attach(job, maker, self.download(output))
        except ComfyFailure as error:
            job.error_code = error.code  # Preserve remote success; retry download, never regenerate.
        return self.save(job, maker)

    def retry(self, job_id, body, maker):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self.get(job_id, maker)
            if job.retry_job_id:
                return self.get(job.retry_job_id, maker)
            if job.version != body.expected_job_version:
                fail(409, "image_job_conflict", "작업 상태를 다시 조회하세요.")
            if job.status not in {"submission_failed", "failed", "canceled", "expired"}:
                fail(409, "image_retry_not_allowed", "확정된 실패만 재생성할 수 있습니다. 접수 불명은 reconcile, 다운로드 오류는 refresh를 사용하세요.")
            if not self.target_current(job, maker):
                fail(409, "image_target_changed", "카드가 바뀌었습니다. 현재 내용으로 새 초안을 만드세요.")
            new_id = uid()
            new_job = ImageJob(id=new_id, document_id=job.document_id, block_id=job.block_id,
                               target_fingerprint=job.target_fingerprint, illustration=job.illustration, retry_of=job.id,
                               workflow=compile_image(job.illustration, secrets.randbits(48), "ihaero-" + new_id))
            self.insert(db, new_job, maker)
            job.retry_job_id = new_id
            self.save(job, maker, db)
        return new_job

    def reconcile(self, job_id, body, maker):
        job = self.get(job_id, maker)
        if job.version != body.expected_job_version or job.status not in {"submitting", "submission_unknown"}:
            fail(409, "image_reconcile_not_allowed", "접수 상태가 불명확한 작업만 현재 버전으로 연결할 수 있습니다.")
        url = ORIGIN + "/api/v2/jobs/" + body.provider_job_id
        try:
            remote = self.comfy.request("GET", url + "/workflow")
            if remote.get("format") != "api" or remote.get("workflow") != {k: n.model_dump() for k, n in job.workflow.items()}:
                fail(422, "image_workflow_mismatch", "이 작업에 제출한 워크플로우와 일치하지 않습니다.")
            parsed = self.comfy.parse_job(self.comfy.request("GET", url), body.provider_job_id, media_type="image")
            self.apply_remote(job, parsed)
        except ComfyFailure as error:
            fail(502, error.code, "외부 작업 확인에 실패했습니다. 재생성하지 않았습니다.")
        return self.save(job, maker)


def register_image_routes(api, owner):
    images = ImageService(api.state.store, api.state.provider, api.state.comfy)
    api.state.images = images
    Owner = Annotated[str, Depends(owner)]
    tag = "03 글그림 편집"

    @api.get("/api/v1/documents/{doc_id}/image-jobs", response_model=list[ImageJob], tags=[tag], summary="카드별 그림 생성 상태 목록")
    def listing(doc_id: str, maker: Owner):
        return images.listing(doc_id, maker)

    @api.get("/api/v1/image-jobs/{job_id}", response_model=ImageJob, tags=[tag], summary="그림 작업 조회 (상태 변경 없음)")
    def get(job_id: str, maker: Owner):
        return images.get(job_id, maker)

    @api.post("/api/v1/image-jobs/{job_id}/refresh", response_model=ImageJob, tags=[tag], summary="그림 상태 갱신·완성 이미지 자동 연결", description="5~10초 간격으로 호출. pending은 미접수 작업을 재개합니다. 성공하면 PNG로 저장하고 카드에 연결하며 문서 version이 증가합니다. 편집된 카드는 덮어쓰지 않습니다. submitting/unknown은 자동 재접수하지 않습니다.")
    def refresh(job_id: str, maker: Owner):
        return images.refresh(job_id, maker)

    @api.post("/api/v1/image-jobs/{job_id}/retry", response_model=ImageJob, status_code=202, tags=[tag], summary="실패한 그림만 비용 재확인 후 재생성")
    def retry(job_id: str, body: ImageRetry, background: BackgroundTasks, maker: Owner):
        job = images.retry(job_id, body, maker)
        background.add_task(images.start_many, [job.id], maker)
        return job

    @api.post("/api/v1/image-jobs/{job_id}/reconcile", response_model=ImageJob, tags=[tag], summary="접수 불명 작업을 Cloud 작업 ID로 복구", description="Cloud 대시보드에서 찾은 ID를 전달. 제출 JSON 전체 일치 확인 후 연결하며 추가 생성/과금 요청은 하지 않습니다.")
    def reconcile(job_id: str, body: ImageReconcile, maker: Owner):
        return images.reconcile(job_id, body, maker)
