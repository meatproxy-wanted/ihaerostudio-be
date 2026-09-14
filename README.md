# ihaerostudio-be · 이해로 스튜디오 FastAPI 백엔드

판결문을 쉬운 글로 **한 번 변환하는 API**가 아니라, 원문 대조 → 편집 → 제작자 검토 → 고정 버전 출력까지 연결하는 실행 가능한 MVP입니다. 첨부 시안의 6단계에 대응합니다. 제작자용 프런트엔드는 포함하지 않으며, 독자 HTML 화면과 Swagger UI는 포함합니다.

## 빠른 실행

Python 3.11 이상을 사용합니다. 아래 명령은 이 README가 있는 프로젝트 폴더에서 실행하세요.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

- Swagger: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- OpenAPI JSON: http://127.0.0.1:8000/openapi.json
- 저장한 API 계약: `docs/openapi.json`

Swagger 오른쪽 **Authorize**에 `dev-only-change-me`를 입력합니다. `Bearer ` 접두사는 Swagger가 붙입니다. 이 토큰은 로컬 개발용입니다.

PDF에는 한글 TTF 글꼴이 필요합니다. macOS에서는 설치된 나눔고딕코딩 또는 Arial Unicode를 자동으로 탐색합니다. 없으면 다음처럼 지정하세요. Docker에는 나눔고딕이 포함됩니다.

```bash
export PDF_FONT_PATH='/absolute/path/to/NanumGothic.ttf'
```

글꼴이 없을 때 문서가 깨진 PDF를 만들지 않고 `503 pdf_font_missing`을 반환합니다.

## 즉시 실행할 수 있는 예제

별도 터미널에서 같은 가상환경을 활성화한 뒤:

```bash
python scripts/demo.py --base-url http://127.0.0.1:8000 --output data/demo.pdf
```

가상 판결 예제로 업로드, 사건 구조 확인, 초안 생성, 수정안 적용, 검토, PDF 출력, 공유 화면을 순서대로 호출합니다. `examples/judgment.json`은 **실제 판결이 아닌 테스트 자료**입니다. 이 스크립트의 자동 확인 절차를 실제 판결의 제작자 검토에 사용하지 마세요.

Swagger에서 직접 시작하려면 `POST /api/v1/documents/text`에 해당 JSON을 붙여 넣습니다. 이후 각 변경 요청의 `expected_version`에 **바로 직전 응답의 `version`**을 넣으세요.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/documents/text \
  -H 'Authorization: Bearer dev-only-change-me' \
  -H 'Content-Type: application/json' \
  --data-binary @examples/judgment.json
```

## 구현 범위

| 화면 | 제공 기능 |
| --- | --- |
| ① 업로드 | PDF/텍스트, 독자·성인 존중 문체·호칭·그림 설정, 원본 보관, 페이지·문단 추출 |
| ② 사건 구조 | 당사자, 당사자별 주장, 법원의 판단·결정, 원문 근거, 직접 수정·확인 |
| ③ 편집 | 카드 추가/삭제/순서 변경/직접 편집, 근거 위치 조회, AI 제안 before/after, 적용/거절, 문장 나누기, 용어 설명, 이미지 업로드·교체 |
| ④ 검토 | 금액, 분류, 지급 명령/지급 완료, 용어, 긴 문장, 원문 누락·그림 확인 항목, 제작자 확인 메모·최종 승인 |
| ⑤ 미리보기 | 독자용 JSON/HTML, A4 한글 PDF, 내부 근거·검토 정보 숨김 |
| ⑥ 내보내기 | 검토 완료 버전 PDF·HTML 스냅샷, 선택적 공유 링크, 만료·공유 해제, 설명자료 고지 |

화면 흐름별 상세 API 사용법은 `docs/API.md`를 참고하세요.

## 실제 AI 연결: Ollama

기본 `AI_PROVIDER=demo`는 **실제 LLM이 아닙니다**. 명시적 `원고:`, `피고:`, `원고 주장:`, `피고 주장:`, `법원 판단:`, `법원 결정:` 표제만 분류하고 본문을 복사합니다. 임의 판결문은 `unknown`으로 남으므로 제작자가 구조를 수정해야 합니다. 데모는 쉬운 표현·용어 의미를 만들어내지 않으며, 해당 AI 작업은 `503 ai_not_configured`를 반환합니다.

로컬 Ollama를 실행하고 구조화 JSON 출력과 한국어를 지원하는 모델을 준비한 뒤 서버를 다음 환경으로 재시작하세요. 아래 모델 값은 설치한 모델의 정확한 이름으로 교체합니다.

```bash
export AI_PROVIDER=ollama
export OLLAMA_URL=http://127.0.0.1:11434
export OLLAMA_MODEL='your-installed-model'
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

