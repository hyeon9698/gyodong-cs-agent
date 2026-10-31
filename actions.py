"""운영진 액션 API: openapi.json 하나로 툴 정의와 호출을 모두 자동 생성한다."""
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger("actions")

SPEC = json.loads(Path(__file__).with_name("openapi.json").read_text(encoding="utf-8"))
BASE_URL = SPEC["servers"][0]["url"]
SCHEMAS = SPEC["components"]["schemas"]
TOKEN = os.getenv("ACTION_TOKEN", "")
_http = httpx.Client(timeout=15)


def _resolve(schema: dict, depth: int = 0) -> dict:
    """$ref 를 풀고 LLM 툴 스키마에 필요한 키만 남긴다."""
    if "$ref" in schema:
        schema = SCHEMAS[schema["$ref"].split("/")[-1]]
    keep = {k: v for k, v in schema.items() if k in ("type", "description", "enum", "format")}
    if depth < 4 and "properties" in schema:
        keep["properties"] = {k: _resolve(v, depth + 1) for k, v in schema["properties"].items()}
        if schema.get("required"):
            keep["required"] = schema["required"]
    if depth < 4 and "items" in schema:
        keep["items"] = _resolve(schema["items"], depth + 1)
    return keep


def _build():
    tools, routes = [], {}
    for path, ops in SPEC["paths"].items():
        for method, op in ops.items():
            oid = op.get("operationId", "")
            if not oid.startswith("API-"):  # 데이터 초기화 API 등은 에이전트에게 주지 않는다
                continue
            name = oid.replace("-", "_")
            params = [p for p in op.get("parameters", []) if p["in"] in ("path", "query")]
            props = {p["name"]: {"type": p["schema"].get("type", "string")} for p in params}
            required = [p["name"] for p in params if p.get("required")]
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                props["body"] = _resolve(body)
                required.append("body")
            desc = f"{oid} {op['summary']} ({method.upper()} {path.split('?')[0]}). {op.get('description', '')}".strip()
            tools.append({"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props, "required": required}}})
            routes[name] = (method.upper(), path.split("?")[0], params)
    return tools, routes


TOOLS, ROUTES = _build()


def call(name: str, args: dict, tc_id: str | None) -> str:
    if name not in ROUTES:
        return f"error: unknown tool {name}"
    method, path, params = ROUTES[name]
    query = {}
    for p in params:
        value = args.get(p["name"])
        if value is None:
            continue
        if p["in"] == "path":
            path = path.replace("{%s}" % p["name"], quote(str(value), safe=""))
        else:
            query[p["name"]] = value
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if tc_id:
        headers["X-Aitop100-Test-Case-Id"] = tc_id
    body = args.get("body") if method != "GET" else None
    for attempt in range(3):
        try:
            r = _http.request(method, BASE_URL + path, params=query, json=body, headers=headers)
        except httpx.HTTPError as e:
            log.warning("%s network error %s", name, e)
            time.sleep(0.5)
            continue
        if r.status_code == 429 and "누적" not in r.text:
            time.sleep(min(int(r.headers.get("Retry-After", "1") or 1), 5))
            continue
        if r.status_code in (500, 503):
            time.sleep(0.5)
            continue
        break
    else:
        return "error: 액션 서버에 연결하지 못함"
    text = r.text[:4000] if r.text else "(본문 없음)"
    log.info("%s %s %s -> %s", name, method, path, r.status_code)
    return f"HTTP {r.status_code}: {text}"
