"""Detailed Korean OpenAPI documentation, isolated from endpoint/AI behavior."""
from copy import deepcopy
import json

from . import studio_doc_examples as ex
from .studio_doc_schemas import COMMON_FIELDS, FIELD_DESCRIPTIONS, RESPONSE_SCHEMAS, array, nullable, ref
from .studio_api import SAMPLE_TEXT

API_DESCRIPTION = """
## 이 API로 할 수 있는 일

현재 이해로 스튜디오 프론트의 `ApiClient`에 대응하는 백엔드입니다. 모든 업무 API는
`/api/studio`로 시작하며 **자료 만들기 → 사건 구조 확인 → 쉬운 설명자료 생성·편집 → 검토 → 게시본 생성·공개** 흐름을 지원합니다.
프론트의 각 요청과 응답은 camelCase 필드명을 사용합니다. 경로 매개변수 `project_id`·`publication_id`는 snake_case입니다.

### 처음 연결할 때의 호출 순서

| 단계 | 호출 | 응답에서 확인할 값 / 다음 동작 |
| --- | --- | --- |
| 1. 자료 만들기 | `POST /projects/text` 또는 `/projects/pdf` | 분석과 저장이 끝나면 `201`과 `project` 객체를 반환합니다. `id`를 보관합니다. |
| 2. 원문·구조 확인 | `GET /projects/{id}/source`, `/structure` | 원문 문단과 추출한 사건 구조를 보여 줍니다. 수정한 구조는 `PUT /structure`로 저장합니다. |
| 3. 쉬운 글 생성 | `POST /projects/{id}/document/generate` | 완료된 `{document, project}`를 반환합니다. 편집 진입 시 POST document/prepare-images를 이어 호출해 모든 카드 그림을 자동 생성·적용합니다. |
| 4. 편집·그림 | `POST /document/prepare-images` 후 문장 보조·`POST /assist/images`·`PUT /document` | 진입 시 모든 그림을 자동 적용하고 인물만 고정합니다. 이후 글·장면을 편집합니다. |
| 5. 검토 | `POST /review/run` → 항목 확인 → `POST /review/complete` | 규칙 점검과 제작자의 원문·그림 대조를 구분합니다. 미처리 required 항목이 없어야 완료할 수 있습니다. |
| 6. 게시·공개 | `POST /publications` → `PUT /public` | 게시본 생성과 외부 공개는 별도 동작입니다. 공개할 검토 완료 게시본 ID를 지정합니다. |
| 7. 독자 조회 | `GET /reader/{id}` | 인증 없이 공개 게시본을 읽습니다. 미공개·삭제·자료 없음은 모두 `200 {"status":"unavailable"}`입니다. |

표의 경로에는 `/api/studio`를 붙입니다. `/structure`·`/document`·`/assist/...`·`/review/...`·`/publications`·`/public`은
`/projects/{id}` 아래 경로를 축약한 표현입니다. 예: 그림 후보의 전체 경로는 `/api/studio/projects/{id}/assist/images`입니다.
응답의 `project`는 목록·진행 상태 요약이며,
원문·사건 구조·문서 본문이 한꺼번에 들어 있지 않습니다. 각 조회 API로 가져오세요.

## 인증과 Swagger 사용법

오른쪽 **Authorize**에는 **토큰 값만** 입력합니다. Swagger가 `Authorization: Bearer <토큰>` 헤더를 붙입니다.
기본값인 익명 모드(`AUTH_MODE=anonymous`)에서는 영문·숫자·`._~-`로 된 16자 이상의 아무 토큰이나 받아들여
그 토큰만의 작업함을 만듭니다. 프론트는 브라우저마다 만든 방문자 ID를 보냅니다. 서버는 토큰의 해시만 저장합니다.
`AUTH_MODE=keys`로 바꾸면 `API_KEYS`에 등록한 토큰만 통과하며, 등록한 토큰은 두 모드 모두에서 그 제작자로 인식됩니다.
**OpenAI 키나 Comfy 키를 Authorize에 넣지 마세요.** 별도 회원가입·로그인·토큰 발급 API는 없고, AI 제공자 키는 서버 환경변수로만 관리합니다.

공개 독자·그림 파일 API를 제외한 모든 업무 API는 인증이 필요합니다. 다른 제작자의 자료, 삭제한 자료,
없는 자료는 제작자 조회에서 동일하게 `404 not_found`가 됩니다. 목록은 현재 토큰의 자료만 반환합니다.

각 API를 펼치면 **호출 시점, 요청 방법, 응답 해석, 저장 영향, 다음 호출, 오류별 대응**이 나옵니다.
`Try it out → Execute`는 설명 보기용 시뮬레이션이 아니라 실제 서버 요청입니다.
예제의 `project-example`, `card-1`, `sentence-1`, `publication-example`, 검토 항목 key는
실제 데이터 ID가 아니므로 **본인이 생성·조회한 응답의 ID와 버전으로 교체**하세요. 예제 사건은 모두 가상입니다.

## 저장 버전과 원문 근거

- 문서 `PUT`에는 직전 응답의 `saveRevision`, 사건 구조 `PUT`에는 직전 응답의 `revision`을 그대로 보냅니다.
  `409 version_conflict`가 나면 최신 데이터를 다시 조회하고 사용자 변경을 합친 뒤 저장합니다. 같은 오래된 본문을 반복 전송하지 마세요.
- `contentRevision`은 독자가 보는 내용의 버전이고, 저장마다 증가하는 `saveRevision`과 다릅니다.
  원문 근거나 대조 표시만 수정하면 contentRevision이 같을 수 있어도 현재 검토 완료는 해제됩니다.
- `basedOnStructureRevision`·`basedOnSettingsRevision`은 초안 생성의 기준 버전입니다. 문서 저장 요청에서 임의로 갱신할 수 없습니다.
- `anchors[].start/end`는 **문단 text 기준 JavaScript UTF-16 인덱스**이며 시작 포함·끝 미포함입니다.
  예: `😀원고`의 `원고`는 start=2, end=4입니다. 바이트 위치·PDF 좌표·문서 전체 위치가 아닙니다.
- 실제 AI가 만든 구조·초안의 근거는 모델이 원문에서 복사한 인용문을 서버가 문단 안에서 찾아 위치로 바꿉니다.
  찾지 못한 인용은 버리고, 구조 항목에는 `anchor-unresolved` 확인 표시를 답니다. 문장은 근거 없는 상태로 남아 점검 대상이 됩니다.

## 실제 AI, 데모, 그림 생성의 차이

| 설정 / 요청 | 실제 동작 |
| --- | --- |
| `AI_PROVIDER=demo` | 입력 원문의 명시된 표제를 단순 분류하고 초안에는 원문을 복사합니다. 실제 쉬운 글 생성이 아닙니다. |
| `AI_PROVIDER=openai` + OpenAI 키·모델 | 자료 분석, 쉬운 글 초안, 문장·용어 보조에서 실제 OpenAI를 호출합니다. |
| 실제 AI 모드 + 그림 요청 | 기본 library 모드의 인물은 GPT가 10종 중 선택한 얼굴 크롭입니다(Comfy 호출 없음). 장면은 Comfy 키가 필요하며 인물 원본 레퍼런스 또는 별도 분위기 샘플을 Qwen-Image-Edit-2511 (FP8, 40 steps, CFG 4)에 전달합니다. stock 참조에는 별도 샘플을 섞지 않습니다. 기본 정사각형은 768×768입니다. |
| 데모의 그림 후보 보기 | 이 자료에 업로드한 그림만 반환합니다. 없으면 candidates는 빈 배열입니다. |

텍스트 생성은 동기 응답입니다. `200/201` 성공 응답은 생성·검증·저장이 필요한 작업을 마친 결과이며
작업 접수 ID나 스트리밍 토큰이 아닙니다. 외부 AI 실패를 데모 결과로 바꿔 성공 처리하지 않습니다.
새 자료 분석이 실패하면 프로젝트를 만들지 않고, 초안 생성이 실패하면 기존 문서를 보존합니다.
기존 데모 자료는 서버를 실제 AI 모드로 바꿔도 자동 재분석되지 않습니다.

그림 API는 작업을 DB에 보관합니다. 동일한 카드 내용은 완성된 그림 또는 진행 중 작업을 재사용합니다.
Comfy 완료를 90초간 기다려도 끝나지 않으면 `503 image_in_progress`를 반환하며 같은 요청으로 이어서 확인합니다.
이 90초는 OpenAI 장면 설명 생성·사전 점검·파일 다운로드 시간을 포함한 전체 HTTP 제한 시간이 아닙니다.
Comfy 접수 결과가 불확실하거나 작업이 실패·만료되면 자동으로 새 유료 작업을 제출하지 않습니다.
생성 그림 파일은 서버가 PNG로 보관하고 `/api/studio/assets/{id}` 주소로 내주므로 제공자의 임시 URL 만료에 영향을 받지 않습니다.

옵트인 `STUDIO_SCENE_MODE=storyboard4` 실험은 prepare-images의 장면 최대 4개를
Qwen Edit 40 steps·CFG 4의 1024×1024 시트로 생성해 각각 512×512로 자릅니다.
기준 인물은 묶음 전체 1~3명이고 시트와 모든 크롭이 자료 용량 한도에 포함됩니다.
진행 중 일괄 요청은 같은 응답 계약을 사용하며 completed가 최대 4씩 증가합니다.
실험의 Comfy 조회 대기는 최대 45초이고 전체 HTTP 제한이 아닙니다.
설정을 single로 되돌려도 이미 예약한 묶음은 중복 유료 제출 없이 이어 확인하며 새 묶음만 기존 경로를 사용합니다.
수동 assist/images는 실험 설정과 관계없이 기존 카드별 후보 경로입니다. 컷 경계·외형·의미는 제작자가 직접 검토합니다.

실제 AI를 호출하면 관련 원문·사건 구조·편집 텍스트와 레퍼런스 픽셀이 OpenAI로 전달됩니다. 장면 생성 시 설명과 레퍼런스 이미지가 Comfy로 전달됩니다.
그림 파일 URL은 인증 없이 조회됩니다. 미공개 문서의 접근 통제와 다르며 공개 해제가 그림 URL 회수는 아닙니다.
그림과 대체텍스트는 생성 초안이며 제작자의 의미 대조가 필요합니다. 검토 API는 의미를 판정하는 AI가 아니라 규칙 점검입니다.

## 오류·제한·기타 경로

업무 오류는 `{ "detail": { "code": "version_conflict", "message": "…" } }` 형식입니다.
필수 필드·타입·열거값 등 요청 검증 오류는 `422`이고 `detail`이 오류 위치와 메시지의 **배열**일 수 있습니다.
프론트는 HTTP 상태와 `detail.code`로 처리하되, 422의 두 응답 형식을 모두 고려해야 합니다.
원본 제공자 오류·API 키는 응답으로 노출하지 않습니다. 개별 API의 Responses에서 발생 조건과 예시를 확인하세요.

텍스트 100~100,000자, PDF 4.5MB·100쪽·추출 텍스트 150,000자·문단 300개, 그림 업로드 2MB,
자료당 그림 100개·그림 파일 합계 12MiB, 전체 HTTP 요청 본문 5MB 제한이 있습니다.
문서는 전체 카드 300개·문장 1,500개를 넘을 수 없습니다. 스캔 PDF의 OCR은 제공하지 않습니다.

`/health`는 상태 확인 전용이며 Swagger 목록에서 제외합니다. `ai_provider`는 선택한 서버 모드이고
외부 키의 실시간 유효성 검사 결과는 아닙니다. PDF 출력은 FE의 인쇄 화면을 사용합니다.
기존 `/api/v1`, 별도 영상·그림 작업 API, `/share` 경로는 제공하지 않습니다.
"""

