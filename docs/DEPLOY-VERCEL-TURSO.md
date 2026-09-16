# Vercel + Turso 배포 매뉴얼

이 문서는 백엔드(`ihaerostudio-be`)와 프론트(`ihaerostudio-fe`)를 각각 Vercel 프로젝트로 올리고, 자료 저장소로 Turso를 쓰는 절차입니다. 로그인 없는 공개 체험용 구성(익명 모드)을 전제로 합니다.

## 구성 요약

| 역할 | 어디에 | 비용 |
| --- | --- | --- |
| 프론트 (Next.js) | Vercel 프로젝트 1 | Hobby 무료 |
| 백엔드 (FastAPI) | Vercel 프로젝트 2 (Python 함수) | Hobby 무료. 함수 최대 300초 |
| 자료·그림 저장 | Turso (SQLite 호환 원격 DB) | Free 무료. 카드 없이 5GB, 월 5억 행 읽기 |
| AI | OpenAI, Comfy Cloud | 사용량 과금. 키는 백엔드 환경 변수에만 |

브라우저는 프론트 도메인을 열고, 프론트가 백엔드 도메인의 `/api/studio`를 직접 호출합니다(CORS). 그림은 백엔드가 Turso에 저장하고 `/api/studio/assets/{id}` 주소로 내줍니다.

## 0단계. 코드 준비 (완료)

백엔드에 다음이 들어가 있습니다. 따로 할 일은 없습니다.

- [x] 저장소 분기: `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`이 있으면 Turso로, 없으면 지금처럼 로컬 SQLite 파일로 연결. `/health`의 `storage`가 `turso` 또는 `sqlite`로 알려 줌
- [x] 그림을 자료 JSON에서 분리해 별도 테이블에 저장하고 `GET /api/studio/assets/{id}`로 제공. 문서·게시본·독자 응답의 `src`는 그 주소(응답 4.5MB 제한 대응). 프론트는 바꿀 것 없음
- [x] `vercel.json`: 함수 최대 시간 300초, 테스트·문서 폴더 번들 제외
- [x] `PUBLIC_BASE_URL`(선택): 그림 주소를 만들 때 쓰는 백엔드 공개 주소. 비우면 Vercel이 주는 프로젝트 주소를 씀. 그림 URL에 저장되므로 자료를 만들기 전에 정해야 함
- [x] 로컬 개발과 pytest는 변함없이 SQLite 파일로 동작하고, Turso 경로도 테스트로 확인함

## 1단계. 계정과 권한

1. Vercel 계정을 만들고 GitHub와 연결합니다. 저장소가 `meatproxy-wanted` 조직 아래에 있으므로, Vercel의 GitHub 앱을 **그 조직**에 설치하고 두 저장소 접근을 허용합니다.
2. Turso는 Vercel Marketplace에서 만들 것이므로 따로 가입할 필요가 없습니다. 이미 Turso 계정이 있으면 그것을 연결해도 됩니다.
3. OpenAI 키, 모델 ID(예: `gpt-5.6-luna`), 필요하면 Comfy Cloud 키를 준비합니다. 로컬 `.env`에 있는 값 그대로입니다.

## 2단계. 백엔드 프로젝트 만들기

1. Vercel 대시보드 → **Add New → Project** → `ihaerostudio-be` 선택.
2. Framework Preset이 **FastAPI**로 잡히는지 확인합니다. Root Directory는 저장소 루트 그대로 둡니다. 진입점은 `app/main.py`의 `app`이라 Vercel이 자동으로 찾습니다.
3. **Environment Variables**에 다음을 넣습니다(Production 환경).

