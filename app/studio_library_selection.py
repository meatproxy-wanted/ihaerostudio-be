"""Persistent AI selection of stock characters; no portrait generation call."""
import hashlib
import json

from .studio_domain import cards, timestamp
from .studio_library import CharacterSelection, SELECTION_TASK, catalog
from .store import fail


class LibrarySelection:
    def __init__(self, generation):
        self.generation = generation
        self.store = generation.store

    def assign(self, project_id, owner):
        state = self.store.get(project_id, owner)[0]
        existing = state.get("character_library")
        if not existing:
            # Existing applied characters and unfinished paid portraits are not replaced.
            if state.get("locked_portraits") or any(c["role"] == "person" and c["imageId"] for c in cards(state["document"])):
                return None
            with self.store.store.connect() as db:
                jobs = db.execute("SELECT body FROM studio_image_jobs WHERE project_id=? AND owner=?", (project_id, owner)).fetchall()
            if any(json.loads(row["body"])["card_id"] != "__character_library_selection__" and json.loads(row["body"])["status"] != "ready" for row in jobs):
                return None
        data = catalog(existing["version"] if existing else None)
        if existing and existing["digest"] != data["digest"]:
            fail(409, "character_library_changed", "고정 캐릭터셋 버전이 바뀌었어요. 이 자료의 원본 캐릭터 자산을 복원해 주세요.")
        bindings = (existing or {}).get("bindings", {})
        party_ids = [p["partyId"] for p in state["document"]["partyNames"]]
        missing = [p for p in party_ids if p not in bindings]
        if not missing:
            return existing
        if len(bindings) + len(missing) > 10:
            fail(422, "character_reference_limit", "고정 캐릭터는 한 자료에서 최대 10종까지 배정할 수 있어요.")
        available = [{"characterId": c["id"], "appearance": c["description"]} for c in data["characters"] if c["id"] not in bindings.values()]
        context = {"unassignedParties": [{"partyId": p} for p in missing],
                   "availableCharacters": available, "existingAssignments": bindings}
        digest = hashlib.sha256(json.dumps([data["digest"], context], sort_keys=True).encode()).hexdigest()
        generation = self.generation
        job = generation.claim(project_id, owner, "__character_library_selection__", digest)
        try:
            if job["status"] == "selection_rejected":
                fail(422, "character_selection_invalid", "캐릭터 배정 결과가 올바르지 않아요. 자료의 등장인물을 확인해 주세요.")
            if job["status"] == "pending":
                selected = generation.provider.call(SELECTION_TASK, context, CharacterSelection)
                job.update(status="planned", choices=selected.model_dump()["choices"])
                generation.persist(job)
            selected = CharacterSelection(choices=job["choices"])
            choices = {c.partyId: c.characterId for c in selected.choices}
            if (len(choices) != len(selected.choices) or set(choices) != set(missing)
                    or len(set(choices.values())) != len(choices)
                    or any(c not in {i["characterId"] for i in available} for c in choices.values())):
                job["status"] = "selection_rejected"
                generation.persist(job)
                fail(422, "character_selection_invalid", "캐릭터 배정 결과가 올바르지 않아요. 기존 인물은 변경하지 않았어요.")
            with self.store.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT body FROM studio_projects WHERE id=? AND owner=? AND deleted=0", (project_id, owner)).fetchone()
                if row is None:
                    fail(404, "not_found", "자료를 찾을 수 없어요.")
                latest = json.loads(row["body"])
                current = latest.get("character_library")
                if ((current or {}).get("bindings", {}) != bindings
                        or not latest.get("document")
                        or [p["partyId"] for p in latest["document"]["partyNames"]] != party_ids):
                    fail(409, "image_card_changed", "캐릭터 배정 중 등장인물이 바뀌었어요. 최신 자료에서 다시 확인해 주세요.")
                library = {"version": data["version"], "digest": data["digest"], "bindings": {**bindings, **choices}}
                latest["character_library"] = library
                latest["project"]["updatedAt"] = timestamp()
                db.execute("UPDATE studio_projects SET body=?,version=version+1,updated_at=? WHERE id=? AND owner=?",
                           (json.dumps(latest), latest["project"]["updatedAt"], project_id, owner))
                job.update(status="ready")
                db.execute("UPDATE studio_image_jobs SET body=? WHERE id=?", (json.dumps(job), job["id"]))
            return library
        finally:
            with self.store.store.connect() as db:
                db.execute("UPDATE studio_image_jobs SET lease_until=0 WHERE id=?", (job["id"],))
