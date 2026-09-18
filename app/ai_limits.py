"""Optional durable admission limits for new external AI calls, not result polling."""
from contextvars import ContextVar
import time

from fastapi import HTTPException


CALL_CONTEXT = ContextVar("studio_ai_call_context", default=None)


class AiCallLimiter:
    def __init__(self, store, config):
        self.store = store
        self.owner_limit = config.ai_calls_per_hour
        self.global_limit = config.ai_calls_global_per_hour
        if self.owner_limit or self.global_limit:
            with store.connect() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS studio_ai_usage (
                    hour INTEGER NOT NULL, scope TEXT NOT NULL, calls INTEGER NOT NULL,
                    PRIMARY KEY(hour, scope)
                )""")

    def consume(self, owner):
        if not (self.owner_limit or self.global_limit):
            return
        now = time.time()
        hour = int(now // 3600)
        scopes = [("owner:" + owner, self.owner_limit), ("global", self.global_limit)]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for scope, limit in scopes:
                if not limit:
                    continue
                row = db.execute("SELECT calls FROM studio_ai_usage WHERE hour=? AND scope=?", (hour, scope)).fetchone()
                if row and row["calls"] >= limit:
                    raise HTTPException(429, detail={"code": "ai_call_limit", "message": "새 AI 호출의 시간당 한도에 도달했어요. 잠시 후 다시 시도해 주세요. 기존 작업 조회와 편집은 계속할 수 있어요."},
                                        headers={"Retry-After": str(max(1, int((hour + 1) * 3600 - now)))})
            for scope, limit in scopes:
                if limit:
                    db.execute("""INSERT INTO studio_ai_usage VALUES(?,?,1)
                        ON CONFLICT(hour,scope) DO UPDATE SET calls=calls+1""", (hour, scope))
            db.execute("DELETE FROM studio_ai_usage WHERE hour<?", (hour - 24,))


def consume_ai_call():
    # Context is attached after HTTP authentication and copied into the thread pool.
    # CLI/offline calls have no HTTP maker identity and are outside this admission policy.
    if context := CALL_CONTEXT.get():
        limiter, owner = context
        limiter.consume(owner)
