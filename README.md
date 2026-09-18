# ihaerostudio-be · 이해로 스튜디오 백엔드

판결문을 쉬운 글과 그림으로 설명하는 자료를 만드는 FastAPI 서버입니다.
범용 LLM보다 초안을 잘 쓴다는 주장보다, **원문 대조 → 글·그림 편집 → 제작자 검토 → 게시·읽기·인쇄**를 연결하는 것이 목적입니다.
최종 결과는 공식 판결문을 대신하지 않습니다.

## 문서 안내

- [프로젝트 요약·화면 기능 매핑·혼동 주의사항](docs/PROJECT_OVERVIEW.md)
- [프론트 연동·API 계약·서버 배포](docs/FRONTEND.md)
- [캐릭터·장면 생성 및 구형 호환 경로](docs/IMAGE_GENERATION.md)
- [Vercel + Turso 배포](docs/DEPLOY-VERCEL-TURSO.md)

현재 제공하는 업무 API는 `/api/studio`입니다. 기존 `/api/v1`, 별도 영상 작업 API, `/share`는 제공하지 않습니다.
영상·PDF 관련 구형 파일이 저장소에 남아 있어도 현재 앱에 등록된 엔드포인트가 아닙니다.

## 현재 제작 흐름

1. PDF 또는 텍스트를 올리면 사건 구조를 분석·저장합니다.
2. 제작자가 원문과 당사자·주장·법원 판단·결정을 대조하고 수정합니다.
3. `POST /projects/{id}/document/generate`가 쉬운 **글** 초안을 저장합니다. 이 요청은 그림을 만들지 않습니다.
4. 편집 진입 시 `POST /projects/{id}/document/prepare-images`를 완료까지 이어 호출합니다.
   기본 모드에서 GPT가 고정 캐릭터 10종 중 인물별로 선택하고 얼굴 크롭을 저장한 다음,
   같은 캐릭터의 한 명짜리 기본 포즈 원본을 레퍼런스로 Qwen 장면을 생성·자동 적용합니다.
5. 인물 그림은 고정하고 글·장면 그림·대체텍스트는 편집합니다.
6. 규칙 점검과 제작자 최종 확인 후 고정 게시본을 만들고, 읽기 화면 공개 또는 FE 인쇄를 사용합니다.

“한 번에 생성”은 편집 진입 시 자동 준비한다는 뜻입니다.
기본 `STUDIO_SCENE_MODE=single`은 요청당 최대 카드 하나를 처리하고 진행 상태를 DB에 보관합니다.
옵트인 `storyboard4` 실험은 같은 인물 레퍼런스로 1024×1024의 2×2 시트를 생성하고
512×512 컷 최대 4개를 잘라 해당 카드에 함께 적용합니다. [실험·실생성 테스트·롤백](docs/STORYBOARD_EXPERIMENT.md)을 참고하세요.

글과 그림 생성은 [이지리드 적용 규칙](docs/EASY_READ.md)을 사용합니다. 한 문장·한 컷의 핵심 뜻을 좁히고,
구체적인 단어 아이콘을 활용하며, 검토 API에서 이중부정·모호한 참조·가까운 용어 풀이 등의 권고를 제공합니다.
기본값과 수동 그림 후보 API는 그대로 유지합니다.
현재 Git 연동 Vercel 배포에서는 `vercel.json`의 비밀이 아닌 환경 플래그로 `storyboard4`를 켭니다.
배포 후 `/health`의 `studio_scene_mode`로 확인하고, 롤백은 JSON의 값을 `single`로 바꿔 다시 푸시합니다.

## 로컬 실행

Python 3.11 이상을 사용합니다.

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

실제 AI는 `.env.example`을 `.env`로 복사하고 서버 전용 키·모델을 넣어 실행합니다.
Python 실행은 `.env`를 자동으로 읽지 않습니다.

```bash
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8100 --no-access-log
```

