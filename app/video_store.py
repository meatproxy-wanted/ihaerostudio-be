from .domain import require_reviewed
from .models import Document, now
from .store import fail
from .video_models import VideoJob, VideoPlan


class VideoStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS video_plans (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL, owner TEXT NOT NULL,
                version INTEGER NOT NULL, body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS video_plan_owner ON video_plans(owner, document_id);
            CREATE TABLE IF NOT EXISTS video_jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, plan_id TEXT NOT NULL,
                scene_index INTEGER NOT NULL, request_key TEXT NOT NULL,
                version INTEGER NOT NULL, body TEXT NOT NULL,
                UNIQUE(owner, request_key), UNIQUE(plan_id, scene_index)
            );
            """)

    def get_plan(self, plan_id, owner):
        with self.store.connect() as db:
            row = db.execute("SELECT body FROM video_plans WHERE id=? AND owner=?", (plan_id, owner)).fetchone()
        if not row:
            fail(404, "video_plan_not_found", "영상 계획을 찾을 수 없습니다.")
        return VideoPlan.model_validate_json(row["body"])

    def plans(self, doc_id, owner):
        self.store.get(doc_id, owner)
        with self.store.connect() as db:
            rows = db.execute("SELECT body FROM video_plans WHERE document_id=? AND owner=? ORDER BY rowid DESC LIMIT 100", (doc_id, owner)).fetchall()
        return [VideoPlan.model_validate_json(r["body"]) for r in rows]

    def create_plan(self, plan, owner):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._doc(db, plan, owner)
            if doc.version != plan.request.expected_version:
                fail(409, "version_conflict", "계획 생성 중 문서가 변경되었습니다. 다시 요청하세요.")
            db.execute("INSERT INTO video_plans VALUES(?,?,?,?,?)", (plan.id, plan.document_id, owner, plan.version, plan.model_dump_json()))
        return plan

    def _doc(self, db, plan, owner):
        row = db.execute("SELECT body FROM documents WHERE id=? AND owner=?", (plan.document_id, owner)).fetchone()
        if not row:
            fail(404, "not_found", "문서를 찾을 수 없습니다.")
        doc = Document.model_validate_json(row["body"])
        if doc.content_revision != plan.source_content_revision:
            fail(409, "stale_video_plan", "문서 내용이 바뀌었습니다. 새 영상 계획을 만드세요.")
        return doc

    def edit_plan(self, plan, owner, expected):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._doc(db, plan, owner)
            if db.execute("SELECT 1 FROM video_jobs WHERE plan_id=?", (plan.id,)).fetchone():
                fail(409, "video_plan_locked", "실행 기록이 있는 계획은 고정됩니다. 수정·재생성하려면 새 계획을 만드세요.")
            plan.version = expected + 1
            plan.updated_at = now()
            result = db.execute("UPDATE video_plans SET version=?,body=? WHERE id=? AND owner=? AND version=?", (plan.version, plan.model_dump_json(), plan.id, owner, expected))
            if result.rowcount != 1:
                fail(409, "version_conflict", "다른 영상 계획 편집이 저장되었습니다.")
        return plan

    def existing_job(self, plan, index, owner, key):
        with self.store.connect() as db:
            row = db.execute("SELECT body FROM video_jobs WHERE owner=? AND (request_key=? OR (plan_id=? AND scene_index=?))", (owner, key, plan.id, index)).fetchone()
        if not row:
            return None
        job = VideoJob.model_validate_json(row["body"])
        if (job.plan_id, job.plan_version, job.scene_index, job.idempotency_key) != (plan.id, plan.version, index, key):
            fail(409, "video_request_conflict", "다른 실행에 사용한 키이거나 이미 생성 요청한 장면입니다.")
        return job

    def reserve(self, plan, index, owner, request):
        key = str(request.idempotency_key)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM video_jobs WHERE owner=? AND (request_key=? OR (plan_id=? AND scene_index=?))", (owner, key, plan.id, index)).fetchone()
            if row:
                job = VideoJob.model_validate_json(row["body"])
                if (job.plan_id, job.plan_version, job.scene_index, job.idempotency_key) != (plan.id, plan.version, index, key):
                    fail(409, "video_request_conflict", "다른 실행에 사용한 키이거나 이미 생성 요청한 장면입니다.")
                return job, False
            doc = self._doc(db, plan, owner)
            require_reviewed(doc)
            row = db.execute("SELECT version FROM video_plans WHERE id=? AND owner=?", (plan.id, owner)).fetchone()
            if row["version"] != request.expected_plan_version:
                fail(409, "version_conflict", "영상 계획이 수정되었습니다. 다시 확인하세요.")
            job = VideoJob(document_id=plan.document_id, plan_id=plan.id, plan_version=plan.version,
                           scene_index=index, idempotency_key=key, review_note=request.note)
            db.execute("INSERT INTO video_jobs VALUES(?,?,?,?,?,?,?)", (job.id, owner, plan.id, index, key, job.version, job.model_dump_json()))
        return job, True

    def get_job(self, job_id, owner):
        with self.store.connect() as db:
            row = db.execute("SELECT body FROM video_jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if not row:
            fail(404, "video_job_not_found", "영상 작업을 찾을 수 없습니다.")
        return VideoJob.model_validate_json(row["body"])

    def jobs(self, plan_id, owner):
        self.get_plan(plan_id, owner)
        with self.store.connect() as db:
            rows = db.execute("SELECT body FROM video_jobs WHERE plan_id=? AND owner=? ORDER BY scene_index", (plan_id, owner)).fetchall()
        return [VideoJob.model_validate_json(r["body"]) for r in rows]

    def save_job(self, job, owner, updates):
        updated = job.model_copy(update={**updates, "version": job.version + 1, "updated_at": now()})
        updated = VideoJob.model_validate(updated.model_dump())
        with self.store.connect() as db:
            result = db.execute("UPDATE video_jobs SET version=?,body=? WHERE id=? AND owner=? AND version=?", (updated.version, updated.model_dump_json(), job.id, owner, job.version))
            if result.rowcount != 1:
                fail(409, "video_job_conflict", "작업 상태가 갱신되었습니다. 다시 조회하세요.")
        return updated
