"""Compile bounded scene data, never LLM-authored executable node classes.

Graph based on Comfy-Org/workflow_templates video_wan2_2_5B_ti2v.
T2V only: omit the optional start_image branch. Model availability is
checked against Cloud object_info before submission; no model downloads.
"""
from .store import fail
from .video_models import CompiledScene, VideoScene, WorkflowNode

SIZES = {"16:9": (1024, 576), "9:16": (576, 1024), "1:1": (768, 768)}
ALLOWED = {"UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "ModelSamplingSD3",
           "Wan22ImageToVideoLatent", "KSampler", "VAEDecode", "CreateVideo", "SaveVideo"}


def compile_scene(scene: VideoScene, aspect_ratio: str, seed: int, prefix: str):
    width, height = SIZES[aspect_ratio]
    frames = scene.duration_seconds * 24 + 1  # Wan temporal shape: 4n + 1.
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    graph = {
        "1": node("UNETLoader", unet_name="wan2.2_ti2v_5B_fp16.safetensors", weight_dtype="default"),
        "2": node("CLIPLoader", clip_name="umt5_xxl_fp8_e4m3fn_scaled.safetensors", type="wan", device="default"),
        "3": node("VAELoader", vae_name="wan2.2_vae.safetensors"),
        "4": node("CLIPTextEncode", clip=["2", 0], text=scene.visual_prompt),
        "5": node("CLIPTextEncode", clip=["2", 0], text=scene.negative_prompt),
        "6": node("ModelSamplingSD3", model=["1", 0], shift=8.0),
        "7": node("Wan22ImageToVideoLatent", vae=["3", 0], width=width, height=height, length=frames, batch_size=1),
        "8": node("KSampler", model=["6", 0], positive=["4", 0], negative=["5", 0], latent_image=["7", 0], seed=seed, steps=20, cfg=5.0, sampler_name="uni_pc", scheduler="simple", denoise=1.0),
        "9": node("VAEDecode", samples=["8", 0], vae=["3", 0]),
        "10": node("CreateVideo", images=["9", 0], fps=24.0),
        "11": node("SaveVideo", video=["10", 0], filename_prefix=prefix, format="auto", **{"format.codec": "auto"}),
    }
    validate_graph(graph)
    return CompiledScene(scene=scene, seed=seed, width=width, height=height, frames=frames,
                         actual_duration_seconds=frames / 24, workflow=graph)


def validate_graph(graph):
    visited, active = set(), set()
    def visit(key):
        if key in active or key not in graph:
            fail(422, "invalid_video_graph", "영상 노드 연결에 순환 또는 누락이 있습니다.")
        if key in visited:
            return
        active.add(key)
        node = graph[key]
        if node.class_type not in ALLOWED:
            fail(422, "invalid_video_graph", "허용되지 않은 영상 노드입니다.")
        for value in node.inputs.values():
            if isinstance(value, list):
                if len(value) != 2 or not isinstance(value[0], str) or type(value[1]) is not int or value[1] < 0:
                    fail(422, "invalid_video_graph", "잘못된 영상 노드 링크입니다.")
                visit(value[0])
        active.remove(key)
        visited.add(key)
    for key in graph:
        visit(key)


def validate_scenes(doc, request, scenes):
    selected = set(request.block_ids)
    known = {b.id: b for b in doc.blocks}
    if len(selected) != len(request.block_ids) or not selected.issubset(known):
        fail(422, "invalid_video_blocks", "현재 문서의 카드 ID를 중복 없이 선택하세요.")
    if len(scenes) > request.max_scenes:
        fail(422, "video_scene_limit", "요청한 최대 장면 수를 초과했습니다.")
    covered = set()
    for scene in scenes:
        refs = set(scene.source_block_ids)
        if len(refs) != len(scene.source_block_ids) or not refs.issubset(selected):
            fail(422, "invalid_video_evidence", "영상 장면이 선택하지 않은 카드를 참조합니다.")
        if len({(known[k].kind, known[k].speaker_id) for k in refs}) != 1:
            fail(422, "mixed_video_claims", "서로 다른 당사자 주장 또는 주장·판단·결정은 별도 장면으로 나누세요.")
        covered |= refs
    if covered != selected:
        fail(422, "missing_video_coverage", "선택한 모든 카드가 영상 계획에 포함되어야 합니다.")
