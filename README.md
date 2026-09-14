# ihaerostudio-be · 이해로 스튜디오 백엔드

현재 프론트엔드의 `ApiClient`에 필요한 `/api/studio` API를 제공합니다.
텍스트/PDF 입력 → 사건 구조 → 쉬운 설명자료 편집 → 검토 → 게시·공개 읽기를 연결합니다.
API 목록과 요청 방식은 [FE 연동 가이드](docs/FRONTEND.md)와 실행 중인 Swagger에서 확인합니다.

기존 `/api/v1` 문서 API, 별도 영상·그림 작업 API, `/share` 경로는 제거했습니다.
배포 상태 확인용 `/health`는 유지하며 Swagger에는 FE 연동 API만 표시합니다.
기존 DB 자료는 삭제하지 않습니다. FE 자료는 `studio_projects` 테이블을 사용합니다.

## 로컬 실행

Python 3.11 이상에서 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
AI_PROVIDER=demo CORS_ORIGINS=http://127.0.0.1:3100,http://localhost:3100 \
  uvicorn app.main:app --host 127.0.0.1 --port 8100 --no-access-log
```

- Swagger: [http://127.0.0.1:8100/docs](http://127.0.0.1:8100/docs)
- OpenAPI: [http://127.0.0.1:8100/openapi.json](http://127.0.0.1:8100/openapi.json)
- 상태 확인: [http://127.0.0.1:8100/health](http://127.0.0.1:8100/health)

Swagger의 Authorize에는 로컬 개발 토큰 `dev-only-change-me`를 입력합니다.
이 토큰은 백엔드 작성자 식별용이며 별도 로그인 화면이나 로그인 API는 없습니다.
공개 읽기에는 인증이 필요하지 않습니다. 운영 토큰은 `API_KEYS`로 설정합니다.

FE 원격 저장소에는 변경을 올리지 않습니다. 로컬 FE의 API 구현만 HTTP로 연결해 테스트하거나,
[테스트 복사본 준비 스크립트](scripts/prepare_frontend_test.py)를 사용할 수 있습니다.
FE의 `createMockApi()`를 그대로 사용하면 BE를 실행해도 해당 화면은 서버를 호출하지 않습니다.

## 생성 응답과 현재 동작

판결문 생성 요청은 원문을 분석·저장한 후 `project`를 반환합니다. 원문과 사건 구조는 별도 GET으로 조회합니다.
`POST /api/studio/projects/{id}/document/generate`는 생성이 끝나면 `{document, project}`를 반환합니다.
`document`에는 제목, 구획별 카드·문장, 원문 근거, 용어 풀이, 이미지 목록이 들어갑니다.
프론트는 이 결과를 기존 편집 화면에 표시하고 전체 문서를 자동 저장합니다.

- `AI_PROVIDER=demo`: 입력 원문을 복사하고 명시된 표제를 단순 분류합니다. 실제 쉬운 글 생성이 아닙니다.
- 실제 분석·쉬운 글·용어 생성: BE 환경에 `AI_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL`을 설정해야 합니다.
- 더 쉽게 바꾸기·용어 후보·용어 설명: 데모에서는 `503 ai_not_configured`를 반환합니다.
- 그림: 자료에 업로드한 그림을 후보로 반환합니다. 자동 그림 생성은 아직 연결하지 않았습니다.
- 검토: 원문 근거·수치·대조 여부 등을 규칙으로 점검하며 의미 정확성을 보장하지 않습니다.
- 출력: FE의 `/print` 화면과 브라우저 인쇄를 사용합니다. 백엔드 PDF 출력 API는 없습니다.

AI 키는 서버 환경변수에만 저장합니다. 실제 AI 모드는 키·모델이 없으면 시작하지 않습니다.
AI 요청은 동기 실행이며 실패하면 기존 자료를 보존합니다. 외부 API 오류 원문이나 키는 FE 응답에 포함하지 않습니다.
실제 AI 호출 시 원문·사건 구조·편집 텍스트가 제공자에게 전달됩니다.

## 저장과 게시

SQLite를 사용합니다. `DATABASE_PATH` 기본값은 `data/studio.sqlite3`입니다.
작성자별 접근을 분리하고, `saveRevision` 또는 구조의 `revision`으로 동시 수정 충돌을 검사합니다.
내용 버전과 초안의 기준 버전은 서버가 결정합니다.

게시본은 고정 스냅샷입니다. 공개는 검토 완료 게시본만 가능하며, 공개 응답에는 원문 근거·검토 메모를 넣지 않습니다.
공개 해제나 자료 삭제 시 읽기 API는 `unavailable`을 반환합니다. 삭제는 soft delete입니다.

PDF 입력은 20MB, 그림 업로드는 2MB입니다. 암호 PDF·스캔본·손상 파일·위험한 SVG는 오류로 반환합니다.
그림은 PNG/JPEG/WebP와 제한된 SVG를 지원합니다.

## 검증 및 배포

```bash
STUDIO_CONTRACT_OUT=../studio-contract.json .venv/bin/python -m pytest -q
node scripts/verify_frontend_contract.mjs ../ihaerostudio-fe ../studio-contract.json
PYTHONPATH=. .venv/bin/python scripts/export_openapi.py
```

테스트는 현재 FE 계약의 생성·편집·검토·게시·공개 해제, 소유권, 저장 충돌, 원문 근거, 업로드 오류,
서버 재시작 후 보존, AI 응답 형식·실패 처리, 불필요한 API의 실제 제거를 확인합니다.
외부 AI 응답은 테스트에서 대체하며 유료 호출을 실행하지 않습니다.

운영 배포는 [Docker Compose·Caddy HTTPS 가이드](docs/FRONTEND.md#서버-배포)를 따릅니다.
