from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


API_URL = os.environ.get("MEMORYOS_API_URL", "http://127.0.0.1:8766").rstrip("/")
API_TOKEN = os.environ.get("MEMORYOS_API_TOKEN")


def is_loopback_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}


TOOLS = [
    ("project_context", "Get one authorized project's details.", {"project_id": {"type": "string"}}, ["project_id"]),
    ("memory_search", "Search memories within one authorized project.", {"project_id": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}}, ["project_id", "query"]),
    ("memory_get", "Get one memory record by ID after project access is checked.", {"memory_id": {"type": "string"}}, ["memory_id"]),
    ("memory_add", "Add a durable, explicitly provided memory.", {"project_id": {"type": "string"}, "category": {"type": "string"}, "source": {"type": "string"}, "content": {"type": "string"}, "source_event_id": {"type": "string"}, "confidence": {"type": "number"}, "verified": {"type": "boolean"}}, ["project_id", "category", "source", "content"]),
    ("memory_update", "Correct, verify, change importance, or archive a memory.", {"memory_id": {"type": "string"}, "content": {"type": "string"}, "confidence": {"type": "number"}, "verified": {"type": "boolean"}, "importance": {"type": "number"}, "archived": {"type": "boolean"}}, ["memory_id"]),
    ("memory_delete", "Remove a memory from retrieval and its Markdown vault projection.", {"memory_id": {"type": "string"}}, ["memory_id"]),
    ("task_create", "Create a project task.", {"project_id": {"type": "string"}, "title": {"type": "string"}, "details": {"type": "string"}}, ["project_id", "title"]),
    ("task_list", "List tasks from one authorized project.", {"project_id": {"type": "string"}, "status": {"type": "string"}}, ["project_id"]),
    ("task_update", "Update a task status.", {"task_id": {"type": "string"}, "status": {"type": "string"}}, ["task_id", "status"]),
    ("session_start", "Start or recover an agent session.", {"project_id": {"type": "string"}, "external_session_id": {"type": "string"}, "state": {"type": "object"}}, ["project_id"]),
    ("session_resume", "Reconstruct useful session context from saved project state.", {"session_id": {"type": "string"}}, ["session_id"]),
    ("event_record", "Record a durable, redacted activity event.", {"project_id": {"type": "string"}, "event_type": {"type": "string"}, "payload": {"type": "object"}, "event_id": {"type": "string"}, "session_id": {"type": "string"}}, ["project_id", "event_type"]),
    ("decision_record", "Record an architectural or implementation decision.", {"project_id": {"type": "string"}, "summary": {"type": "string"}, "rationale": {"type": "string"}}, ["project_id", "summary"]),
    ("error_record", "Record a sanitized error for later troubleshooting.", {"project_id": {"type": "string"}, "category": {"type": "string"}, "summary": {"type": "string"}, "details": {"type": "object"}}, ["project_id", "summary"]),
    ("session_checkpoint", "Save resumable project state.", {"project_id": {"type": "string"}, "session_id": {"type": "string"}, "summary": {"type": "string"}, "state": {"type": "object"}}, ["project_id", "summary"]),
    ("resource_acquire", "Acquire a time-limited advisory lease for a project resource.", {"project_id": {"type": "string"}, "resource_key": {"type": "string"}, "ttl_seconds": {"type": "integer"}}, ["project_id", "resource_key"]),
    ("resource_release", "Release an advisory lease held by this agent.", {"project_id": {"type": "string"}, "resource_key": {"type": "string"}}, ["project_id", "resource_key"]),
]


def api(method: str, path: str, payload=None):
    if not is_loopback_url(API_URL):
        raise RuntimeError("The Memory OS API address must use localhost.")
    if not API_TOKEN:
        raise RuntimeError("MEMORYOS_API_TOKEN is required to connect this MCP server.")
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(API_URL + path, data=body, method=method, headers={
        "Authorization": "Bearer " + API_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urlopen(request, timeout=10) as response:
            content = response.read()
            return json.loads(content) if content else {"ok": True}
    except HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", "replace")
        raise RuntimeError(f"Memory OS API returned {exc.code}: {detail}") from None
    except URLError:
        raise RuntimeError("Memory OS API is unavailable on localhost.") from None


def call_tool(name: str, args: dict):
    if name == "project_context":
        return api("GET", "/v1/projects/" + quote(args["project_id"], safe=""))
    if name == "memory_search":
        params = urlencode({"q": args["query"], "limit": args.get("limit", 10)})
        return api("GET", f"/v1/projects/{quote(args['project_id'], safe='')}/memories/search?{params}")
    if name == "memory_get":
        return api("GET", "/v1/memories/" + quote(args["memory_id"], safe=""))
    if name == "memory_add":
        return api("POST", "/v1/memories", {k: v for k, v in args.items() if k in {"project_id", "category", "source", "content", "source_event_id", "confidence", "verified"}})
    if name == "memory_update":
        return api("PATCH", "/v1/memories/" + quote(args["memory_id"], safe=""), {k: v for k, v in args.items() if k in {"content", "confidence", "verified", "importance", "archived"}})
    if name == "memory_delete":
        return api("DELETE", "/v1/memories/" + quote(args["memory_id"], safe=""))
    if name == "task_create":
        return api("POST", "/v1/tasks", {k: v for k, v in args.items() if k in {"project_id", "title", "details"}})
    if name == "task_list":
        query = "?" + urlencode({"status": args["status"]}) if args.get("status") else ""
        return api("GET", f"/v1/projects/{quote(args['project_id'], safe='')}/tasks{query}")
    if name == "task_update":
        return api("PATCH", "/v1/tasks/" + quote(args["task_id"], safe=""), {"status": args["status"]})
    if name == "session_start":
        return api("POST", "/v1/sessions", {k: v for k, v in args.items() if k in {"project_id", "external_session_id", "state"}})
    if name == "session_resume":
        return api("GET", "/v1/sessions/" + quote(args["session_id"], safe="") + "/resume")
    if name == "event_record":
        payload = {k: v for k, v in args.items() if k in {"project_id", "event_type", "payload", "event_id", "session_id"}}
        if "event_id" in payload:
            payload["event_id"] = payload.pop("event_id")
        return api("POST", "/v1/events", payload)
    if name == "decision_record":
        return api("POST", "/v1/decisions", {k: v for k, v in args.items() if k in {"project_id", "summary", "rationale"}})
    if name == "error_record":
        return api("POST", "/v1/errors", {k: v for k, v in args.items() if k in {"project_id", "category", "summary", "details"}})
    if name == "session_checkpoint":
        return api("POST", "/v1/checkpoints", {k: v for k, v in args.items() if k in {"project_id", "session_id", "summary", "state"}})
    if name == "resource_acquire":
        return api("POST", "/v1/leases", {k: v for k, v in args.items() if k in {"project_id", "resource_key", "ttl_seconds"}})
    if name == "resource_release":
        return api("DELETE", "/v1/leases", {k: v for k, v in args.items() if k in {"project_id", "resource_key"}})
    raise ValueError("Unknown MCP tool.")


def handle(message):
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "memory-os", "version": "0.2.0"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        tools = [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": required, "additionalProperties": False}} for n, d, p, required in TOOLS]
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}
    if method == "tools/call":
        params = message.get("params", {})
        try:
            result = call_tool(params["name"], params.get("arguments", {}))
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": {"isError": True, "content": [{"type": "text", "text": str(exc)}]}}
    if request_id is not None:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found."}}
    return None


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = handle(message)
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
