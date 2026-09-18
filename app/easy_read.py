"""Operational Easy-Read rules; never a claim of accessibility certification.

Based on the bundled judicial guideline and Inclusion Europe's Information
for all (general/written standards). Legal meaning overrides simplification.
"""
import re

VERSION = "easy-read-explanation-v1"

TEXT_RULES = """\n# 이지리드 적용 우선 규칙
목표는 예쁜 글이나 어린이용 이야기가 아니라 성인이 판결의 핵심 상황을 이해하는 것입니다.
짧은 일상어와 존중하는 존댓말을 사용하세요. 같은 인물과 대상은 같은 이름과 말로 반복하세요.
주어와 동사를 가깝게 두고 한 문장에는 한 가지 핵심 정보만 담으세요.
서로 다른 행동·결론·이유는 sentences의 별도 항목으로 나누세요. 한 항목에 여러 문장을 넣지 마세요.
핵심 정보의 생략 없이 짧게 쓰세요. 글자 수를 맞추려고 조건이나 부정을 삭제하지 마세요.
원문의 대조용 quote는 그대로 복사하고, 독자용 설명만 쉽게 바꾸세요.
인물을 소개한 다음 결정을 먼저, 그 결정을 내린 이유를 뒤에 설명하세요.
당사자의 말은 'A씨는 …라고 말했어요/요청했어요', 법원 판단은 '법원은 …라고 판단했어요',
명령은 '법원은 B씨에게 …하라고 결정했어요'처럼 누가 말하거나 결정했는지 밝히세요.
문체는 settings.tone에 맞추세요. 주장한 사건을 이미 일어난 사실로 바꾸지 마세요.
같은 카드에도 주장과 판단을 섞지 말고 role=claim/finding/decision 카드로 분리하세요.
어려운 말이 꼭 필요하면 처음 나오는 곳의 바로 다음 문장에서 짧게 풀이하세요.
glossary만 만들고 본문에서 설명을 생략하지 마세요. '계약=약속' 같은 기계적 치환으로 법적 뜻을 바꾸지 마세요.
그/그것/전술한/위와 같은 대신 실제 인물 이름과 대상을 쓰세요. 은유나 승패·선악 평가를 더하지 마세요.
불필요한 이중부정은 직접적인 표현으로 풀되, '하지 않았다/받아들이지 않았다'의 부정은 보존하세요.
금액·날짜·기간·이율·단위·기한·조건·의무의 주체와 대상은 정확하게 유지하세요.
큰 금액은 같은 값의 읽기 쉬운 단위로 쓰되 반올림·대략치·새 계산을 추가하지 마세요.
예: '10,000,000원'은 '1,000만 원'. '1,000만 원 정도'나 '돈을 이미 받았어요'로 바꾸지 마세요.
시간 순서와 명령/이행 완료를 보존하세요. 새로운 법적 조언이나 절차·감정은 추가하지 마세요.
출력 전 각 문장의 주체, 한 가지 핵심 정보, 쉬운 말, 원문 의미·수치·부정 보존을 점검하세요.
이 규칙만으로 독자의 이해나 정확성을 보장한다고 쓰지 마세요. 독자 참여와 제작자의 대조는 별도로 필요합니다.
"""

