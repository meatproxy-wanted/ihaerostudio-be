# FE 연동 및 서버 배포

현재 FE는 `lib/api/client.ts`의 HTTP 클라이언트로 BE를 호출하고 zod 스키마로 응답을 검증합니다.
BE는 `/api/studio` API와 `/health`를 제공하며, `/api/v1`·영상 작업 API·`/share`는 등록하지 않습니다.
구형 mock/IndexedDB 자료를 DB로 자동 이전하지 않습니다.
전체 제품 설명은 [프로젝트 요약](PROJECT_OVERVIEW.md), 생성 분기는 [그림 생성 상세](IMAGE_GENERATION.md)를 봅니다.

## API 계약

| FE 기능 | HTTP 경로 (`/api/studio` 기준) |
| --- | --- |
| 자료 목록·조회·삭제 | `GET /projects`, `GET/DELETE /projects/{id}` |
| 입력 + 분석 | `POST /projects/text`, `POST /projects/pdf` |
| 제목·설정 | `PATCH /projects/{id}/title`, `PUT /projects/{id}/settings` |
| 원문 | `GET /projects/{id}/source` |
| 사건 구조 | `GET/PUT /projects/{id}/structure` |
| 글 초안 | `POST /projects/{id}/document/generate` |
| 편집 진입 그림 자동 준비 | `POST /projects/{id}/document/prepare-images` |
| 문서 조회·자동 저장 | `GET/PUT /projects/{id}/document` |
| 문장·용어 보조 | `POST /projects/{id}/assist/{simplify,split,terms,explain}` |
| 그림 후보·업로드 | `POST /projects/{id}/assist/{images,upload-image}` |
| 그림 바이너리 | `GET /assets/{assetId}` (인증 없음) |
| 검토 | `GET /projects/{id}/review`, `POST /projects/{id}/review/{run,dismiss,dismiss-all,restore,complete}` |
| 게시본 | `GET/POST /projects/{id}/publications`, `GET /projects/{id}/publications/{publicationId}` |
| 공개·해제 | `PUT /projects/{id}/public` (`publicationId` 또는 null) |
| 독자 | `GET /reader/{id}` (인증 없음) |
| 가상 샘플 | `GET /demo/sample-text` |
| 개발 초기화 | `POST /demo/reset` (운영 환경 또는 실제 AI 모드에서는 403) |

## 연결·인증

FE의 `NEXT_PUBLIC_STUDIO_API_URL`은 BE origin이며 기본값은 `http://127.0.0.1:8100`입니다.
이 값은 빌드 시 반영되므로 운영 주소 변경 시 FE를 다시 빌드합니다.
BE의 `CORS_ORIGINS`에는 호출하는 FE origin을 쉼표로 지정합니다.

독자·그림 파일 외의 업무 API는 Bearer 토큰이 필요합니다.
기본 `AUTH_MODE=anonymous`는 브라우저 방문자 ID처럼 허용된 영문·숫자·`._~-` 토큰(16~200자)을 작업함 구분자로 사용합니다.
브라우저 저장소를 지우거나 다른 브라우저를 쓰면 새 작업함이 됩니다.
`AUTH_MODE=keys`는 BE의 `API_KEYS`에 등록한 토큰만 받습니다.

FE의 `NEXT_PUBLIC_STUDIO_API_TOKEN`에 등록 토큰을 넣으면 모든 방문자가 그 값으로 같은 작업함을 사용합니다.
이 값은 공개 번들에 노출되므로 개인 로그인이나 비밀 API 키로 취급하지 않습니다.
OpenAI·Comfy·Turso 키는 BE 환경변수에만 넣습니다. 별도 회원가입·로그인 API는 없습니다.

## 응답과 저장 버전

입력 API는 분석·저장한 `project` 객체를 반환합니다. 원문과 구조는 별도 GET으로 조회합니다.
글 생성은 `{document, project}`를 반환하고 그림 생성은 하지 않습니다.
문서 PUT은 전체 `EasyDocument`를 보내는 방식이며 부분 PATCH가 아닙니다.

문서 `saveRevision`, 구조 `revision`은 직전 응답 값을 보냅니다.
충돌(409)에서는 최신 응답과 사용자 변경을 합친 뒤 다시 저장합니다.
`contentRevision` 및 초안의 기준 버전은 서버가 결정합니다.
원문 근거 `anchors[].start/end`는 문단 text의 JavaScript UTF-16 인덱스(시작 포함·끝 미포함)입니다.
실제 AI는 원문 인용문을 반환하고 서버가 문단 안에서 위치를 찾습니다.

