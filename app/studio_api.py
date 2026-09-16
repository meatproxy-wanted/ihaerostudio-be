"""HTTP implementation of every ihaerostudio-fe ApiClient feature group."""
import copy
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from . import studio_models as wire
from . import studio_provider
from .models import uid
from .sources import MAX_PDF_BYTES, extract_pdf, source_from_pages
from .store import fail
from .studio_domain import (apply_naming, invalidate_review, reader_content, require_document, review_items,
                            sentences, structure_content, summarize_document, sync_review, timestamp, validate_document, validate_structure)
from .studio_store import StudioStore, asset_size, asset_url
from .studio_images import MAX_UPLOAD, cleaned_upload
from .studio_generation import StudioGeneration

SAMPLE_TEXT = """사건: 데모용 가상 임대차보증금 반환 사건. 실제 사건이나 법원의 판결이 아닙니다.

원고: A씨, 집을 빌린 사람

피고: B씨, 집을 빌려준 사람

원고 주장: A씨는 임대차계약이 끝났고 집도 깨끗하게 돌려주었으므로 보증금 1,000만 원을 전부 돌려달라고 했습니다.

피고 주장: B씨는 수리비가 남았다고 주장했습니다.

법원 판단: 법원은 임대차계약이 끝났다고 판단했습니다.

법원 결정: 법원은 B씨에게 A씨의 보증금 1,000만 원을 판결이 확정된 날부터 30일 안에 돌려주라고 결정했습니다."""


