# 글 생성과 그림 생성 함께 실행하기

화면 ②의 확인 후 **초안 만들기**, 화면 ③의 **쉬운 글·그림 카드**에 해당합니다.
GPT는 쉬운 글과 카드별 그림 설명을 한 번의 구조화 응답으로 작성하고, 서버는 그림 설명을
제한된 ComfyUI 실행 JSON으로 조립해 ComfyCloud 이미지 작업을 시작합니다.
영상 API와는 별개인 정지 이미지 생성입니다. 실행 JSON 파일을 프런트엔드에서 만들 필요가 없습니다.

## 한 번의 초안 요청

`POST /api/v1/documents/{document_id}/draft`

```json
{
  "expected_version": 3,
  "generate_images": true,
  "confirm_image_cost": true,
  "max_images": 12
}
```

`expected_version`은 구조 확인 응답의 실제 version으로 바꾸세요. 기존 초안을 교체할 때는
`replace_existing:true`도 필요합니다. `settings.use_images=true`여야 합니다.
`generate_images` 기본값은 false이며 기존 글 전용 호출과 호환됩니다.

응답은 기존 `Document`에 `blocks`와 `image_job_ids`를 포함합니다. 글·작업 레코드를 원자적으로
저장한 뒤 백그라운드에서 카드별 작업을 접수합니다. 그림을 기다리는 동안 `picture`는 null입니다.
처음부터 최종 그림이 들어 있는 응답을 기다리는 동기 API는 아닙니다.
최대 12개 카드, 카드당 1장입니다. 카드 수가 요청 상한을 넘으면 문서 저장·그림 실행 없이
422를 반환합니다. 이미 수행한 GPT 요청 비용은 발생할 수 있습니다.

## 프런트엔드 연결

| 화면 동작 | API | 처리 |
| --- | --- | --- |
| 글·그림 초안 만들기 | `POST /documents/{id}/draft` | 위 옵션으로 한 번 호출 |
| 카드별 생성 상태 표시 | `GET /documents/{id}/image-jobs` | `block_id`로 카드와 연결 |
| 완료 확인·그림 연결 | `POST /image-jobs/{id}/refresh` | 진행 작업마다 5~10초 간격 호출 |
| 수정 가능한 최신 문서 받기 | `GET /documents/{id}` | 자동 연결 후 최신 `version`·`picture` 반영 |
| 이미지 렌더링 | `GET /documents/{id}/assets/{asset_id}` | Bearer 인증 fetch 후 Blob 사용 |
| 실패한 그림만 재생성 | `POST /image-jobs/{id}/retry` | 비용 재확인, 새 작업 ID 반환 |

표의 경로에는 모두 `/api/v1` 접두사가 필요합니다. `GET` 상태 조회만으로는 외부 작업을 갱신하지
않습니다. **현재 MVP는 프런트엔드의 refresh 폴링이 완료 이미지 다운로드·자동 연결을 진행합니다.**
서버가 혼자 계속 폴링하는 상주 워커/WebSocket은 없습니다. 창을 닫아도 Cloud 작업은 진행될 수 있고,
다시 열어 refresh하면 결과를 가져옵니다. 결과의 Cloud 보관 기한을 지나면 복구가 불가능할 수 있습니다.

`attached`: 이미지가 일반 문서 asset으로 저장되어 카드, 독자 HTML, PDF에 연결되었습니다.
문서 `version`/`content_revision`이 증가하고 기존 검토 승인은 해제됩니다.
다른 이미지가 먼저 연결되어도 나머지 카드의 연결은 계속 가능합니다.

`stale`: 카드 삭제·내용/그림 변경·구조/설정 변경 때문에 자동 적용하지 않았습니다.
생성 전이라면 유료 제출을 생략하고, 이미 완성된 경우 asset은 보관하되 편집을 덮어쓰지 않습니다.
보관된 asset은 제작자가 확인 후 기존 그림 교체 API로 적용할 수 있습니다.

## 실패·재시작 복구

작업은 SQLite에 남습니다. `pending`은 아직 접수하지 않은 작업으로 refresh에서 재개됩니다.
`submitting`은 접수 예약 후 응답 저장 전 상태입니다. 서버가 이때 중단되었다면 자동 재접수하지 않습니다.
`submission_unknown`은 타임아웃 등으로 실제 접수/과금 여부가 불명확한 상태입니다.

실패가 확정된 `submission_failed`, `failed`, `canceled`, `expired`만 재생성합니다.

