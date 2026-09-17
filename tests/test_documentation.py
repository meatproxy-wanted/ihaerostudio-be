"""Keep public documentation aligned with runtime contracts and packaged assets."""
import fnmatch
from pathlib import Path
import re
import tomllib

from app.studio_domain import LONG_SENTENCE
from app.studio_library import ROOT as CHARACTER_ROOT, catalog
from test_studio import BASE, client

ROOT = Path(__file__).resolve().parents[1]


def test_openapi_operation_count_is_derived_from_served_paths(client):
    schema = client.get("/openapi.json").json()
    count = sum(len(methods) for methods in schema["paths"].values())
    assert f"{count}개 동작" in schema["tags"][0]["description"]


def test_image_docs_distinguish_stock_faces_originals_and_application(client):
    schema = client.get("/openapi.json").json()
    prepare = schema["paths"][BASE + "/projects/{project_id}/document/prepare-images"]["post"]
    candidates = schema["paths"][BASE + "/projects/{project_id}/assist/images"]["post"]
    for operation in (prepare, candidates):
        assert "얼굴 크롭" in operation["description"]
        assert "기본 포즈 원본" in operation["description"]
        assert "40 steps" in operation["description"]
    assert "자동 적용하지 않습니다" in candidates["description"]
    assert "10종 후보" in prepare["description"]
    assert "image_in_progress" in prepare["responses"]["503"]["content"]["application/json"]["examples"]


def test_review_and_image_field_docs_do_not_claim_extra_rules(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"][BASE + "/projects/{project_id}/review/run"]["post"]
    assert f"{LONG_SENTENCE}자를 넘는 문장" in operation["description"]
    models = schema["components"]["schemas"]
    assert "자동 검토 규칙에는 포함되지 않습니다" in models["DocImage"]["properties"]["alt"]["description"]
    assert "prepare-images는 자동 추가" in models["EasyDocument"]["properties"]["images"]["description"]
    assert "stock 캐릭터 여부가 아닙니다" in models["DocImage"]["properties"]["source"]["description"]


def test_runtime_asset_paths_are_included_in_package_data():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["app"]
    required = [ROOT / "app/prompts/easy_read_guidelines.md", ROOT / "app/assets/illustration-mood-v1.png",
                CHARACTER_ROOT / "manifest.json", CHARACTER_ROOT / "prompts.json", CHARACTER_ROOT / "cleanup-prompt.txt"]
    required.extend(CHARACTER_ROOT / c["file"] for c in catalog()["characters"])
    for asset in required:
        relative = asset.relative_to(ROOT / "app").as_posix()
        assert asset.is_file(), relative
        assert any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns), relative


def test_local_document_links_resolve():
    documents = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
    for document in documents:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text()):
            if ":" in target or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            assert (document.parent / path).exists(), (document.name, target)