ERRORS = {
    "storyboard_cards_invalid": (422, "4컷 생성 대상이 올바르지 않아요.", "서로 다른 장면 카드 1~4개를 확인합니다."),
    "storyboard_plan_invalid": (502, "4컷 설계의 카드 순서가 달라요.", "Comfy 제출 전 중단합니다. 저장된 카드 순서와 GPT 구조화 출력을 확인합니다."),
    "storyboard_size_invalid": (502, "4컷 원본 크기가 제출한 워크플로와 달라요.", "카드 적용을 중단합니다. 원격 생성 작업과 워크플로를 확인하고 같은 작업을 새 유료 작업으로 대체하지 않습니다."),
    "character_locked": (409, "등장인물 기준 그림은 고정돼 있어요.", "인물 그림의 교체·삭제와 인물 카드 제거는 불가합니다. 문장과 장면 그림, 대체텍스트는 편집할 수 있습니다. 초안 재생성도 기존 인물 그림을 유지합니다."),
    "character_library_unavailable": (503, "고정 캐릭터셋을 읽을 수 없어요.", "서버의 번들 캐릭터 자산과 manifest를 확인합니다. 데모나 새 얼굴 생성으로 대체하지 않습니다."),
    "character_library_changed": (409, "고정 캐릭터셋 버전이 바뀌었어요.", "이 자료에 배정된 원본 캐릭터 자산을 복원합니다. 기존 배정을 바꾸지 않습니다."),
    "character_selection_invalid": (422, "캐릭터 배정 결과가 올바르지 않아요.", "자료의 등장인물을 확인합니다. 잘못된 배정을 적용하지 않고 동일 요청에서 GPT 선택을 자동 반복하지 않습니다."),
    "unauthorized": (401, "유효한 Bearer 토큰이 필요합니다.", "토큰이 없거나 형식이 맞지 않습니다(익명 모드: 영문·숫자·._~- 16자 이상). keys 모드에서는 API_KEYS에 등록한 토큰이어야 합니다."),
    "not_found": (404, "자료 또는 요청한 항목을 찾을 수 없어요.", "ID·소유권·삭제 여부를 확인합니다. 문서 API는 초안 생성 전에도 404입니다."),
    "version_conflict": (409, "다른 편집이 저장됐어요. 새로고침 후 다시 시도해 주세요.", "최신 자료를 GET하고 변경 내용을 합친 뒤 최신 버전으로 다시 저장합니다."),
    "request_too_large": (413, "요청은 최대 5MB입니다.", "HTTP 요청 본문 전체를 줄입니다. 파일 자체 한도는 이보다 작을 수 있습니다."),
    "invalid_input": (422, "입력 내용을 확인해 주세요.", "빈 값·문자 수와 필드 형식을 확인합니다."),
    "invalid_settings": (422, "제작 설정을 확인해 주세요.", "multipart settings가 세 설정 필드를 포함한 올바른 JSON 문자열인지 확인합니다."),
    "invalid_project": (422, "자료 ID가 다릅니다.", "본문 projectId를 경로 project_id와 일치시킵니다."),
    "invalid_anchor": (422, "원문 근거 범위를 확인해 주세요.", "같은 자료의 문단 ID와 UTF-16 범위를 사용합니다. 이모지 중간 경계는 허용하지 않습니다."),
    "duplicate_id": (422, "중복된 항목 ID가 있어요.", "문서 내 각 항목 그룹 또는 구조 전체의 ID 중복을 제거합니다."),
    "invalid_party": (422, "당사자 참조를 확인해 주세요.", "구조 parties 또는 문서 partyNames에 참조하는 당사자가 있는지 확인합니다."),
    "invalid_claim": (422, "판단에 연결된 주장을 확인해 주세요.", "findings[].claimIds에 현재 구조의 claims[].id만 연결합니다."),
    "invalid_image": (422, "그림을 읽거나 연결하지 못했어요.", "유효한 이미지 파일인지, 이 프로젝트에서 생성·업로드한 src와 문서 imageId를 사용하는지 확인합니다."),
    "document_too_large": (413, "카드 300개, 문장 1,500개까지 저장할 수 있어요.", "문서 전체 카드·문장 개수를 줄입니다."),
    "file_too_large": (413, "PDF는 최대 4.5MB입니다.", "4.5MB 이하의 PDF로 나눠 올립니다."),
    "too_many_pages": (413, "PDF는 최대 100쪽입니다.", "100쪽 이하로 나눕니다."),
    "too_many_paragraphs": (413, "문단 300개까지 지원합니다.", "원문을 여러 자료로 나눕니다."),
    "text_too_large": (413, "추출 텍스트는 150,000자까지 지원합니다.", "PDF를 나누거나 추출 텍스트 양을 줄입니다. 붙여넣기 입력의 자체 한도는 100,000자입니다."),
    "page_too_large": (413, "페이지 내용이 너무 큽니다.", "복잡한 PDF 페이지를 단순화합니다. 압축 해제된 페이지 스트림은 10MiB를 제한합니다."),
    "invalid_pdf": (422, "PDF를 읽지 못했습니다.", "유효하고 손상되지 않은 PDF인지 확인합니다."),
    "encrypted_pdf": (422, "암호화된 PDF는 해제 후 올려주세요.", "원본 PDF의 암호를 해제한 뒤 업로드합니다."),
    "ocr_required": (422, "읽을 수 있는 텍스트가 없습니다.", "별도 OCR을 적용하거나 판결문 텍스트를 붙여넣습니다."),
    "image_too_large": (413, "그림의 용량 또는 해상도 제한을 넘었어요.", "업로드는 2MiB, 생성 파일은 5MiB, 래스터 해상도는 1,600만 화소 이하로 준비합니다."),
    "image_limit": (413, "자료의 그림 개수 또는 용량 한도를 넘었어요.", "자료당 보관 그림 100개·보관 그림 바이너리 합계 12MiB 제한입니다. 문서에서 그림 연결만 빼도 보관 자산은 삭제되지 않습니다."),
    "image_too_complex": (413, "SVG가 너무 복잡해요.", "SVG 노드 10,000개·중첩 64단계 이하로 줄이거나 PNG/JPEG를 사용합니다."),
    "unsafe_svg": (422, "외부 참조·스타일·스크립트가 없는 SVG를 사용해 주세요.", "스크립트·외부 참조·HTML 등이 없는 SVG 또는 PNG/JPEG를 올립니다."),
    "unsupported_image": (422, "PNG, JPEG, WebP만 지원합니다.", "래스터 이미지를 지원 형식으로 변환합니다. SVG 업로드는 별도 안전성 검사를 적용합니다."),
    "ai_not_configured": (503, "이 작업은 실제 AI 설정이 필요해요.", "서버를 AI_PROVIDER=openai로 실행하고 OpenAI 키·모델을 설정합니다."),
    "ai_input_too_large": (413, "AI 입력이 너무 큽니다.", "원문·구조·입력 등을 합친 직렬화 JSON이 200,000자를 넘지 않도록 자료를 나눕니다."),
    "ai_provider_error": (502, "OpenAI API 호출 또는 응답 검증에 실패했습니다.", "서버의 키·모델·권한을 확인합니다. 기존 문서는 성공 응답 전까지 유지됩니다."),
    "ai_incomplete_response": (502, "GPT 응답이 완료되지 않았습니다.", "모델 출력 한도를 확인하거나 자료를 줄입니다."),
    "ai_refusal": (502, "GPT가 요청의 처리를 거절했습니다.", "원문을 확인하고 직접 편집합니다."),
    "ai_rate_limited": (503, "OpenAI API 사용 한도 또는 요청 제한에 도달했습니다.", "OpenAI 계정의 API 결제·사용 한도를 확인한 뒤 재시도합니다."),
    "ai_timeout": (504, "GPT 응답 시간이 초과되었습니다.", "기존 데이터를 조회해 상태를 확인한 뒤 재시도합니다. 제공자 처리·과금 여부와 로컬 저장 성공은 별개입니다."),
    "comfy_not_configured": (503, "서버의 COMFY_CLOUD_API_KEY를 설정하세요.", "백엔드 환경변수에 Comfy Cloud 키를 넣고 서버를 재시작합니다."),
    "comfy_auth_error": (503, "Comfy 키 또는 구독 권한을 확인해 주세요.", "Comfy Cloud 키와 계정 실행 권한을 확인합니다."),
    "comfy_insufficient_credits": (503, "Comfy 생성 크레딧이 부족해요.", "Comfy 계정의 크레딧을 확인합니다. 명확한 접수 거절은 기존 준비 작업으로 재시도할 수 있습니다."),
    "comfy_rate_limited": (503, "Comfy 요청이 많아요.", "잠시 후 같은 카드 요청으로 상태를 확인합니다."),
    "comfy_workflow_unavailable": (503, "Comfy에서 그림 생성 모델을 사용할 수 없어요.", "관리자가 고정 워크플로와 Cloud 모델 목록을 확인합니다."),
    "image_in_progress": (503, "그림을 생성하고 있어요. 잠시 후 다시 시도하면 같은 작업을 확인해요.", "완료 실패가 아닙니다. 잠시 기다린 뒤 같은 cardId로 재요청하면 진행 중 작업을 재사용합니다."),
    "image_submission_unknown": (503, "Comfy 접수 결과를 확인하지 못했어요. 중복 생성을 막기 위해 멈췄어요.", "자동 재제출하지 않습니다. 관리자가 studio_image_jobs와 Comfy 작업 목록을 대조해야 합니다."),
    "comfy_connection_or_response_error": (503, "Comfy 그림 응답을 확인하지 못했어요.", "같은 요청으로 상태를 확인합니다. 접수 자체가 불확실했다면 이후 image_submission_unknown으로 멈출 수 있습니다."),
    "comfy_invalid_response": (503, "Comfy 그림 응답을 확인하지 못했어요.", "관리자가 응답 형식·작업 ID·자산 메타데이터를 확인합니다. 새 유료 작업으로 자동 대체하지 않습니다."),
    "comfy_asset_host_not_allowed": (503, "그림은 생성됐지만 저장소 주소가 허용되지 않았어요.", "관리자가 실제 자산 호스트를 검증하고 COMFY_ASSET_ALLOWED_HOSTS에 등록합니다. 이후 같은 작업을 이어받습니다."),
    "comfy_download_failed": (503, "Comfy 그림 파일을 내려받지 못했어요.", "다시 요청하면 자산 메타데이터의 새 서명 URL로 다운로드를 재시도합니다."),
    "comfy_invalid_workflow": (503, "Comfy 그림 응답을 확인하지 못했어요.", "관리자가 고정 워크플로의 노드·입력·모델을 확인합니다. 명확한 접수 거절이면 준비 작업을 보존합니다."),
    "comfy_upstream_error": (503, "Comfy 그림 응답을 확인하지 못했어요.", "제공자 오류입니다. 접수 결과가 불확실하면 중복 제출하지 않고 관리자 확인이 필요합니다."),
    "comfy_not_found": (503, "Comfy 그림 응답을 확인하지 못했어요.", "제공자 작업·자산이 없거나 만료됐는지 확인합니다. 백엔드 자료 not_found와 구분합니다."),
    "comfy_submission_unknown": (503, "Comfy 그림 응답을 확인하지 못했어요.", "제공자가 같은 접수 키의 재사용을 거절했습니다. 기존 작업을 조회하고 새 유료 작업으로 대체하지 않습니다."),
    "comfy_invalid_url": (503, "Comfy 그림 응답을 확인하지 못했어요.", "제공자 호출 URL이 허용된 Comfy Cloud 주소인지 관리자가 확인합니다."),
    "comfy_invalid_node_schema": (503, "Comfy 그림 응답을 확인하지 못했어요.", "제공자의 노드 정의와 서버 사전 점검 코드의 호환성을 확인합니다."),
    "comfy_image_too_large": (503, "Comfy 그림 응답을 확인하지 못했어요.", "생성 파일 다운로드가 5MiB 제한을 넘었습니다. 관리자가 생성 결과를 확인합니다."),
    "image_missing_output": (502, "Comfy 작업에 완성된 그림이 없어요.", "관리자가 해당 Comfy 작업의 출력과 로그를 확인합니다."),
    "image_generation_failed": (502, "Comfy 그림 생성이 완료되지 않았어요.", "작업이 failed·canceled·expired 상태입니다. 자동 새 생성 없이 관리자 확인이 필요합니다."),
    "image_card_changed": (409, "생성 중 카드 내용이 바뀌었어요.", "현재 문서를 다시 조회하고 새 내용으로 그림을 요청합니다. 새 내용은 새 생성 작업이 될 수 있습니다."),
    "character_reference_unclear": (422, "기준 그림에서 한 사람을 특정하기 어려워요.", "한 사람이 있는 그림을 등장인물 카드에 적용하고 저장합니다. 얼굴 가시성만으로 후보를 제외하지 않습니다."),
    "portrait_composition_invalid": (422, "등장인물 그림이 한 명·빈 흰 배경 조건을 통과하지 못했어요.", "구도 검사에서 거부된 초상은 저장·적용·고정하지 않습니다. 같은 요청은 거부 결과를 재사용하고 자동 유료 재생성하지 않습니다. 이미 고정된 인물은 유지합니다. AI 검사로 정확성을 보장하지 않습니다."),
    "character_reference_invalid": (422, "등장인물 기준 그림을 사용할 수 없어요.", "등장인물 카드에 이 자료의 PNG·JPEG·WebP 그림을 적용하고 저장합니다. SVG 기준 그림, 사라진 파일, 다른 자료의 자산은 사용할 수 없습니다."),
    "character_reference_ambiguous": (422, "같은 등장인물에 서로 다른 그림이 연결돼 있어요.", "동일 partyId의 등장인물 카드가 여러 개라면 동일한 기준 그림으로 맞춥니다."),
    "character_reference_limit": (422, "프로젝트 기준 그림은 최대 6개, Qwen Edit 장면에 전달하는 기준 그림은 최대 3개입니다.", "장면에 등장할 인물을 명확히 적거나 카드를 나눠 참조를 3개 이하로 조정합니다. 관련 참조를 임의로 잘라 보내지 않습니다."),
    "review_required": (409, "현재 내용을 검토해 주세요.", "현재 문서를 점검하고 필요한 확인을 마칩니다. 공개할 때는 reviewed=true인 게시본을 선택합니다."),
    "review_incomplete": (409, "확인이 필요한 항목이 남아 있어요.", "현재 점검의 미처리 required 항목을 확인한 뒤 검토를 완료합니다."),
    "checklist_incomplete": (422, "최종 확인 항목을 모두 체크해 주세요.", "그림 설정에 맞는 체크리스트를 중복 없이 정확히 보냅니다."),
    "demo_only": (403, "운영 서버에서는 데모 초기화를 사용할 수 없어요.", "APP_ENV=production이거나 AI_PROVIDER가 demo가 아니면 초기화할 수 없습니다."),
}
AI_ERRORS = "ai_input_too_large ai_provider_error ai_incomplete_response ai_refusal ai_rate_limited ai_timeout"
STRUCTURE_ERRORS = "invalid_anchor duplicate_id invalid_party invalid_claim"
DOCUMENT_ERRORS = "invalid_anchor duplicate_id invalid_party invalid_image document_too_large"
OPS = {}


