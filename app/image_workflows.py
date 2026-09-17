"""Bounded Schnell portraits, FLUX.2 text scenes and Qwen Edit references.

GPT supplies only illustration text, never executable nodes or model names.
https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_flux2_text_to_image.json
https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit-2511
"""
from .video_models import WorkflowNode
from .video_workflows import validate_graph

ALLOWED = {"UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "FluxGuidance", "BasicGuider",
           "EmptyFlux2LatentImage", "RandomNoise", "KSamplerSelect", "Flux2Scheduler",
           "SamplerCustomAdvanced", "VAEDecode", "SaveImage"}
PRESET = "flux2-dev-illustration-v1"
REFERENCE_PRESET = "qwen-image-edit-2511-identity-v1"
PORTRAIT_PRESET = "flux-schnell-illustration-v2"
PORTRAIT_ALLOWED = {"UNETLoader", "DualCLIPLoader", "VAELoader", "CLIPTextEncodeFlux", "ConditioningZeroOut",
                    "EmptySD3LatentImage", "KSampler", "VAEDecode", "SaveImage"}
VISIBLE_FACES = (
    " If people are depicted, show each person's clearly visible face from the front or a three-quarter front view. "
    "Keep eyes, nose and mouth visible and unobstructed, with the whole head inside the frame. "
    "Use an eye-level camera placed in front of the people. No rear view, turned-away faces, "
    "over-the-shoulder camera, faceless silhouettes, or objects covering faces. "
    "For interactions, angle both people toward the viewer so both faces remain visible. "
    "Anonymous means a fictional identity, not a hidden or featureless face. "
    "For scenes without people, do not add a person just to satisfy this framing instruction."
)
REFERENCE_ALLOWED = {"UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "FluxKontextImageScale",
                     "TextEncodeQwenImageEditPlus", "ModelSamplingAuraFlow", "CFGNorm", "VAEEncode",
                     "KSampler", "VAEDecode", "SaveImage"}
WIDTH = HEIGHT = 1024
STEPS = 20


def compile_portrait(illustration, seed, prefix):
    """Preserve the original Schnell workflow for initial character portraits."""
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
    validate_graph(graph, PORTRAIT_ALLOWED)
    return graph


def compile_image(illustration, seed, prefix):
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    prompt = ("Respectful adult educational illustration, simple flat shapes, calm colors, white background. "
              "Anonymous adults, no real likeness, no letters, numbers, logos or speech text. " + illustration.prompt + VISIBLE_FACES)
    graph = {
        "1": node("UNETLoader", unet_name="flux2_dev_fp8mixed.safetensors", weight_dtype="default"),
        "2": node("CLIPLoader", clip_name="mistral_3_small_flux2_bf16.safetensors", type="flux2", device="default"),
        "3": node("VAELoader", vae_name="full_encoder_small_decoder.safetensors"),
        "4": node("CLIPTextEncode", clip=["2", 0], text=prompt),
        "5": node("FluxGuidance", conditioning=["4", 0], guidance=4.0),
        "6": node("EmptyFlux2LatentImage", width=WIDTH, height=HEIGHT, batch_size=1),
        "7": node("RandomNoise", noise_seed=seed),
        "8": node("KSamplerSelect", sampler_name="euler"),
        "9": node("Flux2Scheduler", steps=STEPS, width=WIDTH, height=HEIGHT),
        "10": node("BasicGuider", model=["1", 0], conditioning=["5", 0]),
        "11": node("SamplerCustomAdvanced", noise=["7", 0], guider=["10", 0], sampler=["8", 0], sigmas=["9", 0], latent_image=["6", 0]),
        "12": node("VAEDecode", samples=["11", 0], vae=["3", 0]),
        "13": node("SaveImage", images=["12", 0], filename_prefix=prefix),
    }
    validate_graph(graph, ALLOWED)
    return graph


def compile_reference_image(illustration, seed, prefix, references):
    """Qwen-Image-Edit-2511 with one to three uploaded character images.

    Each reference is an uploaded Cloud filename, in the same order as imageNumber in
    the scene-planning context. Both positive and negative edit encoders receive
    the same image slots and VAE. No Lightning LoRA or automatic fallback.
    Based on Comfy-Org's image_qwen_image_edit_2511.json normal model path.
    """
    if not 1 <= len(references) <= 3:
        raise ValueError("Expected one to three character references")
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    pictures = ", ".join(f"Picture {i + 1}" for i in range(len(references)))
    prompt = ("Edit the supplied character images into one respectful educational scene with a white background. "
              "Keep the exact illustrated people from " + pictures + ". "
              "Preserve their faces, hair, clothing, colors and drawing style. Change only poses and the scene. "
              "Image numbers in the instruction refer to the matching Picture numbers. "
              "Only include characters relevant to the described scene. "
              "Show their distinct roles and the direction of their relationship without swapping identities. "
              "Do not copy the reference layout or create a contact sheet. No text, numbers, logos. " + illustration.prompt +
              " Identity preservation takes priority over generic appearance descriptions in the scene: "
              "re-use the exact illustrated adults from the reference images. Preserve each person's apparent age, "
              "face shape, hairstyle, facial hair (including every beard or moustache), skin tone, clothing, "
              "shoes and body proportions. Do not remove facial hair, make an adult younger, change outfits, "
              "swap faces, or replace anyone with a generic new character. Change only their pose and scene." + VISIBLE_FACES)
    graph = {
        "1": node("UNETLoader", unet_name="qwen_image_edit_2511_fp8mixed.safetensors", weight_dtype="default"),
        "2": node("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default"),
        "3": node("VAELoader", vae_name="qwen_image_vae.safetensors"),
        "7": node("ModelSamplingAuraFlow", model=["1", 0], shift=3.1),
        "8": node("CFGNorm", model=["7", 0], strength=1.0),
    }
    images = {}
    for index, filename in enumerate(references):
        load, scale = str(20 + index * 2), str(21 + index * 2)
        graph[load] = node("LoadImage", image=filename)
        graph[scale] = node("FluxKontextImageScale", image=[load, 0])
        images[f"image{index + 1}"] = [scale, 0]
    graph.update({
        "4": node("TextEncodeQwenImageEditPlus", clip=["2", 0], vae=["3", 0], prompt=prompt, **images),
        "5": node("TextEncodeQwenImageEditPlus", clip=["2", 0], vae=["3", 0], prompt="", **images),
        "6": node("VAEEncode", pixels=images["image1"], vae=["3", 0]),
        "11": node("KSampler", model=["8", 0], positive=["4", 0], negative=["5", 0], latent_image=["6", 0],
                   seed=seed, steps=20, cfg=4.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "12": node("VAEDecode", samples=["11", 0], vae=["3", 0]),
        "13": node("SaveImage", images=["12", 0], filename_prefix=prefix),
    })
    validate_graph(graph, REFERENCE_ALLOWED)
    return graph