VISUAL_TASK_RULES = """\n# 이지리드 그림 설계 우선 규칙
그림은 글 옆에서 한 가지 핵심 상황을 설명하는 보조자료이지, 글 없이 판결 전체를 전달하는 그림이 아닙니다.
카드의 sentences에서 독자가 알아야 할 핵심 정보 하나를 먼저 정하고 그 정보에 필요한 주체·대상·사물만 남기세요.
mainMessage에는 그 핵심 뜻 하나를 짧은 영어 문장으로, keyTerms에는 실제 카드 글/근거에 나오는 구체적인 한국어 단어 최대 3개를 쓰세요.
keyTerms에 있는 단어의 익숙한 그림상징을 objectsAndSetting과 prompt에 연결하세요. 필요한 구체적 단어가 없으면 빈 배열입니다.
단순한 행동 또는 사물 상태 하나를 크게 보여주세요. 여러 시간대, 원인과 결과, 요청과 이행을 한 컷에 섞지 마세요.
구체적인 단어를 설명하는 익숙한 아이콘·그림상징을 적극 활용하세요. 예: 집은 집 모양, 돈은 동전/지폐, 문서는 종이, 법원은 법원 건물.
이 예시는 표현 방식의 참고일 뿐입니다. 현재 카드에 근거가 있는 대상만 사용하고 예시 소품을 그대로 추가하지 마세요.
objectsAndSetting에 어떤 단어를 어떤 아이콘으로 설명할지 적으세요. 하나의 핵심 대상과 필요한 소수의 보조 아이콘만 크게 배치하세요.
인물이 없어도 아이콘과 사물 상태로 더 명확해지면 사물 중심 컷을 선택하세요. 인물 참조가 있다고 무조건 사람을 넣지 마세요.
아이콘의 이름·금액·설명은 이미지 밖의 글에 두세요. 의미가 모호한 아이콘이나 추상적 은유는 쓰지 마세요.
중심 행동을 알아보기 쉽게 인물과 핵심 사물을 크게 배치하고 충분히 떨어뜨리세요.
필요한 인물만 사용하고 사물 중심 설명에는 사람을 추가하지 마세요. 배경은 밝고 단순하며 장식·군중·작은 소품은 빼세요.
기준 인물의 얼굴·옷·색과 같은 대상의 모습을 컷마다 유지하세요. 자세만 상황에 맞춰 바꾸세요.
성인을 존중하는 설명 삽화입니다. 유아용 장난감·과장된 감정·선악 표정을 추가하지 마세요.
긍정/부정을 빨강·초록, 체크·X, 저울, 화살표만으로 표현하지 마세요. 상징을 법적 의미의 유일한 단서로 쓰지 마세요.
문장이 필요한 추상적인 판단은 옆의 쉬운 글이 설명합니다. 그림만으로 표현하려고 새로운 사건이나 행동을 만들지 마세요.
주장한 과거 사실을 확정된 사건처럼 그리지 마세요. 주장/판단/명령의 구분은 semanticBoundary와 한국어 meaning에 명시하세요.
지급 명령은 실제 지급 장면이 아닙니다. 법원 결정을 확인하는 모습 등 중립적인 설명 장면을 사용하세요.
alt는 설계상 보이는 사람·행동·사물만 짧게, meaning은 카드의 핵심 뜻 하나를 쉬운 한국어로 쓰세요.
alt/meaning에 그림을 검수했다거나 정확하다는 표현을 넣지 마세요. 금액과 세부 판단은 글에 두세요.
이미지 안에 글자·숫자·긴 자막을 생성하지 마세요. 카드 이름과 설명은 이미지 밖의 화면 글이 담당합니다.
"""

VISUAL_COMPOSITION = (
    " Easy-Read explanatory composition: one main message and one clear action or object state per scene. "
    "Show only the necessary people and essential objects, large and clearly separated, with generous empty space. "
    "Use a plain light background, clear outlines and a few easily distinguishable details. "
    "Actively use familiar, concrete pictograms for objects actually mentioned in the panel text, "
    "such as a house for a home, coins for money, a sheet of paper for a document or a courthouse for a court. "
    "These are examples, not required props: include only supported objects. "
    "Object-focused panels may use large simple pictograms without people; keep labels in the adjacent text. "
    "The adjacent text explains legal meaning; the image supports it without inventing an event. "
    "Do not rely on color, checkmarks, crosses, arrows or metaphors alone to convey a legal conclusion. "
    "Keep the material respectful of adults and avoid decorative crowds, clutter and invented emotions. "
)

# Advisory surface checks, not a sentence parser or legal/visual verifier.
MULTIPLE_SENTENCES = re.compile(r"[.!?。！？][\s\"'”’]*[가-힣A-Za-z]")
DOUBLE_NEGATIVE = re.compile(r"없지\s*않|않을\s*수\s*없|아니(?:라고|라|지)\s*(?:볼\s*수\s*)?없|하지\s*아니한\s*것은\s*아니")
UNCLEAR_REFERENCE = re.compile(r"그것|그들|전술한|상술한|앞서\s*본|위와\s*같은|(?<![가-힣])그[은는이가을를](?![가-힣])")


def text_hints(text, unexplained_terms=()):
    hints = []
    if MULTIPLE_SENTENCES.search(text):
        hints.append(("long-sentence", "문장을 나누어 주세요", "한 문장 항목에 여러 문장이 있어요. 각각 별도 문장으로 나누고 원문 근거를 유지해 주세요."))
    if DOUBLE_NEGATIVE.search(text):
        hints.append(("hard-term", "이중부정을 쉽게 풀어 주세요", "부정의 뜻이 바뀌지 않게 원문과 비교하고 직접적인 짧은 표현으로 풀어 주세요."))
    if UNCLEAR_REFERENCE.search(text):
        hints.append(("relations", "누구 또는 무엇인지 분명히 써 주세요", "대명사나 앞의 내용 참조 대신 인물 이름과 대상을 반복하는 편이 이해하기 쉬워요."))
    terms = [term for term in unexplained_terms if term and term in text]
    if terms:
        hints.append(("hard-term", "어려운 말을 가까이에서 설명해 주세요", "이 카드에 용어집의 풀이가 없어요: " + ", ".join(terms) + ". 처음 나오는 곳에서 짧게 풀어 주세요. 용어집에 없다는 뜻이나 법적 정의 오류 판정은 아닙니다."))
    return hints
