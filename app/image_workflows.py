"""Bounded Flux Schnell API graph based on Comfy-Org's official template.

GPT supplies only illustration text, never executable nodes or model names.
https://github.com/Comfy-Org/workflow_templates/blob/main/templates/flux_schnell_full_text_to_image.json
"""
from .video_models import WorkflowNode
from .video_workflows import validate_graph

ALLOWED = {"UNETLoader", "DualCLIPLoader", "VAELoader", "CLIPTextEncodeFlux", "ConditioningZeroOut",
           "EmptySD3LatentImage", "KSampler", "VAEDecode", "SaveImage"}
PRESET = "flux-schnell-illustration-v1"


def compile_image(illustration, seed, prefix):
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    prompt = ("Respectful adult educational illustration, simple flat shapes, calm colors, white background. "
              "Anonymous adults, no real likeness, no letters, numbers, logos or speech text. " + illustration.prompt)
    graph = {
        "1": node("UNETLoader", unet_name="flux1-schnell.safetensors", weight_dtype="default"),
        "2": node("DualCLIPLoader", clip_name1="clip_l.safetensors", clip_name2="t5xxl_fp16.safetensors", type="flux", device="default"),
        "3": node("VAELoader", vae_name="ae.safetensors"),
        "4": node("CLIPTextEncodeFlux", clip=["2", 0], clip_l=prompt, t5xxl=prompt, guidance=3.5),
        "5": node("ConditioningZeroOut", conditioning=["4", 0]),
        "6": node("EmptySD3LatentImage", width=768, height=768, batch_size=1),
        "7": node("KSampler", model=["1", 0], positive=["4", 0], negative=["5", 0], latent_image=["6", 0], seed=seed, steps=4, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "8": node("VAEDecode", samples=["7", 0], vae=["3", 0]),
        "9": node("SaveImage", images=["8", 0], filename_prefix=prefix),
    }
    validate_graph(graph, ALLOWED)
    return graph
