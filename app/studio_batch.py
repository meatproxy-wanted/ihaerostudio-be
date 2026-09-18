"""Durable request-driven editor preparation; no serverless background threads."""
import copy
import json
import time

from fastapi import HTTPException

from .models import uid
from .store import fail
from .studio_domain import cards, invalidate_review, require_document, summarize_document, timestamp
from .studio_generation import fingerprint, image_context
from .studio_characters import referenced_party_ids


def storyboard_groups(state, card_ids):
    """Keep document order; split before either four cards or three identities is exceeded."""
    lookup = {c["id"]: c for c in cards(require_document(state))}
    groups, group, identities = [], [], set()
    for card_id in card_ids:
        card = lookup.get(card_id)
        if card is None:
            fail(409, "image_card_changed", "자동 생성 대상 카드가 삭제됐어요.")
        refs = referenced_party_ids(state, card)
        if not 1 <= len(refs) <= 3:
            fail(422, "character_reference_limit", "한 장면의 기준 인물은 1~3명이어야 해요. 해당 카드의 인물과 내용을 나눠 주세요.")
        if group and (len(group) == 4 or len(identities | refs) > 3):
            groups.append(group)
            group, identities = [], set()
        group.append(card_id)
        identities |= refs
    if group:
        groups.append(group)
    return groups


def validate_portrait_locks(state, document):
    images = {i["id"]: i for i in document["images"]}
    people = [c for c in cards(document) if c["role"] == "person"]
    for party_id, locked in state.get("locked_portraits", {}).items():
        matches = [c for c in people if c["partyId"] == party_id]
        image = images.get(locked["image"]["id"])
        if not matches or any(c["imageId"] != locked["image"]["id"] for c in matches) or (
            not image or image["src"] != locked["image"]["src"]
        ):
            fail(409, "character_locked", "등장인물의 기준 그림은 변경하거나 삭제할 수 없어요. 글과 장면 그림은 편집할 수 있어요.")


def preserve_portraits(state, draft):
    """Text regeneration keeps fixed identities instead of silently replacing them."""
    locked = state.get("locked_portraits", {})
    if not locked:
        return
    people_section = next((s for s in draft["sections"] if s["kind"] == "people"), None)
    if people_section is None:
        fail(409, "character_locked", "고정된 등장인물을 유지할 수 없어요. 등장인물 구획을 확인해 주세요.")
    for party_id, portrait in locked.items():
        if not any(n["partyId"] == party_id for n in draft["partyNames"]):
            fail(409, "character_locked", "고정된 등장인물이 바뀌었어요. 다른 등장인물의 자료는 새 프로젝트로 만들어 주세요.")
        matches = [c for c in cards(draft) if c["role"] == "person" and c["partyId"] == party_id]
        if not matches:
            existing = next(c for c in cards(state["document"]) if c["role"] == "person" and c["partyId"] == party_id)
            matches = [copy.deepcopy(existing)]
            people_section["cards"].extend(matches)
        for card in matches:
            card["imageId"] = portrait["image"]["id"]
        draft["images"].append(copy.deepcopy(portrait["image"]))
    validate_portrait_locks(state, draft)


