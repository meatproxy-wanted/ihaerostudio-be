"""가상 사례만을 사용하는 6단계 HTTP 스모크 테스트. 실제 판결에 자동 승인하지 마세요."""
import argparse
import json
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:8000")
parser.add_argument("--token", default="dev-only-change-me")
parser.add_argument("--output", default="data/demo.pdf")
args = parser.parse_args()
sample = json.loads((Path(__file__).resolve().parents[1] / "examples/judgment.json").read_text())

with httpx.Client(base_url=args.base_url, headers={"Authorization": "Bearer " + args.token}, timeout=180) as client:
    def request(method, path, payload=None):
        r = client.request(method, path, json=payload)
        if r.is_error:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text}")
        return r.json()

    doc = request("POST", "/api/v1/documents/text", sample)
    def step(path, **body):
        global doc
        doc = request("POST", f'/api/v1/documents/{doc["id"]}/{path}', {"expected_version": doc["version"], **body})

    step("structure/analyze")
    step("structure/confirm", confirmed=True, note="가상 테스트 데이터의 구조 확인")
    step("draft")
    first = doc["blocks"][0]
    step(f'blocks/{first["id"]}/proposals', action="split")
    proposal = doc["proposals"][-1]
    step(f'proposals/{proposal["id"]}/apply')
    step("reviews")
    # This acknowledgement is only suitable for the fixed synthetic test fixture above.
    for issue in list(doc["review"]["issues"]):
        step(f'reviews/issues/{issue["id"]}/resolve', note="실제 판결이 아닌 자동화 테스트 데이터 확인")
    step("reviews/approve", confirmed=True, note="가상 사례의 API 흐름 검증을 위한 승인")
    export = request("POST", f'/api/v1/documents/{doc["id"]}/exports', {"expected_version": doc["version"], "share": True})
    pdf = client.get(export["pdf_path"]); pdf.raise_for_status()
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(pdf.content)
    reader = client.get(export["share_path"]); reader.raise_for_status()
    path.with_suffix(".html").write_text(reader.text, encoding="utf-8")
    print(json.dumps({"document_id": doc["id"], "version": doc["version"], "pdf": str(path), "reader": args.base_url + export["share_path"]}, ensure_ascii=False, indent=2))
