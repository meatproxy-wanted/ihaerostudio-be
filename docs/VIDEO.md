# 시나리오 → 동적 워크플로우 → ComfyCloud 영상

사용자는 ComfyUI JSON을 작성하거나 업로드하지 않습니다. GPT가 카드와 시나리오에서 장면 계획을 만들고, 서버가 허용한 노드 조합으로 실행 JSON을 컴파일합니다. GPT가 실행 코드·임의 노드·모델 경로·외부 URL을 선택할 수 없습니다.

## 1차 지원 범위

- `wan22-5b-t2v-v1`: Wan 2.2 TI2V 5B의 텍스트→영상 경로. 실제 Cloud 노드/모델 가용성은 preflight에서 확인합니다.
- 최대 8개 카드, 최대 8개 장면. 장면당 목표 2~5초, 24fps. 프레임은 Wan의 `4n+1` 규칙에 맞춰 `목표 초 × 24 + 1`로 생성하며 `actual_duration_seconds`를 함께 반환합니다.
- 화면 비율: 16:9(1024×576), 9:16(576×1024), 1:1(768×768). 프롬프트, 길이, 시드, 장면별 출력 경로가 동적으로 달라집니다.
- 각 장면은 독립적인 무음 영상입니다. `narration`은 검토용 원고이며 TTS/입모양 합성/자막 삽입/자동 합본은 구현하지 않았습니다. 기존 그림을 시작 프레임으로 쓰는 I2V도 다음 범위입니다.
- 생성된 파일은 Cloud에 있고, 서버는 작업 ID·상태·결과 asset ID·만료 다운로드 URL을 SQLite에 저장합니다. 영상 바이너리를 자체 영구 저장소로 복사하지 않습니다.
- 성공 상태는 영상 생성 완료일 뿐 의미 검토 승인이나 법적 정확성 보장이 아닙니다. 모든 영상 API는 제작자 인증을 요구하며 기존 공개 PDF 공유 링크에 영상을 자동 첨부하지 않습니다.

서버가 요구하는 모델 파일:

```text
wan2.2_ti2v_5B_fp16.safetensors
umt5_xxl_fp8_e4m3fn_scaled.safetensors
wan2.2_vae.safetensors
```

서버는 모델을 자동 다운로드하거나 다른 유료 모델로 바꾸지 않습니다. 현재 SaveVideo 동적 입력 형식(`format.codec`)을 사용합니다. Cloud가 구버전 노드 정의를 제공하면 preflight가 호환 불가를 반환합니다. 그 경우 컴파일러의 지원 프리셋을 해당 정의에 맞게 업데이트한 뒤 다시 검증해야 합니다.

## 환경변수

```bash
export AI_PROVIDER=openai
# 서버 Secret에 OPENAI_API_KEY, OPENAI_MODEL, COMFY_CLOUD_API_KEY 등록
```

GPT는 장면 계획 생성에, ComfyCloud는 노드 목록 확인과 영상 실행·조회에 사용합니다. 두 서비스 키는 별개이며 Swagger Authorize에는 **제작자 토큰(API_KEYS)**만 넣습니다. 키를 웹 클라이언트·시나리오·워크플로우 JSON에 넣지 마세요.

`COMFY_CLOUD_API_KEY`가 없어도 계획 JSON 생성·직접 수정은 가능합니다. `AI_PROVIDER=demo`는 카드별 텍스트를 복사한 장면만 만들며 자유 시나리오를 해석하지 않습니다. 데모가 Cloud 실행을 흉내 내거나 가짜 영상 URL을 반환하지도 않습니다. 실행에는 항상 실제 Cloud 키가 필요합니다.

## API 사용 순서

아래 경로는 `/api/v1` 뒤에 붙입니다. 문서/카드 ID, version, UUID는 실제 응답 값으로 바꾸세요.

### 계획 만들기

문서의 사건 구조를 확인하고 글 초안을 만든 뒤:

`POST /documents/{document_id}/video-plans`