| 설정 | 동작 |
| --- | --- |
| `AI_PROVIDER=demo` | 원문 복사·명시된 표제 분류. 실제 쉬운 글·캐릭터 선택·장면 생성이 아닙니다. |
| `AI_PROVIDER=openai` | `OPENAI_API_KEY`, `OPENAI_MODEL`이 필요합니다. 분석·글·문장 보조·캐릭터 선택은 GPT API를 사용합니다. |
| `STUDIO_CHARACTER_MODE=library` (기본) | 고정 캐릭터 10종에서 선택. 얼굴 자산 제작에는 Comfy 호출이 없습니다. |
| `COMFY_CLOUD_API_KEY` | Qwen 장면 생성에 필요합니다. OpenAI 모델은 장면 레퍼런스의 이미지 입력 분석도 지원해야 합니다. |
| `STUDIO_CHARACTER_MODE=generate` | 새 자료의 초상을 Qwen으로 생성하는 호환 모드. 이미 배정·고정한 인물은 바꾸지 않습니다. |
| `STUDIO_SCENE_MODE=single` (기본) / `storyboard4` (실험) | 카드별 장면 / 고해상도 네 컷 생성 후 크롭. 이미 예약된 묶음은 설정 롤백 뒤에도 같은 작업으로 이어 확인합니다. |

현재 FE의 `lib/api/client.ts`는 HTTP 클라이언트입니다.
`NEXT_PUBLIC_STUDIO_API_URL`로 BE 주소를 지정하고, 빌드 시 환경변수를 바꾼 경우 FE를 다시 빌드합니다.
구형 mock/IndexedDB 자료는 서버 자료로 자동 이전하지 않습니다.

## 검토와 출력의 경계

자동 검토는 AI 의미 판정이 아니라 원문 근거·숫자 점검과 이지리드 표면 점검입니다.

- 원문 근거 없는 문장: required
- 원문 전체에 없는 숫자: suggested
- 60자를 넘거나 한 항목에 여러 문장이 있는 경우: suggested
- 일부 이중부정·카드 내 용어 풀이 누락: suggested
- 모호한 대명사·앞의 내용 참조: suggested

표면 점검은 정규식과 용어집 풀이의 문자 일치 기반이며 올바른 다른 표현에도 경고할 수 있습니다.
문장을 자동 수정하지 않으며 이지리드 준수나 독자의 이해를 보장하지 않습니다.

숫자가 원문에 있다고 금액·단위·인물 관계가 맞다는 뜻은 아닙니다.
주장과 판단의 구분, 돈을 지급하라는 명령과 실제 지급 완료의 구분, 글과 그림의 의미는 제작자가 확인합니다.
인물 레퍼런스와 화풍 지시는 완벽한 외형·상황 일치를 보장하지 않습니다.
장면 프롬프트의 인물 외형 지시는 `Use the reference images for character appearance.` 한 문장뿐입니다.
외형 분석·얼굴/머리/의상/수염 텍스트 프로필은 장면 요청에 넣지 않습니다.
새 자료는 흰 배경의 단순 삽화 10종(`stock-characters-v2`)을 사용하고, 이미 배정된 v1 자료는 원래 캐릭터셋을 유지합니다.

PDF 저장은 FE의 `/print` 화면과 브라우저 인쇄를 사용합니다.
백엔드 PDF 다운로드 API, 영상 생성 API, 카드뉴스 PNG 내보내기 API는 현재 제공하지 않습니다.

## 저장·인증·개인정보

기본은 SQLite(`DATABASE_PATH=data/studio.sqlite3`)이고, `TURSO_DATABASE_URL`과 `TURSO_AUTH_TOKEN`을 설정하면 Turso를 사용합니다.
Vercel처럼 로컬 디스크가 영속적이지 않은 배포에서는 원격 DB가 필요합니다.
`/health`의 `storage`는 연결한 저장소 종류이며 AI 키 유효성·생성 품질·배포 최신 여부 검사는 아닙니다.