def describe(name, summary, when, request, response, effect, next_step, model, example, *, errors="", body=None, alternatives=None):
    OPS[name] = dict(summary=summary, description=(
        f"### 프론트 호출 시점\n\n{when}\n\n### 요청 방법\n\n{request}\n\n"
        f"### 응답 해석\n\n{response}\n\n### 저장·버전·외부 호출 영향\n\n{effect}\n\n"
        f"### 다음 동작\n\n{next_step}"), model=model, example=example,
        errors=errors.split(), body=body, alternatives=alternatives or {})


describe("listing", "자료 목록 — 현재 제작자의 작업함 조회",
    "작업함에 들어가거나 자료 생성·삭제·제목 변경 후 목록을 새로고침할 때 호출합니다.",
    "요청 본문과 검색·페이지네이션 파라미터는 없습니다. Authorize에 등록한 제작자 토큰으로 조회합니다.",
    "프로젝트 배열을 updatedAt 내림차순으로 반환합니다. 자료가 없으면 오류 대신 빈 배열 []입니다. 각 원소의 document는 본문이 아닌 요약이며 초안 생성 전에는 null입니다.",
    "현재 작성자가 소유하고 삭제되지 않은 자료만 읽습니다. DB를 수정하거나 AI를 호출하지 않습니다.",
    "선택한 항목의 id로 자료·원문·구조·문서 API를 조회하세요.", array(ref("StudioProject")), [ex.DRAFT_PROJECT], alternatives={"empty": ("자료가 없는 작업함", [])})
describe("create_text", "텍스트 자료 생성 — 원문 분석 후 새 프로젝트 저장",
    "새 자료 만들기에서 판결문을 붙여넣고 제작을 시작할 때 호출합니다.",
    "application/json으로 text와 settings의 세 필드를 모두 전달합니다. 공백 제거 후 최소 100자, 입력 전체 최대 100,000자이며 문단은 300개 이하입니다.",
    "성공 시 201과 project 객체 자체를 반환합니다. {project: ...} 래퍼가 아니며 이 단계의 document는 null입니다. 원문과 분석 구조는 이미 저장돼 있습니다.",
    "실제 AI 모드에서는 OpenAI로 사건 구조를 분석하고 검증한 뒤 새 자료를 저장합니다. 데모는 표제 분류·원문 복사만 수행합니다. 분석 실패 시 프로젝트가 만들어지지 않습니다. 반복 성공 호출은 각각 새 자료를 만듭니다.",
    "응답 id를 경로에 넣어 GET source·structure로 대조한 뒤 POST document/generate를 호출하세요.", ref("StudioProject"), ex.PROJECT,
    errors="invalid_input too_many_paragraphs ocr_required " + STRUCTURE_ERRORS + " " + AI_ERRORS, body={"text": ex.TEXT, "settings": ex.SETTINGS})
describe("create_pdf", "PDF 자료 생성 — 텍스트 추출·분석·원본 보관",
    "새 자료 만들기에서 텍스트를 추출할 수 있는 판결문 PDF를 업로드할 때 호출합니다.",
    "multipart/form-data를 사용합니다. file은 PDF 바이너리, settings는 JSON 객체가 아닌 JSON 문자열입니다. 예: `" + json.dumps(ex.SETTINGS, ensure_ascii=False) + "`. 브라우저 FormData 사용 시 Content-Type 경계를 직접 지정하지 마세요. PDF 20MiB·100쪽·문단 300개 제한입니다.",
    "201과 project 객체를 반환합니다. source.kind=pdf, fileName·byteSize가 포함되며 초안은 아직 없습니다. 추출된 source의 page는 원본 PDF의 1부터 시작하는 쪽 번호입니다.",
    "텍스트 추출과 AI 분석을 마친 뒤 자료·원본 PDF를 보관합니다. 스캔 PDF OCR은 수행하지 않습니다. 암호·손상·텍스트 없음·분석 실패 시 새 자료를 저장하지 않습니다.",
    "GET source에서 추출 결과와 페이지를 확인하고 GET structure로 사건 구조를 대조하세요. 암호 해제/OCR 등은 원본 파일에 별도로 처리해야 합니다.", ref("StudioProject"), {**ex.PROJECT, "source": {"kind": "pdf", "fileName": "fictional-judgment.pdf", "byteSize": 15360, "charCount": len(ex.TEXT)}},
    errors="invalid_settings file_too_large text_too_large too_many_pages too_many_paragraphs page_too_large invalid_pdf encrypted_pdf ocr_required " + STRUCTURE_ERRORS + " " + AI_ERRORS)