```json
{
  "expected_version": 7,
  "block_ids": ["판단-카드-ID", "결정-카드-ID"],
  "scenario": "법원이 판단한 이유를 보여주고 마지막에 돈을 돌려주라는 결정을 설명해주세요. 이미 지급한 장면은 만들지 마세요.",
  "visual_style": "성인 독자를 존중하는 따뜻하고 차분한 설명용 일러스트",
  "aspect_ratio": "16:9",
  "max_scenes": 4
}
```

201 응답에 계획 `id`, `version`, `source_content_revision`, `scenes`가 있습니다. 각 장면의 `scene`은 제작자가 볼 대본·영상 묘사, `workflow`는 실제 Comfy API-format JSON입니다. UI 편집용 `nodes/links` 형식이 아닙니다.

GPT 계획 생성은 유료 GPT 호출일 수 있지만 이 단계에서는 영상을 실행하지 않습니다. 선택한 모든 카드의 연결을 검사하고, 서로 다른 당사자의 주장·법원 판단·결정을 같은 장면에 섞으면 거부합니다. 대본과 장면 묘사의 실제 의미까지 자동으로 보증하지는 않습니다.

조회:

- `GET /documents/{document_id}/video-plans`: 최근 100개 계획
- `GET /video-plans/{plan_id}`: 계획과 장면별 JSON

### 장면 직접 수정

`PUT /video-plans/{plan_id}`

```json
{
  "expected_version": 1,
  "scenes": [
    {
      "title": "법원의 결정",
      "source_block_ids": ["결정-카드-ID"],
      "narration": "법원은 B씨에게 돈을 돌려주라고 결정했습니다.",
      "visual_prompt": "A calm conceptual illustration of an adult receiving a written court order. No money is being transferred. Not footage of a real case.",
      "negative_prompt": "payment completed, text, subtitles, watermark",
      "duration_seconds": 4
    }
  ]
}
```

`scenes`는 전체 장면 목록이며 원래 선택한 카드를 빠짐없이 포함해야 합니다. 위의 단일 장면 예시는 결정 카드 하나로 만든 계획에 해당합니다. 계획 version이 증가하고 JSON을 재생성합니다. 실행 기록이 생긴 계획은 수정할 수 없으며, 재생성하려면 새 계획을 만듭니다.

### 실행 전 확인

`POST /video-plans/{plan_id}/scenes/0/preflight`

Cloud 노드 정보와 모델 선택지, 필수 입력, 타입, 숫자 범위, 연결 출력 타입을 대조합니다. 결과의 `compatible`과 `issues`를 확인하세요. 이 호출은 GPU 영상 생성을 요청하지 않습니다. 실제 제출 전에도 검사하며, 호환되지 않으면 영상 요청을 보내지 않습니다.

### 확인한 장면 실행

먼저 기존 문서 검토 API로 최종 승인합니다. 그 후 장면 대본·외부 전송·비용 발생을 확인하고:

`POST /video-plans/{plan_id}/scenes/0/jobs`

```json
{
  "expected_plan_version": 2,
  "idempotency_key": "a10f86c3-e2bb-4a0e-a21a-415f4e265ceb",
  "confirmed": true,
  "note": "원문과 장면을 대조했고 외부 전송 및 생성 비용을 확인했습니다."
}
```

장면 인덱스는 0부터 시작합니다. 한 요청은 한 장면만 실행합니다. 전체 영상을 요청하려면 각 장면을 확인 후 별도로 호출합니다. 일부분이 실패해도 다른 장면을 다시 만들지 않습니다.

202는 **로컬 실행 기록이 생성됨**을 뜻하며 Cloud 성공 보장이 아닙니다. 응답의 `status`와 `error_code`를 반드시 확인하세요. 문서 내용이 바뀌었거나 검토 승인이 무효하면 실행을 거부합니다. 중복 클릭·동시 요청은 DB의 원자적 예약으로 막습니다.

