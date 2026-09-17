"""Render the actual bundled neutral references and face crops for visual QA."""
import io
from pathlib import Path

from PIL import Image, ImageDraw

from app.studio_library import catalog, face_pixels, reference_pixels

output = Path("../../outputs/character-library-preview-v1")
output.mkdir(parents=True, exist_ok=True)
characters = catalog()["characters"]
for offset in (0, 5):
    canvas = Image.new("RGB", (1500, 650), "#EEF1F8")
    draw = ImageDraw.Draw(canvas)
    for index, entry in enumerate(characters[offset:offset + 5]):
        ref = reference_pixels(entry["id"])
        face = face_pixels(entry["id"])
        (output / (entry["id"] + "-reference.png")).write_bytes(ref)
        (output / (entry["id"] + "-face.png")).write_bytes(face)
        for row, pixels in enumerate((ref, face)):
            image = Image.open(io.BytesIO(pixels)).resize((280, 280), Image.Resampling.LANCZOS)
            canvas.paste(image, (index * 300 + 10, row * 310 + 32))
        draw.text((index * 300 + 12, 12), entry["id"], fill="#23314D")
    canvas.save(output / f"gallery-{offset + 1:02d}-{offset + 5:02d}.png")
faces = Image.new("RGB", (1500, 650), "#EEF1F8")
draw = ImageDraw.Draw(faces)
for index, entry in enumerate(characters):
    column, row = index % 5, index // 5
    image = Image.open(io.BytesIO(face_pixels(entry["id"]))).resize((280, 280), Image.Resampling.LANCZOS)
    faces.paste(image, (column * 300 + 10, row * 325 + 32))
    draw.text((column * 300 + 12, row * 325 + 12), entry["id"], fill="#23314D")
faces.save(output / "faces-10.png")
print(output.resolve())
