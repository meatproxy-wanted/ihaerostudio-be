"""Opt-in four-scene experiment. Never falls back to new paid single-card jobs."""
import hashlib
import io
import json
import secrets
import time

from PIL import Image
from pydantic import Field

from .comfy import ComfyFailure
from .image_workflows import (ILLUSTRATION_STYLE, MOOD_ALLOWED, VISIBLE_FACES, REFERENCE_STEPS,
                              compile_reference_image)
from .models import uid
from .store import fail
from .studio_characters import reference_bytes
from .studio_domain import cards, invalidate_review, summarize_document, timestamp
from .studio_generation import IllustrationPlan, image_context
from .studio_models import Id, Wire
from .studio_scene import SCENE_TASK, SceneIllustrationPlan
from .studio_store import StudioStore, asset_size, asset_url
from .video_models import WorkflowNode
from .easy_read import VISUAL_VERSION as VERSION, VISUAL_COMPOSITION

LEGACY_PRESET = "qwen-edit-2511-storyboard4-2048-40steps-v1"
PRESET = "qwen-edit-2511-storyboard4-1024-50steps-concise-v4"
SHEET_SIZE = 1024
PANEL_SIZE = SHEET_SIZE // 2
MAX_SHEET_BYTES = 8 * 1024 * 1024
WAIT_SECONDS = 45
POSITIONS = ("TOP LEFT", "TOP RIGHT", "BOTTOM LEFT", "BOTTOM RIGHT")


class Panel(SceneIllustrationPlan):
    cardId: Id


class StoryboardPlan(Wire):
    panels: list[Panel] = Field(min_length=1, max_length=4)


TASK = SCENE_TASK + """\n# 4컷 출력
입력 panels 순서대로 카드당 독립된 컷 하나를 출력하고 cardId와 순서를 유지하세요.
Picture 번호는 묶음 전체에서 동일합니다. 컷 사이에 새 사건이나 관계를 만들지 마세요.
각 prompt는 해당 컷의 장면 설명만 적고 전체 시트 배치 지시는 넣지 마세요.
"""


def context_for(state, card_ids):
    if not 1 <= len(card_ids) <= 4 or len(set(card_ids)) != len(card_ids):
        fail(422, "storyboard_cards_invalid", "4컷 대상은 서로 다른 카드 1~4개여야 해요.")
    lookup = {c["id"]: c for c in cards(state["document"])}
    panels, references = [], []
    for card_id in card_ids:
        card = lookup.get(card_id)
        if card is None or card["role"] == "person":
            fail(409, "image_card_changed", "4컷 대상 장면 카드가 바뀌었어요.")
        context = image_context(state, card_id)
        context.pop("styleReference", None)
        for reference in context["characterReferences"]:
            if not any(r["partyId"] == reference["partyId"] for r in references):
                references.append({**reference, "imageNumber": len(references) + 1})
        panels.append({"cardId": card_id, "imageId": card["imageId"], **context})
    numbers = {r["partyId"]: r["imageNumber"] for r in references}
    for panel in panels:
        panel["characterReferences"] = [{**r, "imageNumber": numbers[r["partyId"]]}
                                        for r in panel["characterReferences"]]
    return {"panels": panels, "characterReferences": references}