| 변수 | 값 | 비고 |
| --- | --- | --- |
| `AI_PROVIDER` | `openai` | |
| `OPENAI_API_KEY` | 키 | |
| `OPENAI_MODEL` | `gpt-5.6-luna` 등 | |
| `OPENAI_MAX_OUTPUT_TOKENS` | `16384` | 응답이 잘리면 `32768` |
| `COMFY_CLOUD_API_KEY` | 키 | 그림 생성을 쓸 때만 |
| `APP_ENV` | `production` | |
| `AUTH_MODE` | `anonymous` | 기본값과 같지만 명시 |
| `CORS_ORIGINS` | 일단 `http://localhost:3000` | 4단계에서 프론트 주소로 바꿈 |
| `PUBLIC_BASE_URL` | (비움) | 커스텀 도메인을 쓸 때만 `https://그도메인`. 비우면 프로젝트 주소 |

`TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`은 3단계에서 자동으로 들어옵니다.

4. **Deploy**를 누릅니다. 첫 배포는 Turso가 아직 없어서 시작에 실패하거나 저장이 안 될 수 있습니다. 3단계 뒤 다시 배포합니다.

## 3단계. Turso 만들고 연결하기

1. 백엔드 프로젝트 → **Storage** 탭 → **Create Database** → **Turso Cloud** 선택 (Marketplace 연동).
2. 데이터베이스 이름을 정하고(예: `ihaerostudio`), 위치는 Vercel 함수 리전과 가까운 곳으로 고릅니다. Hobby의 기본 함수 리전은 미국 동부(`iad1`)이므로 AWS `us-east-1` 계열을 고릅니다. 플랜은 Free.
3. **Connect Project**에서 백엔드 프로젝트를 선택합니다. 그러면 `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`이 백엔드 환경 변수에 자동으로 들어갑니다. 백엔드 코드는 이 두 이름을 그대로 읽습니다.
4. 백엔드를 **Redeploy**합니다(Deployments → 최신 항목 → Redeploy). 테이블은 서버가 시작할 때 스스로 만듭니다.
5. 확인: 브라우저에서 `https://<백엔드주소>/health`를 열어 `"ai_provider":"openai"`와 `"storage":"turso"`가 보이면 됩니다. `"storage":"sqlite"`면 Turso 변수가 안 들어간 것입니다. `https://<백엔드주소>/docs`에서 Swagger도 열립니다.

CLI로 만들고 싶다면:

```bash
brew install tursodatabase/tap/turso
turso auth login
turso db create ihaerostudio
turso db show ihaerostudio
turso db tokens create ihaerostudio
```

`show`가 알려 주는 URL을 `TURSO_DATABASE_URL`에, 토큰을 `TURSO_AUTH_TOKEN`에 직접 넣으면 됩니다.

## 4단계. 프론트 프로젝트 만들기

1. **Add New → Project** → `ihaerostudio-fe` 선택. Framework는 Next.js로 자동 감지됩니다. 빌드 명령 `pnpm build`, Node 22가 기본으로 잡힙니다.
2. Environment Variables:

| 변수 | 값 |
| --- | --- |
| `NEXT_PUBLIC_STUDIO_API_URL` | `https://<백엔드주소>` (끝에 슬래시 없이) |
| `NEXT_PUBLIC_STUDIO_API_TOKEN` | 비워 둠 (브라우저별 익명 ID 사용) |

3. **Deploy**. 끝나면 `https://<프론트주소>`가 생깁니다.
4. 백엔드 프로젝트로 돌아가 `CORS_ORIGINS`를 `https://<프론트주소>`로 바꾸고 **Redeploy**합니다. 미리보기 배포 주소도 허용하려면 쉼표로 이어 적습니다.

## 5단계. 확인 체크리스트

