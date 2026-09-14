"""현재 코드의 Swagger/OpenAPI 계약을 JSON 파일로 내보냅니다."""
import argparse
import json
from pathlib import Path

from app.main import app

parser = argparse.ArgumentParser()
parser.add_argument("--output", default="docs/openapi.json")
args = parser.parse_args()
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"OpenAPI exported: {path}")
