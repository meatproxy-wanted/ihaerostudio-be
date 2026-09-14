"""Serve the current frontend ApiClient contract and the deployment health probe."""
import hmac
import json
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


class UploadLimitMiddleware:
    """Bound streamed request bodies too, before multipart parsers allocate files."""
    def __init__(self, app, limit=22 * 1024 * 1024):
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
                response = Response(json.dumps({"detail": {"code": "request_too_large", "message": "요청은 최대 22MB입니다."}}), status_code=413, media_type="application/json")
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
    store, provider = Store(config.db_path), Provider(config)
    api = FastAPI(title="이해로 스튜디오 API", version="0.3.0", description="""
현재 프론트엔드의 ApiClient에 대응하는 API입니다. 모든 업무 경로는 `/api/studio`를 사용합니다.

텍스트/PDF 업로드·분석 → 사건 구조 확인 → 초안 생성·편집 → 검토 → 게시본·공개 읽기.
초안 생성은 `{document, project}`를 반환합니다. 저장 시 `saveRevision` 또는 구조의 `revision`을 전달합니다.

Authorize에는 백엔드의 작성자 Bearer 토큰을 입력합니다. 로컬 기본값은 `dev-only-change-me`입니다.
공개 읽기(`/reader/{project_id}`)에는 인증이 필요하지 않습니다. 별도 로그인 API는 없습니다.

`AI_PROVIDER=demo`에서는 실제 입력 원문을 복사해 응답합니다. 실제 분석·쉬운 글 생성은 서버의 AI 설정이 필요합니다.
그림 후보는 업로드한 그림이며 자동 그림 생성은 아직 연결되지 않았습니다. PDF 출력은 프론트의 인쇄 화면을 사용합니다.
""", responses={
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
        response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/api/studio/reader/"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        return response

    bearer = HTTPBearer(auto_error=False, description="제작자 API 토큰. 개발 환경: dev-only-change-me")
    def owner(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials:
            for token, maker in config.api_keys.items():
                if hmac.compare_digest(credentials.credentials, token):
                    return maker
        fail(401, "unauthorized", "유효한 Bearer 토큰이 필요합니다.")

    @api.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "ai_provider": config.provider, "environment": config.environment}

    register_studio(api, config, store, provider, owner)
    return api


app = create_app()