기본 `AUTH_MODE=anonymous`에서는 16~200자의 허용된 Bearer 토큰마다 작업함이 나뉩니다.
이는 로그인·신원 확인이 아닙니다. `AUTH_MODE=keys`는 등록된 `API_KEYS`만 허용합니다.
현재 FE의 `NEXT_PUBLIC_STUDIO_API_TOKEN`은 브라우저에 노출되므로 비밀 관리자 키를 넣지 않습니다.
독자·그림 파일 조회는 인증이 없고 나머지 업무 API는 제작자 토큰이 필요합니다.

새 PDF 업로드의 원본 파일은 저장하지 않지만 **추출한 판결문 텍스트와 파일 메타데이터는 DB에 남습니다**.
예전 버전에서 저장한 원본을 자동 삭제하지는 않습니다.
실제 AI 사용 시 관련 텍스트와 레퍼런스 픽셀이 OpenAI로, 장면 설명과 레퍼런스 이미지가 Comfy로 전달됩니다.
원문의 개인정보는 업로드 전에 정리해야 합니다.

그림은 `studio_assets`에 별도로 저장하고 `/api/studio/assets/{id}`로 제공합니다.
이 주소를 아는 사람은 미공개 자료의 그림도 조회할 수 있습니다.
게시본 공개 해제는 독자 문서 조회를 막지만 **그림 URL을 폐기하지는 않습니다**.
자료 삭제는 soft delete이며 물리 삭제·보관기간 정리·이미지 URL 회수 기능은 없습니다.
`PUBLIC_BASE_URL`은 그림 URL에 저장되므로 자료 생성 전에 안정적인 BE 공개 주소로 정합니다.

제한: PDF 4,500,000바이트·100쪽, 그림 업로드 2MiB, 자료당 그림 100개·합계 12MiB,
자동 준비의 기준 인물 6명·장면 레퍼런스 3명입니다. 10종 캐릭터셋은 동시 인물 상한이 아닙니다.

## 쉬운 글 작성 지침

텍스트 기반 GPT 호출에는 `app/prompts/easy_read_guidelines.md`를 시스템 프롬프트 앞부분에 붙입니다.
파일은 사법정책연구원 Easy-Read 판결서 작성방안(2024)의 프로젝트용 요약입니다.
이미지 픽셀 분석 호출에는 이 전문 대신 별도의 관찰 지시를 사용합니다.
고정 프롬프트 앞부분은 재사용되지만 실제 캐시 적용·토큰 수·비용은 모델과 요청에 따라 달라집니다.

BE의 해당 파일이 서버 지침 원본입니다. FE의 `docs/references/easy-read-judgment-guidelines.md`는 참고 자료입니다.
지침 수정 시 둘의 목적과 차이를 확인하고, JSON 출력 계약과 원문 근거 규칙을 유지합니다.

## 검증 및 배포

```bash
STUDIO_CONTRACT_OUT=../studio-contract.json .venv/bin/python -m pytest -q
node scripts/verify_frontend_contract.mjs ../ihaerostudio-fe ../studio-contract.json
PYTHONPATH=. .venv/bin/python scripts/export_openapi.py
PYTHONPATH=. .venv/bin/python scripts/preview_character_library.py
```

테스트는 API 계약·소유권·저장 충돌·원문 근거·검토·게시·그림 작업 재사용 및 캐릭터 선택을 확인합니다.
외부 AI 응답은 모의 처리하며 실제 유료 생성 품질·속도·Cloud 가용성을 검증하지 않습니다.
`pip install`용 패키지에도 지침·화풍 샘플·캐릭터셋을 포함합니다.

배포는 [Vercel + Turso](docs/DEPLOY-VERCEL-TURSO.md) 또는 [Docker Compose + Caddy](docs/FRONTEND.md#서버-배포)를 사용합니다.
Git push, 호스팅 빌드 성공, 운영 배포 준비 완료, 실제 생성 성공은 서로 다른 상태입니다.
