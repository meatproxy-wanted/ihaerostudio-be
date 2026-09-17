"""Bounded Qwen portraits, FLUX.2 text scenes and Qwen Edit references.

GPT supplies only illustration text, never executable nodes or model names.
https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_flux2_text_to_image.json
https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit-2511
https://docs.comfy.org/tutorials/image/qwen/qwen-image-2512
"""
from .video_models import WorkflowNode
from .video_workflows import validate_graph
from .studio_style import mood_instruction

ALLOWED = {"UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "FluxGuidance", "BasicGuider",
           "EmptyFlux2LatentImage", "RandomNoise", "KSamplerSelect", "Flux2Scheduler",
           "SamplerCustomAdvanced", "VAEDecode", "SaveImage"}
STYLE_VERSION = "simple-animation-illustration-v3"
RESOLUTION_VERSION = "512px-v2"
PRESET = "flux2-dev-animation-512-action-scene-v7"
REFERENCE_PRESET = "qwen-image-edit-2511-animation-512-40steps-action-scene-v8"
MOOD_PRESET = "qwen-image-edit-2511-light-mood-512-40steps-scene-v1"
MOOD_PORTRAIT_PRESET = "qwen-image-edit-2511-light-mood-512-40steps-portrait-v1"
PORTRAIT_PRESET = "qwen-image-2512-animation-solo-portrait-512-20steps-v8"
PORTRAIT_SIZE = 512
ILLUSTRATION_STYLE = "Simple animation-style image or illustration. "
PLANNING_STYLE_INSTRUCTION = (
    "그림체 요구는 'Simple animation-style image or illustration.'입니다. "
    "출력에는 인물 외형·행동·배경과 사건 의미만 작성하세요. "
    "스타일 문장은 서버에서 붙이므로 출력에 중복하거나 외형 참고의 그림체 설명을 복사하거나 별도의 스타일 지시를 추가하지 마세요. "
)
PORTRAIT_ALLOWED = {"UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "ModelSamplingAuraFlow",
                    "EmptySD3LatentImage", "KSampler", "VAEDecode", "SaveImage"}
PORTRAIT_COMPOSITION = (
    " Mandatory character portrait composition: exactly one fictional adult, alone and centered in a waist-up portrait. "
    "Use a solid pure white (#FFFFFF), empty background. "
    "No other people, background figures, duplicate people, reflections, inset portraits, split panels or collages. "
    "No scenery, furniture, props, icons or background decorations. "
    "Show the entire head and a large, clear face with visible eyes, nose and mouth. "
    "Use a natural pose; do not require looking at or facing the viewer. "
    "Keep the person distinct from the plain white background. These portrait constraints override conflicting scene descriptions."
)
VISIBLE_FACES = (
    " If people are depicted, make each person's face clearly visible and identifiable. "
    "Keep eyes, nose and mouth visible and unobstructed, with the whole head inside the frame. "
    "Choose natural poses and gaze appropriate to the scene; do not require people to face or look at the viewer. "
    "Avoid hidden or cropped faces, faceless silhouettes, or objects covering faces. "
    "Anonymous means a fictional identity, not a hidden or featureless face. "
    "For scenes without people, do not add a person just to satisfy this framing instruction."
)
REFERENCE_ALLOWED = {"UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "FluxKontextImageScale", "ImageScaleBy",
                     "TextEncodeQwenImageEditPlus", "ModelSamplingAuraFlow", "CFGNorm", "VAEEncode",
                     "KSampler", "VAEDecode", "SaveImage"}
MOOD_ALLOWED = REFERENCE_ALLOWED | {"EmptySD3LatentImage"}
WIDTH = HEIGHT = 512
STEPS = 20
REFERENCE_STEPS = 40


