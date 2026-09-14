# 화면별 API 사용 가이드

추가된 시나리오/ComfyCloud 영상 기능은 [영상 API 가이드](VIDEO.md)를 참고하세요. 모든 영상 경로는 제작자 전용이며 공개 PDF 링크에 자동 포함되지 않습니다.

기본 경로: `/api/v1`. 보호된 요청에는 `Authorization: Bearer <token>`을 붙입니다. 공개 경로는 `/health`, `/docs`, `/redoc`, `/openapi.json`, `/share/{token}`입니다. Swagger의 Authorize에는 토큰 문자열만 넣으세요.

실제 AI는 서버의 `AI_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL`로 설정합니다. **OpenAI 키를 프런트엔드나 Swagger에 넣지 마세요.** 프런트엔드 API 경로와 요청 형식은 동일합니다. `/health`의 `ai_provider`로 `demo`/`openai` 설정을 구분할 수 있지만 이 값은 실제 모델 호출 성공 여부를 뜻하지 않습니다. GPT 작업 시 원문·편집 데이터가 OpenAI로 전송되므로 사용자에게 이를 안내하세요.

## ① 판결문 업로드

`POST /documents/text` JSON: `examples/judgment.json` 참고.

`POST /documents/pdf` multipart:

| 필드 | 설명 |
| --- | --- |
| file | PDF 파일, 20MB 이하 |
| title | 자료 제목 |
| settings_json | `{"audience":"발달장애 성인","tone":"adult_respectful","naming":"neutral","use_images":true}` 문자열 |

응답 `id`를 문서 id로 사용합니다. `GET /documents/{id}/source`는 페이지별 추출 원문과 문단을 반환합니다. `source.pdf`는 업로드 파일 그대로입니다. `GET /documents?limit=20&offset=0`으로 내 작업 목록을 조회합니다.

## ② 사건 구조 확인

1. `POST /documents/{id}/structure/analyze`에 `{"expected_version":1}` 전송.
2. 응답의 `structure`를 원문과 대조합니다. `claim`=당사자 주장, `finding`=법원 인정 사실·판단, `decision`=법원 결정, `background`=배경, `unknown`=미분류입니다.
3. `PUT /documents/{id}/structure`에 `expected_version`과 수정한 `structure` 전체를 전송합니다.
4. `POST /documents/{id}/structure/confirm`에 `expected_version`, `confirmed:true`, `note`를 전송합니다.
5. `POST /documents/{id}/draft`로 초안을 만듭니다.

당사자 `id`와 사실 항목 `id`는 조회한 값을 유지하세요. `claim`에는 등록된 당사자의 `speaker_id`가 필수이고, `finding`과 `decision`에는 당사자 발화자를 지정하지 않습니다. `unknown`이 남으면 확인할 수 없습니다.

원문 근거 예시:

```json
{"paragraph_id":"p1-1","start":0,"end":5,"quote":"원고: A"}
```

이 값은 해당 문단의 `text[0:5]`가 정확히 `원고: A`일 때만 유효합니다. `end`는 포함하지 않습니다. Python Unicode 문자 기준입니다. 프런트엔드 JavaScript UTF-16 인덱스와는 이모지 등에서 다르므로 `Array.from(text)` 같은 코드포인트 배열로 처리해야 합니다.

## ③ 쉬운 글·그림 편집

**글을 만들 때 그림도 생성:** 기존 `POST /documents/{id}/draft`에 다음처럼 전달합니다.

```json
{"expected_version":3,"generate_images":true,"confirm_image_cost":true,"max_images":12}
```

글과 카드별 `image_job_ids`를 먼저 반환합니다. `GET /documents/{id}/image-jobs`로 목록을 조회하고
`POST /image-jobs/{jobId}/refresh`를 5~10초 간격으로 호출하면 완성 이미지를 카드에 자동 연결합니다.
연결 후 `GET /documents/{id}`로 최신 `version`을 가져와 편집하세요. 실패한 그림만 재생성할 수 있고
제작자가 수정한 카드를 덮어쓰지 않습니다. 자세한 화면 매핑·복구·설정은 [글·그림 생성 가이드](IMAGES.md).
`generate_images:false` 또는 생략은 기존 글 전용 동작입니다.

`GET /documents/{id}`의 `blocks`가 중앙 편집 카드입니다. 카드 클릭 시 `GET /documents/{id}/blocks/{blockId}/sources`를 호출하면 원문 페이지 번호와 `page_start/page_end/quote`를 반환합니다. PDF 시각 좌표가 아닌 추출 텍스트 위치입니다.

직접 편집은 `PUT /documents/{id}/blocks/{blockId}`:

```json
{
  "expected_version": 5,
  "content": {
    "section": "decision",
    "kind": "decision",
    "speaker_id": null,
    "title": "무엇을 결정했나요",
    "text": "법원은 B씨에게 보증금을 돌려주라고 결정했습니다.",
    "evidence": [{"paragraph_id":"실제 문단 id","start":0,"end":10,"quote":"실제 인용문"}],
    "terms": [{"term":"보증금","explanation":"집을 빌릴 때 맡긴 돈입니다."}],
    "picture": null
  }
}
```

위의 evidence와 version은 실제 조회값으로 바꿔야 합니다. 원문 근거를 편집해도 원본은 변경되지 않습니다. 용어 풀이도 제작자가 맥락을 확인합니다.

AI 제안 생성: `POST /documents/{id}/blocks/{blockId}/proposals`

```json
{"expected_version":5,"action":"simplify","instruction":"한 문장에 한 내용을 담아주세요."}
```

`action`: `simplify` / `split` / `add_term` / `replace_picture`.

