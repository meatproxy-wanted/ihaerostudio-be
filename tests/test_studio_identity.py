"""Quality decisions must follow observed fields, including omitted people."""
import pytest

from app.studio_identity import CharacterIdentity


PROFILE = dict(partyId="a", imageNumber=1, facialHair="none", hair="short brown hair",
               upperClothing="gray shirt", lowerClothing="tan trousers", shoes="black sneakers")
PERSON = dict(referenceImageNumber=1, faceVisible=True, facialHair="none", faceAndHairMatch=True,
              upperClothingMatch=True, lowerClothingMatch=True, shoesMatch=True, differences=[])


def inspect(people, context=None):
    class Observer:
        def call(self, task, payload, schema, *, images):
            assert images == [b"candidate"]
            assert payload["appearances"] == [PROFILE]
            return schema(characters=people, alt="생성된 장면")
    checker = CharacterIdentity.__new__(CharacterIdentity)
    checker.provider = Observer()
    return checker.check([PROFILE], b"candidate", context or {})


@pytest.mark.parametrize("change", [
    {"facialHair": "beard"}, {"faceAndHairMatch": False},
    {"upperClothingMatch": False}, {"lowerClothingMatch": False}, {"shoesMatch": False},
    {"referenceImageNumber": None},
])
def test_rejects_individual_identity_changes(change):
    result = inspect([{**PERSON, **change}])
    assert not result.consistent and result.issues
    assert "black sneakers" in result.correction
    assert '"facialHair": "none"' in result.correction


def test_rejects_duplicate_character_and_missing_named_character():
    assert not inspect([PERSON, PERSON]).consistent
    context = {"parties": [{"partyId": "a", "displayName": "A씨"}],
               "sentences": [{"text": "A씨는 결정을 확인해요."}]}
    assert not inspect([], context).consistent
    assert not inspect([], {"partyId": "a"}).consistent


def test_allows_matching_scene_and_no_unrequired_people():
    assert inspect([PERSON]).consistent
    assert inspect([]).consistent
    assert inspect([PERSON]).correction == ""



@pytest.mark.parametrize("facial_hair,description", [
    ("none", "Clean-shaven. No beard, moustache or stubble."),
    ("beard", "A short beard."),
])
def test_prompt_uses_only_reference_images_for_appearance(facial_hair, description):
    from app.studio_identity import identity_instructions
    profile = {**PROFILE, "face": "oval face", "facialHair": facial_hair, "facialHairDescription": description,
               "accessories": "no accessories", "style": "flat vector"}
    text = identity_instructions([profile])
    assert text == "Use the reference images for character appearance."
    assert "oval face" not in text and "gray shirt" not in text
    assert profile["facialHairDescription"] == description



def test_hidden_face_alone_does_not_reject_candidate():
    assert inspect([{**PERSON, "faceVisible": False, "facialHair": "unclear"}]).consistent
