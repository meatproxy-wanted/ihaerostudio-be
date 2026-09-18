"""Explicit frontend and owner-only inspection surface, plus a health probe."""
import io
import json

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import create_app
from app.sources import MAX_PDF_BYTES
from test_studio import BASE, SETTINGS, client, create, draft, path


def test_only_frontend_and_inspection_routes_are_served(client):
    expected = {
        "/projects": {"get"}, "/projects/text": {"post"}, "/projects/pdf": {"post"},
        "/projects/{project_id}": {"get", "delete"},
        "/projects/{project_id}/title": {"patch"},
        "/projects/{project_id}/settings": {"put"},
        "/projects/{project_id}/source": {"get"},
        "/projects/{project_id}/structure": {"get", "put"},
        "/projects/{project_id}/document": {"get", "put"},
        "/projects/{project_id}/document/generate": {"post"},
        "/projects/{project_id}/document/prepare-images": {"post"},
        "/projects/{project_id}/image-jobs": {"get"},
        "/projects/{project_id}/image-jobs/{job_id}": {"get"},
        "/projects/{project_id}/review": {"get"},
        "/projects/{project_id}/publications": {"get", "post"},
        "/projects/{project_id}/publications/{publication_id}": {"get"},
        "/projects/{project_id}/public": {"put"},
        "/reader/{project_id}": {"get"}, "/assets/{asset_id}": {"get"},
        "/demo/sample-text": {"get"}, "/demo/reset": {"post"},
    }
    for action in ["simplify", "split", "terms", "explain", "images", "upload-image"]:
        expected[f"/projects/{{project_id}}/assist/{action}"] = {"post"}
    for action in ["run", "dismiss", "dismiss-all", "restore", "complete"]:
        expected[f"/projects/{{project_id}}/review/{action}"] = {"post"}
    expected = {BASE + key: value for key, value in expected.items()}
    schema = client.get("/openapi.json").json()
    assert {key: set(value) for key, value in schema["paths"].items()} == expected
    # Also check runtime routes: hiding an endpoint from Swagger is insufficient.
    served = {}
    pending = list(client.app.routes)
    while pending:
        route = pending.pop()
        if isinstance(route, APIRoute):
            served.setdefault(route.path, set()).update(m.lower() for m in route.methods)
        elif hasattr(route, "original_router"):
            pending.extend(route.original_router.routes)
    assert served == {**expected, "/health": {"get"}}
    for old in ["/api/v1/documents", "/api/v1/documents/old", "/api/v1/video-jobs/old",
                "/api/v1/image-jobs/old", "/api/v1/exports/old/pdf", "/share/old"]:
        assert client.get(old).status_code == 404
    assert client.post("/api/v1/documents/text", json={}).status_code == 404
    assert client.get("/health", headers={"Authorization": ""}).json()["status"] == "ok"
    assert client.get("/docs").status_code == client.get("/redoc").status_code == 200
    assert schema["paths"][BASE + "/projects/text"]["post"]["security"]
    assert schema["paths"][BASE + "/projects/{project_id}/image-jobs"]["get"]["security"]
    assert schema["paths"][BASE + "/projects/{project_id}/image-jobs/{job_id}"]["get"]["security"]
    assert not schema["paths"][BASE + "/reader/{project_id}"]["get"].get("security")
    assert not schema["paths"][BASE + "/assets/{asset_id}"]["get"].get("security")
    assert not hasattr(client.app.state, "images")
    assert not hasattr(client.app.state, "videos")


def test_frontend_data_survives_server_restart(client):
    project = create(client)
    document = draft(client, project)
    document["title"] = "서버 재시작 후에도 유지되는 자료"
    saved = client.put(path(project) + "/document", json=document).json()["document"]
    with TestClient(create_app(client.app.state.config), headers=client.headers) as restarted:
        assert restarted.get(path(project) + "/document").json() == saved


@pytest.mark.parametrize("kind,code,status", [
    ("invalid", "invalid_pdf", 422), ("blank", "ocr_required", 422),
    ("encrypted", "encrypted_pdf", 422), ("oversize", "file_too_large", 413),
    ("huge", "request_too_large", 413),
])
def test_pdf_rejections_leave_no_project(client, kind, code, status):
    writer, output = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=612, height=792)
    if kind == "encrypted":
        writer.encrypt("test-password")
    writer.write(output)
    data = output.getvalue()
    if kind == "invalid":
        data = b"not a PDF"
    elif kind == "oversize":
        data = b"%PDF-" + b"x" * MAX_PDF_BYTES  # over the file limit, under the request body cap
    elif kind == "huge":
        data = b"%PDF-" + b"x" * (6 * 1024 * 1024)  # the body cap rejects it before parsing
    response = client.post(BASE + "/projects/pdf", data={"settings": json.dumps(SETTINGS)},
                           files={"file": ("test.pdf", data, "application/pdf")})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert client.get(BASE + "/projects").json() == []


def test_source_offsets_for_multiple_pages():
    from app.sources import source_from_pages
    source = source_from_pages(["  첫 문단입니다.\n\n두 번째 문단입니다.  ", "다음 쪽입니다.\n\n끝입니다."])
    assert len(source.paragraphs) == 4
    for paragraph in source.paragraphs:
        assert source.pages[paragraph.page - 1].text[paragraph.start:paragraph.end] == paragraph.text