응답의 `proposals` 마지막 항목에 `before`, `after` 배열, `id`가 있습니다. 프런트엔드에서 비교한 뒤:

- 적용: `POST /documents/{id}/proposals/{proposalId}/apply`
- 거절: `POST /documents/{id}/proposals/{proposalId}/reject`

둘 다 최신 `expected_version`을 전달합니다. `split` 적용 시 여러 카드로 나뉘며 첫 카드의 id는 유지됩니다. 다른 내용을 편집한 뒤 이전 제안을 적용하면 `409 stale_proposal`입니다.

그림은 `POST /documents/{id}/assets`로 업로드한 다음 반환된 `asset.id`를 `picture.asset_id`로 연결합니다. `alt_text`는 필수이고, `meaning`은 제작자가 지정하는 장면 분류입니다. `payment_completed` 그림과 `decision` 카드의 조합은 검토 경고를 만듭니다. 이 값이 실제 픽셀과 같은지는 직접 확인해야 합니다.

추가 API: 카드 추가 `POST /documents/{id}/blocks`, 삭제 `DELETE /documents/{id}/blocks/{blockId}`(JSON body에 expected_version), 순서 변경 `PUT /documents/{id}/block-order`(expected_version과 전체 block_ids 배열).

## ④ 검토

`POST /documents/{id}/reviews` → 응답 `review.issues` 표시.

수정이 필요하면 카드 편집 → 검토를 다시 실행합니다. 실제 원문을 확인해 경고를 수용할 수 있으면 `POST /documents/{id}/reviews/issues/{issueId}/resolve`에 최신 version과 확인 메모를 보냅니다.

```json
{"expected_version":8,"note":"원문과 대조해 같은 금액의 단위 변환임을 확인했습니다."}
```

모든 항목을 확인한 뒤 `POST /documents/{id}/reviews/approve`:

```json
{"expected_version":9,"confirmed":true,"note":"원문·글·그림을 대조하고 최종 검토했습니다."}
```

자동 점검은 완전한 법률 의미 검증이 아닙니다. 숫자와 분류 규칙에 걸리지 않는 오해석·인물 관계 변경도 있을 수 있어 제작자의 직접 확인 항목이 항상 포함됩니다.

## ⑤ 결과물 미리보기

- `GET /documents/{id}/preview`: 독자 화면 구성 JSON.
- `GET /documents/{id}/reader`: 독자용 HTML(그림 데이터 포함).
- `GET /documents/{id}/preview.pdf`: 현재 편집본 PDF. 승인 전에는 검토 전 표시.

독자 화면은 인물 → 결정 → 명시적으로 구분한 당사자 주장 → 판단 이유 → 용어 순서로 그룹화합니다. 내부 근거와 확인 항목은 내려주지 않습니다. JSON의 그림은 asset API로 조회합니다.

보호된 HTML/PDF URL은 브라우저 주소창에만 붙여 넣으면 Bearer 헤더가 없어 401입니다. 프런트엔드에서 인증된 fetch 후 Blob으로 열거나, 다음 단계의 명시적 공개 링크를 사용하세요.

## ⑥ 내보내기

`POST /documents/{id}/exports`

```json
{"expected_version":10,"share":false,"expires_in_hours":168}
```

출력본은 승인된 현재 내용 버전으로 저장됩니다. 반환된 `pdf_path`는 인증 후 다운로드, `reader_path`는 인증된 HTML 조회 경로입니다. 요청한 `expected_version`이 오래됐거나 검토가 무효하면 409입니다.

`share:true`일 때만 `share_path`와 만료 시각을 반환합니다. 이 링크 토큰은 DB에는 해시로만 저장하므로 응답을 보관해야 합니다. URL을 받은 사람에게 설명자료가 공개되므로 공유 전 개인정보를 확인하세요.

`DELETE /exports/{exportId}/share`로 공개 접근을 해제합니다. 만료·해제된 링크는 404. 이후 문서를 수정해도 기존 출력본은 바뀌지 않습니다.

## 이력과 오류

`GET /documents/{id}/history`는 버전·행동·작성자·시각 목록을 제공합니다. `GET /documents/{id}/history/{version}`은 해당 버전 전체를 반환합니다. 구조 확인과 검토 메모도 저장됩니다.

도메인 오류:

```json
{"detail":{"code":"version_conflict","message":"문서 버전이 달라졌습니다. 다시 조회 후 작업하세요."}}
```

입력 타입·필수값 오류는 FastAPI 표준 `422 {"detail":[...]}` 형식입니다. 도메인 422는 위 객체 형식입니다. 프런트엔드에서는 둘 다 처리하세요.

| 상태 | 의미 |
| --- | --- |
| 401 | 인증 토큰 없음/오류 |
| 404 | 문서·그림·출력 없음, 다른 제작자 소유, 공유 만료/해제 |
| 409 | 버전 충돌, 구조 미확인, 만료된 제안, 미완료 검토 |
| 413 | 파일·텍스트·요청 상한 초과 |
| 422 | 잘못된 원문 근거, 입력, 스캔 PDF, 분류·발화자 모순 |
| 502 | AI 연결/결과 검증 실패, `ai_refusal`, `ai_incomplete_response` |
| 503 | 실제 AI 미설정, 한글 글꼴 미설치, `ai_rate_limited`(OpenAI 한도/429) |
| 504 | `ai_timeout`(GPT 응답 시간 초과) |

AI 오류 시 기존 문서와 버전은 유지됩니다. 프런트엔드는 오류를 표시하고 사용자 요청에 따라 재시도하며, 자동 무한 재시도를 하지 마세요.
