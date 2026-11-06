import importlib.util
from pathlib import Path


SERVER_FILE = Path(__file__).parents[2] / "mcp-server" / "server.py"
spec = importlib.util.spec_from_file_location("memoryos_mcp_server", SERVER_FILE)
mcp_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp_server)


def test_mcp_handshake_and_tool_catalog():
    initialized = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert initialized["result"]["serverInfo"]["name"] == "memory-os"
    response = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert {"memory_search", "memory_get", "memory_add", "memory_update", "memory_delete", "task_create", "event_record", "session_resume", "resource_acquire", "resource_release"} <= names


def test_mcp_call_reports_api_errors_without_hiding_them(monkeypatch):
    def failure(_name, _args):
        raise RuntimeError("Memory OS API is unavailable on localhost.")

    monkeypatch.setattr(mcp_server, "call_tool", failure)
    result = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "project_context", "arguments": {"project_id": "id"}}})
    assert result["result"]["isError"] is True
    assert "unavailable" in result["result"]["content"][0]["text"]


def test_api_url_must_be_loopback():
    assert mcp_server.is_loopback_url("http://127.0.0.1:8766")
    assert mcp_server.is_loopback_url("http://[::1]:8766")
    assert not mcp_server.is_loopback_url("https://example.com")