describe("get_project", "자료 상태 조회 — 제목·설정·진행률·공개 상태",
    "자료 상세 화면이나 제작 단계 표시줄을 불러올 때 호출합니다.", "경로 project_id에 자료 생성/목록 응답의 id를 넣습니다. 요청 본문은 없습니다.",
    "프로젝트 객체를 반환합니다. document·review·publication은 각각 문서 진행률, 검토 진행 상태, 최신/공개 게시본의 요약입니다. null은 아직 해당 단계가 없거나 해제된 상태를 나타냅니다.",
    "저장 상태만 조회하고 AI·그림 생성은 실행하지 않습니다. 다른 제작자의 자료와 삭제한 자료는 404입니다.",
    "본문이 필요하면 source·structure·document를 각각 조회하세요.", ref("StudioProject"), ex.DRAFT_PROJECT, errors="not_found")
describe("rename", "자료 제목 변경 — 작업함 이름만 수정",
    "상단 자료 이름이나 작업함 제목을 편집할 때 호출합니다.", "JSON title을 1~150자로 보냅니다. 앞뒤 공백은 제거하고 공백뿐인 제목은 거절합니다.",
    "변경된 project 객체를 반환하며 updatedAt도 갱신됩니다.", "작업함 제목만 바뀝니다. document.title, 문서 saveRevision/contentRevision, 기존 검토 완료와 게시본 내용은 바꾸지 않습니다. 동시 저장 충돌 시 409가 날 수 있습니다.",
    "반환한 프로젝트로 작업함과 상단 제목을 갱신합니다. 독자용 문서 제목은 PUT document로 수정합니다.", ref("StudioProject"), {**ex.DRAFT_PROJECT, "title": "보증금 반환 설명자료"}, errors="not_found invalid_input version_conflict", body={"title": "보증금 반환 설명자료"})
describe("settings", "제작 설정 저장 — 문체·호칭·그림 표시 변경",
    "제작 설정에서 문체, 당사자 부르는 방식, 그림 포함 여부를 변경할 때 호출합니다.", "Settings의 tone·naming·illustrations를 모두 보냅니다. 부분 PATCH가 아닙니다. 별도의 expectedRevision 입력은 없습니다.",
    "갱신된 project를 반환합니다. 실제 변경 시 settingsRevision이 증가하며 naming 변경은 structureRevision도 증가시킵니다. 동일한 설정을 보내면 그대로 반환합니다.",
    "설정 변경 시 기존 문서가 있으면 saveRevision/contentRevision이 증가하고 현재 검토를 해제합니다. naming은 구조의 표시 이름을 바꾸지만 기존 문장·문서 partyNames를 자동 재작성하지 않습니다. with는 그림 표시 설정이며 편집 진입 시 prepare-images로 자동 일괄 생성합니다.",
    "문서가 열려 있으면 최신 project·structure·document를 다시 읽으세요. 새 기준을 기존 글에 직접 반영하거나 필요할 때 초안을 재생성하고 재검토합니다.", ref("StudioProject"), {**ex.DRAFT_PROJECT, "settingsRevision": 1}, errors="not_found version_conflict", body=ex.SETTINGS)
describe("remove", "자료 삭제 — 작업함 제외 및 공개 읽기 중단",
    "제작자가 해당 자료를 삭제할 때 호출합니다.", "project_id만 지정합니다. 요청 본문은 없습니다.", "성공 응답은 204이며 JSON 본문이 없습니다. 응답에 response.json()을 호출하지 마세요.",
    "soft delete로 자료를 숨기고 버전을 증가시킵니다. 원본 DB를 즉시 물리 삭제하지 않으며 공개 독자 조회도 unavailable이 됩니다. 현재 API에는 복구 기능이 없습니다. 이미 삭제한 자료에 반복 호출하면 404입니다.",
    "작업함에서 해당 항목을 제거하거나 GET projects로 목록을 새로고침합니다.", None, None, errors="not_found")
describe("source", "원문 조회 — 문단·페이지·근거 인덱스 기준",
    "원문 판결문 패널을 표시하거나 근거 연결·대조 기능을 사용할 때 호출합니다.", "자료의 project_id를 지정합니다. PDF 원본 다운로드 요청이 아니라 추출된 원문 JSON 조회입니다.",
    "{projectId, paragraphs}를 반환합니다. 문단별 id·block·kind·level·page·text를 포함합니다. 텍스트 입력의 page는 null, PDF는 1부터 시작합니다.",
    "원문을 읽기만 하며 재추출·재분석하지 않습니다. paragraph.text를 가공하면 근거 start/end의 기준이 달라지므로 그대로 보관하세요.",
    "구조/문서의 anchors[].paragraphId를 연결합니다. start/end는 문단 text의 UTF-16 인덱스이고 끝은 포함하지 않습니다.", ref("StudioSource"), ex.SOURCE, errors="not_found")
describe("structure", "사건 구조 조회 — 개요·당사자·주장·판단·결정",
    "사건 구조 확인 화면에 들어가거나 구조 저장 충돌 후 최신 상태가 필요할 때 호출합니다.", "project_id를 지정하며 요청 본문은 없습니다.",
    "CaseStructure 객체 전체를 반환합니다. overview, parties, keyFacts, claims, findings, decisions를 분리하고 각 항목의 anchors와 flags를 보존합니다. 누락 메타데이터는 빈 문자열일 수 있습니다.",
    "저장된 분석 결과를 읽습니다. 서버를 demo에서 openai로 바꿔도 이 요청은 기존 자료를 재분석하지 않습니다. demo-unclassified 플래그는 저장된 데모 분류 결과를 뜻합니다.",
    "원문과 대조해 수정한 뒤 동일 projectId·최신 revision을 포함한 전체 객체를 PUT structure로 저장합니다.", ref("CaseStructure"), ex.STRUCTURE, errors="not_found")
describe("save_structure", "사건 구조 저장 — 전체 구조 교체 및 참조 검증",
    "사건 구조 화면에서 당사자·수치·주장·판단·결정의 수정을 저장할 때 호출합니다.", "직전 GET 구조 전체를 편집해 보냅니다. projectId는 경로와 같아야 하고 revision은 직전 응답 값을 유지합니다. 모든 항목 ID는 구조 전체에서 유일해야 하며 partyId·claimIds·anchors 참조가 유효해야 합니다.",
    "{structure, project}를 반환합니다. 실제 구조 내용이 바뀌면 revision이 1 증가합니다. flags만 바뀌면 revision을 증가시키지 않습니다.",
    "내용 변경은 기존 문서의 saveRevision/contentRevision을 증가시키고 검토를 해제합니다. 기존 글을 새 구조로 자동 다시 쓰지는 않습니다. 유효성 검증이나 저장 충돌이 실패하면 해당 요청의 구조는 저장하지 않습니다.",
    "응답의 새 버전을 반영하고 문서도 다시 조회하세요. 초안 기준 버전과 현재 구조가 다르면 직접 반영하거나 명시적으로 재생성 후 검토합니다.", ref("StudioStructureResult"), {"structure": ex.STRUCTURE, "project": ex.PROJECT}, errors="not_found version_conflict invalid_project " + STRUCTURE_ERRORS, body=ex.STRUCTURE)
describe("generate", "쉬운 글 초안 생성 — 완료된 문서와 프로젝트 반환",
    "사건 구조 확인 후 처음 초안을 만들거나 기존 초안을 명시적으로 다시 만들 때 호출합니다.", "project_id만 보내며 본문은 없습니다. 현재 DB에 저장된 원문·사건 구조·설정을 기준으로 생성하므로 구조 수정을 먼저 저장하세요.",
    "200과 {document, project}를 반환합니다. document에는 네 구획, 카드별 문장, 근거, 당사자 이름, 용어 풀이 등이 들어갑니다. 작업 ID가 아니라 완료된 문서입니다. 첫 초안의 images는 빈 배열이고 imageId는 null입니다. 재생성 시 고정된 인물 그림은 유지합니다.",
    "OpenAI 모드는 실제 생성, demo는 원문 복사입니다. 서버는 모든 새 문장에 origin=ai-draft·verified=false를 강제합니다. 처음 버전은 0이고 재생성은 기존 편집 문서를 교체하며 saveRevision/contentRevision을 각각 증가시키고 검토를 해제합니다. 실패하면 기존 문서를 보존합니다.",
    "반환한 document를 편집기에, project를 진행 상태에 반영하세요. 편집 진입 시 document/prepare-images를 완료까지 이어 호출하면 인물과 장면 그림을 자동 생성·적용합니다. 이후 장면 변경만 assist/images와 PUT document로 처리합니다. 재호출은 새 생성이므로 중복 클릭을 막으세요.", ref("StudioDocumentResult"), {"document": ex.DOCUMENT, "project": ex.DRAFT_PROJECT}, errors="not_found version_conflict character_locked " + DOCUMENT_ERRORS + " " + AI_ERRORS)
