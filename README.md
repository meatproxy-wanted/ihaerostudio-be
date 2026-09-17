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

Swagger에는 31개 동작별 호출 시점·요청 방법·저장 영향·다음 동작과 성공/오류 예시가 포함돼 있습니다.
`Schema` 탭에서 각 필드 의미를, `Examples`에서 빈 결과·공개 해제·오류별 응답을 확인할 수 있습니다.
문서 설명은 `app/studio_docs.py`, 필드·응답 스키마는 `app/studio_doc_schemas.py`, 가상 예시는
`app/studio_doc_examples.py`에서 관리하며 실제 엔드포인트나 AI 출력 스키마를 변경하지 않습니다.

인증은 두 모드입니다. 기본값인 익명 모드(`AUTH_MODE=anonymous`)는 16자 이상의 아무 Bearer 토큰이나 받아들여
그 토큰만의 작업함을 만듭니다. 프론트는 브라우저마다 만든 방문자 ID를 보내므로 로그인 없이 각자 체험할 수 있고,
서버는 토큰의 해시만 저장합니다. `AUTH_MODE=keys`로 바꾸면 `API_KEYS`에 등록한 토큰만 통과합니다.
등록한 토큰은 두 모드 모두에서 그 제작자로 인식되고, Swagger의 Authorize에는 토큰 값만 입력합니다.
별도 로그인 화면이나 로그인 API는 없으며, 공개 읽기에는 인증이 필요하지 않습니다.

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
- 그림: 실제 AI 모드에서 `COMFY_CLOUD_API_KEY`를 설정하면 카드의 그림 후보 보기에서 Comfy 그림 1장을 생성합니다. 같은 내용의 카드는 저장된 그림을 재사용합니다. 데모 모드는 업로드한 그림만 반환합니다.
- 검토: 원문 근거가 없는 문장, 원문에 없는 숫자, 45자를 넘는 문장만 규칙으로 점검하며 의미 정확성을 보장하지 않습니다.
- 근거: 실제 AI에는 위치 대신 원문 인용문을 받고 서버가 문단 안에서 찾아 UTF-16 위치로 바꿉니다. 못 찾은 인용은 버리고 구조 항목에 `anchor-unresolved` 표시를 답니다.
- 출력: FE의 `/print` 화면과 브라우저 인쇄를 사용합니다. 백엔드 PDF 출력 API는 없습니다.

AI 키는 서버 환경변수에만 저장합니다. 실제 AI 모드는 키·모델이 없으면 시작하지 않습니다.
AI 요청은 동기 실행이며 실패하면 기존 자료를 보존합니다. 외부 API 오류 원문이나 키는 FE 응답에 포함하지 않습니다.
실제 AI 호출 시 원문·사건 구조·편집 텍스트가 제공자에게 전달됩니다.

## 쉬운 글 작성 지침 (시스템 프롬프트)

실제 AI 모드의 모든 OpenAI 호출은 시스템 메시지 앞부분에 [`app/prompts/easy_read_guidelines.md`](app/prompts/easy_read_guidelines.md)
전문을 붙입니다. 이 파일은 사법정책연구원 『장애인 등을 위한 이해하기 쉬운(Easy-Read) 판결서 작성방안』(2024)에서
AI 변환에 필요한 규칙만 추린 요약본으로, 사건 구조 추출·초안·더 쉽게 바꾸기·문장 나누기·용어 설명·그림 설계에 함께 전달됩니다.
`app/providers.py`의 `system_prompt()`가 역할·안전 규칙 → 지침 전문 → 작업 지시 순서로 조립하므로,
호출마다 달라지는 부분은 맨 뒤의 작업 지시뿐이고 앞부분은 OpenAI 프롬프트 캐시가 재사용합니다.
지침은 GPT 토크나이저 기준 약 1만 7천 토큰이라 호출당 입력 토큰이 그만큼 늘어납니다.

지침 내용을 고칠 때는 FE 저장소의 `docs/references/easy-read-judgment-guidelines.md`(PDF 원본)를 먼저 고치고
같은 내용을 이 파일에 복사합니다. 형식 규칙(쪽수, 글꼴, "끝." 표시, 원문 우선 안내)은 화면이 처리하므로
모델에게는 출력하지 말라고 지시하며, FE 구획(people/decision/reasons/glossary)과의 대응은 `app/studio_provider.py`의
`STUDIO_INSTRUCTIONS`에 있습니다.

## 실제 AI 실행과 그림 생성

