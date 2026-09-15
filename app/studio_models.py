"""The camelCase wire contract used by ihaerostudio-fe/lib/domain.

A frontend card contains multiple independently verified sentences. Preserve
that structure in requests and responses.
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=100)]
Text = Annotated[str, Field(max_length=10000)]
Revision = Annotated[int, Field(ge=0, strict=True)]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(Wire):
    tone: Literal["haeyo", "hamnida"]
    naming: Literal["initial", "role", "legal"]
    illustrations: Literal["with", "none"]


class Anchor(Wire):
    paragraphId: Id
    start: Revision
    end: Annotated[int, Field(gt=0, strict=True)]

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("근거 범위의 끝은 시작보다 뒤여야 합니다.")
        return self


class Flag(Wire):
    code: Id
    message: Text


class Item(Wire):
    id: Id
    anchors: list[Anchor] = Field(max_length=30)
    flags: list[Flag] = Field(max_length=30)


class Overview(Wire):
    caseName: Text
    caseNumber: Text
    court: Text
    decisionDate: Text


class Party(Item):
    sourceLabel: Text
    legalStatus: Text
    displayName: Text
    easyRole: Text


class KeyFact(Item):
    kind: Literal["money", "date", "period", "other"]
    label: Text
    value: Text


class Claim(Item):
    partyId: Id
    text: Text


class Finding(Item):
    text: Text
    claimIds: list[Id] = Field(max_length=300)
    stance: Literal["accepted", "rejected", "partial", "none"]


class Decision(Item):
    text: Text


class StructureContent(Wire):
    overview: Overview
    parties: list[Party] = Field(max_length=100)
    keyFacts: list[KeyFact] = Field(max_length=300)
    claims: list[Claim] = Field(max_length=300)
    findings: list[Finding] = Field(max_length=300)
    decisions: list[Decision] = Field(max_length=300)


class CaseStructure(StructureContent):
    projectId: Id
    revision: Revision


class Sentence(Wire):
    id: Id
    text: Text
    anchors: list[Anchor] = Field(max_length=30)
    origin: Literal["ai-draft", "ai-suggestion", "manual"]
    verified: bool


class Card(Wire):
    id: Id
    role: Literal["person", "background", "claim", "finding", "decision"]
    partyId: Id | None
    imageId: Id | None
    sentences: list[Sentence] = Field(min_length=1, max_length=100)


class Section(Wire):
    kind: Literal["people", "decision", "reasons", "glossary"]
    title: Text
    cards: list[Card] = Field(max_length=300)


class GlossaryTerm(Wire):
    id: Id
    term: Annotated[str, Field(min_length=1, max_length=100)]
    explanation: Text


class DocImage(Wire):
    id: Id
    src: Annotated[str, Field(min_length=1, max_length=8000000)]
    alt: Text
    meaning: Text
    source: Literal["library", "upload"]


class PartyName(Wire):
    partyId: Id
    displayName: Text


class Quote(Wire):
    """Evidence as the model writes it: text copied from one paragraph. The server locates it (see studio_domain.resolve_quotes)."""
    paragraphId: Id
    quote: Annotated[str, Field(min_length=1, max_length=4000)]


# What the model is asked to produce. Same shape as the wire models, except that every
# anchor is a quote; models cannot count UTF-16 offsets reliably, but they can copy text.
class AiParty(Party):
    anchors: list[Quote] = Field(max_length=30)


class AiKeyFact(KeyFact):
    anchors: list[Quote] = Field(max_length=30)


class AiClaim(Claim):
    anchors: list[Quote] = Field(max_length=30)


class AiFinding(Finding):
    anchors: list[Quote] = Field(max_length=30)


class AiDecision(Decision):
    anchors: list[Quote] = Field(max_length=30)


class AiStructureContent(Wire):
    overview: Overview
    parties: list[AiParty] = Field(max_length=100)
    keyFacts: list[AiKeyFact] = Field(max_length=300)
    claims: list[AiClaim] = Field(max_length=300)
    findings: list[AiFinding] = Field(max_length=300)
    decisions: list[AiDecision] = Field(max_length=300)


class AiSentence(Sentence):
    anchors: list[Quote] = Field(max_length=30)


class AiCard(Card):
    sentences: list[AiSentence] = Field(min_length=1, max_length=100)


class AiSection(Section):
    cards: list[AiCard] = Field(max_length=300)


class DraftContent(Wire):
    title: Text
    subtitle: Text
    partyNames: list[PartyName] = Field(max_length=100)
    sections: list[Section] = Field(min_length=4, max_length=4)
    glossary: list[GlossaryTerm] = Field(max_length=300)
    images: list[DocImage] = Field(max_length=100)

    @model_validator(mode="after")
    def section_order(self):
        if [s.kind for s in self.sections] != ["people", "decision", "reasons", "glossary"]:
            raise ValueError("people, decision, reasons, glossary 순서가 필요합니다.")
        allowed = {"people": {"person", "background"}, "decision": {"decision"},
                   "reasons": {"background", "claim", "finding"}, "glossary": set()}
        if any(c.role not in allowed[s.kind] for s in self.sections for c in s.cards):
            raise ValueError("카드 종류와 섹션이 맞지 않습니다.")
        return self


class AiDraftContent(DraftContent):
    sections: list[AiSection] = Field(min_length=4, max_length=4)


class EasyDocument(DraftContent):
    projectId: Id
    saveRevision: Revision
    contentRevision: Revision
    basedOnStructureRevision: Revision
    basedOnSettingsRevision: Revision


class CreateText(Wire):
    text: Annotated[str, Field(min_length=100, max_length=100000)]
    settings: Settings


class Rename(Wire):
    title: Annotated[str, Field(min_length=1, max_length=150)]


class Dismiss(Wire):
    key: Annotated[str, Field(min_length=1, max_length=300)]
    memo: Annotated[str, Field(max_length=2000)]


class Restore(Wire):
    key: Annotated[str, Field(min_length=1, max_length=300)]


class Complete(Wire):
    checklist: list[Literal["numbers", "relations", "images", "claims"]] = Field(max_length=4)


class SetPublic(Wire):
    publicationId: Id | None


class SentenceInput(Wire):
    sentenceId: Id
    text: Annotated[str, Field(min_length=1, max_length=10000)]


class TermInput(Wire):
    text: Annotated[str, Field(min_length=1, max_length=10000)]


class ExplainInput(Wire):
    term: Annotated[str, Field(min_length=1, max_length=100)]
    context: Text


class ImageInput(Wire):
    cardId: Id


class Suggestions(Wire):
    suggestions: list[Text] = Field(min_length=1, max_length=5)


class Sentences(Wire):
    sentences: list[Text] = Field(min_length=1, max_length=20)


class Terms(Wire):
    terms: list[Text] = Field(max_length=30)


class Explanation(Wire):
    explanation: Text