describe("prepare_images", "편집 진입 그림 자동 준비 — 캐릭터 선택·얼굴 적용 후 장면 생성",
    "편집 진입 직후 자동으로 호출합니다. 글은 먼저 document/generate로 만듭니다. 기본 STUDIO_SCENE_MODE=single은 요청당 최대 카드 하나를 처리합니다. 옵트인 storyboard4는 장면 최대 4개를 한 시트로 생성·분할합니다.",
    "project_id만 지정하고 본문은 없습니다. document/generate로 글을 먼저 만듭니다.",
    "{document,project,generation:{status,phase,completed,total,currentCardId}}를 반환합니다. running이면 같은 POST를 이어 호출합니다. ready는 전체 적용 완료, skipped는 그림 없음 설정 또는 demo 모드입니다.",
    "기본 STUDIO_CHARACTER_MODE=library에서는 GPT가 고정 캐릭터 10종에서 선택하고 partyId 배정을 저장합니다. 실제 당사자의 외모 재현이 아닙니다. 얼굴 크롭(768×768)을 카드에 자동 적용·고정하며 얼굴을 Comfy로 새로 그리지 않습니다. 장면의 OpenAI 외형 분석과 Comfy Qwen 입력에는 같은 캐릭터의 한 명짜리 기본 포즈 원본을 전달합니다. 4포즈 시트나 얼굴 크롭은 장면 입력으로 보내지 않고 stock 참조에 별도 화풍 샘플을 섞지 않습니다. 10종 후보와 자동 준비 인물 상한 6명·장면 참조 상한 3명은 별개입니다. Qwen Edit 장면은 40 steps·CFG 4, 정사각형 기준 768×768입니다. stock 참조 없는 새 장면은 분위기 샘플 경로입니다.\n\n완료 그림과 기존 적용·고정 인물은 유지합니다. 페이지를 닫으면 추가 카드 요청은 멈추고 재진입 시 진행 중 원격 작업을 조회합니다. 완료 일괄 준비는 장면 삭제·카드 추가만으로 다시 실행되지 않습니다. 새 장면은 assist/images로 만들고 초안 재생성은 고정 인물을 보존한 뒤 새 장면의 일괄 준비를 초기화합니다. 글·장면·대체텍스트는 편집 가능하며 인물 교체·삭제는 서버에서 차단합니다. 앞서 적용된 카드 결과는 이후 오류에도 남습니다. 접수 불확실·실행 실패·만료 작업을 자동 새 유료 작업으로 대체하지 않습니다.\n\ngenerate 호환 모드는 기존 Qwen 초상 생성과 한 명·흰 배경 GPT 검사를 사용합니다. 접수·접수 불확실 구형 작업은 원래 워크플로우로 조회하므로 steps·해상도가 다를 수 있습니다. 외형·상황 일치는 제작자가 직접 확인합니다.",
    "ready/skipped 뒤 최신 document와 project를 캐시에 넣고 편집기를 엽니다. 진행 중에는 phase와 completed/total을 표시하고 편집·자동 저장을 시작하지 않습니다. 오류 시 중단하고 사용자에게 표시합니다. 프론트 취소는 원격 유료 작업 취소가 아니며 다음 진입에 이어받습니다.",
    ref("StudioImagePreparation"), {"document": ex.DOCUMENT, "project": ex.DRAFT_PROJECT, "generation": {"status": "running", "phase": "scenes", "completed": 0, "total": 4, "currentCardId": "card-1"}},
    errors="not_found character_library_unavailable character_library_changed character_selection_invalid version_conflict image_in_progress portrait_composition_invalid character_locked character_reference_limit image_card_changed invalid_party image_limit image_missing_output character_reference_invalid character_reference_ambiguous character_reference_unclear comfy_not_configured comfy_auth_error comfy_insufficient_credits comfy_rate_limited comfy_workflow_unavailable image_submission_unknown comfy_connection_or_response_error comfy_invalid_response comfy_asset_host_not_allowed comfy_download_failed image_generation_failed " + AI_ERRORS)
describe("document", "편집 문서 조회 — 최신 본문과 저장 버전",
    "편집 화면을 열거나 새로고침·저장 충돌 후 최신 문서를 읽을 때 호출합니다.", "project_id를 지정하며 요청 본문은 없습니다. 자료를 만들기만 하고 초안을 생성하지 않았다면 404입니다.",
    "EasyDocument 객체 자체를 반환합니다. project 요약 래퍼는 없습니다. sections는 people→decision→reasons→glossary 순서이며 문장마다 근거·origin·verified가 있습니다.",
    "저장된 문서 조회만 수행합니다. AI를 실행하지 않고 저장 버전이나 검토 상태도 바꾸지 않습니다.",
    "편집 후 이 응답의 saveRevision과 전체 문서를 PUT document에 보냅니다. 프로젝트 상태는 GET project로 별도 조회할 수 있습니다.", ref("EasyDocument"), ex.DOCUMENT, errors="not_found")
describe("save_document", "편집 문서 저장 — 자동 저장·버전 충돌·그림 연결",
    "직접 편집, AI 제안 적용, 그림 선택·업로드 적용, 원문 대조 표시 변경 후 자동 저장할 때 호출합니다.", "EasyDocument 전체를 보냅니다. projectId와 직전 saveRevision을 유지하고 네 구획 및 참조 목록을 모두 포함하세요. 부분 PATCH가 아닙니다. 카드의 imageId는 images[].id와 연결하고 src는 이 프로젝트에 이미 보관된 그림이어야 합니다.",
    "{document, project}를 반환합니다. 성공할 때마다 saveRevision이 증가합니다. contentRevision은 독자용 내용이 바뀐 경우에만 서버가 증가시키며 클라이언트가 보낸 임의 버전은 무시합니다.",
    "저장 시 현재 점검과 검토 완료를 해제합니다. 대조 표시/근거만 바뀌면 contentRevision이 같아도 검토는 다시 해야 합니다. basedOnStructureRevision·basedOnSettingsRevision은 이전 초안의 값을 유지합니다. 이미 공개한 고정 게시본은 바꾸지 않습니다.",
    "반환된 문서·버전을 저장 상태로 교체하세요. 409이면 최신 GET 결과에 사용자 변경을 합쳐 다시 저장합니다. 저장 후 검토 및 새 게시본 생성·공개는 별도로 수행합니다.", ref("StudioDocumentResult"), {"document": {**ex.DOCUMENT, "saveRevision": 1}, "project": {**ex.DRAFT_PROJECT, "document": {**ex.DRAFT_PROJECT["document"], "saveRevision": 1}}}, errors="not_found version_conflict invalid_project character_locked " + DOCUMENT_ERRORS, body=ex.DOCUMENT)

ASSIST_EFFECT = "OpenAI를 호출해 제안만 반환합니다. 문서·카드·문장·용어 목록을 자동으로 저장하거나 검토 완료로 표시하지 않습니다. 데모에서는 ai_not_configured 오류입니다."
describe("simplify", "문장 쉽게 바꾸기 — 의미를 보존한 후보 제안",
    "편집기에서 문장을 선택하고 더 쉽게 바꾸기를 누를 때 호출합니다.", "sentenceId는 이미 저장된 문장 ID, text는 실제 변환할 최신 문장 텍스트(1~10,000자)입니다. 존재하는 문서와 문장이 필요합니다.",
    "{suggestions:[문자열,...]}를 반환합니다. 모델에는 1~3개를 요청하고 계약은 1~5개를 허용합니다. 원문 수치·부정·주체를 보존하도록 요청하지만 제작자 대조가 필요합니다.", ASSIST_EFFECT,
    "사용자가 후보를 선택하면 FE에서 대상 문장에 적용하고 전체 문서를 PUT document로 저장합니다.", ref("Suggestions"), {"suggestions": ["법원은 B씨에게 A씨의 보증금 1,000만 원을 돌려주라고 했어요."]}, errors="not_found ai_not_configured " + AI_ERRORS, body={"sentenceId": "sentence-1", "text": ex.DOCUMENT["sections"][1]["cards"][0]["sentences"][0]["text"]})
describe("split", "문장 나누기 — 한 문장에 한 가지 내용 제안",
    "긴 문장을 여러 문장으로 나누는 도구에서 호출합니다.", "기존 sentenceId와 나눌 text를 전달합니다. 현재 편집 텍스트를 보내되 ID는 저장된 문서에 존재해야 합니다.",
    "{sentences:[문자열,...]} 형태이며 1~20개입니다. 새 문장 ID·anchors·origin은 응답에 포함되지 않습니다.",
    "실제 AI 모드는 OpenAI로 분할을 제안합니다. 데모는 문장부호 뒤 공백/줄바꿈을 기준으로 단순 분리하고 20개를 넘으면 원문 하나를 반환합니다. 두 모드 모두 문서를 자동 수정하지 않습니다.",
    "FE에서 분할 결과의 문장 ID와 원문 근거를 구성해 사용자가 적용한 뒤 PUT document로 저장합니다.", ref("Sentences"), {"sentences": ["계약이 끝났어요.", "A씨는 보증금을 돌려달라고 했어요."]}, errors="not_found " + AI_ERRORS, body={"sentenceId": "sentence-1", "text": "계약이 끝났어요. A씨는 보증금을 돌려달라고 했어요."})
describe("terms", "어려운 용어 찾기 — 풀이가 필요한 단어 후보",
    "편집 문장에서 어려운 말 후보를 찾을 때 호출합니다.", "검색할 text를 1~10,000자로 보냅니다. 특정 sentenceId는 받지 않지만 저장된 초안 문서가 있어야 합니다.",
    "{terms:[문자열,...]}를 반환합니다. 최대 30개이며 후보가 없으면 terms=[]입니다. 용어 설명은 이 응답에 포함되지 않습니다.", ASSIST_EFFECT,
    "선택한 용어를 assist/explain에 보내 설명을 제안받고, 사용자가 적용하면 document.glossary에 넣어 저장합니다.", ref("Terms"), {"terms": ["임대차보증금"]}, errors="not_found ai_not_configured " + AI_ERRORS, body={"text": "임대차보증금을 반환해야 합니다."})
describe("explain", "용어 설명 제안 — 사건 문맥에 맞춘 쉬운 풀이",
    "용어 후보를 선택하거나 사용자가 직접 입력한 용어의 풀이를 만들 때 호출합니다.", "term은 1~100자, context는 주변 문맥(최대 10,000자)입니다. context는 빈 문자열도 가능하며 두 필드 모두 보냅니다. 저장된 문서가 필요합니다.",
    "{explanation: 문자열}을 반환합니다. 현재 사건 문맥에 맞는 쉬운 설명을 요청하며 새 법적 조언을 추가하지 않도록 합니다.", ASSIST_EFFECT,
    "풀이를 대조한 뒤 FE에서 glossary 항목의 id·term·explanation을 구성하고 문서 전체를 저장합니다.", ref("Explanation"), {"explanation": "집을 빌릴 때 맡겨 두는 돈이에요. 이 사건에서는 계약이 끝난 뒤 돌려받을 돈을 말해요."}, errors="not_found ai_not_configured " + AI_ERRORS, body={"term": "임대차보증금", "context": "계약이 끝난 뒤 보증금을 돌려달라고 했습니다."})
