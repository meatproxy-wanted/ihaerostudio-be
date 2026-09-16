"""A minimal SQL-over-HTTP (Hrana v3) server backed by sqlite3, for tests only.

It speaks the subset turso_serverless uses: /v3/pipeline (execute, sequence, close,
get_autocommit) and /v3/cursor (a batch of steps with is_autocommit conditions).
"""
import base64
import json
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def encode(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool) or isinstance(value, int):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode()}


def decode(value):
    kind = value["type"]
    if kind == "null":
        return None
    if kind == "integer":
        return int(value["value"])
    if kind == "float":
        return float(value["value"])
    if kind == "text":
        return value["value"]
    return base64.b64decode(value["base64"])


class FakeHrana:
    def __init__(self, path):
        self.path = path
        self.streams = {}
        self.lock = threading.Lock()
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append(self.headers.get("Authorization"))
                if self.path == "/v3/pipeline":
                    payload = json.dumps(server.pipeline(body)).encode()
                else:
                    payload = server.cursor(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for conn in self.streams.values():
            conn.close()

    def stream(self, baton):
        with self.lock:
            if baton and baton in self.streams:
                return baton, self.streams[baton]
            baton = uuid.uuid4().hex
            conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=10)
            self.streams[baton] = conn
            return baton, conn

    def run(self, conn, stmt):
        args = [decode(a) for a in stmt.get("args") or []]
        cursor = conn.execute(stmt["sql"], args)
        cols = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall() if cols and stmt.get("want_rows", True) else []
        affected = cursor.rowcount if not cols and cursor.rowcount >= 0 else 0
        return cols, rows, affected, cursor.lastrowid

    def pipeline(self, body):
        baton, conn = self.stream(body.get("baton"))
        results = []
        for request in body["requests"]:
            try:
                kind = request["type"]
                if kind == "execute":
                    cols, rows, affected, rowid = self.run(conn, request["stmt"])
                    results.append({"type": "ok", "response": {"type": "execute", "result": {
                        "cols": [{"name": c, "decltype": None} for c in cols],
                        "rows": [[encode(v) for v in row] for row in rows],
                        "affected_row_count": affected, "last_insert_rowid": str(rowid) if rowid else None}}})
                elif kind == "sequence":
                    conn.executescript(request["sql"])
                    results.append({"type": "ok", "response": {"type": "sequence"}})
                elif kind == "get_autocommit":
                    results.append({"type": "ok", "response": {"type": "get_autocommit", "is_autocommit": not conn.in_transaction}})
                elif kind == "close":
                    with self.lock:
                        self.streams.pop(baton, None)
                    conn.close()
                    results.append({"type": "ok", "response": {"type": "close"}})
                    return {"baton": None, "base_url": None, "results": results}
                else:
                    raise ValueError(f"unsupported request {kind}")
            except Exception as error:  # noqa: BLE001 - the protocol reports any failure per request
                results.append({"type": "error", "error": {"message": str(error)}})
        return {"baton": baton, "base_url": None, "results": results}

    def cursor(self, body):
        baton, conn = self.stream(body.get("baton"))
        lines = [json.dumps({"baton": baton, "base_url": None})]
        for index, step in enumerate(body["batch"]["steps"]):
            condition = step.get("condition")
            if condition and condition["type"] == "is_autocommit" and conn.in_transaction:
                continue
            try:
                cols, rows, affected, rowid = self.run(conn, step["stmt"])
            except Exception as error:  # noqa: BLE001
                lines.append(json.dumps({"type": "step_error", "step": index, "error": {"message": str(error)}}))
                continue
            lines.append(json.dumps({"type": "step_begin", "step": index, "cols": [{"name": c, "decltype": None} for c in cols]}))
            for row in rows:
                lines.append(json.dumps({"type": "row", "row": [encode(v) for v in row]}))
            lines.append(json.dumps({"type": "step_end", "affected_row_count": affected,
                                     "last_insert_rowid": str(rowid) if rowid else None}))
        return "\n".join(lines) + "\n"
