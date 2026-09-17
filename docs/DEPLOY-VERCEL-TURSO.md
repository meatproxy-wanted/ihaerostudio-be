# Vercel + Turso 배포 매뉴얼

현재 FE(Next.js)와 BE(FastAPI)를 별도 Vercel 프로젝트로 배포하고 Turso에 자료·그림을 저장하는 구성입니다.
이 문서는 저장소 설정 기준입니다. 플랫폼 플랜·가격·UI·리전·시간/본문 한도는 바뀔 수 있으므로 배포 시 계정의 실제 설정을 확인합니다.
무료 운영 가능 여부나 특정 모델의 사용 권한을 보장하지 않습니다.

## 구성

| 역할 | 배포 대상 | 확인 사항 |
| --- | --- | --- |
| FE | Vercel Next.js 프로젝트 | BE 주소는 빌드 시 반영 |
| BE | Vercel FastAPI/Python 프로젝트 | 진입점 `app/main.py`의 `app` |
| 자료·이미지·작업 상태 | Turso | 로컬 임시 디스크를 영속 DB로 사용하지 않음 |
| GPT | OpenAI API | 분석·글·캐릭터 선택·장면 설계와 레퍼런스 이미지 분석 |
| 장면 이미지 | Comfy Cloud Qwen 워크플로우 | 계정 크레딧, 모델·노드 가용성, 결과 호스트 |

서버 환경변수의 OpenAI·Comfy·Turso 키를 FE 또는 Swagger Authorize에 넣지 않습니다.
브라우저는 FE에서 BE의 `/api/studio`를 직접 호출하므로 CORS가 필요합니다.
기본 library 모드의 캐릭터 얼굴은 번들 이미지 크롭이며 Qwen을 호출하지 않습니다.
Qwen은 상황 그림 생성에 사용합니다.

## 1. DB와 BE 준비

GitHub 조직의 두 저장소에 Vercel 연동 권한이 있는지 확인합니다.
백엔드 저장소 루트를 프로젝트 Root Directory로 선택하고 FastAPI 진입점이 올바르게 감지되는지 확인합니다.
Turso DB URL·토큰을 먼저 준비하거나 Marketplace 연동을 사용합니다.
Marketplace가 어떤 변수 이름을 만들었든 아래 **코드가 읽는 이름**으로 실제 값이 들어 있는지 확인합니다.

| BE 환경변수 | 값 / 의미 |
| --- | --- |
| `AI_PROVIDER` | `openai` (유료 생성 없이 연결만 확인할 때 `demo`) |
| `OPENAI_API_KEY` | 서버 전용 API 키 |
| `OPENAI_MODEL` | 해당 API 계정에서 사용 가능한 Responses·Structured Outputs·이미지 입력 지원 모델 ID |
| `OPENAI_MAX_OUTPUT_TOKENS` | 기본 `16384`. 실제 모델·응답 크기에 맞춰 조정 |
| `STUDIO_CHARACTER_MODE` | `library` (기본) |
| `STUDIO_SCENE_MODE` | `single` (기본). 고해상도 4컷 실험만 `storyboard4`. [실험·롤백](STORYBOARD_EXPERIMENT.md) 확인 |
| `COMFY_CLOUD_API_KEY` | 장면 자동 준비·후보 생성에 필요 |
| `TURSO_DATABASE_URL` | Turso DB URL |
| `TURSO_AUTH_TOKEN` | 해당 DB 접근 토큰 |
| `APP_ENV` | `production` |
| `AUTH_MODE` | 공개 체험용 `anonymous` 또는 제한용 `keys` |
| `API_KEYS` | keys 모드라면 충분히 긴 등록 토큰 → 제작자 ID JSON |
| `CORS_ORIGINS` | 실제 FE origin. 여러 개는 쉼표로 구분 |
| `PUBLIC_BASE_URL` | 안정적인 BE 공개 origin. Production 도메인 자동값을 쓸 수도 있음 |
| `COMFY_ASSET_ALLOWED_HOSTS` | 기본 `cloud.comfy.org,storage.googleapis.com`. 실제 결과 CDN은 확인 후 정확한 호스트만 추가 |

`vercel.json`의 `maxDuration`은 현재 300초로 **요청**합니다.
계정의 허용 상한과 런타임 적용 여부는 별도 확인합니다.
테스트·문서·스크립트·로컬 data는 함수 번들에서 제외하고 `app/assets`는 포함합니다.
Python 패키지 설치 경로에도 번들 캐릭터·분위기 샘플·지침이 포함되어야 합니다.

배포 후 `https://<BE>/health`에서 `storage=turso`, 의도한 `ai_provider`를 확인합니다.
`storage=sqlite`인 Vercel 배포를 영속 저장 성공으로 해석하지 않습니다.
이 응답은 AI 키 유효성이나 Qwen 생성 성공을 검증하지 않습니다.
`https://<BE>/docs`에서 실제 API 문서를 확인합니다.

## 2. FE 연결

프론트 프로젝트는 `ihaerostudio-fe`의 Next.js 앱입니다.
저장소의 package manager·빌드 스크립트·Node 요구 버전을 기준으로 설정합니다.

| FE 환경변수 | 값 |
| --- | --- |
| `NEXT_PUBLIC_STUDIO_API_URL` | 안정적인 BE origin (`https://<BE>`, 끝 슬래시 없이) |
| `NEXT_PUBLIC_STUDIO_API_TOKEN` | 공개 익명 체험에서는 비움. 등록 토큰을 넣으면 방문자 모두가 같은 작업함을 사용 |