describe("image_candidates", "그림 후보 생성·조회 — OpenAI 장면 설명과 Comfy 그림",
    "저장된 장면 카드의 그림 후보를 요청할 때 호출합니다. 인물 얼굴은 prepare-images에서 먼저 자동 적용하며 고정 인물 변경은 불가합니다.", "JSON {cardId}를 보냅니다. 카드 문장·원문 근거·당사자 표시 이름은 저장된 문서에서 읽으므로 자동 저장 완료 후 요청하세요. 프롬프트·워크플로·jobId는 FE에서 보내지 않습니다.",
    "{candidates:[{src,alt,meaning}]}를 반환합니다. 실제 AI의 인물 후보는 기본 모드에서 고정 캐릭터 얼굴이며 장면은 Comfy 결과 한 장입니다. 업로드 자산이 이어집니다. src는 서버에 보관한 /api/studio/assets/{id} 주소입니다. 예제 PNG는 형식 설명용이며 실제 품질 예시가 아닙니다.",
    "기본 library 모드의 인물 후보는 저장한 캐릭터 배정의 얼굴 크롭입니다. 장면은 저장된 문장·원문 근거와 적용된 인물 레퍼런스를 사용합니다. 고정 캐릭터의 경우 같은 캐릭터의 한 명짜리 기본 포즈 원본을 OpenAI 외형 분석과 Qwen 입력에 전달하며 시트 전체나 얼굴 크롭은 보내지 않습니다. 분위기 샘플은 stock 참조 없는 경로에서만 가볍게 참고하고 인물 참조가 3개면 별도 샘플은 없습니다.\n\nOpenAI는 외형을 분석·캐시하고 행동·관계·사물 상태·주장/사실/명령/완료를 구분해 장면을 설계합니다. Qwen-Image-Edit-2511 FP8, 40 steps·CFG 4, 정사각형 기준 768×768로 한 장을 생성합니다. 장면 참조는 최대 3명, 프로젝트 기준 그림은 최대 6명입니다. 결과 외형·상황·의미를 자동 보증하거나 불일치 그림을 자동 유료 보정하지 않습니다.\n\ngenerate 호환 모드의 새 초상은 분위기 샘플을 사용하는 Qwen Edit 생성과 한 명·흰 배경 검사를 거칩니다. 기존 적용 인물 및 접수·접수 불확실 구형 작업은 유지합니다. 그 작업은 원래 steps·해상도로 끝날 수 있습니다.\n\n후보는 자산으로 보관하지만 문서에 자동 적용하지 않습니다(자동 적용은 prepare-images). source=library는 생성 장면에도 쓰는 분류값이며 고정 10종만 뜻하지 않습니다. 동일 문맥 결과·진행 작업을 재사용하고 바뀐 문맥은 새 작업일 수 있습니다. demo는 업로드 후보만 반환합니다. 진행 중은 503 image_in_progress로 같은 요청을 이어 조회합니다. 접수 불확실·실행 실패·만료 작업을 새 유료 작업으로 자동 대체하지 않습니다. 생성 중 문맥 변경은 image_card_changed로 오래된 결과 저장을 막습니다. 브라우저 취소는 Comfy 작업 취소가 아닙니다.",
    "사용자가 고른 후보에 FE 그림 ID와 source=library를 부여하고 document.images에 넣습니다. 대상 card.imageId를 연결한 뒤 PUT document로 저장하세요. alt와 meaning은 실제 그림과 대조합니다. 편집 중 FE가 이전 후보를 캐시했다면 최신 내용 저장 후 새 요청이 실제 전송되도록 새로고침합니다.", ref("StudioImageCandidates"), ex.IMAGE_CANDIDATES,
    errors="not_found character_library_unavailable character_library_changed character_selection_invalid portrait_composition_invalid character_locked image_limit image_too_large invalid_image unsupported_image image_card_changed character_reference_invalid character_reference_ambiguous character_reference_limit character_reference_unclear comfy_not_configured comfy_auth_error comfy_insufficient_credits comfy_rate_limited comfy_workflow_unavailable image_in_progress image_submission_unknown comfy_connection_or_response_error comfy_invalid_response comfy_asset_host_not_allowed comfy_download_failed image_missing_output image_generation_failed " + AI_ERRORS,
    body={"cardId": "card-1"}, alternatives={"demo_empty": ("데모 모드이며 업로드한 그림이 없음", {"candidates": []})})
describe("upload_image", "내 그림 업로드 — 안전한 이미지 보관 후 문서 연결",
    "카드의 내 그림 올리기 도구에서 파일과 설명을 입력할 때 호출합니다.", "multipart/form-data로 file 바이너리와 alt·meaning 텍스트를 전달합니다. file은 필수, alt·meaning은 생략 시 빈 문자열이며 각각 최대 10,000자입니다. 업로드 파일 2MiB 제한, PNG/JPEG/WebP 및 제한된 SVG를 지원합니다.",
    "{image:{id,src,alt,meaning,source:'upload'}}를 반환합니다. 래스터는 PNG로 정제하고 긴 변을 최대 1,600픽셀로 줄입니다. SVG는 외부 참조·스크립트 등을 제한해 보관합니다.",
    "프로젝트 자산과 updatedAt을 저장하지만 카드나 문서를 자동 수정하지 않습니다. AI 비용은 발생하지 않습니다. 자료당 자산 100개와 보관 그림 바이너리 합계 12MiB 제한을 적용하며 문서에서 연결을 해제해도 보관 자산은 삭제하지 않습니다.",
    "반환한 image를 document.images에 추가하고 카드의 imageId를 연결해 PUT document로 저장합니다. 업로드 API는 설명 공란을 허용해도 실제 그림의 대체텍스트를 작성하세요.", ref("StudioImageUploadResult"), {"image": ex.IMAGE}, errors="not_found version_conflict image_limit image_too_large invalid_image unsupported_image unsafe_svg image_too_complex")

describe("latest_review", "최근 점검 조회 — 저장된 검토 결과 또는 null",
    "검토 화면이나 마지막 점검 요약을 불러올 때 호출합니다.", "project_id를 지정합니다. 요청 본문은 없습니다.",
    "최근 ReviewRun 객체를 반환하며 점검 전 또는 문서 저장 등으로 점검이 해제된 상태는 200 null입니다. 빈 배열이나 404가 아닙니다. 프로젝트 자체가 없거나 접근 불가하면 404입니다.",
    "저장 결과를 읽기만 하며 새 점검을 수행하지 않습니다. items[].dismissal이 null이면 아직 문제없음 확인을 하지 않은 항목입니다.",
    "결과가 null이거나 최신 내용 점검이 필요하면 POST review/run을 호출합니다. level=required와 dismissal=null인 항목을 확인해야 검토를 완료할 수 있습니다.", nullable(ref("StudioReviewRun")), ex.REVIEW, errors="not_found", alternatives={"not_checked": ("아직 점검하지 않았거나 점검이 해제됨", None)})
describe("run_review", "문서 점검 실행 — 원문 근거·숫자·문장 길이 규칙 검사",
    "저장된 편집 문서를 검토하거나 편집 후 다시 점검할 때 호출합니다.", "project_id만 전달합니다. 요청 본문은 없으며 초안 문서가 먼저 존재해야 합니다. 편집 중 내용은 PUT document 저장을 마친 뒤 점검합니다.",
    "{run, project}를 반환합니다. run.items에는 원문 근거 누락(no-anchor, required), 원문에 없는 숫자(numbers), 60자를 넘는 문장 또는 한 항목의 여러 문장(long-sentence), 이중부정·용어집 풀이의 카드 내 누락(hard-term), 대명사·앞의 내용 참조(relations)가 들어갑니다. no-anchor 외에는 모두 suggested입니다. required는 검토 완료를 막고 suggested는 권고 항목입니다.",
    "OpenAI나 Comfy를 호출하지 않는 규칙 점검입니다. 이지리드 점검은 정규식과 용어집 풀이의 문자 일치 기반이라 다른 표현의 올바른 풀이에도 경고할 수 있습니다. 누가 말했는지, 주장과 판단의 구분, 그림의 뜻이나 독자의 이해는 판정하지 않으며 제작자가 직접 확인합니다. 문장은 자동 수정하지 않습니다. 점검 결과를 저장하고 기존 최종 검토 완료를 해제합니다. 같은 점검 입력의 확인 기록은 key가 같아 다시 점검해도 유지되고, 이지리드 점검 입력이 바뀌면 해제됩니다.",
    "각 항목의 target 위치를 보여 주고 원문·그림을 대조하세요. 문제가 없다고 판단한 항목은 review/dismiss, 수정이 필요하면 문서 저장 후 재점검합니다.", ref("StudioReviewResult"), {"run": ex.REVIEW, "project": ex.REVIEW_PROJECT}, errors="not_found version_conflict")

HANDLED_REVIEW = deepcopy(ex.REVIEW)
HANDLED_REVIEW["items"][0]["dismissal"] = {"memo": "가상 예제 원문과 직접 비교했습니다.", "at": ex.AT}
HANDLED_PROJECT = deepcopy(ex.REVIEW_PROJECT)
HANDLED_PROJECT["review"]["openRequiredCount"] = 0
describe("dismiss", "점검 항목 확인 — 제작자가 문제없음을 기록",
    "제작자가 해당 점검 항목을 원문·그림과 비교하여 문제없다고 확인했을 때 호출합니다.", "최근 점검 items[].key와 memo를 보냅니다. memo 필드는 필수지만 빈 문자열도 허용하며 최대 2,000자입니다. 예제 key를 그대로 쓰지 말고 실제 점검 응답의 값을 사용하세요.",
    "{run, project}를 반환합니다. 해당 항목 dismissal에 메모·시각이 기록되고 미처리 required 수가 갱신됩니다.",
    "점검 경고를 자동 수정하거나 문장 verified를 바꾸지 않습니다. 제작자의 확인 기록만 저장하고 기존 최종 검토 완료는 해제합니다. 최근 점검이 없으면 review_required, key가 없으면 not_found입니다.",
    "남은 required 항목을 처리한 뒤 review/complete로 최종 체크리스트를 제출합니다. 문장 원문 대조 표시를 바꾸려면 문서를 별도로 저장해야 합니다.", ref("StudioReviewResult"), {"run": HANDLED_REVIEW, "project": HANDLED_PROJECT}, errors="not_found review_required version_conflict", body={"key": "numbers:example", "memo": "가상 예제 원문과 직접 비교했습니다."})
describe("dismiss_all", "점검 항목 일괄 확인 — 여러 항목을 한 번에 문제없음으로 기록",
    "제작자가 남은 점검 항목 여러 개를 한 번에 문제없다고 확인할 때 호출합니다. 화면에 보이는 항목 전체에 씁니다.",
    "최근 점검 items[].key 배열과 공통 memo를 보냅니다. keys는 1개 이상 500개 이하이고 중복은 무시합니다. memo는 빈 문자열도 허용하며 최대 2,000자입니다.",
    "{run, project}를 반환합니다. 보낸 모든 항목의 dismissal에 같은 메모·시각이 기록되고 미처리 required 수가 갱신됩니다.",
    "하나의 저장으로 처리하므로 항목별 dismiss를 연달아 호출할 때 생기는 version_conflict가 없습니다. 기존 최종 검토 완료는 해제합니다. 최근 점검이 없으면 review_required, 하나라도 없는 key가 있으면 not_found이고 아무것도 저장하지 않습니다.",
    "남은 required 항목이 없어지면 review/complete로 최종 체크리스트를 제출합니다. 잘못 확인한 항목은 review/restore로 하나씩 되돌립니다.", ref("StudioReviewResult"), {"run": HANDLED_REVIEW, "project": HANDLED_PROJECT}, errors="not_found review_required version_conflict", body={"keys": ["numbers:example"], "memo": "가상 예제 원문과 직접 비교했습니다."})
