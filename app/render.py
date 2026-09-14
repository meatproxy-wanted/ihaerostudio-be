import base64
import hashlib
import io
import threading
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, KeepTogether

from .domain import reader_view
from .store import fail

_font_lock = threading.Lock()


def korean_font(path=None):
    candidates = [path] if path else [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        str(Path.home() / "Library/Fonts/NanumGothicCoding-Regular.ttf"),
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            name = "Korean-" + hashlib.sha256(candidate.encode()).hexdigest()[:10]
            with _font_lock:
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, candidate))
            return name
    fail(503, "pdf_font_missing", "한글 TTF 글꼴이 필요합니다. PDF_FONT_PATH를 지정하세요. Docker에는 나눔고딕이 설치됩니다.")


def render_pdf(doc, store, font_path=None):
    view = reader_view(doc)
    font = korean_font(font_path)
    output = io.BytesIO()
    pdf = SimpleDocTemplate(output, pagesize=A4, rightMargin=44, leftMargin=44, topMargin=50, bottomMargin=58, title=view.title, author="이해로 스튜디오")
    body = ParagraphStyle("body", fontName=font, fontSize=14, leading=23, wordWrap="CJK", spaceAfter=12)
    heading = ParagraphStyle("heading", parent=body, fontSize=17, leading=26, textColor=colors.HexColor("#2b49d8"), spaceBefore=20, spaceAfter=12, keepWithNext=True)
    title = ParagraphStyle("title", parent=body, fontSize=24, leading=34, textColor=colors.HexColor("#101938"), spaceAfter=20)
    small = ParagraphStyle("small", parent=body, fontSize=10, leading=16, textColor=colors.HexColor("#56627a"))
    p = lambda text, style=body: Paragraph(escape(text).replace("\n", "<br/>"), style)
    story = [p("이해로 스튜디오", small), p(view.title, title), p("검토 전 미리보기" if view.is_draft else f"제작자 검토본 · 버전 {view.version}", small), p(view.disclaimer, small), Spacer(1, 12)]
    if view.people:
        story.append(p("누가 나오나요", heading))
        for person in view.people:
            story.append(p(f"{person.label} ({person.role}) {person.relationship}"))
    for i, card in enumerate(view.cards, 1):
        story.append(p(f"{i}. {card.title}", heading))
        if card.picture:
            blob = store.asset(doc.id, card.picture.asset_id)
            img = Image(io.BytesIO(blob))
            scale = min(220 / img.imageWidth, 160 / img.imageHeight)
            img.drawWidth, img.drawHeight = img.imageWidth * scale, img.imageHeight * scale
            img.hAlign = "LEFT"
            story.append(img)
            story.append(p(card.picture.alt_text, small))
        story.append(p(card.text))
        for term in card.terms:
            story.append(p(f"{term.term}: {term.explanation}", small))
    def footer(canvas, _):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#dce2ef"))
        canvas.line(44, 43, A4[0] - 44, 43)
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#66718a"))
        canvas.drawString(44, 28, "공식 판결문을 대신하지 않는 쉬운 설명자료")
        canvas.drawRightString(A4[0] - 44, 28, str(canvas.getPageNumber()))
        canvas.restoreState()
    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def render_html(doc, store):
    view = reader_view(doc)
    people = "".join(f"<li>{escape(p.label)} · {escape(p.role)} {escape(p.relationship)}</li>" for p in view.people)
    cards = []
    for card in view.cards:
        img = ""
        if card.picture:
            data = base64.b64encode(store.asset(doc.id, card.picture.asset_id)).decode()
            img = f'<img src="data:image/png;base64,{data}" alt="{escape(card.picture.alt_text, quote=True)}">'
        terms = "".join(f"<dt>{escape(t.term)}</dt><dd>{escape(t.explanation)}</dd>" for t in card.terms)
        cards.append(f'<article><h2>{escape(card.title)}</h2>{img}<p>{escape(card.text)}</p><dl>{terms}</dl></article>')
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(view.title)}</title>
<style>body{{margin:0;background:#f4f7fc;color:#18223b;font-family:system-ui,sans-serif;font-size:21px;line-height:1.8}}main{{max-width:780px;margin:auto;padding:36px 20px}}.brand{{color:#3555e8;font-size:17px}}h1{{font-size:32px;line-height:1.4}}h2{{font-size:24px;line-height:1.5;color:#2543b5}}article,section{{background:white;border:1px solid #dce3ef;border-radius:16px;margin:24px 0;padding:26px}}p{{white-space:pre-wrap;overflow-wrap:anywhere}}.notice,footer{{font-size:16px;color:#4d5b73}}img{{display:block;max-width:100%;max-height:320px;object-fit:contain}}dt{{font-weight:bold}}dd{{margin:0 0 16px}}@media print{{body{{background:white}}main{{padding:0}}article{{break-inside:avoid}}}}</style></head><body><main><p class="brand">이해로 스튜디오</p><h1>{escape(view.title)}</h1><p class="notice">{'검토 전 미리보기' if view.is_draft else '제작자가 검토한 설명자료'}</p><p class="notice">{escape(view.disclaimer)}</p><section><h2>누가 나오나요</h2><ul>{people}</ul></section>{''.join(cards)}<footer>설명자료 버전 {view.version}</footer></main></body></html>'''