동일 요청을 다시 보낼 때는 **동일 idempotency_key**를 사용합니다. 기존 로컬 작업을 반환할 뿐 다시 실행하지 않습니다. Cloud 측 키에는 로컬 작업 ID를 사용해 서로 다른 제작자의 UUID 충돌을 격리합니다.

### 상태·결과 확인

- `GET /video-plans/{plan_id}/jobs`: 장면별 로컬 실행 기록
- `GET /video-jobs/{job_id}`: 저장된 상태만 조회(외부 호출 없음)
- `POST /video-jobs/{job_id}/refresh`: Cloud 상태·진행률·영상 결과 저장
- `POST /video-jobs/{job_id}/outputs/{asset_id}/refresh`: 다운로드 URL 재발급
- `POST /video-jobs/{job_id}/cancel`: 해당 작업만 취소 요청

프런트엔드는 진행 중에 약 5~10초 간격으로 refresh하고 `succeeded`, `failed`, `canceled`, `expired`에서 중단합니다. 서버 내부 무한 폴링/인메모리 백그라운드 태스크는 사용하지 않습니다. 재시작 후에도 저장된 작업 ID로 갱신을 재개할 수 있습니다.

`outputs[].url`은 만료되는 서명 URL입니다. 제작자에게만 전달하며 HTML 삽입 없이 안전한 동영상 URL로 취급하세요. 자체 장기 보관이 필요하면 별도 객체 저장소로 복사하는 작업과 개인정보 보관·삭제 정책을 추가해야 합니다. 생성 영상에는 프롬프트 등 메타데이터가 포함될 수 있으므로 외부 공유 전 확인하세요.

### 결과 불명 복구 — 자동 재실행 금지

Cloud v2의 Idempotency-Key는 응답 재생이 아니라 **중복 거부** 방식이며 24시간 후 만료됩니다. 따라서 연결 시간 초과/5xx/중복 키 응답은 `submission_unknown`으로 저장하고 새 키로 자동 재제출하지 않습니다. 제출 직후 서버가 중단되면 `submitting` 상태가 남을 수 있습니다.

Cloud 화면에서 해당 실행을 확인하고 다음을 호출합니다:

`POST /video-jobs/{job_id}/reconcile`

```json
{"provider_job_id":"Cloud에서 확인한 실제 작업 ID"}
```

Cloud의 API-format 실행 JSON이 저장한 장면 JSON과 정확히 일치할 때만 연결합니다. 상태가 `submission_failed`인 명시적 거부도 자동 재시도하지 않습니다. 원인을 해결하고 제작자 확인 후 새 계획에서 요청하세요. Cloud에서 실행 여부를 확인하지 않은 채 새 계획으로 다시 요청하면 중복 과금될 수 있습니다.

## 검증과 제한

자동 테스트는 GPT/Cloud 모의 응답으로 동적 JSON, strict 스키마, 문서/계획 버전 충돌, 제작자 격리, 중복 요청, 클라우드 오류·취소·복구·만료 URL 갱신을 검증합니다. 실제 Cloud 계정의 모델 가용성, GPU 실행 및 영상 품질은 실제 키로 preflight와 짧은 유료 실행을 거쳐 별도로 확인해야 합니다.

영상 공개 공유, 생성 영상의 의미 검토 UI, 합본, TTS/자막, 이미지 참조, 사용자별 비용 할당/속도 제한, 영구 영상 저장·삭제 정책은 1차 구현 범위 밖입니다. 공개 운영 전에 비용 상한과 사용자별 요청 제한을 적용하세요.

## 공식 근거

- [Comfy API v2 규격](https://docs.comfy.org/api-reference/v2/overview), [OpenAPI v2](https://docs.comfy.org/openapi-v2.yaml)
- [Cloud 노드 정보 조회](https://docs.comfy.org/development/cloud/api-reference) — 노드/모델 조회만 v1 표면 사용
- [공식 Wan 2.2 5B 템플릿](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_5B_ti2v.json)
- [ComfyUI 영상 노드 구현](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_video.py)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