describe("restore", "점검 항목 확인 취소 — 다시 미처리 상태로 복원",
    "문제없음으로 확인한 점검 항목을 다시 검토 대상으로 되돌릴 때 호출합니다.", "최근 점검 결과에 존재하는 key만 보냅니다. 요청은 {key}이며 memo를 보내지 않습니다.",
    "{run, project}를 반환하고 해당 dismissal은 null이 됩니다. required 항목이면 미처리 required 수가 다시 증가합니다.",
    "그 항목의 보관 확인 기록을 지우고 최종 검토 완료를 해제합니다. 문서·원문·그림 내용은 바꾸지 않습니다. 이미 미처리여도 같은 상태로 저장될 수 있습니다.",
    "해당 항목을 다시 대조하거나 편집합니다. 필요한 확인을 마친 뒤 review/complete를 다시 호출하세요.", ref("StudioReviewResult"), {"run": ex.REVIEW, "project": ex.REVIEW_PROJECT}, errors="not_found review_required version_conflict", body={"key": "numbers:example"})
describe("complete", "최종 검토 완료 — 필수 항목과 체크리스트 확인",
    "제작자가 현재 문서의 필수 점검 항목을 확인하고 최종 대조를 마쳤을 때 호출합니다.", "JSON checklist를 보냅니다. numbers·relations·claims는 항상 필요하고 illustrations=with이면 images도 필요합니다. 순서는 무관하지만 중복·누락·추가 항목은 거절합니다.",
    "{completion, project}를 반환합니다. completion에는 contentRevision·completedAt·제출 checklist가 들어가며 프로젝트에도 완료한 내용 버전을 기록합니다.",
    "최신 내용 버전의 점검 결과가 있어야 하고 미처리 required 항목이 없어야 합니다. suggested 항목은 완료를 막지 않습니다. 이는 제작자의 최종 확인 기록이며 AI의 정확성 보증이나 자동 공개가 아닙니다.",
    "POST publications로 이 내용의 게시본을 만들고, 공개하려면 이어서 PUT public에 해당 게시본 ID를 지정합니다. 편집·재점검 등은 현재 검토 완료를 해제합니다.", ref("StudioCompletionResult"), {"completion": ex.COMPLETION, "project": ex.COMPLETE_PROJECT}, errors="not_found review_required review_incomplete checklist_incomplete version_conflict", body={"checklist": ["numbers", "relations", "images", "claims"]})
describe("publications", "게시본 목록 — 최신 버전부터 고정 스냅샷 조회",
    "내보내기 화면에서 이전에 만든 게시본을 선택하거나 최신 게시본을 확인할 때 호출합니다.", "project_id만 지정하며 페이지네이션이나 요청 본문은 없습니다.",
    "PublicationSummary 배열을 최신 생성 순으로 반환합니다. 각 항목은 id·version·createdAt·contentRevision·reviewed이며 content 본문은 없습니다. 아직 게시본이 없으면 빈 배열입니다.",
    "작성자용 조회이므로 미검토 게시본도 보입니다. 읽기만 수행하고 현재 편집 문서나 공개 상태를 바꾸지 않습니다.",
    "선택한 id로 GET publications/{publication_id}에서 내용 전체를 가져옵니다. 공개 상태는 project.publication.publicPublicationId를 따로 확인합니다.", array(ref("StudioPublicationSummary")), [ex.PUBLICATION_SUMMARY], errors="not_found", alternatives={"empty": ("게시본을 만들기 전", [])})
describe("get_publication", "게시본 상세 — 생성 당시 고정된 독자용 내용",
    "특정 게시본을 미리보거나 인쇄·내보내기할 때 호출합니다.", "project_id와 publication_id를 함께 지정합니다. 게시본 ID는 이 프로젝트의 게시본 목록/생성 응답에서 가져옵니다.",
    "Publication 객체를 반환합니다. 요약 필드와 content가 포함되며 content에는 독자용 문장·그림·용어만 들어갑니다. anchors·verified·origin·검토 메모는 없습니다.",
    "현재 편집 문서가 아니라 게시 당시 고정한 내용을 읽습니다. 이후 편집해도 이 게시본은 바뀌지 않습니다. 작성자는 reviewed=false 게시본도 조회할 수 있지만 이를 공개할 수는 없습니다.",
    "해당 스냅샷을 FE 미리보기·인쇄 화면에 사용합니다. 공개하려면 reviewed=true인지 확인하고 PUT public을 호출합니다.", ref("StudioPublication"), ex.PUBLICATION, errors="not_found")
describe("publish", "게시본 만들기 — 현재 문서의 독자용 스냅샷 저장",
    "내보내기에서 현재 편집 내용을 고정된 게시본으로 만들 때 호출합니다.", "project_id만 보내며 본문은 없습니다. 최신 편집 내용을 먼저 저장하세요. 초안이 있어야 하지만 검토 완료 전에도 비공개 게시본을 만들 수 있습니다.",
    "200과 {publication, project}를 반환합니다. publication은 content 없는 요약입니다. version은 자료별 1부터 증가하며 reviewed는 현재 내용 버전의 검토 완료 여부입니다.",
    "최신 게시본과 contentRevision·reviewed가 같으면 기존 최신 게시본을 재사용합니다. 둘 중 하나가 다르면 새 스냅샷을 생성합니다. 게시본 생성만으로 외부에 공개되지 않고 이미 공개된 버전도 교체하지 않습니다.",
    "본문이 필요하면 GET publications/{id}를 호출합니다. 검토 완료 게시본을 공개할 때만 PUT public에 그 id를 전달하세요.", ref("StudioPublishResult"), {"publication": ex.PUBLICATION_SUMMARY, "project": ex.PUBLISHED_PROJECT}, errors="not_found version_conflict")
describe("set_public", "공개 게시본 지정·해제 — 독자가 볼 버전 선택",
    "제작자가 검토 완료 게시본을 공개하거나 현재 공개를 중단할 때 호출합니다.", "공개는 {publicationId: 실제 게시본 ID}, 해제는 {publicationId: null}입니다. 이 프로젝트에 속하고 reviewed=true인 게시본만 공개할 수 있습니다.",
    "갱신된 project 객체를 반환합니다. 공개 시 publicPublicationId·publicVersion이 채워지고 해제 시 둘 다 null입니다.",
    "자료별 공개 게시본 하나를 선택합니다. 현재 편집 문서가 아니라 지정한 고정 게시본이 인증 없이 노출됩니다. 이전에 공개한 버전은 새 지정으로 교체됩니다. 공개 해제는 게시본 데이터를 삭제하지 않습니다.",
    "인증 없는 GET reader/{project_id}로 공개 여부를 확인합니다. 문서를 나중에 편집해도 공개 내용은 자동 갱신되지 않으므로 새 검토·게시·공개 지정을 거쳐야 합니다.", ref("StudioProject"), ex.PUBLIC_PROJECT, errors="not_found review_required version_conflict", body={"publicationId": ex.PUBLICATION["id"]})
describe("reader", "공개 독자 자료 — 인증 없이 지정 게시본 읽기",
    "공유 링크를 연 독자의 읽기 화면에서 호출합니다.", "project_id만 지정합니다. 제작자 토큰 없이 사용할 수 있으며 Swagger Authorize를 하지 않아도 됩니다.",
    "공개 상태면 {status:'available', publication: 전체 게시본}입니다. 미공개·공개 해제·삭제·존재하지 않는 자료는 모두 200 {status:'unavailable'}입니다. status를 먼저 분기하고 unavailable에는 publication이 없습니다.",
    "공개로 지정된 검토 완료 스냅샷만 반환합니다. 미검토 초안·현재 편집 내용·원문 근거·검토 메모를 반환하지 않습니다. AI 실행이나 새 게시본 생성도 없습니다.",
    "available이면 publication.content로 읽기 화면을 구성합니다. unavailable이면 읽을 수 없다는 안내를 보여 주고 제작자 인증을 요구하지 않습니다.", ref("StudioPublicReading"), {"status": "available", "publication": ex.PUBLICATION}, alternatives={"unavailable": ("미공개·삭제·자료 없음", {"status": "unavailable"})})
describe("asset", "그림 파일 조회 — 인증 없이 보관된 그림 바이너리",
    "문서·게시본·독자 응답의 images[].src, 그림 후보의 src를 <img>가 불러올 때 브라우저가 직접 호출합니다.", "asset_id는 src 주소의 마지막 경로 조각입니다. 인증 헤더 없이 호출하며 본문·쿼리는 없습니다.",
    "200과 그림 바이너리(PNG 또는 정제된 SVG)를 Content-Type과 함께 반환합니다. 없는 ID는 404 not_found입니다. 응답은 변하지 않으므로 오래 캐시됩니다.",
    "읽기만 하며 자료를 바꾸지 않습니다. ID는 추측할 수 없는 값이지만 주소를 아는 누구나 볼 수 있으므로 독자 공개 여부와 무관하게 접근됩니다.",
    "FE 코드가 따로 호출할 일은 없고 src를 그대로 <img src>에 넣으면 됩니다.", {"type": "string", "format": "binary"}, None, errors="not_found")
describe("sample_text", "가상 원문 예제 조회 — 새 자료 입력용 문자열",
    "새 자료 화면에서 예제 판결문 채우기를 사용할 때 호출합니다.", "제작자 토큰으로 호출하며 본문·쿼리 파라미터는 없습니다. 실제 AI 모드에서도 조회할 수 있습니다.",
    "JSON 문자열 자체를 반환합니다. {text: ...} 객체가 아닙니다. 내용은 실제 사건이 아닌 가상 임대차보증금 반환 예제입니다.",
    "샘플 문자열만 제공하고 자료 생성·AI 호출·초기화를 수행하지 않습니다. 이 문자열을 create_text에 제출하면 그때 현재 서버 모드에 따라 분석됩니다.",
    "FE 입력란에 채운 뒤 사용자가 새 자료 만들기를 실행하도록 합니다.", {"type": "string"}, SAMPLE_TEXT)
