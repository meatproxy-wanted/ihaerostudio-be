"""Saved character cards are the shared identity reference, never arbitrary URLs."""
import base64
import binascii
import hashlib

from .sources import clean_image
from .store import fail
from .studio_domain import anchor_text, cards

MAX_REFERENCES = 6


def character_context(state, target):
    document = state["document"]
    parties = {p["id"]: p for p in state["structure"]["parties"]}
    images = {i["id"]: i for i in document["images"]}
    assets = {a["src"]: a for a in state["assets"]}
    people = [c for c in cards(document) if c["role"] == "person"]
    characters, references = [], []
    for name in document["partyNames"]:
        party_id = name["partyId"]
        party = parties.get(party_id, {})
        portraits = [c for c in people if c["partyId"] == party_id]
        descriptions = [{"text": s["text"], "evidence": [anchor_text(state["source"], a) for a in s["anchors"]]}
                        for c in portraits for s in c["sentences"]]
        character = {**name, "legalStatus": party.get("legalStatus", ""), "easyRole": party.get("easyRole", ""),
                     "descriptions": descriptions,
                     "evidence": [anchor_text(state["source"], a) for a in party.get("anchors", [])]}
        characters.append(character)
        # Creating a portrait must not copy a different person's face, or invalidate its own cache on attachment.
        if target["role"] == "person":
            continue
        selected = {}
        for card in portraits:
            if image := images.get(card["imageId"]):
                asset = assets.get(image["src"])
                if asset is None:
                    fail(422, "character_reference_invalid", "등장인물 그림을 이 자료에 다시 저장해 주세요.")
                selected[asset["id"]] = (asset, image)
        if len(selected) > 1:
            fail(422, "character_reference_ambiguous", "같은 등장인물에 서로 다른 그림이 연결돼 있어요. 기준 그림을 하나로 맞춰 주세요.")
        for asset, image in selected.values():
            references.append({"partyId": party_id, "displayName": name["displayName"],
                               "imageNumber": len(references) + 1, "assetId": asset["id"],
                               "digest": hashlib.sha256(asset["src"].encode()).hexdigest(),
                               "alt": image["alt"], "meaning": image["meaning"]})
    if len(references) > MAX_REFERENCES:
        fail(422, "character_reference_limit", "한 장의 그림에는 등장인물 기준 그림을 6개까지 사용할 수 있어요.")
    # The portrait's own saved explanation is already in the target card context.
    # Exclude other character cards so completing another portrait does not regenerate this one.
    if target["role"] == "person":
        characters = [c for c in characters if c["partyId"] == target["partyId"]]
    return characters, references


def reference_bytes(store, state, owner, reference):
    asset = next((a for a in state["assets"] if a["id"] == reference["assetId"]), None)
    if asset is None or hashlib.sha256(asset["src"].encode()).hexdigest() != reference["digest"]:
        fail(422, "character_reference_invalid", "등장인물 기준 그림을 다시 저장해 주세요.")
    # Compatibility with old local SQLite projects. Never fetch a document-supplied URL.
    if asset["src"].startswith("data:"):
        try:
            header, encoded = asset["src"].split(",", 1)
            if header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
                raise ValueError("format")
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            fail(422, "character_reference_invalid", "등장인물 기준 그림은 PNG·JPEG·WebP로 저장해 주세요.")
    else:
        with store.store.connect() as db:
            row = db.execute("SELECT content_type,body FROM studio_assets WHERE id=? AND project_id=? AND owner=?",
                             (asset["id"], state["project"]["id"], owner)).fetchone()
        if not row or row["content_type"] not in {"image/png", "image/jpeg", "image/webp"}:
            fail(422, "character_reference_invalid", "등장인물 기준 그림은 이 자료의 PNG·JPEG·WebP로 저장해 주세요.")
        data = bytes(row["body"])
    if len(data) > 5 * 1024 * 1024:
        fail(422, "character_reference_invalid", "등장인물 기준 그림은 5MB 이하로 저장해 주세요.")
    return clean_image(data)[0]
