# 캐릭터와 장면 생성 · 현재 경로와 호환 경로

프로젝트 전체 흐름은 [프로젝트 요약](PROJECT_OVERVIEW.md), 요청 계약은 [FE 연동](FRONTEND.md)을 봅니다.
이 문서는 `app/studio_generation.py` 등의 현재 분기 조건을 설명하며 과거 변경 이력을 기본 동작으로 나열하지 않습니다.

## 생성 경로

| 대상 / 조건 | 실제 처리 | 설정 |
| --- | --- | --- |
| library 모드의 새 인물 | GPT가 번들 캐릭터 10종에서 선택 → 얼굴 크롭 저장 | Comfy 호출·샘플 초상 생성·매 얼굴 GPT 구도 검사 없음 |
| 고정 캐릭터를 참조하는 장면 | 같은 캐릭터의 한 명짜리 기본 포즈 원본 → Qwen Edit | 40 steps·CFG 4, 별도 화풍 샘플 없음 |
| 고정 캐릭터 참조가 없는 새 장면 | 번들 분위기 샘플 → Qwen Edit | 40 steps·CFG 4. 샘플은 분위기 참고이며 사건 인물/구도가 아님 |
| generate 모드의 새 초상 | 분위기 샘플 → Qwen Edit, 빈 latent에서 새 인물 생성 | 768×768·40 steps·CFG 4, 결과의 한 명·흰 배경 여부 GPT 검사 |
| 기존 적용 인물을 참조하는 장면 | 기존 PNG/JPEG/WebP 픽셀 → Qwen Edit | 40 steps·CFG 4. 인물 참조가 2개 이하이면 분위기 샘플도 추가 |
| 샘플 없는 구형 호환 초상 / 장면 | Qwen-Image-2512 초상 / FLUX.2 Dev 장면 | 각 20 steps. 기본 신규 경로로 설명하지 않음 |

모드가 library여도 적용된 기존 인물이나 미완료 그림 작업이 있는 자료는 이를 보존합니다.
정상 번들의 신규 무참조 작업도 분위기 샘플을 사용합니다. 표의 샘플 없는 20-step 경로를 현재 기본 경로로 해석하지 않습니다.
이미 캐릭터셋을 배정한 프로젝트는 모드를 generate로 바꿔도 기존 캐릭터를 유지합니다.
그림 없음 설정과 demo 모드는 일괄 준비를 건너뜁니다.

## 캐릭터 원본과 얼굴

자산 위치는 `app/assets/characters-v1/`, 선택 후보·파일·얼굴 영역은 `manifest.json`입니다.
10종은 내장 이미지 생성으로 제작한 가상 성인이며 실제 사건 당사자의 외모를 나타내지 않습니다.
생성 지시·배경 정리 지시는 `prompts.json`과 `cleanup-prompt.txt`에 남깁니다.

원본은 같은 인물의 4포즈 시트입니다. `studio_library.neutral_sprite()`는 첫 1/4만 잘라 한 명짜리 기본 포즈를 만듭니다.

- `face_pixels()`: 머리·얼굴 중심으로 잘라 흰 768×768 캔버스에 배치. 카드 표시 자산.
- `reference_pixels()`: 같은 기본 포즈의 얼굴과 의상을 포함한 상반신을 흰 768×768 캔버스에 배치. 장면 입력 자산.
- stock 얼굴 자산의 내부 `libraryCharacterId`·`libraryDigest`와 프로젝트 배정을 대조해 원본을 찾습니다.
  FE에는 기존 `src/alt/meaning` 계약을 유지하며 별도 레퍼런스 URL을 받지 않습니다.

자산 버전과 픽셀 digest를 배정에 저장합니다.
같은 버전의 manifest나 픽셀을 나중에 덮어쓰면 기존 프로젝트가 `character_library_changed`로 중단될 수 있습니다.
기존 배정을 조용히 새 캐릭터로 바꾸지 않으며, 향후 자산 변경은 버전 관리와 명시적인 이전 정책이 필요합니다.

선택은 텍스트 기반 GPT 호출로 캐릭터 ID만 받습니다. 원고/피고를 근거로 외모를 추정하지 않도록 지시합니다.
배정 중복·없는 partyId 등 잘못된 결과는 적용하지 않습니다.
배정은 DB에 남고 동일 입력의 거부 결과도 재사용합니다. 추가 등장인물은 남은 후보 중에서만 선택합니다.

## 장면 설계와 레퍼런스

인물 카드의 partyId·imageId 및 문서 images를 연결해 **적용·저장된** 그림만 찾습니다.
이름·역할·원문 근거에 언급된 인물을 선택하고 번호를 다시 매깁니다.
인물이 불명확하면 임의로 참조를 버리지 않습니다.
전체 기준 그림 6개, 장면 참조 3개를 넘으면 `character_reference_limit` 오류입니다.

레퍼런스의 외형을 OpenAI 이미지 입력으로 분석하고 동일 소유자·자료·자산·픽셀의 분석을 캐시합니다.
단일 인물을 특정하지 못하면 `character_reference_unclear`입니다.
보이지 않는 의상·신발 등을 실제 정보처럼 발명하지 않도록 지시합니다.

