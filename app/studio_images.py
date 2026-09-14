"""Raster cleanup and a deliberately small, inert SVG subset for the FE upload contract."""
import re
import xml.etree.ElementTree as ET

from .sources import clean_image
from .store import fail

MAX_UPLOAD = 2 * 1024 * 1024
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
        "title", "desc", "defs", "linearGradient", "radialGradient", "stop", "clipPath"}
ATTRIBUTES = {"id", "viewBox", "width", "height", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry",
              "d", "points", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
              "fill-rule", "clip-rule", "opacity", "fill-opacity", "stroke-opacity", "transform", "clip-path",
              "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "fx", "fy",
              "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline", "dx", "dy",
              "preserveAspectRatio"}


def cleaned_upload(data, filename):
    if len(data) > MAX_UPLOAD:
        fail(413, "image_too_large", "2MB 이하의 그림만 올릴 수 있어요.")
    if not filename.lower().endswith(".svg"):
        body, _, _ = clean_image(data)
        return body, "image/png"
    # No DTD/entity processing, network references, CSS sheets, scripts, embedded HTML,
    # animation, filters or image/use nodes. Only local paint and clip references survive.
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", data, re.I):
        fail(422, "unsafe_svg", "외부 참조가 없는 SVG 또는 PNG/JPG 그림을 사용해 주세요.")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        fail(422, "invalid_image", "SVG 그림을 읽지 못했어요.")
    if root.tag not in {"svg", f"{{{SVG_NAMESPACE}}}svg"}:
        fail(422, "invalid_image", "SVG 그림이 아닙니다.")
    nodes = list(root.iter())
    if len(nodes) > 10000:
        fail(413, "image_too_complex", "SVG가 너무 복잡해요. PNG/JPG로 변환해 주세요.")
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 64:
            fail(413, "image_too_complex", "SVG 중첩이 너무 깊어요. PNG/JPG로 변환해 주세요.")
        stack.extend((child, depth + 1) for child in node)
    for node in nodes:
        tag = node.tag.removeprefix(f"{{{SVG_NAMESPACE}}}")
        if tag not in TAGS:
            fail(422, "unsafe_svg", "이 SVG의 기능은 지원하지 않아요. PNG/JPG로 변환해 주세요.")
        for key, value in node.attrib.items():
            if key not in ATTRIBUTES:
                fail(422, "unsafe_svg", "외부 참조·스타일·스크립트가 없는 SVG를 사용해 주세요.")
            if len(value) > 200000 or re.search(r"(?:javascript:|data:|https?:|//|@import|expression\s*\()", value, re.I):
                fail(422, "unsafe_svg", "외부 참조가 없는 SVG를 사용해 주세요.")
            if "url" in value.lower() and not re.fullmatch(r"url\(#[A-Za-z_][A-Za-z0-9_-]*\)", value):
                fail(422, "unsafe_svg", "SVG의 외부 참조는 지원하지 않아요.")
    ET.register_namespace("", SVG_NAMESPACE)
    return ET.tostring(root, encoding="utf-8"), "image/svg+xml"