class StudioBatch:
    def __init__(self, generation):
        self.generation, self.store = generation, generation.store

    def update(self, project_id, owner, operation, *, with_db=False):
        with self.store.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM studio_projects WHERE id=? AND owner=? AND deleted=0", (project_id, owner)).fetchone()
            if row is None:
                fail(404, "not_found", "자료를 찾을 수 없어요.")
            state = json.loads(row["body"])
            if with_db:
                operation(state, db)
            else:
                operation(state)
            state["project"]["updatedAt"] = timestamp()
            state["project"]["document"] = summarize_document(require_document(state))
            db.execute("UPDATE studio_projects SET body=?,version=version+1,updated_at=? WHERE id=? AND owner=?",
                       (json.dumps(state), state["project"]["updatedAt"], project_id, owner))
            return state

    @staticmethod
    def lock(state, card):
        if card["role"] == "person":
            if not card["partyId"]:
                fail(422, "invalid_party", "등장인물 카드의 partyId를 지정해 주세요.")
            image = next(i for i in state["document"]["images"] if i["id"] == card["imageId"])
            locks = state.setdefault("locked_portraits", {})
            existing = locks.get(card["partyId"])
            if existing and existing["image"]["id"] != image["id"]:
                fail(409, "character_locked", "같은 등장인물의 기준 그림은 하나로 고정돼 있어요.")
            locks[card["partyId"]] = {"image": copy.deepcopy(image)}

    def prepare(self, project_id, owner):
        def initialize(state, db):
            document = require_document(state)
            batch = state.get("image_batch", {})
            group = batch.get("storyboardGroup")
            if group and self.generation.config.studio_scene_mode == "single":
                row = db.execute("SELECT body,lease_until FROM studio_image_jobs WHERE owner=? AND project_id=? AND card_id=? AND fingerprint=?",
                    (owner, project_id, "storyboard4:" + group["id"], group["digest"])).fetchone()
                # A configuration rollback may abandon only definitely unsubmitted,
                # idle work. Accepted, uncertain or failed paid work never falls back.
                job = json.loads(row["body"]) if row else None
                if row is None or (row["lease_until"] <= time.time() and job["status"] in {"pending", "planned", "prepared"}):
                    batch.pop("storyboardGroup")
                    batch["sceneMode"] = "single"
                    if job:
                        job["status"] = "abandoned"
                        db.execute("UPDATE studio_image_jobs SET body=?,lease_until=0 WHERE id=?", (json.dumps(job), job["id"]))
            elif group:
                # Repair a legacy over-wide reservation only when certainly idle
                # and unsubmitted. Never regroup accepted or uncertain paid work.
                row = db.execute("SELECT body,lease_until FROM studio_image_jobs WHERE owner=? AND project_id=? AND card_id=? AND fingerprint=?",
                    (owner, project_id, "storyboard4:" + group["id"], group["digest"])).fetchone()
                job = json.loads(row["body"]) if row else None
                unsubmitted = row is None or (row["lease_until"] <= time.time() and job["status"] in {"pending", "planned", "prepared"})
                if unsubmitted and len(storyboard_groups(state, group["cardIds"])) > 1:
                    batch.pop("storyboardGroup")
                    if job:
                        job["status"] = "abandoned"
                        db.execute("UPDATE studio_image_jobs SET body=?,lease_until=0 WHERE id=?", (json.dumps(job), job["id"]))
            active = state["project"]["settings"]["illustrations"] == "with" and self.generation.provider.name != "demo"
            if active and state.get("image_batch", {}).get("status") == "skipped":
                if state["image_batch"].get("storyboardGroup"):
                    state["image_batch"]["status"] = "running"
                else:
                    state.pop("image_batch", None)
            if (state["project"]["settings"]["illustrations"] != "with" or self.generation.provider.name == "demo") and state.get("image_batch", {}).get("status") == "running":
                state["image_batch"]["status"] = "skipped"
            if state.get("image_batch"):
                if not state["image_batch"].get("storyboardGroup"):
                    state["image_batch"]["sceneMode"] = self.generation.config.studio_scene_mode
                return
            if state["project"]["settings"]["illustrations"] != "with" or self.generation.provider.name == "demo":
                state["image_batch"] = {"status": "skipped", "targets": [], "completed": []}
                return
            # Older drafts may have names but no portrait cards. Establish every
            # character before scenes rather than creating unrelated scene actors.
            known = {c["partyId"] for c in cards(document) if c["role"] == "person"}
            missing = [n for n in document["partyNames"] if n["partyId"] not in known]
            if missing:
                section = next((s for s in document["sections"] if s["kind"] == "people"), None)
                if section is None:
                    fail(422, "invalid_party", "등장인물 구획이 없어요. 초안을 다시 생성해 주세요.")
                parties = {p["id"]: p for p in state["structure"]["parties"]}
                for name in missing:
                    party = parties.get(name["partyId"], {})
                    role = party.get("easyRole") or party.get("legalStatus") or "등장인물"
                    section["cards"].append({"id": uid(), "role": "person", "partyId": name["partyId"], "imageId": None,
                        "sentences": [{"id": uid(), "text": name["displayName"] + ": " + role, "anchors": party.get("anchors", []),
                                       "origin": "ai-draft", "verified": False}]})
                document["saveRevision"] += 1
                document["contentRevision"] += 1
                invalidate_review(state)
                state["review"] = None
                state["project"]["review"].update(checkedContentRevision=None, openRequiredCount=None)
            ordered = sorted(cards(document), key=lambda c: c["role"] != "person")
            if len({c["partyId"] for c in ordered if c["role"] == "person"}) > 6:
                fail(422, "character_reference_limit", "자동 생성은 기준 인물 6명까지 지원해요. 프로젝트를 나눠 주세요.")
            if any(c["role"] == "person" and not c["partyId"] for c in ordered):
                fail(422, "invalid_party", "등장인물 카드의 partyId를 지정해 주세요.")
            missing = [c for c in ordered if not c["imageId"]]
            sheets = 0
            if self.generation.config.studio_scene_mode == "storyboard4":
                sheets = len(storyboard_groups(state, [c["id"] for c in missing if c["role"] != "person"]))
            if len(state["assets"]) + len(missing) + sheets > 100:
                fail(413, "image_limit", "모든 카드를 생성하면 그림 100개 한도를 넘어요. 프로젝트를 나눠 주세요.")
            if any(not c["imageId"] for c in ordered) and self.generation.config.studio_character_mode == "generate" and not state.get("character_library"):
                self.generation.cloud.require_key()
            state["image_batch"] = {"status": "running", "targets": [c["id"] for c in ordered], "completed": [],
                                    "sceneMode": self.generation.config.studio_scene_mode}

        state = self.update(project_id, owner, initialize, with_db=True)
        if state["image_batch"]["status"] in {"ready", "skipped"}:
            return self.result(state)

        def skip_attached(state):
            batch = state["image_batch"]
            # A reserved group must be resumed/validated as a unit. An autosave
            # attaching one of its cards must not start a second paid grouping.
            if batch.get("storyboardGroup"):
                return
            lookup = {c["id"]: c for c in cards(state["document"])}
            for card_id in batch["targets"]:
                if card_id in batch["completed"]:
                    continue
                card = lookup.get(card_id)
                if card is None:
                    fail(409, "image_card_changed", "자동 생성 중 카드가 바뀌었어요. 문서를 다시 확인해 주세요.")
                if not card["imageId"] and card["role"] == "person" and card["partyId"] in state.get("locked_portraits", {}):
                    card["imageId"] = state["locked_portraits"][card["partyId"]]["image"]["id"]
                    state["document"]["saveRevision"] += 1
                    state["document"]["contentRevision"] += 1
                    invalidate_review(state)
                    state["review"] = None
                    state["project"]["review"].update(checkedContentRevision=None, openRequiredCount=None)
                if not card["imageId"]:
                    break
                self.lock(state, card)
                batch["completed"].append(card_id)
            if len(batch["completed"]) == len(batch["targets"]):
                batch["status"] = "ready"

        state = self.update(project_id, owner, skip_attached)
        batch = state["image_batch"]
        pending = [c for c in batch["targets"] if c not in batch["completed"]]
        if not pending:
            return self.result(state)
        card_id = pending[0]
        if self.generation.config.studio_character_mode == "library" or state.get("character_library"):
            self.generation.library.assign(project_id, owner)
            state = self.store.get(project_id, owner)[0]
        current = next((c for c in cards(state["document"]) if c["id"] == card_id), None)
        if current is None:
            fail(409, "image_card_changed", "자동 생성 대상 카드가 삭제됐어요.")
        if current["role"] != "person" and batch.get("sceneMode", "single") == "storyboard4":
            from .studio_storyboard import PRESET, StudioStoryboard, context_for, digest_for

            def reserve(state):
                batch = state["image_batch"]
                if batch.get("storyboardGroup"):
                    return
                lookup = {c["id"]: c for c in cards(state["document"])}
                pending_scenes = [i for i in batch["targets"] if i not in batch["completed"] and
                                  i in lookup and not lookup[i]["imageId"] and lookup[i]["role"] != "person"]
                groups = storyboard_groups(state, pending_scenes)
                ids = groups[0] if groups else []
                if batch["status"] != "running" or not ids or ids[0] != card_id:
                    fail(409, "image_card_changed", "4컷 생성 대상이 바뀌었어요.")
                context = context_for(state, ids)
                batch["storyboardGroup"] = {"id": uid(), "cardIds": ids, "digest": digest_for(context), "preset": PRESET}

            state = self.update(project_id, owner, reserve)
            try:
                StudioStoryboard(self.generation).run(project_id, owner, state["image_batch"]["storyboardGroup"])
            except HTTPException as error:
                if not isinstance(error.detail, dict) or error.detail.get("code") != "image_in_progress":
                    raise
            return self.result(self.store.get(project_id, owner)[0])
        digest = fingerprint(image_context(state, card_id))
        try:
            result = self.generation.candidates(project_id, owner, card_id)
        except HTTPException as error:
            if isinstance(error.detail, dict) and error.detail.get("code") == "image_in_progress":
                return self.result(self.store.get(project_id, owner)[0])
            raise
        if not result["candidates"]:
            fail(502, "image_missing_output", "자동 생성한 그림을 찾지 못했어요.")
        generated = result["candidates"][0]

        def attach(state):
            batch = state.get("image_batch")
            if not batch or card_id not in batch["targets"]:
                fail(409, "image_card_changed", "자동 생성 대상 문서가 바뀌었어요.")
            if batch["status"] == "skipped":
                fail(409, "image_card_changed", "자동 그림 생성이 중단됐어요. 최신 문서를 확인해 주세요.")
            if card_id in batch["completed"]:
                return
            if fingerprint(image_context(state, card_id)) != digest:
                fail(409, "image_card_changed", "자동 생성 중 카드 내용이 바뀌었어요.")
            card = next(c for c in cards(state["document"]) if c["id"] == card_id)
            if not card["imageId"]:
                asset = next((a for a in state["assets"] if a["src"] == generated["src"] and a["source"] == "library"), None)
                if asset is None:
                    fail(502, "image_missing_output", "자동 생성한 그림이 이 프로젝트에 저장되지 않았어요.")
                image = {k: asset[k] for k in ("id", "src", "alt", "meaning", "source")}
                if not any(i["id"] == image["id"] for i in state["document"]["images"]):
                    state["document"]["images"].append(image)
                card["imageId"] = image["id"]
                state["document"]["saveRevision"] += 1
                state["document"]["contentRevision"] += 1
                invalidate_review(state)
                state["review"] = None
                state["project"]["review"].update(checkedContentRevision=None, openRequiredCount=None)
            self.lock(state, card)
            batch["completed"].append(card_id)
            if len(batch["completed"]) == len(batch["targets"]):
                batch["status"] = "ready"

        return self.result(self.update(project_id, owner, attach))

    @staticmethod
    def result(state):
        batch = state["image_batch"]
        pending = [c for c in batch["targets"] if c not in batch["completed"]]
        current = next((c for c in cards(state["document"]) if pending and c["id"] == pending[0]), None)
        return {"document": state["document"], "project": state["project"], "generation": {
            "status": batch["status"], "phase": "portraits" if current and current["role"] == "person" else "scenes" if current else "complete",
            "completed": len(batch["completed"]), "total": len(batch["targets"]), "currentCardId": current["id"] if current else None}}
