import io
import re
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader

from .models import Page, Paragraph, Source
from .store import fail

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT = 150000


def source_from_pages(texts: list[str], filename: str | None = None) -> Source:
    if sum(len(t) for t in texts) > MAX_TEXT:
        fail(413, "text_too_large", "추출 텍스트는 150,000자까지 지원합니다.")
    pages, paragraphs, notices = [], [], []
    for number, raw in enumerate(texts, 1):
        text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        pages.append(Page(number=number, text=text))
        if not text:
            notices.append(f"{number}쪽의 텍스트가 없습니다. 스캔 페이지는 OCR이 필요합니다.")
        # Keep offsets against original extracted page text, not rendered PDF pixels.
        for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|$)", text, flags=re.S):
            start, end = match.span()
            # Bounded paragraphs permit evidence validation and model requests.
            for part_start in range(start, end, 4000):
                part_end = min(part_start + 4000, end)
                while part_start < part_end and text[part_start].isspace():
                    part_start += 1
                while part_end > part_start and text[part_end - 1].isspace():
                    part_end -= 1
                if part_start < part_end:
                    paragraphs.append(Paragraph(id=f"p{number}-{len(paragraphs)+1}", page=number, start=part_start, end=part_end, text=text[part_start:part_end]))
    if not paragraphs:
        fail(422, "ocr_required", "읽을 수 있는 텍스트가 없습니다. OCR 처리 PDF 또는 텍스트를 입력하세요.")
    if len(paragraphs) > 300:
        fail(413, "too_many_paragraphs", "문단 300개까지 지원합니다. 문서를 나누어 올려주세요.")
    return Source(filename=filename, pages=pages, paragraphs=paragraphs, warnings=notices)


def extract_pdf(data: bytes, filename: str) -> Source:
    if len(data) > MAX_PDF_BYTES:
        fail(413, "file_too_large", "PDF는 최대 20MB입니다.")
    if not data.startswith(b"%PDF-"):
        fail(422, "invalid_pdf", "올바른 PDF 파일이 아닙니다.")
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            fail(422, "encrypted_pdf", "암호화된 PDF는 해제 후 올려주세요.")
        if len(reader.pages) > 100:
            fail(413, "too_many_pages", "PDF는 최대 100쪽입니다.")
        texts = []
        for page in reader.pages:
            # Guard decompressed page streams before expensive text layout processing.
            content = page.get_contents()
            if content and len(content.get_data()) > 10 * 1024 * 1024:
                fail(413, "page_too_large", "페이지 내용이 너무 큽니다.")
            texts.append(page.extract_text() or "")
        return source_from_pages(texts, filename)
    except Exception as exc:
        from fastapi import HTTPException
        if isinstance(exc, HTTPException):
            raise
        fail(422, "invalid_pdf", "PDF를 읽지 못했습니다. 손상 여부를 확인해주세요.")


def clean_image(data: bytes) -> tuple[bytes, int, int]:
    if len(data) > MAX_IMAGE_BYTES:
        fail(413, "image_too_large", "그림은 최대 5MB입니다.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    fail(422, "unsupported_image", "PNG, JPEG, WebP만 지원합니다.")
                if image.width * image.height > 16000000:
                    fail(413, "image_too_large", "그림은 최대 1,600만 화소입니다.")
                image.load()
                cleaned = ImageOps.exif_transpose(image).convert("RGB")
                cleaned.thumbnail((1600, 1600))
                out = io.BytesIO()
                cleaned.save(out, "PNG")
                return out.getvalue(), cleaned.width, cleaned.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        fail(422, "invalid_image", "그림 파일을 읽지 못했습니다.")
