"""OpenAPI-only response models and field guidance; no runtime/AI schema changes."""


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def array(item):
    return {"type": "array", "items": item}


def nullable(item):
    return {"anyOf": [item, {"type": "null"}]}


def field(schema, description):
    return {**schema, "description": description}


def obj(description, **properties):
    return {"type": "object", "description": description, "properties": properties,
            "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
INTEGER = {"type": "integer", "minimum": 0}
BOOLEAN = {"type": "boolean"}
DATETIME = {"type": "string", "format": "date-time"}
ID = {"type": "string", "minLength": 1}

RESPONSE_SCHEMAS = {
    "StudioSourceSummary": obj("작업함에서 사용하는 원본 파일·텍스트의 요약입니다. 원문은 source API로 별도 조회합니다.",
        kind=field({"type": "string", "enum": ["text", "pdf"]}, "원문 입력 방식입니다."),
        fileName=field(nullable(STRING), "PDF 파일명입니다. 붙여넣은 텍스트는 null입니다."),
        byteSize=field(nullable(INTEGER), "PDF 원본 바이트 수입니다. 텍스트 입력은 null입니다."),
        charCount=field(INTEGER, "추출한 원문 페이지 텍스트의 문자 수입니다. UTF-16 근거 오프셋과는 별개입니다.")),
    "StudioDocumentSummary": obj("문서 본문을 제외한 저장·생성 기준 버전과 원문 대조 진행률입니다.",
        saveRevision=field(INTEGER, "최신 문서 저장 버전. 자동 저장 요청에 그대로 보내 충돌을 감지합니다."),
        contentRevision=field(INTEGER, "독자가 보는 내용의 버전. 검토·게시본이 어떤 내용을 대상으로 했는지 비교합니다."),
        basedOnStructureRevision=field(INTEGER, "마지막 초안 생성에 사용한 사건 구조 버전입니다."),
        basedOnSettingsRevision=field(INTEGER, "마지막 초안 생성에 사용한 제작 설정 버전입니다."),
        sentenceCount=field(INTEGER, "문서 전체 카드의 문장 수입니다."),
        verifiedCount=field(INTEGER, "제작자가 원문과 대조했다고 표시한 문장 수입니다. AI의 정확도 점수가 아닙니다.")),
    "StudioReviewSummary": obj("작업함·단계 표시줄용 검토 상태입니다.",
        checkedContentRevision=field(nullable(INTEGER), "최근 점검 대상 내용 버전. 점검 전 또는 점검이 해제되면 null입니다."),
        openRequiredCount=field(nullable(INTEGER), "확인하지 않은 required 항목 수. 점검 전 또는 해제 시 null입니다."),
        completedContentRevision=field(nullable(INTEGER), "최종 검토를 완료한 내용 버전. 편집·재점검 등으로 완료가 해제되면 null입니다."),
        completedAt=field(nullable(DATETIME), "최종 검토 완료 시각. 현재 검토 미완료는 null입니다.")),
    "StudioPublicationState": obj("최신 게시본과 현재 공개한 게시본은 서로 다를 수 있습니다.",
        latestVersion=field(nullable(INTEGER), "가장 최근에 만든 게시본 번호. 아직 게시본이 없으면 null입니다."),
        latestContentRevision=field(nullable(INTEGER), "최신 게시본이 담은 문서 내용 버전입니다."),
        publicPublicationId=field(nullable(ID), "현재 독자에게 공개 중인 게시본 ID. 비공개는 null입니다."),
        publicVersion=field(nullable(INTEGER), "현재 공개 게시본의 번호. 비공개는 null입니다.")),
    "StudioProject": obj("자료의 메타데이터와 제작 진행 상태. 원문·사건 구조·문서 본문은 각각 별도 API로 조회합니다.",
        id=field(ID, "서버가 발급한 자료 ID. 이후 모든 프로젝트 경로의 project_id에 사용합니다."),
        title=field(STRING, "작업함에서 표시하는 자료 이름. document.title과 별도로 관리합니다."),
        createdAt=field(DATETIME, "자료가 저장된 UTC 시각입니다."),
        updatedAt=field(DATETIME, "제목·문서·검토·그림 등 자료 상태를 마지막으로 저장한 UTC 시각입니다."),
        settings=ref("Settings"), settingsRevision=field(INTEGER, "제작 설정이 바뀔 때 증가하는 버전입니다."),
        source=ref("StudioSourceSummary"), caseNumber=field(nullable(STRING), "사건 구조에서 추출·입력한 사건번호. 값이 없으면 null입니다."),
        structureRevision=field(INTEGER, "현재 사건 구조 내용의 버전입니다."),
        document=field(nullable(ref("StudioDocumentSummary")), "초안 생성 전에는 null. 생성 후에도 본문 대신 진행 요약만 포함합니다."),
        review=ref("StudioReviewSummary"), publication=ref("StudioPublicationState")),
    "StudioSourceParagraph": obj("원문 대조의 기준 문단. 텍스트를 다시 가공하면 저장된 근거 오프셋이 달라질 수 있습니다.",
        id=field(ID, "Anchor.paragraphId에서 참조할 원문 문단 ID입니다."),
        block=field({"type": "string", "enum": ["header", "order", "claim-purpose", "reasons", "footer"]}, "사건 정보·주문·청구취지·이유·끝 구획입니다."),
        kind=field({"type": "string", "enum": ["heading", "body"]}, "구획 제목 문단 또는 본문 문단입니다."),
        level=field(nullable({"type": "integer", "minimum": 1, "maximum": 3}), "제목 깊이입니다. 현재 서버의 구획 제목은 1, 본문은 null입니다."),
        page=field(nullable({"type": "integer", "minimum": 1}), "PDF 원본의 1부터 시작하는 페이지 번호. 텍스트 입력은 null입니다."),
        text=field(STRING, "문단 원문입니다. 근거의 start/end는 이 문자열의 JavaScript UTF-16 단위 인덱스입니다.")),
    "StudioSource": obj("원문 대조 패널에서 사용하는 문단 목록입니다.", projectId=ID, paragraphs=array(ref("StudioSourceParagraph"))),
    "StudioStructureResult": obj("저장된 사건 구조와 갱신된 자료 상태를 함께 반환합니다.", structure=ref("CaseStructure"), project=ref("StudioProject")),
    "StudioDocumentResult": obj("프론트는 document를 편집 상태에, project를 작업함·단계 상태에 반영합니다.", document=ref("EasyDocument"), project=ref("StudioProject")),
    "StudioImageCandidate": obj("선택 전 그림 후보. 문서에 붙일 때 FE에서 그림 ID를 부여하고 source=library로 구성합니다.",
        src=field(STRING, "서버가 보관한 그림의 절대 주소(/api/studio/assets/{id}). 생성 결과는 PNG이며 업로드 후보에는 안전한 SVG도 포함될 수 있습니다."),
        alt=field(STRING, "대체텍스트 초안. 생성 이미지의 실제 모습과 대조하여 수정할 수 있습니다."),
        meaning=field(STRING, "이 그림으로 표현하려는 의미. 최종 그림과 문장이 같은 뜻인지 제작자가 확인합니다.")),
    "StudioImageCandidates": obj("현재 카드의 생성 결과가 먼저 나오고, 이 자료에 업로드한 그림도 함께 반환됩니다.", candidates=array(ref("StudioImageCandidate"))),
    "StudioImageUploadResult": obj("프로젝트에 보관된 그림입니다. 문서에는 아직 연결되지 않았습니다.", image=ref("DocImage")),
    "StudioReviewTarget": {"oneOf": [
        obj("문장 위치", type={"const": "sentence", "type": "string"}, cardId=ID, sentenceId=ID),
        obj("카드 위치", type={"const": "card", "type": "string"}, cardId=ID),
        obj("그림 위치", type={"const": "image", "type": "string"}, cardId=ID, imageId=ID),
        obj("용어 위치", type={"const": "term", "type": "string"}, termId=ID),
        obj("문서 전체", type={"const": "document", "type": "string"})]},
    "StudioReviewEvidence": obj("점검 당시 비교할 텍스트·원문 근거·그림 참조입니다.",
        text=field(nullable(STRING), "점검 대상 문장 또는 의미 설명입니다."), anchors=array(ref("Anchor")),
        structureValue=field(nullable(STRING), "사건 구조 대조 값. 현재 규칙 점검은 null을 반환합니다."), imageId=nullable(ID)),
    "StudioDismissal": obj("제작자가 항목을 직접 확인했다는 기록입니다.", memo=field(STRING, "선택 메모. 빈 문자열도 허용합니다."), at=DATETIME),
    "StudioReviewItem": obj("required는 처리해야 검토 완료가 가능하고 suggested는 완료를 막지 않습니다.",
        key=field(STRING, "확인/복원 요청에 그대로 보내는 항목 키. 대상 내용·근거·구조·설정이 달라지면 바뀔 수 있습니다."),
        category=field({"type": "string", "enum": ["numbers", "relations", "claim-mix", "image-meaning", "no-anchor", "structure-changed", "hard-term", "long-sentence", "alt-text"]}, "점검 유형. 현재 서버 규칙은 no-anchor, numbers, long-sentence만 생성하며 나머지 값은 FE 계약 호환용입니다."),
        level={"type": "string", "enum": ["required", "suggested"]}, title=STRING, detail=STRING,
        target=ref("StudioReviewTarget"), evidence=ref("StudioReviewEvidence"),
        suggestion=field(nullable(obj("수정 제안", text=STRING)), "현재 규칙 점검은 자동 수정 제안 없이 null을 반환합니다."),
        dismissal=field(nullable(ref("StudioDismissal")), "미처리이면 null, 문제없음 확인 후 메모·시각이 들어갑니다.")),
    "StudioReviewRun": obj("저장된 문서에 대한 최근 규칙 점검 결과입니다.", projectId=ID, contentRevision=INTEGER, ranAt=DATETIME, items=array(ref("StudioReviewItem"))),
    "StudioReviewResult": obj("점검 결과와 자료의 검토 진행 요약입니다.", run=ref("StudioReviewRun"), project=ref("StudioProject")),
    "StudioReviewCompletion": obj("현재 내용 버전의 최종 체크리스트 완료 기록입니다.", contentRevision=INTEGER, completedAt=DATETIME,
        checklist=array({"type": "string", "enum": ["numbers", "relations", "images", "claims"]})),
    "StudioCompletionResult": obj("검토 완료 기록과 자료 상태입니다.", completion=ref("StudioReviewCompletion"), project=ref("StudioProject")),
    "StudioReaderCard": obj("독자용 카드. 원문 근거·verified·origin·검토 메모는 포함하지 않습니다.", id=ID,
        role={"type": "string", "enum": ["person", "background", "claim", "finding", "decision"]},
        partyName=field(nullable(STRING), "person·claim 카드의 표시 이름. 그 외 역할은 null입니다."),
        image=field(nullable(obj("독자용 그림", src=STRING, alt=STRING)), "그림 없음 또는 illustrations=none이면 null입니다."),
        sentences=array(obj("독자용 문장", id=ID, text=STRING))),
    "StudioReaderContent": obj("게시 시점에 고정한 독자용 내용입니다.", title=STRING, subtitle=STRING,
        tone={"type": "string", "enum": ["haeyo", "hamnida"]}, overview=ref("Overview"),
        sections=array(obj("독자용 구획", kind={"type": "string", "enum": ["people", "decision", "reasons", "glossary"]}, title=STRING, cards=array(ref("StudioReaderCard")))),
        glossary=array(ref("GlossaryTerm"))),
    "StudioPublicationSummary": obj("본문을 제외한 고정 게시본 요약입니다.", id=ID, projectId=ID,
        version=field({"type": "integer", "minimum": 1}, "자료별로 1부터 증가하는 게시본 번호입니다."),
        createdAt=DATETIME, contentRevision=INTEGER,
        reviewed=field(BOOLEAN, "게시본 생성 당시 현재 내용의 검토 완료 여부. false인 게시본은 공개할 수 없습니다.")),
    "StudioPublicReading": {"description": "공개 가능 여부. unavailable도 오류가 아닌 200 응답입니다.", "oneOf": [
        obj("공개된 검토 완료 게시본", status={"const": "available", "type": "string"}, publication=ref("StudioPublication")),
        obj("미공개·삭제·자료 없음", status={"const": "unavailable", "type": "string"})]},
    "StudioPublishResult": obj("생성하거나 재사용한 게시본 요약과 자료 상태입니다.", publication=ref("StudioPublicationSummary"), project=ref("StudioProject")),
}
RESPONSE_SCHEMAS["StudioPublication"] = obj("게시 시점의 내용과 검토 상태가 고정된 게시본입니다.",
    **RESPONSE_SCHEMAS["StudioPublicationSummary"]["properties"], content=ref("StudioReaderContent"))

COMMON_FIELDS = {
    "id": "현재 객체의 고유 ID입니다. 조회한 값을 참조 관계와 저장 요청에서 유지하세요.",
    "projectId": "경로 project_id와 동일한 자료 ID입니다. 다른 자료의 ID로 바꾸면 저장할 수 없습니다.",
    "anchors": "원문 근거 목록. paragraphId와 UTF-16 start/end로 source의 문단 일부를 가리킵니다. 빈 목록은 점검에서 확인 대상이 될 수 있습니다.",
    "flags": "구조 추출 시 확인이 필요한 사항. 구조 내용과 달리 flags만 수정하면 구조 revision은 증가하지 않습니다.",
    "partyId": "연결할 당사자 ID. 구조의 주장은 parties.id, 문서의 카드는 partyNames.partyId를 참조합니다.",
    "title": "표시할 제목입니다. 프로젝트 제목·문서 제목·구획 제목은 별도 값입니다.",
    "text": "이 항목의 텍스트입니다. 주장·판단·결정의 주체와 수치·부정 표현을 보존하세요.",
}
FIELD_DESCRIPTIONS = {
    "Settings": {
        "tone": "haeyo=해요체, hamnida=합니다체. 이후 AI 생성·편집 보조와 독자 표시의 기준입니다. 기존 문장을 자동으로 다시 쓰지는 않습니다.",
        "naming": "initial=A씨·B씨, role=집을 빌린 사람 등 쉬운 역할, legal=원고·피고 등 법적 지위. 변경하면 구조의 표시 이름과 버전이 갱신됩니다.",
        "illustrations": "with=독자 화면에 연결된 그림을 표시하고 검토 시 그림 체크리스트를 요구, none=독자 그림 숨김. with 설정만으로 전체 카드 그림을 생성하지는 않습니다."},
    "CreateText": {"text": "실제 분석할 원문 전체. 앞뒤 공백을 제외하여 100자 이상, 입력 전체 100,000자 이하입니다. 예시는 실제 사건이 아닌 가상 판결 내용입니다.", "settings": "이 자료의 초기 문체·당사자 호칭·그림 표시 설정. 세 필드를 모두 전달합니다."},
    "Anchor": {"paragraphId": "GET source 응답의 paragraphs[].id. 페이지 ID나 카드 ID가 아닙니다.", "start": "문단 text 기준 0부터 시작하는 JavaScript UTF-16 시작 오프셋(포함). 이모지 중간을 가르면 오류입니다.", "end": "UTF-16 끝 오프셋(미포함). start보다 크고 문단 길이 이하여야 합니다. 예: '😀원고'의 '원고'는 start=2, end=4입니다."},
    "Flag": {"code": "추출 결과의 확인 사유 코드. demo-unclassified는 데모가 직접 분류하지 못한 원문 항목을 뜻합니다.", "message": "제작자가 사건 구조 화면에서 읽는 확인 안내입니다."},
    "Overview": {"caseName": "사건을 설명하는 이름. AI 추출 또는 제작자가 입력한 값입니다.", "caseNumber": "원문 사건번호. 알 수 없으면 빈 문자열입니다.", "court": "판결 법원. 원문에 없거나 알 수 없으면 빈 문자열입니다.", "decisionDate": "판결일의 원문·입력 표현. 날짜 형식을 강제하지 않으며 알 수 없으면 빈 문자열입니다."},
    "Party": {"sourceLabel": "원문에 나온 당사자 명칭. 독자 공개 응답에는 원문 명칭을 별도 필드로 전달하지 않습니다.", "legalStatus": "원고·피고 등 사건에서의 법적 지위입니다.", "displayName": "제작 설정에 따른 현재 표시 이름입니다.", "easyRole": "집을 빌린 사람 등 성인 독자가 이해할 수 있는 역할 설명입니다."},
    "KeyFact": {"kind": "money=금액, date=날짜, period=기간, other=기타 핵심 정보입니다.", "label": "보증금·계약 종료일 등 정보의 이름입니다.", "value": "금액·일자·사실의 값. 원문의 단위와 수치를 보존합니다."},
    "Finding": {"claimIds": "이 판단과 연결된 claims[].id 목록. 아직 연결하지 않았으면 빈 배열입니다.", "stance": "accepted=주장 수용, rejected=배척, partial=일부 수용, none=관계 없음 또는 판단하지 못함입니다."},
    "CaseStructure": {"revision": "직전 GET/PUT 응답의 구조 revision을 그대로 보냅니다. 내용 변경 시 서버가 1 증가시킵니다. 오래된 값은 409이며 flags만 바뀌면 값은 유지됩니다.", "overview": "사건 개요. 없는 정보를 추정해서 채우지 않습니다.", "parties": "당사자 목록. 항목 ID는 핵심 사실·주장·판단·결정의 ID와도 중복되면 안 됩니다.", "keyFacts": "금액·날짜·기간 등 핵심 정보 목록입니다.", "claims": "당사자가 한 주장. 각 partyId는 같은 구조의 parties에 존재해야 합니다.", "findings": "법원이 인정하거나 판단한 내용. 연결한 claimIds는 같은 구조의 claims에 존재해야 합니다.", "decisions": "법원의 최종 주문·결정. 지급하라는 명령을 지급 완료로 바꾸지 않습니다."},
    "Sentence": {"origin": "ai-draft=초안 생성, ai-suggestion=보조 제안 적용, manual=직접 작성입니다.", "verified": "제작자가 원문과 직접 대조했는지 표시합니다. AI 초안은 서버가 항상 false로 저장합니다."},
    "Card": {"role": "person=인물, background=배경, claim=당사자 주장, finding=법원 판단, decision=최종 결정. 소속 구획과 맞아야 합니다.", "imageId": "같은 문서 images[].id를 참조합니다. 그림이 없으면 null입니다.", "sentences": "이 카드에 속한 문장 목록. 최소 1개, 카드당 최대 100개입니다."},
    "Section": {"kind": "people→decision→reasons→glossary 순서로 정확히 네 구획을 전달합니다. glossary의 cards는 빈 배열입니다.", "cards": "구획의 카드 목록. people은 person/background, decision은 decision, reasons는 background/claim/finding 역할만 허용합니다."},
    "DocImage": {"src": "이 프로젝트에서 생성·업로드한 그림의 주소(/api/studio/assets/{id})와 같아야 합니다. 임의 외부 URL은 저장할 수 없습니다.", "alt": "그림의 실제 모습을 설명하는 대체텍스트. 비어 있으면 검토 항목이 생성될 수 있습니다.", "meaning": "그림으로 전달하려는 의미. 글의 뜻과 같은지 별도 대조합니다.", "source": "upload=직접 업로드, library=후보에서 선택. 현재 생성 그림 후보도 FE 계약에 맞춰 library로 연결합니다."},
    "GlossaryTerm": {"term": "풀이할 용어. 1~100자입니다.", "explanation": "문서에 포함할 쉬운 말 풀이. 보조 API 응답은 직접 적용해야 여기에 저장됩니다."},
    "PartyName": {"displayName": "이 문서의 인물·주장 카드에서 보여줄 당사자 이름입니다."},
    "EasyDocument": {"saveRevision": "직전 서버 응답의 저장 버전. PUT 성공 시 1 증가하며 오래된 값은 409입니다.", "contentRevision": "서버가 독자용 내용 변경 여부로 결정하는 버전. 클라이언트가 보낸 임의 값은 저장 기준으로 사용하지 않습니다.", "basedOnStructureRevision": "초안을 만들 때 사용한 구조 revision. PUT에서 임의로 바꿀 수 없습니다.", "basedOnSettingsRevision": "초안을 만들 때 사용한 settingsRevision. PUT에서 임의로 바꿀 수 없습니다.", "subtitle": "독자에게 보여줄 한 줄 설명. 빈 문자열도 허용합니다.", "partyNames": "카드에 연결할 당사자 ID와 문서 표시 이름 목록입니다.", "sections": "people, decision, reasons, glossary 순서의 네 구획을 모두 보냅니다. 부분 수정 PATCH 형식이 아닙니다.", "glossary": "문서 전체 용어 풀이 목록입니다.", "images": "문서에 등록된 그림 목록. 후보 생성이나 업로드만으로 자동 추가되지 않습니다."},
    "Rename": {"title": "작업함 자료 제목. 앞뒤 공백은 제거하며 빈 제목은 거절합니다. 최대 150자이고 문서 본문 제목은 바뀌지 않습니다."},
    "Dismiss": {"key": "최근 review.run 응답의 items[].key를 그대로 전달합니다.", "memo": "제작자의 확인 메모. 필드는 필수지만 값은 빈 문자열도 허용합니다. 최대 2,000자이며 앞뒤 공백을 제거합니다."},
    "Restore": {"key": "문제없음 확인을 취소할 최근 점검 항목의 key입니다."},
    "Complete": {"checklist": "numbers·relations·claims를 각각 한 번씩 전달합니다. illustrations=with이면 images도 필수입니다. 순서는 무관하며 중복·누락·불필요한 항목은 422입니다."},
    "SetPublic": {"publicationId": "공개할 이 프로젝트의 검토 완료 게시본 ID. null이면 공개를 해제합니다. 게시본 생성 API의 응답에서 가져옵니다."},
    "SentenceInput": {"sentenceId": "이미 저장된 document의 문장 ID. 보조 결과를 자동 적용할 위치 지정은 아닙니다.", "text": "변환·분할할 현재 문장 텍스트(1~10,000자). 서버는 이 입력을 보조 생성에 사용하므로 최신 편집 값을 전달합니다."},
    "TermInput": {"text": "어려운 용어를 찾을 편집 텍스트(1~10,000자)입니다."},
    "ExplainInput": {"term": "풀이할 용어(1~100자)입니다.", "context": "용어가 쓰인 문장·주변 문맥(최대 10,000자). 빈 문자열도 허용합니다."},
    "ImageInput": {"cardId": "이미 저장된 document의 카드 ID. 해당 카드의 저장된 문장·근거·당사자 표시 이름으로 그림을 생성하므로 편집을 먼저 저장하세요."},
    "Suggestions": {"suggestions": "쉬운 표현 후보 문자열 목록. 모델에는 1~3개를 요청하며 응답 계약은 1~5개를 허용합니다. 문서는 자동 변경하지 않습니다."},
    "Sentences": {"sentences": "분할 결과 문자열 목록(1~20개). 카드·문장 ID와 근거는 FE에서 적용할 때 구성합니다."},
    "Terms": {"terms": "어려운 용어 문자열 목록(최대 30개). 찾은 용어가 없으면 빈 배열입니다."},
    "Explanation": {"explanation": "문맥에 맞춘 쉬운 말 풀이 제안입니다. document.glossary에 적용·저장해야 문서에 남습니다."},
    "ErrorDetail": {"code": "프론트가 분기 처리할 오류 코드입니다. 예: version_conflict, image_in_progress.", "message": "화면에 표시할 한국어 오류 설명입니다. 제공자 키나 원본 오류 내용은 포함하지 않습니다."},
    "ErrorResponse": {"detail": "업무 오류 객체. 요청 형식 검증 오류(422)의 detail은 이 객체 대신 위치·메시지 목록일 수 있습니다."},
}