연동 구현은 Ollama `/api/chat`의 JSON Schema `format`을 사용합니다. 사건 구조 추출, 쉬운 초안 작성, 더 쉽게 바꾸기, 문장 나누기, 용어 설명 추가를 지원합니다. 문서의 내용을 지시로 실행하지 않도록 시스템 프롬프트에서 구분하고, 결과 스키마와 원문 인용·오프셋을 서버에서 다시 검증합니다. **근거 인용이 일치해도 해석의 정확성이 보장되는 것은 아닙니다.**

로컬 주소를 사용하면 원문은 설정된 로컬 Ollama로 전달됩니다. `OLLAMA_URL`을 외부 서버로 변경하면 원문이 그 서버로 전송됩니다. 본 구현에는 자동 웹 검색, AI 도구 실행, 이미지 생성·그림 픽셀 의미 판독이 없습니다. 그림은 PNG/JPEG/WebP 업로드 후 교체합니다.

AI 작업은 제한된 길이의 문서에 대한 **동기 요청(최대 120초)**입니다. 대규모 운영에는 별도 작업 큐·워커·문서 분할 처리가 필요합니다. 긴 문서를 모델 문맥에 충분히 담을 수 있는지 운영자가 확인해야 합니다. 입력 상한을 넘으면 원문을 몰래 잘라 쓰지 않고 거부합니다.

## 버전과 검토 규칙

- `version`: 상태·제안·확인 메모를 포함한 저장마다 증가. 충돌 검사에 사용합니다.
- `content_revision`: 내용 또는 제작 설정이 변경될 때 증가. 검토 대상 버전입니다.
- 본문 편집, 그림 변경, 카드 삭제·순서 변경, 구조·설정 수정은 기존 검토를 해제합니다.
- AI 제안 생성은 본문을 바꾸지 않습니다. 내용 버전이 바뀌면 이전 제안은 적용할 수 없습니다.
- 사건 구조 확인 없이는 초안을 생성할 수 없습니다. `unknown` 분류는 확인 전에 해결해야 합니다.
- 모든 검토 항목을 제작자가 확인하고 승인해야 내보내기가 가능합니다. 경고 수용 사유가 이력에 남습니다.
- 내보내기는 고정 스냅샷입니다. 이후 편집은 기존 PDF·공유 화면에 반영되지 않습니다. 수정본은 다시 검토 후 새로 내보내세요.
- 공개 공유는 기본 비활성입니다. `share=true`로 생성한 링크의 소지자는 승인된 설명자료를 읽을 수 있습니다. 원문 PDF와 근거 인용, 편집 기록은 공개하지 않습니다.
- **호칭 설정은 자동 익명화 보장이 아닙니다.** 데모 본문은 원문을 복사합니다. 본문·그림·용어에 남은 개인정보는 공유 전에 제작자가 확인해야 합니다.

## 저장·인증

SQLite WAL을 사용하며 문서, 업로드 원본, 정제된 이미지, 이력, 내보낸 PDF와 스냅샷을 저장합니다. 기본 파일은 `data/studio.sqlite3`입니다. 쿼리는 매개변수 바인딩을 사용하며 변경 시 SQL 조건으로 버전과 작성자를 함께 검사합니다.