describe("reset", "데모 자료 초기화 — 현재 제작자의 모든 자료 숨김",
    "로컬 데모 테스트에서 현재 제작자의 작업함을 초기화할 때만 호출합니다.", "본문은 없습니다. AI_PROVIDER=demo이고 APP_ENV가 production이 아닌 서버에서만 허용합니다. 실제 OpenAI 모드의 로컬 서버에서도 사용할 수 없습니다.",
    "성공 시 204이며 응답 본문이 없습니다. 사용할 수 없는 서버 모드에서는 403 demo_only입니다.",
    "현재 토큰 소유자의 모든 활성 프로젝트를 soft delete합니다. 하나의 선택 자료만 지우는 동작이 아닙니다. 다른 제작자의 자료는 변경하지 않고 삭제된 자료의 공개 읽기는 중단됩니다. API 복구 기능은 없습니다.",
    "작업함과 FE 캐시를 갱신합니다. 실제 자료에서 실행하지 말고 단일 자료 삭제가 필요하면 DELETE projects/{id}를 사용하세요.", None, None, errors="demo_only not_found")

OPS["image_candidates"]["errors"] += ["comfy_invalid_workflow", "comfy_upstream_error", "comfy_not_found",
    "comfy_submission_unknown", "comfy_invalid_url", "comfy_invalid_node_schema", "comfy_image_too_large"]
OPS["prepare_images"]["errors"] += ["storyboard_cards_invalid", "storyboard_plan_invalid", "storyboard_size_invalid",
    "comfy_invalid_workflow", "comfy_upstream_error", "comfy_not_found", "comfy_submission_unknown",
    "comfy_invalid_url", "comfy_invalid_node_schema", "comfy_image_too_large", "image_too_large", "unsupported_image", "invalid_image"]
OPS["prepare_images"]["description"] += (
    "\n\n### 롤백 가능한 4컷 실험\n\n"
    "위 해상도는 기본 single 경로입니다. STUDIO_SCENE_MODE=storyboard4에서는 인물 선택·고정은 그대로 두고 "
    "장면 최대 4개를 문서 순서로 예약합니다. GPT는 컷별 글·원문 근거를 설계하고 기준 인물 번호를 통일합니다. "
    "Qwen Edit 40 steps·CFG 4, 1024×1024 latent로 2×2 시트를 생성해 좌상→우상→좌하→우하의 "
    "512×512 컷으로 잘라 한 트랜잭션에서 함께 적용합니다. 원본도 보관하지만 독자 문서에는 넣지 않습니다. "
    "이지리드 설계는 컷마다 핵심 뜻 하나와 구체적인 단어를 정하고, 근거 있는 집·돈·문서 등의 익숙한 아이콘을 적극 활용합니다. "
    "장식과 배경을 줄이며 아이콘 설명·금액은 그림 밖의 글에 둡니다. 고정 아이콘 조합 렌더러나 실제 그림 의미 검사 기능은 아닙니다. "
    "남은 카드가 1~3개면 나머지 칸은 비우도록 지시합니다. 묶음 전체의 기준 인물은 1~3명이며 "
    "모든 원본·크롭을 100개·12MiB 한도에 합산합니다. 원본 크기가 제출한 워크플로와 다르면 적용하지 않습니다. "
    "이전 버전의 이미 접수한 2048×2048 결과는 재생성 없이 1024×1024로 축소해 저장합니다. "
    "컷 경계·얼굴·상황의 정확성을 자동 보장하지 않습니다. single로 되돌리면 새 묶음은 기존 경로지만 "
    "이미 예약된 묶음은 같은 유료 작업을 이어 확인합니다. 수동 assist/images는 계속 단일 카드 경로입니다. "
    "실험의 Comfy 조회는 최대 45초이며 running이면 동일 API로 이어 호출합니다.")


def error_responses(codes, validation):
    responses = {}
    for code in dict.fromkeys(codes):
        status, message, action = ERRORS[code]
        entry = responses.setdefault(str(status), {"description": "발생 조건과 처리 방법:\n", "content": {
            "application/json": {"schema": ref("ErrorResponse"), "examples": {}}}})
        entry["description"] += f"\n- **{code}**: {message} {action}\n"
        entry["content"]["application/json"]["examples"][code] = {
            "summary": code, "description": action, "value": {"detail": {"code": code, "message": message}}}
    if validation:
        entry = responses.setdefault("422", {"description": "입력 필드의 형식·필수 여부·길이·열거값 검증 오류입니다.", "content": {
            "application/json": {"schema": ref("HTTPValidationError"), "examples": {}}}})
        media = entry["content"]["application/json"]
        if media["schema"] != ref("HTTPValidationError"):
            media["schema"] = {"anyOf": [ref("ErrorResponse"), ref("HTTPValidationError")]}
        entry["description"] += "\n\n요청 형식 검증 실패 시 detail은 객체 대신 오류 위치(loc)·메시지(msg)·유형(type)을 담은 배열입니다."
        media["examples"]["request_validation"] = {"summary": "필수 필드 누락 예시", "value": {
            "detail": [{"type": "missing", "loc": ["body", "필수필드"], "msg": "Field required", "input": {}}]}}
    return responses


def enrich_openapi(schema):
    """Modify documentation dictionaries only; preserve operation IDs and runtime contracts."""
    schemas = schema["components"]["schemas"]
    schemas.update(deepcopy(RESPONSE_SCHEMAS))
    for name, model in schemas.items():
        for key, prop in model.get("properties", {}).items():
            note = FIELD_DESCRIPTIONS.get(name, {}).get(key, COMMON_FIELDS.get(key))
            if note and "description" not in prop:
                prop["description"] = note
    operation_count = sum(len(methods) for methods in schema["paths"].values())
    schema["tags"] = [{"name": "FE 연동", "description": f"현재 제공하는 {operation_count}개 동작입니다. 각 행의 한글 제목을 펼쳐 요청·응답·오류 예시와 다음 호출 순서를 확인하세요."}]
    schema["components"]["securitySchemes"]["HTTPBearer"]["description"] = (
        "토큰 값만 입력합니다. Swagger가 Bearer 헤더를 붙입니다. "
        "익명 모드(기본)에서는 16자 이상의 아무 토큰이나 자기 작업함이 되고, AUTH_MODE=keys에서는 API_KEYS에 등록한 토큰만 통과합니다. "
        "OpenAI/Comfy 키를 입력하는 곳이 아니며 별도 로그인 API는 없습니다. 공개 독자 API와 그림 파일 API는 인증이 필요하지 않습니다.")
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            name = operation["operationId"].split("_api_studio_", 1)[0]
            doc = OPS[name]
            operation.update(summary=doc["summary"], description=doc["description"])
            public = name in {"reader", "asset"}
            if not public:
                operation["description"] += "\n\n**인증:** 제작자 Bearer 토큰이 필요합니다. 다른 제작자의 자료에는 접근할 수 없습니다."
            codes = ([] if public else ["unauthorized"]) + doc["errors"]
            if "requestBody" in operation:
                codes.append("request_too_large")
            operation["responses"] = error_responses(codes, "requestBody" in operation)
            status = "204" if name in {"remove", "reset"} else "201" if name in {"create_text", "create_pdf"} else "200"
            if status == "204":
                operation["responses"][status] = {"description": "처리 완료. 응답 본문이 없습니다."}
            elif name == "asset":
                operation["responses"][status] = {"description": "그림 바이너리. Content-Type은 image/png 또는 image/svg+xml입니다.",
                    "content": {"image/png": {"schema": {"type": "string", "format": "binary"}},
                                "image/svg+xml": {"schema": {"type": "string", "format": "binary"}}}}
            else:
                examples = {"success": {"summary": "가상 자료의 성공 응답 예시", "value": deepcopy(doc["example"])}}
                examples.update({key: {"summary": title, "value": deepcopy(value)} for key, (title, value) in doc["alternatives"].items()})
                operation["responses"][status] = {"description": "처리 완료. 응답의 의미와 다음 동작은 위 설명을 참고하세요.",
                    "content": {"application/json": {"schema": deepcopy(doc["model"]), "examples": examples}}}
            if doc["body"] is not None:
                media = operation["requestBody"]["content"]["application/json"]
                media["examples"] = {"request": {"summary": "요청 예시 — ID·버전은 실제 조회 값으로 바꾸세요", "value": deepcopy(doc["body"])}}
                if name == "set_public":
                    media["examples"]["unpublish"] = {"summary": "공개 해제", "value": {"publicationId": None}}
                if name == "complete":
                    media["examples"]["without_images"] = {"summary": "그림 없음 설정의 최종 체크리스트", "value": {"checklist": ["numbers", "relations", "claims"]}}
            for parameter in operation.get("parameters", []):
                if parameter["name"] == "project_id":
                    parameter.update(description="자료 생성 응답의 id 또는 작업함 목록의 id. 예시 ID를 실제 값으로 교체하세요.", example="project-example")
                elif parameter["name"] == "publication_id":
                    parameter.update(description="이 자료의 게시본 생성/목록 응답에서 얻은 id. 게시본 version 숫자와 다릅니다.", example="publication-example")
            if name in {"create_pdf", "upload_image"}:
                media = operation["requestBody"]["content"]["multipart/form-data"]
                body_model = schemas[media["schema"]["$ref"].rsplit("/", 1)[-1]]
                props = body_model["properties"]
                props["file"]["description"] = "업로드할 원본 파일 바이너리. PDF는 4.5MB, 그림은 2MiB 이하입니다. 로컬 파일 경로 문자열을 JSON으로 보내지 마세요."
                if name == "create_pdf":
                    props["settings"].update(description="Settings 객체를 JSON.stringify한 문자열. multipart의 텍스트 필드로 보냅니다.", example=json.dumps(ex.SETTINGS))
                else:
                    props["alt"].update(description="그림 모습을 설명할 대체텍스트. 생략하면 빈 문자열이며 최대 10,000자입니다.", example="법원에서 설명을 듣는 두 사람")
                    props["meaning"].update(description="그림이 나타내려는 의미. 생략하면 빈 문자열이며 최대 10,000자입니다.", example="보증금 반환에 관한 법원의 결정을 설명하는 그림")
    return schema


def install_docs(api):
    original_openapi = api.openapi
    def documented_openapi():
        if api.openapi_schema is None:
            api.openapi_schema = enrich_openapi(original_openapi())
        return api.openapi_schema
    api.openapi = documented_openapi