`.env.example`을 `.env`로 복사하고 OpenAI 키·모델 및 Comfy Cloud 키를 입력한 뒤 실행합니다.
Python 실행은 `.env`를 자동으로 읽지 않으므로 다음과 같이 직접 작성한 설정을 불러옵니다.

```bash
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8100 --no-access-log
```

FE에서 **카드 → 그림 넣기 → 그림 후보 보기 → 후보 선택 → 이 그림으로 바꾸기**를 사용합니다.
OpenAI가 선택한 카드의 문장과 근거로 영문 장면 설명·한국어 대체텍스트 초안을 만들고,
Comfy Cloud v2가 **FLUX.2 Dev (FP8), 1024×1024, 20 steps**로 그림 1장을 생성합니다.
등장인물 초상과 일반 장면 모두 같은 상위 모델을 사용합니다. 등장인물 그림을 **적용·저장한 뒤**
결론·사건 설명 그림을 요청하면 FLUX.2 Dev가 그 그림들을 실제 레퍼런스로 사용합니다.
기존 4-step 모델보다 생성 시간·GPU 사용량이 늘어날 수 있습니다. 90초 대기 후 진행 중이면
같은 요청으로 이어서 조회하며, 업그레이드 전에 접수한 작업은 저장된 기존 워크플로우로 끝까지 확인합니다.
이미 완료된 구형 모델 후보는 보관하고 다음 신규 후보 요청부터 새 모델을 사용합니다.
실행 전 Cloud 모델·노드 가용성을 확인하며 지원되지 않으면 `comfy_workflow_unavailable`을 반환합니다.
사용 모델은 `flux2_dev_fp8mixed.safetensors`, `mistral_3_small_flux2_bf16.safetensors`,
`full_encoder_small_decoder.safetensors`입니다.
워크플로우는 [Comfy 공식 FLUX.2 Dev 가이드](https://docs.comfy.org/tutorials/flux/flux-2-dev)와
공식 템플릿의 일반 Dev 경로(터보 LoRA 제외)를 기준으로 구성했습니다.
`role=person` 카드의 `partyId`와 `imageId`를 기준으로 최대 6명의 PNG/JPEG/WebP 그림을 자동 연결합니다.
이름·역할·등장인물 카드 설명과 대상 문장의 근거도 장면 설계에 전달하여 인물과 관계의 방향을 구분합니다.
아직 적용하지 않은 후보는 기준으로 쓰지 않습니다. 기준 그림이 없으면 기존 텍스트 기반 생성을 사용합니다.
초안 생성에도 동일한 partyId·이름·역할을 유지하고 등장인물별 person 카드를 작성하도록 지시합니다.
레퍼런스 입력은 외형 유지에 도움을 주지만 완전한 일치를 보장하지 않으므로 결과를 확인해야 합니다.
등장인물 그림은 얼굴이 크게 보이는 정면·앞쪽 3/4 상반신 초상을 기본으로 지시합니다.
다른 장면도 사람이 있다면 얼굴이 보이는 구도를 지시하며, 익명이라는 이유로 얼굴을 숨기지 않습니다.
기준 그림은 OpenAI 이미지 입력으로 읽어 얼굴·머리·수염 유무·상의·하의·신발을 고정 외형으로 저장합니다. 같은 소유자·프로젝트·자산의 동일 픽셀은 분석 결과를 재사용합니다. 생성 결과를 외형 검사로 차단하거나 자동 보정하지 않고 후보로 반환합니다. 이전 외형 검사에서 제외된 작업도 같은 요청으로 기존 결과를 다시 조회합니다. 한 사람을 특정할 수 없는 기준 그림은 422 character_reference_unclear입니다. 외형 유지 지시가 완벽한 일치를 보장하지 않으므로 후보를 직접 확인합니다.
이미지 입력을 지원하는 OPENAI_MODEL이 필요합니다. 최초 기준 이미지 분석에 API 사용량·시간이 추가됩니다.
기준이 없는 생성과 등장인물 카드 자체 재생성에는 이 외형 검사가 적용되지 않습니다. 기존 그림은 자동 교체되지 않습니다.
이전 구도 결과를 재사용하지 않도록 생성 버전을 갱신했으므로 저장 후 새로고침해 그림 후보를 다시 요청하고 선택하세요.
API 경로나 FE 응답 형식은 추가하지 않습니다. 생성된 그림은 선택하기 전까지 문서에 자동 적용되지 않습니다.

작업은 `studio_image_jobs`에 저장합니다. 진행 중 요청·서버 재시작·완료 후 조회는 같은 작업을 재사용합니다.
90초간 완료를 기다려도 끝나지 않으면 `503 image_in_progress`를 반환하며, FE의 다시 시도로 이어서 확인합니다.
Comfy 접수 결과가 불확실하면 자동 재접수하지 않습니다. 관리자가 DB 작업 ID와 Comfy 작업 목록/로그를 대조해야 합니다.
완료 실패·만료도 자동 재생성하지 않습니다. 카드 내용이 바뀌면 다음 후보 요청은 새 생성 요청입니다.
등장인물 설명·역할·기준 그림이 바뀌어도 결론·사건 설명의 생성 캐시를 갱신합니다.
이미 적용한 그림은 자동 교체하지 않습니다. 저장 후 다시 그림 후보를 요청하고 새 결과를 선택하세요.
FE가 이전 후보를 캐시하고 있으면 새로고침 후 요청해야 합니다. 생성 중 기준이 바뀌면 409로 오래된 결과 저장을 막습니다.
SVG 기준 그림·동일 인물의 서로 다른 기준 그림·7개 이상의 기준 그림은 422로 안내하며 임의로 무시하지 않습니다.
참조 픽셀은 현재 자료·사용자의 저장 자산에서만 읽습니다. 문서의 임의 URL을 다운로드하지 않습니다.
Comfy 입력 업로드와 계획도 작업에 저장하여 재시도 시 재사용하고, 만료된 업로드는 미접수 작업에서만 갱신합니다.

결과 파일은 인증된 Comfy 자산 메타데이터의 URL에서 내려받고 PNG로 정제해 프로젝트에 보관합니다.
허용 호스트 기본값은 `cloud.comfy.org,storage.googleapis.com`이며 서명 URL에 API 키를 전달하지 않습니다.
프론트에는 만료 URL 대신 서버가 보관한 그림의 주소(`/api/studio/assets/{id}`)를 반환합니다. 그림 내용과 대체텍스트는 제작자가 대조해야 합니다.

## 저장과 게시

기본은 SQLite 파일이고 `DATABASE_PATH` 기본값은 `data/studio.sqlite3`입니다. 디스크가 유지되지 않는 호스팅(Vercel)에서는
`TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`을 넣으면 같은 SQL을 Turso(SQL over HTTP)로 보냅니다. 두 경우 모두 `/health`의 `storage`로 확인합니다.
그림 파일은 자료 JSON이 아니라 별도 행(`studio_assets`)에 두고 인증 없는 `/api/studio/assets/{id}`로 내주며, 그 주소는
`PUBLIC_BASE_URL`(Vercel에서는 프로젝트 주소가 기본)로 만듭니다. 배포 절차는 [Vercel + Turso 매뉴얼](docs/DEPLOY-VERCEL-TURSO.md)을 봅니다.
작성자별 접근을 분리하고, `saveRevision` 또는 구조의 `revision`으로 동시 수정 충돌을 검사합니다.
내용 버전과 초안의 기준 버전은 서버가 결정합니다.

게시본은 고정 스냅샷입니다. 공개는 검토 완료 게시본만 가능하며, 공개 응답에는 원문 근거·검토 메모를 넣지 않습니다.
공개 해제나 자료 삭제 시 읽기 API는 `unavailable`을 반환합니다. 삭제는 soft delete입니다.

PDF 입력은 4.5MB(본문 4.5MB를 넘지 못하는 호스팅에 맞춘 값), 그림 업로드는 2MB입니다. 암호 PDF·스캔본·손상 파일·위험한 SVG는 오류로 반환합니다.
PDF 원본은 저장하지 않습니다. 추출한 텍스트와 파일 이름·크기만 남기며, 판결문의 개인정보가 서버에 파일로 쌓이지 않습니다.
그림은 PNG/JPEG/WebP와 제한된 SVG를 지원합니다.

## 검증 및 배포

```bash
STUDIO_CONTRACT_OUT=../studio-contract.json .venv/bin/python -m pytest -q
node scripts/verify_frontend_contract.mjs ../ihaerostudio-fe ../studio-contract.json
PYTHONPATH=. .venv/bin/python scripts/export_openapi.py
```

테스트는 현재 FE 계약의 생성·편집·검토·게시·공개 해제, 소유권, 저장 충돌, 원문 근거, 업로드 오류,
서버 재시작 후 보존, AI 응답 형식·실패 처리, 그림 생성·중복 요청·서명 URL 보안·동시 편집 보존, 불필요한 API의 실제 제거를 확인합니다.
외부 AI 응답은 테스트에서 대체하며 유료 호출을 실행하지 않습니다.

운영 배포는 [Docker Compose·Caddy HTTPS 가이드](docs/FRONTEND.md#서버-배포)를 따릅니다.