```http
POST /api/v1/image-jobs/{job_id}/retry
Content-Type: application/json

{"expected_job_version":3,"confirmed":true}
```

응답 202의 새 `id`를 폴링하세요. 같은 실패 작업을 다시 retry해도 같은 후속 작업을 반환합니다.
그 후속 작업이 또 실패했다면 후속 작업 ID로 재시도합니다. 원래 Document의 `image_job_ids`는
초기 ID이며, 후속 ID는 `retry_job_id`와 목록에서 찾을 수 있습니다.

`succeeded` + `error_code`는 생성은 성공했지만 이미지 가져오기에 실패한 상태입니다.
호스트 설정/연결을 해결하고 **refresh**를 호출하세요. 서명 URL을 새로 조회해 다운로드하며
그림 재생성 과금은 발생시키지 않습니다. 409 동시 갱신 충돌도 다시 조회하면 됩니다.

접수 불명 상태는 Cloud 대시보드에서 해당 작업을 확인한 뒤 복구할 수 있습니다.

```http
POST /api/v1/image-jobs/{job_id}/reconcile
Content-Type: application/json

{"expected_job_version":3,"provider_job_id":"실제-cloud-job-id"}
```

작업별 랜덤 seed/파일 prefix를 포함한 제출 JSON 전체가 일치해야 연결됩니다.
이 경로는 새 생성 요청을 보내지 않습니다. Cloud에서 접수 여부를 확인할 수 없는 경우
안전하게 재접수하는 자동 우회는 제공하지 않습니다.

## 서버 설정·검증 범위

- 실제 글: `AI_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL`.
- 실제 그림: `COMFY_CLOUD_API_KEY`. 키가 없으면 503이며 가짜 성공/대체 이미지를 만들지 않습니다.
- `AI_PROVIDER=demo`는 글 복사와 일반 그림 설명만 만듭니다. Comfy 키와 비용 동의가 있으면
  **데모에서도 실제 유료 그림 요청**을 보냅니다. 데모는 무료 Cloud 실행 모드가 아닙니다.
- 초기 프리셋: FLUX.1 Schnell, 768×768, 4 steps, 1장. 모델/노드가 계정에서 제공되는지
  `object_info`로 초안 생성 전에 검사합니다. 지원하지 않으면 422이며 다른 모델로 자동 변경하지 않습니다.
- 모델 이름: `flux1-schnell.safetensors`, `clip_l.safetensors`, `t5xxl_fp16.safetensors`, `ae.safetensors`.
- 이미지 결과를 받는 정확한 HTTPS 호스트를 `COMFY_ASSET_ALLOWED_HOSTS`에 등록하세요.
  기본은 `cloud.comfy.org`입니다. 실제 계정의 CDN 호스트를 운영자가 확인해 추가해야 할 수 있습니다.
  임의 호스트·와일드카드는 허용하지 말고, 운영에서는 외부 통신 방화벽도 적용하세요.
  다운로드에는 API 키를 전달하지 않고 리다이렉트를 따라가지 않습니다. 최대 5MB,
  기존 이미지 정제 제한(최대 1,600만 픽셀)을 적용하고 메타데이터 없는 PNG로 저장합니다.
- 실제 Cloud GPU 실행·사용 모델 가용성은 계정 키로 추가 검증해야 합니다. 자동 테스트는 외부 호출을 모킹합니다.

원문/사건 구조는 GPT로 전송되고, GPT가 작성한 그림 설명은 ComfyCloud로 전송됩니다.
GPT에 그림 프롬프트 비식별화를 지시하지만 **개인정보 제거를 보장하지 않습니다.** 자동 전송 전
사용자가 입력 판결문을 비식별화하고 외부 전송 권한을 확인해야 합니다. 민감 자료에서 사람의
프롬프트 사전 검토가 필요하면 자동 그림 옵션을 켜지 마세요.

대체 텍스트는 생성 의도이며 그림 픽셀을 분석한 결과가 아닙니다. 생성 그림의 `meaning`은
`neutral`로 두고 검토 화면에서 사람이 글·그림 의미를 확인합니다. 지급 명령을 지급 완료로
그리지 않도록 프롬프트에 지시하지만 법적 정확성, 외형 일관성, 이미지 품질은 보장하지 않습니다.

## 구현 근거

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [ComfyCloud v2 API](https://docs.comfy.org/api-reference/v2/overview)
- [공식 FLUX Schnell 워크플로우](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/flux_schnell_full_text_to_image.json)
