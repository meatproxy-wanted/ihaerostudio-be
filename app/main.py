"""Serve the current frontend ApiClient contract and the deployment health probe."""
import hashlib
import hmac
import json
import re
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Config
from .models import ErrorResponse
from .providers import Provider
from .store import Store, fail
from .studio_api import register_studio
from .studio_docs import API_DESCRIPTION, install_docs


ANONYMOUS_TOKEN = re.compile(r"[A-Za-z0-9._~-]{16,200}")


class UploadLimitMiddleware:
    """Bound streamed request bodies too, before multipart parsers allocate files."""
    def __init__(self, app, limit=5 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                response = Response(json.dumps({"detail": {"code": "request_too_large", "message": "요청은 최대 5MB입니다."}}), status_code=413, media_type="application/json")
                return await response(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        index = 0
        async def replay():
            nonlocal index
            if index < len(chunks):
                message = chunks[index]
                index += 1
                return message
            return await receive()
        await self.app(scope, replay, send)


def create_app(config: Config | None = None):
    config = config or Config()
    store, provider = Store(config.db_path, config.turso_url, config.turso_token), Provider(config)
    api = FastAPI(title="이해로 스튜디오 API", version="0.3.0", description=API_DESCRIPTION,
        swagger_ui_parameters={"filter": True, "displayRequestDuration": True}, responses={
        401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse, "description": "Content Too Large"},
        502: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    })
    api.state.store, api.state.provider, api.state.config = store, provider, config
    api.add_middleware(UploadLimitMiddleware)
    api.add_middleware(CORSMiddleware, allow_origins=config.cors_origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @api.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/studio/assets/"):
            return response  # pictures are immutable and set their own caching
        response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/api/studio/reader/"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        return response

    bearer = HTTPBearer(auto_error=False, description="제작자 토큰. 익명 모드(기본)에서는 브라우저가 만든 방문자 ID처럼 16자 이상의 아무 토큰이나 자기 작업함이 됩니다.")
    def owner(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials:
            token = credentials.credentials
            for key, maker in config.api_keys.items():
                if hmac.compare_digest(token, key):
                    return maker
            # A public demo without accounts: every well-formed token is its own workspace.
            # The database keeps a hash, so a leaked table cannot be replayed as tokens.
            if config.auth_mode == "anonymous" and ANONYMOUS_TOKEN.fullmatch(token):
                return "anon-" + hashlib.sha256(token.encode()).hexdigest()[:32]
        fail(401, "unauthorized", "유효한 Bearer 토큰이 필요합니다.")

    @api.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "ai_provider": config.provider, "environment": config.environment, "storage": store.kind,
                "studio_scene_mode": config.studio_scene_mode}

    register_studio(api, config, store, provider, owner)
    install_docs(api)
    return api


app = create_app()