Bearer 토큰별 제작자 ID를 환경변수로 설정합니다. 같은 제작자 ID를 가진 토큰은 같은 문서를 접근합니다. 회원가입·로그인·조직 협업 기능은 별도 범위입니다.

```bash
export APP_ENV=production
export API_KEYS='{"a-random-secret-at-least-32-characters":"maker-01"}'
```

운영 환경에서는 실제 랜덤 토큰을 사용하세요. `python -c 'import secrets; print(secrets.token_urlsafe(32))'`로 생성할 수 있습니다. `production`은 기본 개발 토큰과 짧은 토큰으로 시작하지 않습니다.

업로드 제한: PDF 20MB·100쪽·추출 텍스트 15만 자, 문단/카드 300개, 이미지 5MB·1,600만 화소(최대 1600px로 정제), 요청 본문 22MB. 암호 PDF, 텍스트 없는 스캔 PDF, 유효하지 않은 그림은 명시적으로 거부합니다. PDF 원문 강조는 추출 텍스트의 문자 위치 기준이며 **원본 PDF 좌표 bbox는 제공하지 않습니다**.

## Docker

```bash
cp .env.example .env
# .env에서 API_KEYS를 실제 랜덤 토큰으로 변경
docker compose up --build
```

접속 주소는 동일하게 http://127.0.0.1:8000/docs 입니다. Docker Compose는 호스트의 localhost에만 포트를 노출합니다. Ollama 사용 시 `OLLAMA_URL=http://host.docker.internal:11434`를 사용합니다.

`APP_ENV=production` 플래그는 인증 설정 검사를 강화할 뿐, 이 MVP가 외부 공개 운영 준비를 완료했다는 의미는 아닙니다. 외부 공개 전 TLS·실제 사용자 인증/토큰 관리·요청 속도 제한·업로드 악성 파일 검사와 격리된 PDF 처리·저장 암호화/보관 및 삭제 정책·백업을 적용하세요. SQLite 이력은 감사 확인용이며 변조 방지 저장소는 아닙니다. 공유 해제는 이미 다운로드한 파일을 회수하지 않습니다.

## 검증 및 문서 재생성

```bash
pytest -q
PYTHONPATH=. python scripts/export_openapi.py
```

테스트는 원문 근거, 작성자 격리, 수정 승인, 동시 편집 충돌, 검토 게이트, PDF 텍스트, 공유 만료·해제, XSS 이스케이프, 업로드 오류와 Ollama JSON 계약을 검증합니다. 실제 로컬 AI 모델은 설치되어 있지 않아 라이브 추론 품질은 검증하지 않았습니다. Docker 빌드는 제공하지만 현재 Mac에서 Docker 실행 검증은 하지 않았습니다.

`requirements.lock`은 이번 검증 환경의 정확한 버전입니다. `pyproject.toml`은 지원 버전 범위를 제공합니다.

## 파일 구조

```text
app/
  main.py       FastAPI 라우트, 인증, 업로드 상한, Swagger 메타데이터
  models.py     요청·응답 스키마
  config.py     환경설정
  store.py      SQLite 저장·버전 충돌·출력 스냅샷
  sources.py    PDF 텍스트·이미지 정제
  providers.py  데모와 Ollama 연동
  domain.py     근거 검증·검토 규칙·독자용 데이터
  render.py     한글 PDF·독자 HTML
docs/           OpenAPI JSON과 화면별 API 가이드
examples/       가상 판결 입력
scripts/        전체 흐름 데모·OpenAPI 내보내기
tests/          통합·도메인 테스트
```

설계에 참고한 공식 문서: [FastAPI 파일 업로드](https://fastapi.tiangolo.com/tutorial/request-files/), [FastAPI OpenAPI](https://fastapi.tiangolo.com/tutorial/first-steps/), [Ollama 구조화 출력](https://docs.ollama.com/capabilities/structured-outputs), [pypdf 텍스트 추출/OCR 한계](https://pypdf.readthedocs.io/en/5.7.0/user/extract-text.html).
