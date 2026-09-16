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
    {"facialHair": "beard"}, {"faceVisible": False}, {"faceAndHairMatch": False},
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



def test_prompt_repeats_fixed_english_features_and_explicit_absence_of_beard():
    from app.studio_identity import identity_instructions
    profile = {**PROFILE, "face": "oval face", "facialHairDescription": "none",
               "accessories": "no accessories", "style": "flat vector"}
    text = identity_instructions([profile])
    assert "Reference image 1:" in text
    assert "Clean-shaven. No beard, moustache or stubble." in text
    assert "gray shirt" in text and "black sneakers" in text
    assert "Only pose, expression and setting may change" in text
    assert "partyId" not in text



@pytest.mark.parametrize("faces,portrait,expected", [
    ([], True, False), ([], False, True), ([False], True, False),
    ([True], True, True), ([True, True], True, False), ([True, False], False, False),
])
def test_face_gate_checks_every_person_and_requires_one_portrait(faces, portrait, expected):
    from app.studio_identity import check_faces
    class Observer:
        def call(self, task, payload, schema, *, images):
            return schema(visibleFaces=faces, alt="관찰 결과")
    assert check_faces(Observer(), b"image", portrait).consistent == expected
