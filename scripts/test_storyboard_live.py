"""Explicit paid smoke test in a new local SQLite workspace, with resumable Comfy jobs."""
import argparse
import json
import secrets
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Config
from app.studio_domain import cards


def checked(response):
    if not response.is_success:
        detail = response.json().get("detail", {})
        code = detail.get("code", "invalid_response") if isinstance(detail, dict) else "invalid_response"
        # Never print upstream content, signed URLs, auth tokens or raw inputs.
        raise SystemExit(f"HTTP {response.status_code}: {code}. Same directory can be resumed; no automatic new paid job.")
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Required acknowledgement: GPT/Comfy API costs apply")
    parser.add_argument("--resume", type=Path, help="Previously printed test directory; never creates a replacement project")
    parser.add_argument("--attempts", type=int, default=10, help="Maximum prepare-image polls per invocation")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly enable paid API calls")
    if not 1 <= args.attempts <= 100:
        parser.error("--attempts must be 1..100")
    config = Config(provider="openai", studio_character_mode="library", studio_scene_mode="storyboard4",
                    turso_url="", turso_token="", auth_mode="keys", api_keys={secrets.token_urlsafe(32): "storyboard-test"})
    if not config.comfy_api_key:
        parser.error("COMFY_CLOUD_API_KEY is required")
    directory = args.resume.resolve() if args.resume else Path(tempfile.mkdtemp(prefix="ihaero-storyboard4-"))
    run_file = directory / "run.json"
    if args.resume and not run_file.is_file():
        parser.error("--resume directory must contain an existing run.json")
    config.db_path = str(directory / "test.sqlite3")
    print(f"Test directory: {directory}", flush=True)
    # Import after validation. This test app always uses its own local DB, not Turso.
    from app.main import create_app
    from app.studio_api import SAMPLE_TEXT
    api = create_app(config)
    with TestClient(api, headers={"Authorization": "Bearer " + next(iter(config.api_keys))}) as client:
        if args.resume:
            run = json.loads(run_file.read_text())
            project_id = run["projectId"]
            checked(client.get(f"/api/studio/projects/{project_id}/document"))
        else:
            project = checked(client.post("/api/studio/projects/text", json={"text": SAMPLE_TEXT,
                "settings": {"tone": "haeyo", "naming": "initial", "illustrations": "with"}}))
            project_id = project["id"]
            # Record immediately: a later failure must not accidentally recreate a paid run.
            run = {"projectId": project_id, "preset": "storyboard4", "stage": "draft"}
            run_file.write_text(json.dumps(run, indent=2))
        base = f"/api/studio/projects/{project_id}"
        if run.get("stage") == "draft":
            document_response = client.get(base + "/document")
            if document_response.status_code == 404 or (document_response.status_code == 409 and
                    document_response.json().get("detail", {}).get("code") == "document_required"):
                doc = checked(client.post(base + "/document/generate"))["document"]
            else:
                doc = checked(document_response)
            scenes = [c for c in cards(doc) if c["role"] != "person"]
            if len(scenes) < 4:
                raise SystemExit("Draft has fewer than four scene cards. Edit this test document before resuming; no scenes are invented.")
            keep = {c["id"] for c in scenes[:4]}
            for section in doc["sections"]:
                section["cards"] = [c for c in section["cards"] if c["role"] == "person" or c["id"] in keep]
            checked(client.put(base + "/document", json=doc))
            run.update(stage="images", cardIds=[c["id"] for c in scenes[:4]])
            run_file.write_text(json.dumps(run, indent=2))
        for _ in range(args.attempts):
            result = checked(client.post(base + "/document/prepare-images"))
            progress = result["generation"]
            print(f"{progress['phase']}: {progress['completed']}/{progress['total']} ({progress['status']})", flush=True)
            if progress["status"] == "ready":
                break
        else:
            raise SystemExit("Still running. Resume the same directory to poll the existing job.")
        service = api.state.studio_generation
        state = service.store.get(project_id, "storyboard-test")[0]
        originals = state["image_batch"].get("storyboardSheets", [])
        if len(originals) != 1:
            raise SystemExit("Expected one four-panel sheet. Inspect saved test state; no new generation was started.")
        sheet_id = originals[0]["sheetId"]
        (directory / "sheet.png").write_bytes(service.store.asset(sheet_id)[1])
        lookup = {c["id"]: c for c in cards(state["document"])}
        for index, card_id in enumerate(run["cardIds"]):
            (directory / f"panel-{index + 1}.png").write_bytes(service.store.asset(lookup[card_id]["imageId"])[1])
        (directory / "document.json").write_text(json.dumps(state["document"], ensure_ascii=False, indent=2))
        print("Saved sheet.png (1024²), panel-1..4.png (512²), document.json. Inspect layout, identity and semantic fidelity manually.")


if __name__ == "__main__":
    main()