def compile_portrait(illustration, seed, prefix):
    """Qwen-Image-2512 normal text-to-image path, without reference or Lightning."""
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    prompt = (ILLUSTRATION_STYLE + "Respectful adult educational material, white background. "
              "One anonymous adult, no real likeness, no letters, numbers, logos or speech text. " +
              illustration.prompt + PORTRAIT_COMPOSITION)
    negative = ("two people, multiple people, crowd, background people, duplicate person, reflections, "
                "inset portrait, split panels, collage, scenery, furniture, props, icons, background decorations, "
                "colored background, text, numbers, logos, watermark, hidden face, cropped head")
    graph = {
        "1": node("UNETLoader", unet_name="qwen_image_2512_fp8_e4m3fn.safetensors", weight_dtype="default"),
        "2": node("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default"),
        "3": node("VAELoader", vae_name="qwen_image_vae.safetensors"),
        "4": node("CLIPTextEncode", clip=["2", 0], text=prompt),
        "5": node("CLIPTextEncode", clip=["2", 0], text=negative),
        "6": node("EmptySD3LatentImage", width=PORTRAIT_SIZE, height=PORTRAIT_SIZE, batch_size=1),
        "10": node("ModelSamplingAuraFlow", model=["1", 0], shift=3.1),
        "7": node("KSampler", model=["10", 0], positive=["4", 0], negative=["5", 0], latent_image=["6", 0], seed=seed, steps=20, cfg=4.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "8": node("VAEDecode", samples=["7", 0], vae=["3", 0]),
        "9": node("SaveImage", images=["8", 0], filename_prefix=prefix),
    }
    validate_graph(graph, PORTRAIT_ALLOWED)
    return graph


def compile_image(illustration, seed, prefix):
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    prompt = (ILLUSTRATION_STYLE + "Respectful adult educational scene. "
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


def compile_reference_image(illustration, seed, prefix, references, *, steps=REFERENCE_STEPS, mood=None, portrait=False):
    """Qwen Edit with at most three uploaded character/mood images.

    Each reference is an uploaded Cloud filename, in the same order as imageNumber in
    the scene-planning context. Both positive and negative edit encoders receive
    the same image slots and VAE. No Lightning LoRA or automatic fallback.
    Based on Comfy-Org's image_qwen_image_edit_2511.json normal model path.
    """
    if (not references and not mood) or len(references) + bool(mood) > 3:
        raise ValueError("Expected one to three total references")
    if portrait and (references or not mood):
        raise ValueError("New portraits use only a mood sample, never other character references")
    if steps not in {20, 40}:
        raise ValueError("Reference comparison supports only 20 or 40 steps")
    def node(kind, **inputs):
        return WorkflowNode(class_type=kind, inputs=inputs)
    pictures = ", ".join(f"Picture {i + 1}" for i in range(len(references)))
    prompt = (ILLUSTRATION_STYLE + "Edit the supplied character images into one respectful educational scene. "
              "Recompose the solo portraits into the described actions and setting, not a portrait lineup. "
              "The reference backgrounds and poses are not scene requirements. "
              "Keep the same people from " + pictures + ". "
              "Preserve their faces, hair, clothing and colors. "
              "Change poses and the scene while keeping the characters recognizable. "
              "Image numbers in the instruction refer to the matching Picture numbers. "
              "Only include characters relevant to the described scene. "
              "Show their distinct roles and the direction of their relationship without swapping identities. "
              "Do not copy the reference layout or create a contact sheet. No text, numbers, logos. " + illustration.prompt +
              " Identity preservation takes priority over generic appearance descriptions in the scene: "
              "re-use the same adults from the reference images. Preserve each person's apparent age, "
              "face shape, hairstyle, facial hair (including every beard or moustache), skin tone, clothing, "
              "shoes and body proportions. Do not remove facial hair, make an adult younger, change outfits, "
              "swap faces, or replace anyone with a generic new character. Preserve identity while changing pose and scene." + VISIBLE_FACES)
    if not references:
        prompt = ILLUSTRATION_STYLE + "Create the requested fictional adult educational illustration. " + illustration.prompt + VISIBLE_FACES
    if mood:
        prompt += mood_instruction(len(references) + 1)
    if portrait:
        prompt += PORTRAIT_COMPOSITION
    graph = {
        "1": node("UNETLoader", unet_name="qwen_image_edit_2511_fp8mixed.safetensors", weight_dtype="default"),
        "2": node("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default"),
        "3": node("VAELoader", vae_name="qwen_image_vae.safetensors"),
        "7": node("ModelSamplingAuraFlow", model=["1", 0], shift=3.1),
        "8": node("CFGNorm", model=["7", 0], strength=1.0),
    }
    images = {}
    for index, filename in enumerate([*references, *([mood] if mood else [])]):
        load, scale = str(20 + index * 2), str(21 + index * 2)
        graph[load] = node("LoadImage", image=filename)
        graph[scale] = node("FluxKontextImageScale", image=[load, 0])
        reduced = str(30 + index)
        graph[reduced] = node("ImageScaleBy", image=[scale, 0], upscale_method="area", scale_by=0.5)
        images[f"image{index + 1}"] = [reduced, 0]
    graph.update({
        "4": node("TextEncodeQwenImageEditPlus", clip=["2", 0], vae=["3", 0], prompt=prompt, **images),
        "5": node("TextEncodeQwenImageEditPlus", clip=["2", 0], vae=["3", 0], prompt="", **images),
        "6": node("VAEEncode", pixels=images["image1"], vae=["3", 0]),
        "11": node("KSampler", model=["8", 0], positive=["4", 0], negative=["5", 0], latent_image=["6", 0],
                   seed=seed, steps=steps, cfg=4.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "12": node("VAEDecode", samples=["11", 0], vae=["3", 0]),
        "13": node("SaveImage", images=["12", 0], filename_prefix=prefix),
    })
    if not references:
        # The mood sample must not become the base composition (it has two
        # people and scenery). Generate from a blank square latent instead.
        graph["6"] = node("EmptySD3LatentImage", width=WIDTH, height=HEIGHT, batch_size=1)
    if portrait:
        graph["5"].inputs["prompt"] = (
            "two people, multiple people, crowd, duplicate person, reflections, inset portrait, split panels, "
            "collage, scenery, furniture, props, icons, colored background, text, numbers, logos, watermark, hidden face, cropped head")
    validate_graph(graph, MOOD_ALLOWED if mood else REFERENCE_ALLOWED)
    return graph