## 편집 진입 그림 자동 준비

글 생성 후 `POST /projects/{id}/document/prepare-images`를 이어 호출합니다.
응답은 `{document,project,generation:{status,phase,completed,total,currentCardId}}`입니다.

- `running`: 같은 POST를 이어 호출합니다. 기본 모드는 요청당 최대 카드 하나, `storyboard4` 실험은 장면 최대 4개를 함께 처리합니다.
- `ready`: 전체 적용 완료. 최신 문서와 project를 캐시에 넣고 편집기를 엽니다.
- `skipped`: demo 또는 그림 없음 설정. 실제 생성 성공으로 표시하지 않습니다.

기본 library 모드는 GPT 선택 → 고정 캐릭터 얼굴 크롭 저장 → 인물 고정 → Qwen 장면 자동 적용 순서입니다.
카드 표시용 얼굴과 장면 입력용 기본 포즈 원본은 서버가 구분하므로 새 FE 필드가 필요 없습니다.
`STUDIO_SCENE_MODE=storyboard4`는 서버 설정만으로 켭니다. 응답 구조는 같고 completed가 최대 4씩 증가할 수 있습니다.
수동 `assist/images`는 계속 카드별 후보 경로입니다. [실험 문서](STORYBOARD_EXPERIMENT.md)를 참고하세요.
같은 캐릭터를 장면에 실제 레퍼런스로 넣지만 완벽한 얼굴 일치를 보장하지 않습니다.
10종 후보와 자동 준비의 인물 상한 6명·장면 상한 3명은 서로 다른 수치입니다.

진행 중 FE는 편집·자동 저장을 시작하지 않습니다.
현재 FE는 실패 시 다시 시도 또는 “생성된 자료로 편집 계속하기”로 부분 결과를 열 수 있습니다.
부분 편집 진입은 남은 그림을 완료했다는 뜻이 아닙니다.
페이지를 떠나면 남은 카드 처리는 멈추고 재진입 시 완료 카드를 건너뛰며 진행 중 원격 작업을 조회합니다.
브라우저 요청 취소는 Comfy 작업 취소가 아닙니다.

인물 그림 교체·삭제 및 고정 인물 카드 제거는 `409 character_locked`로 차단합니다.
글·장면·대체텍스트는 계속 편집할 수 있습니다.
완료한 일괄 작업에서 장면을 제거하거나 새 카드를 추가해도 진입만으로 자동 재생성하지 않습니다.
새 장면은 후보 API로 만들고, 초안 재생성은 고정 인물을 유지하면서 새 장면의 일괄 작업을 초기화합니다.

## 그림 후보와 업로드

`POST /projects/{id}/assist/images`는 `{cardId}`를 받습니다.
저장된 카드·문장·원문 근거를 사용하므로 자동 저장 완료 후 요청합니다.
후보 응답은 `{candidates:[{src,alt,meaning}]}`이며 이 요청 자체는 문서에 적용하지 않습니다.
선택한 후보를 document.images와 card.imageId에 연결해 PUT document로 저장합니다.

실제 AI의 library 인물 후보는 고정 캐릭터 얼굴 자산입니다.
장면 생성에는 Comfy 키가 필요합니다. demo는 업로드 후보만 반환합니다.
`source=library`는 stock 얼굴과 생성 장면 모두에서 사용하는 기존 자산 분류입니다.
동일 내용은 결과·작업을 재사용하지만 문맥 변경 후 요청은 새 유료 작업이 될 수 있습니다.

업로드는 multipart의 file·alt·meaning을 받으며 PNG/JPEG/WebP 및 제한된 안전 SVG를 정제·보관합니다.
업로드 성공도 카드에 자동 연결하지 않습니다.
그림 업로드는 2MiB, 자료당 이미지 100개·합계 12MiB입니다. 연결을 해제해도 자산은 용량에 남습니다.
생성 결과와 업로드 그림의 src는 서버가 보관한 절대 주소이며 그대로 `<img src>`에 사용합니다.
임의 외부 URL은 문서 그림으로 저장하지 못합니다.

