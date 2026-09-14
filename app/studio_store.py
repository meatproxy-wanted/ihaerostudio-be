"""Atomic aggregate storage. Existing /api/v1 tables and documents are untouched."""
import json

from .models import now
from .store import fail


class StudioStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS studio_projects (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, version INTEGER NOT NULL,
                updated_at TEXT NOT NULL, body TEXT NOT NULL, original BLOB,
                deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS studio_owner ON studio_projects(owner, deleted, updated_at);
            """)

    def create(self, owner, state, original=None):
        project = state["project"]
        with self.store.connect() as db:
            db.execute("INSERT INTO studio_projects VALUES(?,?,?,?,?,?,0)",
                       (project["id"], owner, 1, project["updatedAt"], json.dumps(state), original))
        return project

    def get(self, project_id, owner):
        with self.store.connect() as db:
            row = db.execute("SELECT body,version FROM studio_projects WHERE id=? AND owner=? AND deleted=0",
                             (project_id, owner)).fetchone()
        if not row:
            fail(404, "not_found", "자료를 찾을 수 없어요.")
        return json.loads(row["body"]), row["version"]

    def save(self, state, owner, version):
        project = state["project"]
        project["updatedAt"] = now().replace("+00:00", "Z")
        with self.store.connect() as db:
            result = db.execute("""UPDATE studio_projects SET body=?,version=version+1,updated_at=?
                WHERE id=? AND owner=? AND version=? AND deleted=0""",
                (json.dumps(state), project["updatedAt"], project["id"], owner, version))
            if result.rowcount != 1:
                fail(409, "version_conflict", "다른 편집이 저장됐어요. 새로고침 후 다시 시도해 주세요.")

    def listing(self, owner):
        with self.store.connect() as db:
            rows = db.execute("SELECT body FROM studio_projects WHERE owner=? AND deleted=0 ORDER BY updated_at DESC",
                              (owner,)).fetchall()
        return [json.loads(row["body"])["project"] for row in rows]

    def remove(self, project_id, owner):
        self.get(project_id, owner)
        # Soft delete also revokes public access; a mistaken removal can be recovered by an operator.
        with self.store.connect() as db:
            db.execute("UPDATE studio_projects SET deleted=1,version=version+1 WHERE id=? AND owner=?",
                       (project_id, owner))

    def public(self, project_id):
        with self.store.connect() as db:
            row = db.execute("SELECT body FROM studio_projects WHERE id=? AND deleted=0", (project_id,)).fetchone()
        if row:
            state = json.loads(row["body"])
            public_id = state["project"]["publication"]["publicPublicationId"]
            publication = next((p for p in state["publications"] if p["id"] == public_id and p["reviewed"]), None)
            if publication:
                return {"status": "available", "publication": publication}
        return {"status": "unavailable"}
