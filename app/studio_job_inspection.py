"""Owner-only, read-only views of persisted image jobs, never recompiled prompts."""
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from .store import fail
from .studio_models import Wire
from .studio_scene import SceneComposition


class ImageJobSummary(Wire):
    jobId: str
    projectId: str
    mode: Literal["single", "storyboard4", "library"]
    cardIds: list[str]
    status: str
    submissionState: Literal["not-submitted", "uncertain", "accepted", "not-applicable"]
    providerJobId: str | None
    workflowAvailable: bool
    createdAt: float | None = Field(description="Unix seconds; null for historical jobs without a timestamp.")
    preparedAt: float | None
    assetIds: list[str]


class ImageJobList(Wire):
    jobs: list[ImageJobSummary]
    nextOffset: int | None


class WorkflowPrompt(Wire):
    nodeId: str
    classType: str
    role: Literal["positive", "negative", "other"]
    text: str = Field(description="Exact saved encoder input, including empty negative prompts. Not regenerated.")


class WorkflowSettings(Wire):
    nodeId: str
    classType: str
    values: dict[str, str | int | float | bool]


class WorkflowReference(Wire):
    imageNumber: int
    loadNodeId: str
    filename: str | None
    purpose: Literal["character", "mood", "unknown"]
    partyId: str | None
    assetId: str | None


class CutDesign(Wire):
    cardId: str
    composition: SceneComposition | None
    legacyPrompt: str | None = Field(description="Original free-form LLM plan for old jobs/portraits, not the final encoder input.")
    mainMessage: str | None
    semanticBoundary: str | None
    alt: str | None
    meaning: str | None


class ImageJobDetail(ImageJobSummary):
    prompts: list[WorkflowPrompt]
    settings: list[WorkflowSettings]
    references: list[WorkflowReference]
    designs: list[CutDesign]


def summary(row, job):
    target = row["card_id"]
    storyboard = target.startswith("storyboard4:")
    plan = job.get("plan", {})
    card_ids = [p["cardId"] for p in plan.get("panels", [])] if storyboard else [target]
    available = bool(job.get("workflow")) and job["status"] not in {"pending", "planned"}
    submission = ("not-applicable" if job.get("library_portrait") else
                  "accepted" if job.get("provider_job_id") else
                  "uncertain" if job["status"] in {"submitting", "submission_unknown"} else "not-submitted")
    assets = job.get("asset_ids", [job["asset_id"]] if job.get("asset_id") else [])
    if job.get("sheet_id"):
        assets = [job["sheet_id"], *assets]
    return ImageJobSummary(jobId=row["id"], projectId=row["project_id"],
        mode="library" if job.get("library_portrait") else "storyboard4" if storyboard else "single",
        cardIds=card_ids, status=job["status"], submissionState=submission,
        providerJobId=job.get("provider_job_id"), workflowAvailable=available,
        createdAt=job.get("created_at"), preparedAt=job.get("prepared_at") if available else None,
        assetIds=assets)


# Only compiler-owned settings are exposed: no raw graph, URL, headers or credentials.
SETTING_FIELDS = {
    "UNETLoader": ("unet_name", "weight_dtype"), "CLIPLoader": ("clip_name", "type", "device"),
    "VAELoader": ("vae_name",), "KSampler": ("seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"),
    "Flux2Scheduler": ("steps", "width", "height"), "RandomNoise": ("noise_seed",),
    "KSamplerSelect": ("sampler_name",), "FluxGuidance": ("guidance",),
    "EmptySD3LatentImage": ("width", "height", "batch_size"),
    "EmptyFlux2LatentImage": ("width", "height", "batch_size"),
    "ModelSamplingAuraFlow": ("shift",), "CFGNorm": ("strength",),
    "ImageScaleBy": ("upscale_method", "scale_by"),
}


def upstream(graph, link, seen=None):
    seen = set() if seen is None else seen
    if not isinstance(link, list) or len(link) != 2 or not isinstance(link[0], str) or link[0] in seen:
        return seen
    node_id = link[0]
    seen.add(node_id)
    for value in graph.get(node_id, {}).get("inputs", {}).values():
        upstream(graph, value, seen)
    return seen


