# FE 연동 및 배포

`ihaerostudio-fe/lib/api/types.ts`의 전체 `ApiClient`는 `/api/studio`를 사용합니다.
기존 `/api/v1`의 카드별 API, PDF, ComfyCloud 영상·그림 작업은 유지됩니다.
새 FE 자료는 SQLite의 `studio_projects` 테이블에 별도로 저장됩니다.
기존 `/api/v1` 문서나 브라우저 IndexedDB 자료를 자동으로 옮기지 않습니다.

## API 계약

| FE 기능 | HTTP 경로 (`/api/studio` 기준) |
| --- | --- |
| 자료 목록·조회·삭제 | `GET /projects`, `GET/DELETE /projects/{id}` |
| 업로드 + 분석 | `POST /projects/text`, `POST /projects/pdf` |
| 제목·설정 | `PATCH /projects/{id}/title`, `PUT /projects/{id}/settings` |
| 원문 | `GET /projects/{id}/source` |
| 사건 구조 | `GET/PUT /projects/{id}/structure` |
| 초안·자동 저장 | `POST /projects/{id}/document/generate`, `GET/PUT /projects/{id}/document` |
| 문장·용어 보조 | `POST /projects/{id}/assist/{simplify,split,terms,explain}` |
| 그림 후보·업로드 | `POST /projects/{id}/assist/{images,upload-image}` |
| 검토 | `GET /projects/{id}/review`, `POST /projects/{id}/review/{run,dismiss,restore,complete}` |
| 게시본 | `GET/POST /projects/{id}/publications`, `GET /projects/{id}/publications/{publicationId}` |
| 공개·해제 | `PUT /projects/{id}/public` (`publicationId` 또는 null) |
| 독자 | `GET /reader/{id}` (인증 없음) |
| 가상 샘플 | `GET /demo/sample-text` |
| 개발 초기화 | `POST /demo/reset` (운영 환경은 403) |

읽기 API 외에는 작성자 Bearer 토큰이 필요합니다. OpenAI/ComfyCloud 키는 BE 환경변수에만 저장합니다.
**이번 변경은 BE에만 적용됩니다. FE 코드는 변경하지 않습니다.** 현재 FE는 `createMockApi()`를
사용하므로 BE를 배포하는 것만으로 화면이 실제 서버로 전환되지는 않습니다. 위 표와 응답 계약은
FE에서 추후 HTTP 구현을 연결할 때 사용할 수 있습니다.

FE의 원문 근거 오프셋은 JavaScript UTF-16 코드 단위입니다. 기존 `/api/v1`의 Python
문자 오프셋과 다르며, 이모지·보조 평면 문자 경계도 검증합니다.
자동 저장은 `saveRevision`, 구조 저장은 `revision`으로 동시 수정 충돌을 검사합니다.
`contentRevision`과 초안의 기준 버전은 서버가 결정합니다.

구조·설정·문장·근거·검증 표시가 바뀌면 현재 검토는 해제됩니다. 검토 항목 확인의
메모는 기존 FE와 같이 선택 사항입니다. 자동 점검은 규칙 기반이며, 의미 정확성이나 그림 의미를 판정하지 않습니다.
원문 대조·주장 분류·그림 의미를 제작자가 직접 확인하도록 항목을 생성합니다.
미검토 게시본은 제작자만 조회하고 인쇄할 수 있습니다. 공개는 검토 완료 게시본만 가능합니다.
게시본은 불변 스냅샷이며 원문 근거·검토 메모·작성 기록은 독자 응답에 포함되지 않습니다.
삭제는 soft delete이고 공개 읽기도 즉시 중단됩니다.

## 현재 그림·출력 동작

FE 그림 후보는 **이 자료에 이미 업로드한 그림**입니다. PNG/JPEG/WebP 및 제한된 안전한 SVG를 정제하여 저장합니다.
새 FE 초안의 자동 ComfyCloud 그림 생성은 아직 연결하지 않았습니다. 기존 `/api/v1`의
그림·영상 작업은 종전 API로 사용할 수 있습니다. FE의 PDF 저장은 기존 `/print` 화면과
브라우저 인쇄를 사용합니다. 새 자료를 기존 `/api/v1` PDF 엔드포인트로 조회할 수는 없습니다.

