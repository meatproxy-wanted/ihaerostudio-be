"""Bounded Flux Schnell API graph based on Comfy-Org's official template.

GPT supplies only illustration text, never executable nodes or model names.
https://github.com/Comfy-Org/workflow_templates/blob/main/templates/flux_schnell_full_text_to_image.json
"""
from .video_models import WorkflowNode
from .video_workflows import validate_graph

ALLOWED = {"UNETLoader", "DualCLIPLoader", "VAELoader", "CLIPTextEncodeFlux", "ConditioningZeroOut",
           "EmptySD3LatentImage", "KSampler", "VAEDecode", "SaveImage"}
PRESET = "flux-schnell-illustration-v2"
REFERENCE_PRESET = "flux2-klein-9b-verified-identity-v2"
VISIBLE_FACES = (
    " If people are depicted, show each person's clearly visible face from the front or a three-quarter front view. "
    "Keep eyes, nose and mouth visible and unobstructed, with the whole head inside the frame. "
    "Use an eye-level camera placed in front of the people. No rear view, turned-away faces, "
    "over-the-shoulder camera, faceless silhouettes, or objects covering faces. "
    "For interactions, angle both people toward the viewer so both faces remain visible. "
    "Anonymous means a fictional identity, not a hidden or featureless face. "
    "For scenes without people, do not add a person just to satisfy this framing instruction."
)
REFERENCE_ALLOWED = {"UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "LoadImage",
                     "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent", "ConditioningZeroOut",
                     "CFGGuider", "EmptyFlux2LatentImage", "RandomNoise", "KSamplerSelect",
                     "Flux2Scheduler", "SamplerCustomAdvanced", "VAEDecode", "SaveImage"}


def compile_image(illustration, seed, prefix):
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    prompt = ("Respectful adult educational illustration, simple flat shapes, calm colors, white background. "
              "Anonymous adults, no real likeness, no letters, numbers, logos or speech text. " + illustration.prompt + VISIBLE_FACES)
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


def compile_reference_image(illustration, seed, prefix, references):
    """Bounded multi-image variant of Comfy-Org's Klein 9B distilled edit template.

    Each reference is an uploaded Cloud filename, in the same order as imageNumber in
    the scene-planning context. Positive and negative reference chains match the
    official image_flux2_klein_image_edit_9b_distilled.json workflow.
    """
    if not 1 <= len(references) <= 6:
        raise ValueError("Expected one to six character references")
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    prompt = ("Create one new respectful educational illustration with a white background. "
              "Use the supplied images as character identity references: keep each character's face, hair, "
              "clothes and colors consistent. Only include characters relevant to the described scene. "
              "Show their distinct roles and the direction of their relationship without swapping identities. "
              "Do not copy the reference layout or create a contact sheet. No text, numbers, logos. " + illustration.prompt +
              " Identity preservation takes priority over generic appearance descriptions in the scene: "
              "re-use the exact illustrated adults from the reference images. Preserve each person's apparent age, "
              "face shape, hairstyle, facial hair (including every beard or moustache), skin tone, clothing, "
              "shoes and body proportions. Do not remove facial hair, make an adult younger, change outfits, "
              "swap faces, or replace anyone with a generic new character. Change only their pose and scene." + VISIBLE_FACES)
    graph = {
        "1": node("UNETLoader", unet_name="flux-2-klein-9b.safetensors", weight_dtype="default"),
        "2": node("CLIPLoader", clip_name="qwen_3_8b_fp8mixed.safetensors", type="flux2", device="default"),
        "3": node("VAELoader", vae_name="flux2-vae.safetensors"),
        "4": node("CLIPTextEncode", clip=["2", 0], text=prompt),
        "5": node("ConditioningZeroOut", conditioning=["4", 0]),
        "6": node("EmptyFlux2LatentImage", width=768, height=768, batch_size=1),
        "7": node("RandomNoise", noise_seed=seed),
        "8": node("KSamplerSelect", sampler_name="euler"),
        "9": node("Flux2Scheduler", steps=4, width=768, height=768),
    }
    positive, negative = ["4", 0], ["5", 0]
    for index, filename in enumerate(references):
        load, scale, encode, pos, neg = [str(20 + index * 5 + offset) for offset in range(5)]
        graph[load] = node("LoadImage", image=filename)
        graph[scale] = node("ImageScaleToTotalPixels", image=[load, 0], upscale_method="lanczos", megapixels=0.6, resolution_steps=16)
        graph[encode] = node("VAEEncode", pixels=[scale, 0], vae=["3", 0])
        graph[pos] = node("ReferenceLatent", conditioning=positive, latent=[encode, 0])
        graph[neg] = node("ReferenceLatent", conditioning=negative, latent=[encode, 0])
        positive, negative = [pos, 0], [neg, 0]
    graph.update({
        "10": node("CFGGuider", model=["1", 0], positive=positive, negative=negative, cfg=1.0),
        "11": node("SamplerCustomAdvanced", noise=["7", 0], guider=["10", 0], sampler=["8", 0], sigmas=["9", 0], latent_image=["6", 0]),
        "12": node("VAEDecode", samples=["11", 0], vae=["3", 0]),
        "13": node("SaveImage", images=["12", 0], filename_prefix=prefix),
    })
    validate_graph(graph, REFERENCE_ALLOWED)
    return graph
