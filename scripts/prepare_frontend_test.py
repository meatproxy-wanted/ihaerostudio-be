"""Prepare a disposable FE integration workspace without editing its repository.

The only source substitutions are ApiClient's implementation selector and the
local HTTP transport. All screens, stores and domain schemas are copied from HEAD.
"""
import argparse
import io
from pathlib import Path
import subprocess
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--frontend", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
frontend, output = args.frontend.resolve(), args.output.resolve()
repo = Path(__file__).resolve().parents[1]
if output.exists():
    parser.error("Output must be a new directory; existing files are never overwritten.")
if not (frontend / "node_modules").is_dir():
    parser.error("Install FE dependencies first.")
archive = subprocess.check_output(["git", "-C", str(frontend), "archive", "HEAD"])
output.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    tar.extractall(output, filter="data")
(output / "node_modules").symlink_to(frontend / "node_modules", target_is_directory=True)
transport = (repo / "tests/frontend/http-client.ts").read_text()
(output / "lib/api/http-client.ts").write_text(transport)
(output / "lib/api/client.ts").write_text('''// Temporary integration workspace only.
import { createHttpApi } from "./http-client";
import { createValidatedClient } from "./validated-client";
export const api = createValidatedClient(createHttpApi());
''')
print(f"Prepared integration workspace: {output}")
print("BE: AI_PROVIDER=demo CORS_ORIGINS=http://127.0.0.1:3100 uvicorn app.main:app --host 127.0.0.1 --port 8100 --no-access-log")
print("FE (in the generated workspace): node_modules/.bin/next build --webpack")
print("FE: node_modules/.bin/next start --hostname 127.0.0.1 --port 3100")