def digest_for(context, preset=PRESET):
    return hashlib.sha256(json.dumps([preset, context], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_capacity(state, count, size=0):
    if len(state["assets"]) + count > 100 or sum(asset_size(a) for a in state["assets"]) + size > 12 * 1024 * 1024:
        fail(413, "image_limit", "4컷 원본과 잘린 그림을 저장하면 자료의 그림 개수 또는 용량 한도를 넘어요.")


def compile_storyboard(plan, roles, seed, prefix, references, profiles):
    """Independent 1024 latent, not an upscale of the first character reference."""
    if not 1 <= len(references) <= 3:
        raise ValueError("Storyboard requires one to three character references")
    prompt = (ILLUSTRATION_STYLE +
              "Create one square 2-by-2 storyboard with four equal quadrants and a thin white central gutter. "
              "Use the reference images for character appearance. "
              "Picture numbers below identify the corresponding input images; change only poses and situations. "
              "Keep each scene inside its quadrant, away from the center seams. "
              "No text, numbers, labels or speech bubbles. " + VISIBLE_FACES + VISUAL_COMPOSITION)
    for index, position in enumerate(POSITIONS):
        if index >= len(plan.panels):
            prompt += f"\n{position} quadrant: leave entirely blank white; no scene, people or objects."
            continue
        panel = plan.panels[index]
        prompt += f"\n{position} quadrant ONLY: {panel.rendered_prompt()}"
        if roles[index] == "decision":
            prompt += (" Court-order consideration only, not performance of the order. Hands apart; "
                       "no giving, receiving or exchange of money, envelopes, papers, keys or any object.")
    graph = compile_reference_image(IllustrationPlan(prompt="Temporary compiler input", alt="4컷", meaning="4컷"),
                                    seed, prefix, references)
    # Replace, do not append to the single-scene/contact-sheet prohibition.
    graph["4"].inputs["prompt"] = prompt
    graph["6"] = WorkflowNode(class_type="EmptySD3LatentImage",
                              inputs={"width": SHEET_SIZE, "height": SHEET_SIZE, "batch_size": 1})
    return graph


def normalized_sheet(image, expected_size):
    """Downsize already paid legacy results without creating a new Comfy job."""
    with Image.open(io.BytesIO(image)) as source:
        if expected_size not in {1024, 2048} or source.size != (expected_size, expected_size):
            fail(502, "storyboard_size_invalid", "4컷 원본 크기가 제출한 워크플로와 달라서 적용하지 않았어요.")
        source.load()
        cleaned = source.convert("RGB")
        if expected_size != SHEET_SIZE:
            cleaned = cleaned.resize((SHEET_SIZE, SHEET_SIZE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        cleaned.save(out, "PNG")
        return out.getvalue()


def crop_sheet(image, count):
    if not 1 <= count <= 4:
        raise ValueError("Expected one to four panels")
    with Image.open(io.BytesIO(image)) as source:
        if source.size != (SHEET_SIZE, SHEET_SIZE):
            fail(502, "storyboard_size_invalid", "4컷 저장 시트가 1024×1024가 아니어서 적용하지 않았어요.")
        source.load()
        result = []
        for index in range(count):
            x, y = (index % 2) * PANEL_SIZE, (index // 2) * PANEL_SIZE
            out = io.BytesIO()
            source.crop((x, y, x + PANEL_SIZE, y + PANEL_SIZE)).convert("RGB").save(out, "PNG")
            result.append(out.getvalue())
        return result


class StudioStoryboard:
    def __init__(self, generation):
        self.g = generation

    def run(self, project_id, owner, group):
        g = self.g
        state = g.store.get(project_id, owner)[0]
        context = context_for(state, group["cardIds"])
        preset = group.get("preset", LEGACY_PRESET)
        if digest_for(context, preset) != group["digest"]:
            fail(409, "image_card_changed", "4컷 생성 중 카드나 인물 기준이 바뀌었어요. 자동 재생성하지 않습니다.")
        references = context["characterReferences"]
        if not 1 <= len(references) <= 3:
            fail(422, "character_reference_limit", "4컷 실험은 묶음 전체의 기준 인물 1~3명이 필요해요. 레퍼런스를 생략하지 않습니다.")
        g.cloud.require_key()
        pixels = [reference_bytes(g.store, state, owner, r) for r in references]
        check_capacity(state, 1 + len(group["cardIds"]))
        job = g.claim(project_id, owner, "storyboard4:" + group["id"], group["digest"])
        try:
            if job["status"] == "ready":
                return
            if job["status"] in {"failed", "canceled", "expired"}:
                fail(502, "image_generation_failed", "4컷 생성이 실패했어요. 자동으로 새 유료 작업을 만들지 않습니다.")
            if job["status"] in {"planned", "prepared"} and job.get("easy_read_revision") != VERSION:
                # Replan only definitely unsubmitted work. Paid/uncertain jobs
                # continue with their original plan, graph and provider job ID.
                job["status"] = "pending"
                g.persist(job)
            if job["status"] == "pending":
                plan = g.provider.call(TASK, context, StoryboardPlan)
                if [p.cardId for p in plan.panels] != group["cardIds"]:
                    fail(502, "storyboard_plan_invalid", "4컷 설계의 카드 순서가 달라서 생성을 요청하지 않았어요.")
                job.update(status="planned", plan=plan.model_dump(), reference_uploads=[], seed=secrets.randbits(48),
                           easy_read_revision=VERSION)
                g.persist(job)
            if job["status"] == "prepared" and (
                job["workflow"]["6"]["inputs"].get("width") != SHEET_SIZE or
                job["workflow"]["11"]["inputs"].get("steps") != REFERENCE_STEPS):
                # Definitely unsubmitted work can use the smaller compiler;
                # accepted/uncertain jobs retain their original paid graph.
                job["status"] = "planned"
                g.persist(job)
            if job["status"] == "prepared" and time.time() - job["prepared_at"] > 23 * 3600:
                job.update(status="planned", reference_uploads=[])
                g.persist(job)
            if job["status"] == "planned":
                uploads = job["reference_uploads"]
                if any(time.time() - item["uploaded_at"] > 23 * 3600 for item in uploads):
                    uploads.clear()
                    g.persist(job)
                for index in range(len(uploads), len(pixels)):
                    filename = g.cloud.upload_reference(pixels[index], f"ihaero-{job['id']}-character-{index + 1}.png")
                    uploads.append({"filename": filename, "uploaded_at": time.time()})
                    g.persist(job)
                filenames = [u["filename"] for u in uploads]
                graph = compile_storyboard(StoryboardPlan.model_validate(job["plan"]),
                    [p["role"] for p in context["panels"]], job["seed"], "ihaero-" + job["id"], filenames, [])
                check = g.cloud.preflight(graph, allowed=MOOD_ALLOWED, preset=PRESET, uploaded_images=filenames)
                if not check.compatible:
                    fail(503, "comfy_workflow_unavailable", "Comfy에서 고해상도 4컷 워크플로를 사용할 수 없어요.")
                job.update(status="prepared", prepared_at=time.time(), workflow={k: n.model_dump() for k, n in graph.items()})
                g.persist(job)
            if job["status"] == "prepared":
                latest = g.store.get(project_id, owner)[0]
                if (digest_for(context_for(latest, group["cardIds"]), preset) != group["digest"] or
                        latest["image_batch"]["status"] != "running" or
                        latest["image_batch"].get("storyboardGroup") != group):
                    fail(409, "image_card_changed", "4컷 대상이 바뀌거나 생성이 중단됐어요.")
                job["status"] = "submitting"
                g.persist(job)
                try:
                    parsed = g.cloud.parse_job(g.cloud.submit({k: WorkflowNode.model_validate(n)
                        for k, n in job["workflow"].items()}, job["id"]), media_type="image")
                except ComfyFailure as error:
                    job["status"] = "submission_unknown" if error.uncertain else "prepared"
                    g.persist(job)
                    raise
                job.update(status=parsed["status"], provider_job_id=parsed["provider_job_id"], poll_url=parsed["poll_url"])
                g.persist(job)
            if job["status"] in {"submitting", "submission_unknown"}:
                fail(503, "image_submission_unknown", "4컷 접수 결과가 불확실해 중복 생성을 막았어요. 서버 작업을 확인해 주세요.")
            deadline = time.monotonic() + WAIT_SECONDS
            while True:
                parsed = g.cloud.parse_job(g.cloud.request("GET", job["poll_url"]), job["provider_job_id"], media_type="image")
                job["status"] = parsed["status"]
                g.persist(job)
                if job["status"] == "succeeded":
                    if not parsed["outputs"]:
                        fail(502, "image_missing_output", "4컷 생성 결과가 없어요.")
                    # Preserve input dimensions for validation, then downsize legacy sheets.
                    image = g.download(parsed["outputs"][0].asset_id, job["provider_job_id"],
                                       max_side=4096, max_bytes=MAX_SHEET_BYTES)
                    return self.finish(job, project_id, owner, group, image)
                if job["status"] in {"failed", "canceled", "expired"}:
                    fail(502, "image_generation_failed", "4컷 생성이 완료되지 않았어요. 자동 재생성하지 않습니다.")
                if time.monotonic() >= deadline:
                    fail(503, "image_in_progress", "4컷을 생성하고 있어요. 같은 준비 요청으로 진행 상황을 확인해요.")
                time.sleep(2)
        except ComfyFailure as error:
            fail(503, error.code, "Comfy 4컷 작업을 확인하지 못했어요. 서버 설정과 생성 작업을 확인해 주세요.")
        finally:
            with g.store.store.connect() as db:
                db.execute("UPDATE studio_image_jobs SET lease_until=0 WHERE id=?", (job["id"],))

    def finish(self, job, project_id, owner, group, image):
        g = self.g
        image = normalized_sheet(image, job["workflow"]["6"]["inputs"]["width"])
        crops = crop_sheet(image, len(group["cardIds"]))
        plan = StoryboardPlan.model_validate(job["plan"])
        sheet_id = job["id"]
        outputs = [(sheet_id, image, "4컷 원본 삽화", "검토용 4컷 원본")]
        outputs += [(uid(), data, panel.alt, panel.meaning) for data, panel in zip(crops, plan.panels)]
        with g.store.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM studio_projects WHERE id=? AND owner=? AND deleted=0", (project_id, owner)).fetchone()
            if not row:
                fail(404, "not_found", "자료를 찾을 수 없어요.")
            state = json.loads(row["body"])
            batch = state.get("image_batch", {})
            if (batch.get("status") != "running" or batch.get("storyboardGroup") != group or
                    digest_for(context_for(state, group["cardIds"]), group.get("preset", LEGACY_PRESET)) != group["digest"]):
                fail(409, "image_card_changed", "4컷 생성 중 문서가 바뀌었어요. 그림을 덮어쓰지 않았습니다.")
            check_capacity(state, len(outputs), sum(len(o[1]) for o in outputs))
            lookup = {c["id"]: c for c in cards(state["document"])}
            for index, (asset_id, data, alt, meaning) in enumerate(outputs):
                asset = {"id": asset_id, "src": asset_url(g.config.public_base_url, asset_id), "alt": alt,
                         "meaning": meaning, "source": "library"}
                StudioStore.insert_asset(db, asset_id, project_id, owner, "image/png", data)
                state["assets"].append({**asset, "byteSize": len(data), "storyboardSheetId": sheet_id,
                                        "storyboardPanel": index - 1 if index else None})
                if index:
                    card_id = group["cardIds"][index - 1]
                    state["document"]["images"].append(asset)
                    lookup[card_id]["imageId"] = asset_id
                    batch["completed"].append(card_id)
            batch.setdefault("storyboardSheets", []).append({"sheetId": sheet_id, "cardIds": group["cardIds"]})
            batch.pop("storyboardGroup")
            if len(batch["completed"]) == len(batch["targets"]):
                batch["status"] = "ready"
            state["document"]["saveRevision"] += 1
            state["document"]["contentRevision"] += 1
            invalidate_review(state)
            state["review"] = None
            state["project"]["review"].update(checkedContentRevision=None, openRequiredCount=None)
            state["project"]["updatedAt"] = timestamp()
            state["project"]["document"] = summarize_document(state["document"])
            db.execute("UPDATE studio_projects SET body=?,version=version+1,updated_at=? WHERE id=? AND owner=?",
                       (json.dumps(state), state["project"]["updatedAt"], project_id, owner))
            job.update(status="ready", sheet_id=sheet_id, asset_ids=[o[0] for o in outputs[1:]])
            db.execute("UPDATE studio_image_jobs SET body=? WHERE id=?", (json.dumps(job), job["id"]))
