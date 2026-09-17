"""Bundled, approved mood sample. Never treated as a character or case fact."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from .sources import clean_image

SAMPLE_PATH = Path(__file__).parent / "assets" / "illustration-mood-v1.png"
STYLE_POLICY = "light-mood-only-v1"
STYLE_PLANNING = (
    "styleReference는 사건이나 인물이 아닌 화면 분위기 참고입니다. "
    "단순한 애니메이션 이미지 또는 삽화 느낌을 가볍게 참고하며 인물 외형과 카드 상황이 우선입니다. "
    "샘플의 인물·얼굴·옷·자세·사물·배경·구도는 복사하지 마세요. "
    "샘플 그림체를 정밀하게 일치시키는 지시를 추가하지 마세요. "
)


@lru_cache(maxsize=1)
def sample_pixels():
    return clean_image(SAMPLE_PATH.read_bytes())[0]


def style_sample():
    return {"id": "illustration-mood-v1", "digest": sha256(sample_pixels()).hexdigest(),
            "purpose": "overall mood only; not character identity, layout or case evidence", "policy": STYLE_POLICY}


def mood_instruction(number):
    return (f" Picture {number} is only a light reference for the overall illustration mood. "
            "A similar general feeling is sufficient; do not closely imitate it. "
            "Do not copy its people, faces, clothes, poses, objects, background or layout. "
            "Existing character identity and the requested situation take priority over this mood sample.")