def register_studio(api, config, base_store, provider, owner):
    store = StudioStore(base_store)
    api.state.studio = store
    generation = StudioGeneration(store, config, provider)
    api.state.studio_generation = generation
    router = APIRouter(prefix="/api/studio", tags=["FE 연동"])
    Owner = Annotated[str, Depends(owner)]

    def state_for(project_id, maker):
        return store.get(project_id, maker)

    def document_result(state):
        state["project"]["document"] = summarize_document(state["document"])
        return {"document": state["document"], "project": state["project"]}

    def clear_review(state):
        invalidate_review(state)
        state["review"] = None
        state["project"]["review"].update(checkedContentRevision=None, openRequiredCount=None)

    def changed_context(state):
        if state["document"]:
            state["document"]["saveRevision"] += 1
            state["document"]["contentRevision"] += 1
            document_result(state)
        clear_review(state)

    def create(source, settings, maker, pdf_bytes=None):
        # Only the extracted text is kept. The PDF itself is never read again, and judgments
        # carry personal details, so its size is recorded and the file is dropped.
        project_id, created_at = uid(), timestamp()
        projected = studio_provider.source_projection(project_id, source, pdf_bytes is not None)
        settings = settings.model_dump()
        structure = {**studio_provider.analyze(provider, source, projected, settings), "projectId": project_id, "revision": 0}
        validate_structure(structure, projected)
        project = {"id": project_id, "title": (structure["overview"]["caseName"] or "판결문")[:130] + " 쉬운 설명자료",
            "createdAt": created_at, "updatedAt": created_at, "settings": settings, "settingsRevision": 0,
            "source": {"kind": "pdf" if pdf_bytes is not None else "text", "fileName": source.filename,
                       "byteSize": pdf_bytes, "charCount": sum(len(p.text) for p in source.pages)},
            "caseNumber": structure["overview"]["caseNumber"] or None, "structureRevision": 0, "document": None,
            "review": {"checkedContentRevision": None, "openRequiredCount": None, "completedContentRevision": None, "completedAt": None},
            "publication": {"latestVersion": None, "latestContentRevision": None, "publicPublicationId": None, "publicVersion": None}}
        state = {"project": project, "source": projected, "structure": structure, "document": None,
                 "review": None, "completion": None, "dismissals": {}, "publications": [], "assets": []}
        return store.create(maker, state)

    @router.get("/projects")
    def listing(maker: Owner):
        return store.listing(maker)

    @router.post("/projects/text", status_code=201)
    def create_text(body: wire.CreateText, maker: Owner):
        if not 100 <= len(body.text.strip()) <= 100000:
            fail(422, "invalid_input", "판결문은 100자 이상 10만 자 이하로 입력해 주세요.")
        return create(source_from_pages([body.text]), body.settings, maker)

    @router.post("/projects/pdf", status_code=201)
    async def create_pdf(maker: Owner, file: Annotated[UploadFile, File()], settings: Annotated[str, Form()]):
        try:
            parsed = wire.Settings.model_validate_json(settings)
        except ValidationError:
            fail(422, "invalid_settings", "제작 설정을 확인해 주세요.")
        data = await file.read(MAX_PDF_BYTES + 1)
        await file.close()
        filename = (file.filename or "judgment.pdf").replace("\\", "/").split("/")[-1]
        source = await run_in_threadpool(extract_pdf, data, filename)
        return await run_in_threadpool(create, source, parsed, maker, len(data))

    @router.get("/projects/{project_id}")
    def get_project(project_id: str, maker: Owner):
        return state_for(project_id, maker)[0]["project"]

    @router.patch("/projects/{project_id}/title")
    def rename(project_id: str, body: wire.Rename, maker: Owner):
        if not body.title.strip():
            fail(422, "invalid_input", "제목을 입력해 주세요.")
        state, version = state_for(project_id, maker)
        state["project"]["title"] = body.title.strip()
        store.save(state, maker, version)
        return state["project"]

    @router.put("/projects/{project_id}/settings")
    def settings(project_id: str, body: wire.Settings, maker: Owner):
        state, version = state_for(project_id, maker)
        current = state["project"]
        new = body.model_dump()
        if current["settings"] != new:
            if current["settings"]["naming"] != new["naming"]:
                apply_naming(state["structure"], new["naming"])
                state["structure"]["revision"] += 1
                current["structureRevision"] = state["structure"]["revision"]
            current["settings"] = new
            current["settingsRevision"] += 1
            changed_context(state)
            store.save(state, maker, version)
        return current

    @router.delete("/projects/{project_id}", status_code=204)
    def remove(project_id: str, maker: Owner):
        store.remove(project_id, maker)
        return Response(status_code=204)

    @router.get("/projects/{project_id}/source")
    def source(project_id: str, maker: Owner):
        return state_for(project_id, maker)[0]["source"]

    @router.get("/projects/{project_id}/structure", response_model=wire.CaseStructure)
    def structure(project_id: str, maker: Owner):
        return state_for(project_id, maker)[0]["structure"]

    @router.put("/projects/{project_id}/structure")
    def save_structure(project_id: str, body: wire.CaseStructure, maker: Owner):
        state, version = state_for(project_id, maker)
        previous, new = state["structure"], body.model_dump()
        if body.projectId != project_id:
            fail(422, "invalid_project", "자료 ID가 다릅니다.")
        if body.revision != previous["revision"]:
            fail(409, "version_conflict", "다른 사건 구조가 저장됐어요. 새로고침해 주세요.")
        validate_structure(new, state["source"])
        if new != previous:
            content_changed = structure_content(previous) != structure_content(new)
            new["revision"] += int(content_changed)
            state["structure"] = new
            state["project"].update(structureRevision=new["revision"], caseNumber=new["overview"]["caseNumber"] or None)
            if content_changed:
                changed_context(state)
            store.save(state, maker, version)
        return {"structure": state["structure"], "project": state["project"]}

    @router.post("/projects/{project_id}/document/generate")
    def generate(project_id: str, maker: Owner):
        state, version = state_for(project_id, maker)
        previous = state["document"]
        draft = wire.DraftContent.model_validate(studio_provider.draft(provider, state)).model_dump()
        # AI cannot pre-approve sentences or attach arbitrary external assets.
        for sentence in sentences(draft):
            sentence.update(verified=False, origin="ai-draft")
        validate_document(draft, state)
        state["document"] = {**draft, "projectId": project_id,
            "saveRevision": previous["saveRevision"] + 1 if previous else 0,
            "contentRevision": previous["contentRevision"] + 1 if previous else 0,
            "basedOnStructureRevision": state["structure"]["revision"],
            "basedOnSettingsRevision": state["project"]["settingsRevision"]}
        clear_review(state)
        result = document_result(state)
        store.save(state, maker, version)
        return result

    @router.get("/projects/{project_id}/document", response_model=wire.EasyDocument)
    def document(project_id: str, maker: Owner):
        return require_document(state_for(project_id, maker)[0])

    @router.put("/projects/{project_id}/document")
    def save_document(project_id: str, body: wire.EasyDocument, maker: Owner):
        state, version = state_for(project_id, maker)
        previous, new = require_document(state), body.model_dump()
        if new["projectId"] != project_id:
            fail(422, "invalid_project", "자료 ID가 다릅니다.")
        if new["saveRevision"] != previous["saveRevision"]:
            fail(409, "version_conflict", "다른 문서가 저장됐어요. 새로고침해 주세요.")
        validate_document(new, state)
        content_changed = reader_content(previous, state) != reader_content(new, state)
        new.update(saveRevision=previous["saveRevision"] + 1,
                   contentRevision=previous["contentRevision"] + int(content_changed),
                   basedOnStructureRevision=previous["basedOnStructureRevision"],
                   basedOnSettingsRevision=previous["basedOnSettingsRevision"])
        state["document"] = new
        clear_review(state)
        result = document_result(state)
        store.save(state, maker, version)
        return result

    def assist_state(project_id, maker, sentence_id=None):
        state = state_for(project_id, maker)[0]
        doc = require_document(state)
        if sentence_id and not any(s["id"] == sentence_id for s in sentences(doc)):
            fail(404, "not_found", "문장을 찾을 수 없어요.")
        return state

    def ai_assist(state, task, payload, schema):
        if provider.name == "demo":
            fail(503, "ai_not_configured", "이 작업은 실제 AI 설정이 필요해요. 직접 편집하거나 관리자에게 문의해 주세요.")
        return provider.call(studio_provider.STUDIO_INSTRUCTIONS + task,
            {"input": payload, "source": state["source"], "structure": state["structure"], "settings": state["project"]["settings"]}, schema)

    @router.post("/projects/{project_id}/assist/simplify", response_model=wire.Suggestions)
    def simplify(project_id: str, body: wire.SentenceInput, maker: Owner):
        state = assist_state(project_id, maker, body.sentenceId)
        return ai_assist(state, "뜻과 수치·부정·주체를 보존한 쉬운 문장 후보 1~3개를 제안하세요. 자동 적용하지 않습니다.", body.model_dump(), wire.Suggestions)

    @router.post("/projects/{project_id}/assist/split", response_model=wire.Sentences)
    def split(project_id: str, body: wire.SentenceInput, maker: Owner):
        state = assist_state(project_id, maker, body.sentenceId)
        if provider.name == "demo":
            split_text = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", body.text) if s.strip()]
            return {"sentences": split_text if len(split_text) <= 20 else [body.text]}
        return ai_assist(state, "뜻을 보존하고 한 문장에 한 가지 내용으로 나눠 주세요.", body.model_dump(), wire.Sentences)

    @router.post("/projects/{project_id}/assist/terms", response_model=wire.Terms)
    def terms(project_id: str, body: wire.TermInput, maker: Owner):
        return ai_assist(assist_state(project_id, maker), "입력 text에서 어려운 법률 용어만 골라 주세요.", body.model_dump(), wire.Terms)

    @router.post("/projects/{project_id}/assist/explain", response_model=wire.Explanation)
    def explain(project_id: str, body: wire.ExplainInput, maker: Owner):
        return ai_assist(assist_state(project_id, maker), "사건 문맥에 맞게 용어를 성인 독자가 읽기 쉽게 설명하세요. 새로운 법적 조언을 덧붙이지 마세요.", body.model_dump(), wire.Explanation)

    @router.post("/projects/{project_id}/assist/images")
    def image_candidates(project_id: str, body: wire.ImageInput, maker: Owner):
        return generation.candidates(project_id, maker, body.cardId)

    @router.post("/projects/{project_id}/assist/upload-image")
    async def upload_image(project_id: str, maker: Owner, file: Annotated[UploadFile, File()],
                           alt: Annotated[str, Form(max_length=10000)] = "", meaning: Annotated[str, Form(max_length=10000)] = ""):
        state, version = state_for(project_id, maker)
        if len(state["assets"]) >= 100:
            fail(413, "image_limit", "그림은 자료당 100개까지 올릴 수 있어요.")
        data = await file.read(MAX_UPLOAD + 1)
        await file.close()
        cleaned, content_type = await run_in_threadpool(cleaned_upload, data, file.filename or "image.png")
        if sum(asset_size(i) for i in state["assets"]) + len(cleaned) > 12 * 1024 * 1024:
            fail(413, "image_limit", "자료의 그림 용량 한도를 넘었어요. 작은 그림을 사용해 주세요.")
        asset_id = uid()
        image = {"id": asset_id, "src": asset_url(config.public_base_url, asset_id),
                 "alt": alt, "meaning": meaning, "source": "upload"}
        # The bytes go to their own row; the project keeps only the URL, so documents stay small.
        store.put_asset(asset_id, project_id, maker, content_type, cleaned)
        state["assets"].append({**image, "byteSize": len(cleaned)})
        store.save(state, maker, version)
        return {"image": image}

    @router.get("/assets/{asset_id}")
    def asset(asset_id: str):
        found = store.asset(asset_id)
        if found is None:
            fail(404, "not_found", "그림을 찾을 수 없어요.")
        content_type, data = found
        return Response(content=data, media_type=content_type, headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @router.get("/projects/{project_id}/review")
    def latest_review(project_id: str, maker: Owner):
        return state_for(project_id, maker)[0]["review"]

    @router.post("/projects/{project_id}/review/run")
    def run_review(project_id: str, maker: Owner):
        state, version = state_for(project_id, maker)
        doc = require_document(state)
        state["review"] = {"projectId": project_id, "contentRevision": doc["contentRevision"], "ranAt": timestamp(), "items": review_items(state)}
        invalidate_review(state)
        sync_review(state)
        store.save(state, maker, version)
        return {"run": state["review"], "project": state["project"]}

    def dismissal(project_id, maker, keys, value):
        state, version = state_for(project_id, maker)
        if not state["review"]:
            fail(409, "review_required", "다시 점검해 주세요.")
        items = {i["key"]: i for i in state["review"]["items"]}
        if any(key not in items for key in keys):
            fail(404, "not_found", "검토 항목이 없어요.")
        for key in keys:
            items[key]["dismissal"] = value
            if value:
                state["dismissals"][key] = value
            else:
                state["dismissals"].pop(key, None)
        invalidate_review(state)
        sync_review(state)
        store.save(state, maker, version)
        return {"run": state["review"], "project": state["project"]}

    @router.post("/projects/{project_id}/review/dismiss")
    def dismiss(project_id: str, body: wire.Dismiss, maker: Owner):
        return dismissal(project_id, maker, [body.key], {"memo": body.memo.strip(), "at": timestamp()})

    @router.post("/projects/{project_id}/review/dismiss-all")
    def dismiss_all(project_id: str, body: wire.DismissAll, maker: Owner):
        # One save for the whole batch; per-item calls would race on the project version.
        return dismissal(project_id, maker, list(dict.fromkeys(body.keys)), {"memo": body.memo.strip(), "at": timestamp()})

    @router.post("/projects/{project_id}/review/restore")
    def restore(project_id: str, body: wire.Restore, maker: Owner):
        return dismissal(project_id, maker, [body.key], None)

    @router.post("/projects/{project_id}/review/complete")
    def complete(project_id: str, body: wire.Complete, maker: Owner):
        state, version = state_for(project_id, maker)
        doc = require_document(state)
        if not state["review"] or state["review"]["contentRevision"] != doc["contentRevision"]:
            fail(409, "review_required", "현재 문서를 다시 점검해 주세요.")
        if any(i["level"] == "required" and i["dismissal"] is None for i in state["review"]["items"]):
            fail(409, "review_incomplete", "확인이 필요한 항목이 남아 있어요.")
        needed = {"numbers", "relations", "claims"}
        if state["project"]["settings"]["illustrations"] == "with":
            needed.add("images")
        if set(body.checklist) != needed or len(body.checklist) != len(needed):
            fail(422, "checklist_incomplete", "최종 확인 항목을 모두 체크해 주세요.")
        completion = {"contentRevision": doc["contentRevision"], "completedAt": timestamp(), "checklist": body.checklist}
        state["completion"] = completion
        state["project"]["review"].update(completedContentRevision=doc["contentRevision"], completedAt=completion["completedAt"])
        store.save(state, maker, version)
        return {"completion": completion, "project": state["project"]}

    def publication_for(state, publication_id):
        publication = next((p for p in state["publications"] if p["id"] == publication_id), None)
        if publication is None:
            fail(404, "not_found", "게시본을 찾을 수 없어요.")
        return publication

    def summary(publication):
        return {k: v for k, v in publication.items() if k != "content"}

    @router.get("/projects/{project_id}/publications")
    def publications(project_id: str, maker: Owner):
        return [summary(p) for p in reversed(state_for(project_id, maker)[0]["publications"])]

    @router.get("/projects/{project_id}/publications/{publication_id}")
    def get_publication(project_id: str, publication_id: str, maker: Owner):
        return publication_for(state_for(project_id, maker)[0], publication_id)

    @router.post("/projects/{project_id}/publications")
    def publish(project_id: str, maker: Owner):
        state, version = state_for(project_id, maker)
        doc = require_document(state)
        reviewed = state["project"]["review"]["completedContentRevision"] == doc["contentRevision"]
        publication = state["publications"][-1] if state["publications"] else None
        if publication is None or publication["contentRevision"] != doc["contentRevision"] or publication["reviewed"] != reviewed:
            publication = {"id": uid(), "projectId": project_id, "version": len(state["publications"]) + 1,
                           "createdAt": timestamp(), "contentRevision": doc["contentRevision"], "reviewed": reviewed,
                           "content": copy.deepcopy(reader_content(doc, state))}
            state["publications"].append(publication)
        state["project"]["publication"].update(latestVersion=publication["version"], latestContentRevision=publication["contentRevision"])
        store.save(state, maker, version)
        return {"publication": summary(publication), "project": state["project"]}

    @router.put("/projects/{project_id}/public")
    def set_public(project_id: str, body: wire.SetPublic, maker: Owner):
        state, version = state_for(project_id, maker)
        public_version = None
        if body.publicationId:
            publication = publication_for(state, body.publicationId)
            if not publication["reviewed"]:
                fail(409, "review_required", "검토를 마친 게시본만 공개할 수 있어요.")
            public_version = publication["version"]
        state["project"]["publication"].update(publicPublicationId=body.publicationId, publicVersion=public_version)
        store.save(state, maker, version)
        return state["project"]

    @router.get("/reader/{project_id}")
    def reader(project_id: str):
        return store.public(project_id)

    @router.get("/demo/sample-text")
    def sample_text(maker: Owner):
        return SAMPLE_TEXT

    @router.post("/demo/reset", status_code=204)
    def reset(maker: Owner):
        if config.environment == "production" or provider.name != "demo":
            fail(403, "demo_only", "운영 서버에서는 데모 초기화를 사용할 수 없어요.")
        for project in store.listing(maker):
            store.remove(project["id"], maker)
        return Response(status_code=204)

    api.include_router(router)
