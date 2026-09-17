import json

import pytest

from app.image_workflows import ALLOWED, REFERENCE_ALLOWED, compile_image, compile_reference_image
from app.studio_generation import IllustrationPlan, fingerprint, image_context
from app.video_workflows import validate_graph
from test_studio_generation import client, setup, request_image, submissions


def test_dev_workflows_use_guidance_and_ordered_reference_chain():
    plan = IllustrationPlan(prompt="Two anonymous adults discussing a request.", alt="대화", meaning="요청")
    graph = compile_image(plan, 42, "test")
    validate_graph(graph, ALLOWED)
    assert graph["1"].inputs["unet_name"] == "flux2_dev_fp8mixed.safetensors"
    assert graph["2"].inputs["clip_name"] == "mistral_3_small_flux2_bf16.safetensors"
    assert graph["3"].inputs["vae_name"] == "full_encoder_small_decoder.safetensors"
    assert graph["9"].inputs == {"steps": 20, "width": 1024, "height": 1024}
    assert graph["6"].inputs["batch_size"] == 1
    assert graph["5"].inputs["guidance"] == 4.0
    refs = compile_reference_image(plan, 42, "test", ["first.png", "second.png"])
    validate_graph(refs, REFERENCE_ALLOWED)
    assert refs["23"].inputs == {"conditioning": ["5", 0], "latent": ["22", 0]}
    assert refs["27"].inputs == {"conditioning": ["23", 0], "latent": ["26", 0]}
    assert refs["10"].inputs["conditioning"] == ["27", 0]
    assert [refs[k].inputs["image"] for k in ["20", "24"]] == ["first.png", "second.png"]
    assert not any(n.class_type in {"CFGGuider", "ConditioningZeroOut", "LoraLoaderModelOnly"} for n in refs.values())


@pytest.mark.parametrize("status", ["running", "submission_unknown", "ready"])
def test_model_upgrade_preserves_paid_work_and_invalidates_only_completed_cache(setup, monkeypatch, status):
    client, service, project, _, card, control = setup
    monkeypatch.setattr("app.studio_generation.WAIT_SECONDS", 0)
    control["status"] = "running" if status == "running" else "succeeded"
    control["submit_error"] = "timeout" if status == "submission_unknown" else None
    first = request_image(setup)
    assert first.status_code == (200 if status == "ready" else 503)
    context = image_context(service.store.get(project["id"], "alice")[0], card["id"])
    old_digest = fingerprint(context, "flux-schnell-illustration-v2")
    with service.store.store.connect() as db:
        row = db.execute("SELECT * FROM studio_image_jobs").fetchone()
        job = json.loads(row["body"])
        job["fingerprint"] = old_digest
        # Simulate a persisted pre-upgrade graph: its model must not be recompiled.
        job["workflow"]["1"]["inputs"]["unet_name"] = "flux1-schnell.safetensors"
        original_id = job["id"]
        db.execute("UPDATE studio_image_jobs SET fingerprint=?,body=?,lease_until=0 WHERE id=?",
                   (old_digest, json.dumps(job), original_id))
    control["status"], control["submit_error"] = "succeeded", None
    second = request_image(setup)
    if status == "submission_unknown":
        assert second.json()["detail"]["code"] == "image_submission_unknown"
    else:
        assert second.status_code == 200, second.text
    assert len(submissions(control)) == (2 if status == "ready" else 1)
    with service.store.store.connect() as db:
        rows = db.execute("SELECT body FROM studio_image_jobs").fetchall()
        current = next(json.loads(r["body"]) for r in rows if json.loads(r["body"])["fingerprint"] == fingerprint(context))
    if status == "ready":
        assert current["id"] != original_id
        assert current["workflow"]["1"]["inputs"]["unet_name"] == "flux2_dev_fp8mixed.safetensors"
    else:
        assert current["id"] == original_id
        assert current["workflow"]["1"]["inputs"]["unet_name"] == "flux1-schnell.safetensors"
