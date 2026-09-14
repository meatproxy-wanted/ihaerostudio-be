import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from .models import Document, HistoryEntry, now


def fail(status: int, code: str, message: str):
    raise HTTPException(status, detail={"code": code, "message": message})


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, version INTEGER NOT NULL,
              body TEXT NOT NULL, original BLOB
            );
            CREATE INDEX IF NOT EXISTS doc_owner ON documents(owner);
            CREATE TABLE IF NOT EXISTS history (
              document_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
              event TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY(document_id, version)
            );
            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY, document_id TEXT NOT NULL, body BLOB NOT NULL,
              width INTEGER NOT NULL, height INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exports (
              id TEXT PRIMARY KEY, document_id TEXT NOT NULL, owner TEXT NOT NULL,
              body TEXT NOT NULL, snapshot TEXT NOT NULL, pdf BLOB NOT NULL,
              token_hash TEXT UNIQUE, expires_at TEXT, revoked INTEGER DEFAULT 0
            );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner: str, doc: Document, original: bytes | None = None):
        with self.connect() as db:
            db.execute("INSERT INTO documents VALUES(?,?,?,?,?)", (doc.id, owner, doc.version, doc.model_dump_json(), original))
            self._history(db, doc, "uploaded", owner)
        return doc

    def get(self, doc_id: str, owner: str) -> Document:
        with self.connect() as db:
            row = db.execute("SELECT body FROM documents WHERE id=? AND owner=?", (doc_id, owner)).fetchone()
        if not row:
            fail(404, "not_found", "문서를 찾을 수 없습니다.")
        return Document.model_validate_json(row["body"])

    def save(self, doc: Document, owner: str, expected: int, event: str):
        doc.version = expected + 1
        doc.updated_at = now()
        with self.connect() as db:
            result = db.execute("UPDATE documents SET version=?,body=? WHERE id=? AND owner=? AND version=?", (doc.version, doc.model_dump_json(), doc.id, owner, expected))
            if result.rowcount != 1:
                fail(409, "version_conflict", "다른 편집이 저장되었습니다. 문서를 다시 조회하세요.")
            self._history(db, doc, event, owner)
        return doc

    def _history(self, db, doc, event, owner):
        db.execute("INSERT INTO history VALUES(?,?,?,?,?,?)", (doc.id, doc.version, doc.model_dump_json(), event, owner, now()))

    def listing(self, owner, limit, offset):
        with self.connect() as db:
            rows = db.execute("SELECT body FROM documents WHERE owner=? ORDER BY rowid DESC LIMIT ? OFFSET ?", (owner, limit, offset)).fetchall()
        return [Document.model_validate_json(r["body"]) for r in rows]

    def history(self, doc_id, owner):
        self.get(doc_id, owner)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM history WHERE document_id=? ORDER BY version DESC", (doc_id,)).fetchall()
        return [HistoryEntry(version=r["version"], content_revision=json.loads(r["body"])["content_revision"], event=r["event"], actor=r["actor"], created_at=r["created_at"]) for r in rows]

    def snapshot(self, doc_id, owner, version):
        self.get(doc_id, owner)
        with self.connect() as db:
            row = db.execute("SELECT body FROM history WHERE document_id=? AND version=?", (doc_id, version)).fetchone()
        if not row:
            fail(404, "not_found", "해당 버전이 없습니다.")
        return Document.model_validate_json(row["body"])

    def original(self, doc_id, owner):
        self.get(doc_id, owner)
        with self.connect() as db:
            row = db.execute("SELECT original FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row["original"]:
            fail(404, "no_original_pdf", "텍스트로 등록한 문서입니다.")
        return row["original"]

    def put_asset(self, doc_id, owner, asset_id, body, width, height):
        self.get(doc_id, owner)
        with self.connect() as db:
            db.execute("INSERT INTO assets VALUES(?,?,?,?,?)", (asset_id, doc_id, body, width, height))

    def asset(self, doc_id, asset_id):
        with self.connect() as db:
            row = db.execute("SELECT body FROM assets WHERE document_id=? AND id=?", (doc_id, asset_id)).fetchone()
        if not row:
            fail(404, "asset_not_found", "이 문서의 그림이 아닙니다.")
        return row["body"]

    def put_export(self, owner, doc, result, pdf, token):
        with self.connect() as db:
            # Export computation may overlap another edit. Check and insert in one transaction.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM documents WHERE id=? AND owner=?", (doc.id, owner)).fetchone()
            if not row or row["version"] != doc.version:
                fail(409, "version_conflict", "내보내기 중 편집되었습니다. 다시 검토하세요.")
            public_free = result.model_copy(update={"share_path": None})
            db.execute("INSERT INTO exports VALUES(?,?,?,?,?,?,?,?,0)", (result.id, doc.id, owner, public_free.model_dump_json(), doc.model_dump_json(), pdf, hashlib.sha256(token.encode()).hexdigest() if token else None, result.expires_at))

    def get_export(self, export_id, owner):
        with self.connect() as db:
            row = db.execute("SELECT * FROM exports WHERE id=? AND owner=?", (export_id, owner)).fetchone()
        if not row:
            fail(404, "not_found", "내보낸 자료가 없습니다.")
        return row

    def shared(self, token):
        with self.connect() as db:
            row = db.execute("SELECT * FROM exports WHERE token_hash=? AND revoked=0 AND expires_at>?", (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
        if not row:
            fail(404, "not_found", "만료되었거나 해제된 공유 링크입니다.")
        return row

    def revoke(self, export_id, owner):
        self.get_export(export_id, owner)
        with self.connect() as db:
            db.execute("UPDATE exports SET revoked=1 WHERE id=? AND owner=?", (export_id, owner))