FE의 기존 제한에 맞춰 PDF는 20MB, 그림 파일은 2MB입니다. 자료의 정제 그림 전체는 base64 기준
12MB까지 보관합니다. 추후 Vercel 프록시로 연결할 경우 플랫폼 요청 한도에 유의하고, 큰 파일은
BE 직접 업로드 또는 객체 저장소 업로드 경로를 사용해야 합니다.

## 서버 배포

Docker Compose가 설치된 Linux 서버와 해당 서버를 가리키는 DNS가 필요합니다.
80/443 포트를 열고 다음을 실행합니다. API 컨테이너 포트는 호스트에 직접 노출하지 않습니다.

```bash
git clone git@github.com:meatproxy-wanted/ihaerostudio-be.git
cd ihaerostudio-be/deploy
cp .env.example .env
# .env에 API_DOMAIN, 랜덤 API_KEYS, OPENAI_API_KEY, OPENAI_MODEL 입력
chmod 600 .env
docker compose up -d --build --wait
curl --fail https://YOUR_API_DOMAIN/health
```

Caddy가 HTTPS 인증서를 발급·갱신합니다. SQLite와 인증서는 이름 있는 Docker 볼륨에
보존됩니다. `docker compose down -v`는 데이터를 삭제하므로 사용하지 마세요.
업데이트 전 SQLite 온라인 백업을 별도 보관하고, 확인한 커밋으로 checkout한 후 위 명령으로
다시 빌드합니다. 문제가 생기면 이전 커밋으로 checkout하여 재빌드합니다. 자동 데이터 마이그레이션
되돌리기는 제공하지 않습니다.

로컬 화면 검증은 `AI_PROVIDER=demo`로 가능합니다. 데모는 실제 입력의 표제만 분류하고
원문을 복사합니다. 실제 쉬운 글·용어 생성에는 `AI_PROVIDER=openai`와 유효한 키/모델이 필요하며,
키가 없을 때 실제 AI인 것처럼 대체 응답하지 않습니다.

## 양쪽 계약 검증

FE 원본을 수정하지 않는 로컬 브라우저 테스트 환경:

```bash
python scripts/prepare_frontend_test.py --frontend ../ihaerostudio-fe --output ../integration-fe-new
# BE 서버
AI_PROVIDER=demo CORS_ORIGINS=http://127.0.0.1:3100 uvicorn app.main:app --host 127.0.0.1 --port 8100 --no-access-log
# 생성된 integration-fe-new 디렉터리의 별도 터미널
node_modules/.bin/next build --webpack
node_modules/.bin/next start --hostname 127.0.0.1 --port 3100
```

테스트 복사본에만 HTTP transport와 `ApiClient` 선택 코드를 넣습니다. FE 화면·스토어·스키마는
원본 그대로이며 로그인 UI를 추가하지 않습니다. 개발용 토큰은 로컬 테스트 transport에만 들어갑니다.
이 복사본을 운영 배포하지 마세요. 화면의 기존 데모 안내 문구도 변경하지 않아 그대로 보입니다.

두 저장소가 이웃 디렉터리에 있을 때:

```bash
# BE
STUDIO_CONTRACT_OUT=../studio-contract.json .venv/bin/python -m pytest tests/test_studio.py -q
node scripts/verify_frontend_contract.mjs ../ihaerostudio-fe ../studio-contract.json
```

백엔드 TestClient가 업로드부터 공개/해제까지 실행한 **실제 응답**을 내보냅니다.
FE 저장소 밖에서 별도 계약 검사를 실행하면 원본 FE의 zod 스키마로 검증할 수 있습니다.
AI 호출의 기본 통합 테스트는 유료 외부 API를 호출하지 않습니다. 운영 키와 배포 주소에서의
실제 생성 품질 및 네트워크 검증은 별도 배포 단계에서 수행해야 합니다.
