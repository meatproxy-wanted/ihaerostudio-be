"""Verify runtime assets in a built wheel, without importing the source checkout."""
import argparse
import io
import json
from zipfile import ZipFile

from PIL import Image

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("wheel")
args = parser.parse_args()
with ZipFile(args.wheel) as package:
    prefix = "app/assets/characters-v1/"
    manifest = json.loads(package.read(prefix + "manifest.json"))
    assert len(manifest["characters"]) == 10
    assert len({c["id"] for c in manifest["characters"]}) == 10
    for character in manifest["characters"]:
        with Image.open(io.BytesIO(package.read(prefix + character["file"]))) as image:
            image.verify()
    assert package.read("app/prompts/easy_read_guidelines.md")
    assert package.read("app/assets/illustration-mood-v1.png").startswith(b"\x89PNG")
    assert package.read(prefix + "prompts.json")
    assert package.read(prefix + "cleanup-prompt.txt")
print("Wheel verified: 10 character originals, manifest, prompt records, mood sample, Easy-Read guideline")