`NEXT_PUBLIC_` 토큰은 공개 번들 값이며 비밀 키나 개인 로그인으로 사용하지 않습니다.
현재 FE의 `lib/api/client.ts`는 HTTP로 연결되어 있습니다.
이 환경변수는 빌드 시 적용되므로 변경 후 FE를 다시 배포합니다.
BE의 `CORS_ORIGINS`도 FE 실제 Production origin과 맞추고 변경 후 BE를 다시 배포합니다.
Preview origin은 필요한 경우에만 명시적으로 허용합니다.

## 3. 연결 및 생성 확인

먼저 외부 AI를 호출하지 않는 상태·인증·CORS를 확인합니다.
다음 체크는 실제 API 사용량이 발생할 수 있으므로 짧은 가상 판결문으로 실행합니다.

- [ ] BE Production 배포가 ready이고 해당 커밋이 배포되어 있다.
- [ ] health의 저장소와 AI 모드가 의도한 설정이다.
- [ ] FE 작업함이 서버에 연결되고 다른 브라우저의 익명 작업함과 분리된다.
- [ ] 새 자료 분석 후 원문과 사건 구조를 조회할 수 있다.
- [ ] 글 초안 생성 후 편집 진입에서 인물 얼굴과 장면 그림이 자동 적용된다.
- [ ] 얼굴은 고정 캐릭터 크롭이며 장면은 같은 캐릭터 원본을 사용한다. 실제 외형·상황도 사람이 확인한다.
- [ ] 새로고침 후 완료된 얼굴·장면과 캐릭터 배정이 유지된다.
- [ ] 고정 인물은 교체·삭제할 수 없고 글·장면·대체텍스트는 편집할 수 있다.
- [ ] 생성 실패에서 앞서 완성된 자료를 보존하고 반복 요청이 중복 유료 작업을 만들지 않는다.
- [ ] 검토·게시본 생성·공개를 각각 마친 뒤 독자 링크를 다른 브라우저에서 읽는다.
- [ ] FE 인쇄 화면에서 브라우저 PDF 저장이 된다. BE PDF 다운로드 API가 있다고 가정하지 않는다.
- [ ] 공개 해제 시 reader는 unavailable이다. 그림 URL 회수까지 된 것으로 해석하지 않는다.

## 운영 주의사항

- 공개 익명 모드는 로그인 없는 체험용이며 서버 호출 횟수 제한이 없습니다.
  제공자의 사용량·예산 관리 기능을 확인하고 운영 접근 정책을 따로 정합니다. 이 저장소는 비용 상한을 강제하지 않습니다.
- 플랫폼 플랜의 사용 조건·현재 가격·함수 한도는 [Vercel 공식 문서](https://vercel.com/docs),
  DB 저장·트래픽 한도는 [Turso 공식 문서](https://docs.turso.tech)에서 배포 시 확인합니다.
- 텍스트 생성은 동기 응답입니다. 그림 준비는 카드당 요청이지만 GPT 분석·설계·업로드 시간까지 포함하므로 HTTP 시간이 Comfy 대기 90초보다 길 수 있습니다.
- 페이지를 닫으면 남은 카드 요청은 멈춥니다. 원격 접수 작업은 다음 요청에서 조회하며 자동 백그라운드 완주 작업자는 없습니다.
- 자료 삭제는 soft delete입니다. 추출 텍스트·그림의 물리 삭제·보관기간 정리 기능은 없습니다.
- 그림은 인증 없는 asset URL입니다. 민감한 이미지나 판결문 정보를 업로드하기 전에 개인정보 정책을 확인합니다.
- 도메인 변경 시 FE API URL·BE CORS·PUBLIC_BASE_URL을 함께 맞춥니다.
  이전 그림 src는 DB에 저장된 이전 주소이므로 자동으로 새 도메인으로 바뀌지 않습니다. 기존 주소 유지 또는 명시적인 이전이 필요합니다.
- git push는 배포 성공이 아닙니다. 호스팅 빌드·Production ready·배포 커밋·실제 생성 결과를 각각 확인합니다.

## 문제 구분

| 증상 | 확인 / 대응 |
| --- | --- |
| FE에서 연결 불가 | 빌드된 BE 주소, Production ready, health, CORS 확인 |
| 401 | anonymous 방문자 토큰 형식 또는 keys 등록 상태 확인 |
| 자료가 사라짐 | Turso 변수 이름·권한·health storage 확인. Vercel 로컬 DB로 운영하지 않음 |
| 분석·생성 504 | 실제 함수 한도·GPT 응답 시간·문서 크기 확인. 같은 응답을 무조건 반복하지 않음 |
| `comfy_not_configured` | 얼굴 생성은 가능해도 장면에는 Comfy 키 필요 |
| `character_library_unavailable` | 번들 manifest와 10종 원본이 배포에 포함됐는지 확인 |
| `character_library_changed` | 해당 프로젝트의 원본 캐릭터 버전 복원. 새 배정으로 조용히 대체하지 않음 |
| `character_reference_limit` | 자동 준비 인물 6명 / 장면 참조 3명 제한을 구분해 카드·자료 조정 |
| `image_in_progress` | 진행 중 작업 조회. 새 작업 제출과 구분 |
| `image_submission_unknown` | DB 작업과 Comfy 목록 대조. 중복 생성 금지 |
| 그림 URL 깨짐 | 저장된 src 도메인·PUBLIC_BASE_URL·기존 주소 유지 여부 확인 |
| PDF 413 | 원본 4,500,000바이트 이하 또는 텍스트 입력. 직접 객체 저장소 업로드 API는 없음 |