def detail(row, job):
    base = summary(row, job)
    graph = job.get("workflow", {}) if base.workflowAvailable else {}
    positive, negative = set(), set()
    for node in graph.values():
        inputs = node.get("inputs", {})
        for key in ("positive", "conditioning"):
            positive.update(upstream(graph, inputs.get(key)))
        negative.update(upstream(graph, inputs.get("negative")))
    prompts, settings, references = [], [], []
    for node_id, node in graph.items():
        kind, inputs = node["class_type"], node["inputs"]
        if kind in {"CLIPTextEncode", "TextEncodeQwenImageEditPlus"}:
            text = inputs.get("prompt" if kind == "TextEncodeQwenImageEditPlus" else "text")
            if isinstance(text, str):
                prompts.append(WorkflowPrompt(nodeId=node_id, classType=kind,
                    role="positive" if node_id in positive else "negative" if node_id in negative else "other", text=text))
        values = {key: inputs[key] for key in SETTING_FIELDS.get(kind, ())
                  if key in inputs and isinstance(inputs[key], (str, int, float, bool))}
        if values:
            settings.append(WorkflowSettings(nodeId=node_id, classType=kind, values=values))
    # Resolve actual encoder slots through the persisted resize chain, not today's project.
    encoder = next((n for n in graph.values() if n["class_type"] == "TextEncodeQwenImageEditPlus"), None)
    for number in range(1, 4) if encoder else ():
        ids = upstream(graph, encoder["inputs"].get(f"image{number}"))
        load_id = next((i for i in ids if graph.get(i, {}).get("class_type") == "LoadImage"), None)
        if load_id is None:
            continue
        filename = graph[load_id]["inputs"].get("image")
        # A future provider might return a signed URL; never publish that URL.
        filename = filename if isinstance(filename, str) and not urlsplit(filename).scheme and "?" not in filename else None
        binding = next((r for r in job.get("reference_bindings", []) if r["imageNumber"] == number), {})
        references.append(WorkflowReference(imageNumber=number, loadNodeId=load_id, filename=filename,
            purpose=binding.get("purpose", "unknown"), partyId=binding.get("partyId"), assetId=binding.get("assetId")))
    plan = job.get("plan", {}) if job["status"] != "pending" else {}
    panels = plan.get("panels", []) if base.mode == "storyboard4" else ([plan] if plan else [])
    designs = [CutDesign(cardId=p.get("cardId", row["card_id"]), composition=p.get("composition"),
        legacyPrompt=None if p.get("composition") else p.get("prompt"),
        mainMessage=p.get("mainMessage"), semanticBoundary=p.get("semanticBoundary"),
        alt=p.get("alt"), meaning=p.get("meaning")) for p in panels]
    return ImageJobDetail(**base.model_dump(), prompts=prompts, settings=settings, references=references, designs=designs)


class ImageJobInspection:
    def __init__(self, store):
        self.store = store

    def listing(self, project_id, owner, limit, offset):
        self.store.get(project_id, owner)
        with self.store.store.connect() as db:
            rows = db.execute("""SELECT id,project_id,card_id,body FROM studio_image_jobs
                WHERE project_id=? AND owner=? ORDER BY rowid DESC LIMIT ? OFFSET ?""",
                (project_id, owner, limit + 1, offset)).fetchall()
        return ImageJobList(jobs=[summary(row, json.loads(row["body"])) for row in rows[:limit]],
                            nextOffset=offset + limit if len(rows) > limit else None)

    def get(self, project_id, owner, job_id):
        self.store.get(project_id, owner)
        with self.store.store.connect() as db:
            row = db.execute("""SELECT id,project_id,card_id,body FROM studio_image_jobs
                WHERE id=? AND project_id=? AND owner=?""", (job_id, project_id, owner)).fetchone()
        if row is None:
            fail(404, "not_found", "그림 생성 작업을 찾을 수 없어요.")
        return detail(row, json.loads(row["body"]))