- [ ] 프론트를 열면 작업함이 비어 있고 "서버에 연결하지 못했어요"가 뜨지 않는다
- [ ] 새 자료 화면에 "서버가 데모 모드예요" 경고가 **없다** (있으면 `AI_PROVIDER`가 안 먹은 것)
- [ ] [샘플 판결문으로 체험하기] → 분석 완료 → 사건 구조에 사건번호·법원이 채워진다
- [ ] [초안 만들기] → 편집 화면 → 문장 하나를 [원문과 대조했어요] → 상단 "저장됨"
- [ ] Comfy 키를 넣었다면 카드 → [그림 후보 보기]에서 그림이 한 장 나오고, 적용 후 새로고침해도 그림이 보인다
- [ ] 검토 마치기 → 내보내기 → [읽기 화면 공개] → 그 주소를 시크릿 창에서 열면 읽힌다
- [ ] 다른 브라우저(또는 시크릿 창)에서 프론트를 열면 작업함이 비어 있다 (방문자별 작업함)
- [ ] 5MB짜리 PDF를 올리면 "4.5MB 이하의 PDF만" 안내가 뜬다

## 6단계. 운영에서 알아 둘 것

- **비용 한도**: 누구나 분석·초안·그림 생성을 누를 수 있습니다. OpenAI 대시보드의 월 지출 한도와 Comfy 크레딧 한도를 먼저 걸어 두세요. 백엔드에는 아직 호출 횟수 제한이 없습니다.
- **Hobby 플랜 조건**: Vercel Hobby는 개인·비상업 용도 조건이 붙습니다. 회사 시연이나 사업 목적이면 Pro(월 20달러)로 올리는 편이 안전하고, 그러면 함수 시간도 800초까지 늘릴 수 있습니다.
- **콜드 스타트**: 한동안 요청이 없으면 첫 요청이 몇 초 느립니다. 시연 직전에 한 번 열어 두세요.
- **긴 판결문**: 분석·초안이 300초를 넘기면 504가 납니다. Luna 기준 샘플은 25초 안팎이지만, 긴 문서나 큰 모델은 Pro 플랜의 `maxDuration` 상향이 필요할 수 있습니다.
- **자료 정리**: 지운 자료도 소프트 삭제라 Turso에 남습니다. 오래된 자료를 지우는 정리 작업은 아직 없으니 필요해지면 추가합니다.
- **커스텀 도메인**: 각 프로젝트 Settings → Domains에서 붙입니다. 붙이면 `NEXT_PUBLIC_STUDIO_API_URL`과 `CORS_ORIGINS`도 새 주소로 바꾸고 재배포합니다. 백엔드 도메인을 바꾸면 `PUBLIC_BASE_URL`도 맞추세요. 이전 주소로 저장된 그림은 이전 주소가 살아 있는 동안만 보입니다.
- **미리보기 배포 보호**: Vercel은 미리보기 배포에 로그인을 요구할 수 있습니다. 공개 링크로 시연할 때는 Production 주소를 쓰세요.

## 문제가 생기면

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| 작업함에 "서버에 연결하지 못했어요" | 백엔드 주소가 틀리거나 백엔드가 죽음 | `NEXT_PUBLIC_STUDIO_API_URL` 확인, `/health` 열어 보기 |
| 브라우저 콘솔에 CORS 오류 | `CORS_ORIGINS`에 프론트 주소가 없음 | 값을 고치고 백엔드 재배포 |
| "서버가 API 토큰을 받아들이지 않았어요" | `AUTH_MODE=keys`인데 토큰이 미등록 | `AUTH_MODE=anonymous`로 |
| 분석이 오래 걸리다 실패(504) | 함수 시간 초과 | 짧은 문서로 확인, Pro에서 `maxDuration` 상향 |
| 배포 로그에 `OPENAI_API_KEY is required` | 환경 변수 누락 | 변수를 넣고 재배포 |
| 배포는 됐는데 자료가 사라짐 | Turso 미연결로 임시 디스크 사용 | 3단계 확인 후 재배포 |
| PDF 업로드 413 | 4.5MB 초과 | 파일을 줄이거나 텍스트로 붙여넣기 |
| 그림이 깨져 보임 | 그림 주소의 도메인이 지금 백엔드와 다름 | `PUBLIC_BASE_URL`을 확인하고, 자료를 새로 만들어 그림을 다시 생성 |