후보 API는 Comfy 진행 중에 `503 image_in_progress`를 반환합니다.
일괄 준비는 카드 생성의 이 오류를 보통 `200 running`으로 변환합니다.
접수 불확실·실행 실패·만료 작업을 자동 새 유료 작업으로 바꾸지 않습니다.
동일 요청 재조회와 재생성을 구분해 오류를 표시합니다. 자세한 호환·재시도 정책은 그림 생성 문서를 봅니다.

## 검토·게시·출력

자동 검토는 근거 없는 문장(required), 원문 전체에 없는 숫자(suggested),
60자 초과 문장(suggested)만 점검합니다.
인물 관계·주장/판단 구분·그림 의미·빈 대체텍스트를 자동으로 판정하는 기능은 없습니다.
금액·관계·주장, 그림 사용 시 글그림 의미는 최종 체크리스트에서 제작자가 확인합니다.

게시본 생성과 공개는 별도 API입니다. 미검토 게시본은 제작자만 문서로 조회하고 인쇄할 수 있습니다.
검토 완료 게시본만 공개할 수 있고 독자 응답에는 원문 근거·검토 메모·작성 기록이 없습니다.
이 문서 접근 제한과 그림 파일 접근은 다릅니다. 그림 URL은 인증 없이 조회됩니다.
공개 해제는 그림 URL 회수가 아니고 soft delete도 그림 파일 물리 삭제가 아닙니다.

PDF 저장은 FE의 `/print/[id]?publication=...` 화면과 브라우저 인쇄입니다.
BE PDF 다운로드·영상·카드뉴스 PNG 출력 API는 없습니다.
PDF 입력은 원본 4,500,000바이트·100쪽 이하이며 OCR은 없습니다.
새 PDF 원본은 저장하지 않지만 추출 텍스트·메타데이터는 DB에 남습니다.
현재는 큰 파일을 위한 직접 객체 저장소 업로드 API를 제공하지 않습니다.

## 서버 배포

Vercel 배포는 [Vercel + Turso 매뉴얼](DEPLOY-VERCEL-TURSO.md)을 봅니다.
아래는 Docker Compose·Caddy를 쓰는 별도 서버 구성입니다.

Docker Compose가 설치된 Linux 서버와 해당 서버를 가리키는 DNS가 필요합니다.
80/443 포트를 열고 실행합니다. API 컨테이너 포트는 호스트에 직접 노출하지 않습니다.

```bash
git clone git@github.com:meatproxy-wanted/ihaerostudio-be.git
cd ihaerostudio-be/deploy
cp .env.example .env
# API_DOMAIN, CORS_ORIGINS, PUBLIC_BASE_URL, OpenAI 키·모델, Comfy 키를 설정
# keys 모드라면 별도의 충분히 긴 API_KEYS도 설정
chmod 600 .env
docker compose up -d --build --wait
curl --fail https://YOUR_API_DOMAIN/health
```

`PUBLIC_BASE_URL=https://YOUR_API_DOMAIN`을 자료 생성 전에 지정합니다.
Caddy가 HTTPS 인증서를 발급·갱신합니다. SQLite·인증서는 Docker 볼륨에 보존됩니다.
`docker compose down -v`는 데이터를 삭제하므로 사용하지 않습니다.
업데이트 전 SQLite 온라인 백업과 현재 커밋을 기록합니다. 문제가 생기면 이전 코드로 재빌드합니다.
자동 데이터 마이그레이션 되돌리기는 없습니다.

## 계약 검증

FE를 수정하지 않고 현재 zod 스키마로 BE 응답을 검사합니다.

```bash
STUDIO_CONTRACT_OUT=../studio-contract.json .venv/bin/python -m pytest -q
node scripts/verify_frontend_contract.mjs ../ihaerostudio-fe ../studio-contract.json
```

BE TestClient의 업로드부터 공개/해제까지 실제 응답을 내보내 검증합니다.
AI 제공자 응답은 모의 처리하므로 유료 생성 품질·운영 네트워크 검증은 별도입니다.

현재 FE는 이미 HTTP로 연결되므로 보통 별도의 테스트 복사본이 필요하지 않습니다.
`scripts/prepare_frontend_test.py`는 구형 FE를 위한 진단 도구이며 테스트 전용 transport로 교체합니다.
그 복사본을 현재 운영 FE나 인증·이미지 자동 준비 계약의 기준으로 삼거나 배포하지 않습니다.