장면 설계는 카드 문장과 원문 근거를 바탕으로 행동, 주체/대상, 인물 위치·시선·손동작,
사물 상태와 주장/사실/명령/완료 구분을 구조화합니다.
장면에는 흰 배경을 강제하지 않고 장소가 불명확하면 중립 공간을 사용하도록 지시합니다.
스타일 요구는 단순한 애니메이션 이미지 또는 삽화이며 stock 레퍼런스의 선과 삽화 느낌을 유지하도록 추가 지시합니다.
Qwen Edit의 negative prompt는 비워 둡니다. 완벽한 외형·상황·글그림 의미 일치는 보장하지 않습니다.

## 워크플로우와 해상도

모델은 Qwen-Image-Edit-2511 FP8, Qwen 2.5 VL 인코더, Qwen image VAE를 사용합니다.
Lightning LoRA는 사용하지 않습니다. 실행 전 해당 Cloud 계정의 모델·노드 가용성을 점검합니다.
서버가 계획에 맞춰 노드 JSON을 조립하며 FE는 prompt·workflow·steps를 보내지 않습니다.

인물 레퍼런스는 약 1MP로 정규화한 뒤 가로·세로를 0.75배로 축소합니다.
출력은 첫 참조의 비율을 유지한 약 0.59MP이며 768×768 정사각형 stock 입력의 출력도 768×768입니다.
샘플 기반 새 초상·인물 참조 없는 장면은 768×768 latent에서 생성합니다.
파일 저장 뒤 크기만 줄이는 방식은 아닙니다.

현재 40 steps는 해당 신규 Qwen Edit 경로의 설정입니다.
이미 접수되었거나 접수 여부가 불확실한 구형 작업은 원래 워크플로우로 조회하므로 다른 steps·해상도로 끝날 수 있습니다.

## 자동 적용과 후보 반환

`document/prepare-images`는 인물 → 장면 순서로 카드당 최대 한 개씩 생성·자동 저장합니다.
`running`이면 이어 호출하고 `ready` 또는 `skipped`에서 끝냅니다.
완료한 일괄 작업은 다시 실행되지 않으며 장면을 제거하거나 새 카드를 추가한 것만으로 자동 재생성하지 않습니다.
초안 재생성은 고정 인물만 보존하고 새 장면의 일괄 작업을 초기화합니다.

`assist/images`는 생성·보관한 후보를 반환할 뿐 문서에 자동 적용하지 않습니다.
선택한 후보를 문서 images와 card.imageId에 연결해 `PUT document`로 저장합니다.
고정 인물의 변경은 `character_locked`로 막고 장면 변경만 허용합니다.

`source=library`는 AI 생성 장면에도 쓰는 기존 wire 값이므로 stock 자산 여부를 나타내지 않습니다.

## 재시도·과금·저장

작업과 입력 업로드를 `studio_image_jobs`에 보관합니다.
동일 내용의 완료 결과·진행 작업을 재사용하며 카드·인물 문맥이 바뀌면 다음 요청은 새 작업일 수 있습니다.
이미 적용된 그림은 자동 교체하지 않습니다.

Comfy 완료를 최대 90초 기다립니다. 이 값은 HTTP 전체 시간 제한이 아닙니다.
`assist/images`의 진행 중 응답은 `503 image_in_progress`이고 일괄 준비는 이를 `200 generation.status=running`으로 반환합니다.
아직 확보하지 못한 lease 등에서는 일괄 준비도 `image_in_progress`를 반환할 수 있습니다.

이미 접수된 작업은 재제출 없이 조회합니다.
접수 불확실·실행 실패·만료 작업을 자동으로 새 유료 작업으로 바꾸지 않습니다.
확정된 미접수 오류는 동일 계획 제출을 다시 시도할 수 있으며 모든 오류가 같은 재시도 정책은 아닙니다.
접수 불확실은 운영자가 Comfy 작업 목록과 저장된 작업을 대조해야 합니다.

생성 중 문맥이 달라지면 `image_card_changed`로 오래된 결과 저장·적용을 막습니다.
다운로드는 인증된 결과 메타데이터의 허용 호스트만 사용하고 서명 URL에는 키를 붙이지 않습니다.
결과 PNG는 서버 DB에 보관하며 제공자의 임시 URL을 FE에 내주지 않습니다.
일괄 준비는 여러 요청 중 앞서 적용된 얼굴·장면을 이후 오류가 나도 보존합니다.

## 확인 방법

```bash
PYTHONPATH=. .venv/bin/python scripts/preview_character_library.py
.venv/bin/python -m pytest tests/test_character_library.py tests/test_studio_generation.py tests/test_studio_batch.py -q
```

프리뷰는 실제 얼굴·기본 포즈 크롭을 렌더링합니다.
테스트의 OpenAI·Comfy 응답은 모의 값이므로 테스트 통과를 실제 생성 품질 확인으로 해석하지 않습니다.

설치 패키지의 자산도 따로 확인합니다.

```bash
python -m pip wheel . --no-deps --wheel-dir dist
.venv/bin/python scripts/verify_package_assets.py dist/ihaerostudio_be-0.3.0-py3-none-any.whl
```
