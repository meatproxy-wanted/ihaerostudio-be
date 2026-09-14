"""Comfy Cloud v2 HTTP boundary. No automatic paid submission retries."""
import re
from urllib.parse import urljoin, urlsplit

import httpx

from .store import fail
from .video_models import VideoOutput, VideoPreflight
from .video_workflows import validate_graph

ORIGIN = "https://cloud.comfy.org"
STATES = {"queued", "running", "succeeded", "canceling", "canceled", "failed", "expired"}


def expanded_inputs(groups, values, prefix="", depth=0):
    if depth > 8:
        raise ComfyFailure("comfy_invalid_node_schema")
    required, inputs = set(), {}
    for group in ("required", "optional"):
        for name, spec in groups.get(group, {}).items():
            if not isinstance(spec, list) or not spec or not isinstance(spec[0], (list, str)):
                raise ComfyFailure("comfy_invalid_node_schema")
            full = prefix + name
            inputs[full] = spec
            if group == "required":
                required.add(full)
            if spec[0] == "COMFY_DYNAMICCOMBO_V3" and full in values:
                options = spec[1]["options"]
                selected = next((o for o in options if o["key"] == values[full]), None)
                if selected:
                    child_required, child_inputs = expanded_inputs(selected["inputs"], values, full + ".", depth + 1)
                    required |= child_required
                    inputs.update(child_inputs)
    return required, inputs


class ComfyFailure(Exception):
    def __init__(self, code, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


def job_link(link, provider_id, suffix=""):
    url = urljoin(ORIGIN, link)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "cloud.comfy.org" or parsed.query or parsed.fragment
            or parsed.path != f"/api/v2/jobs/{provider_id}{suffix}"):
        raise ComfyFailure("comfy_invalid_response", uncertain=True)
    return url


class ComfyCloud:
    def __init__(self, config):
        self.config = config

    def require_key(self):
        if not self.config.comfy_api_key.strip():
            fail(503, "comfy_not_configured", "서버의 COMFY_CLOUD_API_KEY를 설정하세요. 영상은 생성되지 않았습니다.")

    def request(self, method, url, *, data=None, key=None):
        self.require_key()
        if urlsplit(url).scheme != "https" or urlsplit(url).netloc != "cloud.comfy.org":
            raise ComfyFailure("comfy_invalid_url")
        headers = {"Authorization": "Bearer " + self.config.comfy_api_key}
        if "/api/object_info" in url:
            # Cloud's model/node catalog is still on its v1 surface.
            headers = {"X-API-Key": self.config.comfy_api_key}
        if key:
            headers["Idempotency-Key"] = key
        try:
            with httpx.Client(timeout=httpx.Timeout(45, connect=5), trust_env=False, follow_redirects=False) as client:
                response = client.request(method, url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Invalid response shape")
            return result
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            code = {401: "comfy_auth_error", 403: "comfy_auth_error", 402: "comfy_insufficient_credits",
                    404: "comfy_not_found", 429: "comfy_rate_limited", 400: "comfy_invalid_workflow",
                    422: "comfy_invalid_workflow"}.get(status, "comfy_upstream_error")
            if status == 422:
                try:
                    if error.response.json().get("error", {}).get("code") == "idempotency_key_reuse":
                        raise ComfyFailure("comfy_submission_unknown", uncertain=True)
                except (ValueError, TypeError, AttributeError):
                    pass
            raise ComfyFailure(code, uncertain=status >= 500 or 300 <= status < 400) from None
        except (httpx.HTTPError, ValueError):
            raise ComfyFailure("comfy_connection_or_response_error", uncertain=True) from None

    def preflight(self, graph):
        validate_graph(graph)
        info = self.request("GET", ORIGIN + "/api/object_info")
        issues = []
        for key, node in graph.items():
            definition = info.get(node.class_type)
            if not isinstance(definition, dict):
                issues.append(f"{node.class_type}: 사용 가능한 노드가 아닙니다.")
                continue
            groups = definition.get("input", {})
            try:
                required, inputs = expanded_inputs(groups, node.inputs)
            except (KeyError, TypeError, AttributeError, IndexError):
                raise ComfyFailure("comfy_invalid_node_schema") from None
            for missing in required - node.inputs.keys():
                issues.append(f"{node.class_type}.{missing}: 필수 입력이 누락되었습니다.")
            for name, value in node.inputs.items():
                spec = inputs.get(name)
                if not isinstance(spec, list) or not spec:
                    issues.append(f"{node.class_type}.{name}: 지원하지 않는 입력입니다.")
                    continue
                kind = spec[0]
                if isinstance(value, list):
                    upstream = graph[value[0]]
                    outputs = info.get(upstream.class_type, {}).get("output", [])
                    if value[1] >= len(outputs) or outputs[value[1]] != kind:
                        issues.append(f"{key}.{name}: 노드 출력 타입이 일치하지 않습니다.")
                elif isinstance(kind, list):
                    if value not in kind:
                        issues.append(f"{node.class_type}.{name}: 선택한 모델/옵션이 없습니다.")
                elif kind in {"INT", "FLOAT", "STRING", "BOOLEAN"}:
                    valid = {"INT": type(value) is int, "FLOAT": type(value) in {int, float},
                             "STRING": isinstance(value, str), "BOOLEAN": type(value) is bool}[kind]
                    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                    if not valid or (kind in {"INT", "FLOAT"} and (value < opts.get("min", value) or value > opts.get("max", value))):
                        issues.append(f"{node.class_type}.{name}: 입력 타입/범위가 일치하지 않습니다.")
                elif kind == "COMFY_DYNAMICCOMBO_V3":
                    if value not in [o["key"] for o in spec[1]["options"]]:
                        issues.append(f"{node.class_type}.{name}: 지원하지 않는 동적 옵션입니다.")
                else:
                    issues.append(f"{node.class_type}.{name}: 검증할 수 없는 입력 형식입니다.")
        return VideoPreflight(compatible=not issues, preset="wan22-5b-t2v-v1", node_count=len(graph), issues=issues)

    def submit(self, graph, key):
        return self.request("POST", ORIGIN + "/api/v2/jobs", data={"workflow": {k: n.model_dump() for k, n in graph.items()}}, key=key)

    def parse_job(self, body, expected_id=None):
        try:
            provider_id = body["id"]
            if not isinstance(provider_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", provider_id):
                raise ValueError("id")
            if expected_id and provider_id != expected_id:
                raise ValueError("mismatched id")
            if body["status"] not in STATES:
                raise ValueError("status")
            outputs = []
            for item in body["outputs"]:
                if item["type"] != "video":
                    continue
                url = urljoin(ORIGIN, item["url"])
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("output url")
                if not item["content_type"].startswith("video/"):
                    raise ValueError("video content type")
                outputs.append(VideoOutput(asset_id=item["id"], name=item["name"], content_type=item["content_type"],
                                           size_bytes=item["size_bytes"], url=url, url_expires_at=item["url_expires_at"]))
            progress = body.get("progress")
            value = progress["value"] if progress else None
            if value is not None and (type(value) not in {float, int} or not 0 <= value <= 1):
                raise ValueError("progress")
            return {"provider_job_id": provider_id, "status": body["status"], "progress": value,
                    "poll_url": job_link(body["urls"]["self"], provider_id),
                    "cancel_url": job_link(body["urls"]["cancel"], provider_id, "/cancel"),
                    "outputs": outputs, "expires_at": body.get("expires_at"),
                    "error_code": "comfy_execution_failed" if body["status"] == "failed" else None}
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ComfyFailure("comfy_invalid_response", uncertain=True) from None
