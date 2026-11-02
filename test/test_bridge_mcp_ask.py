import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bridge" / "mcp_ask.py"

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _run(requests):
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_initialize_and_list_tool():
    out = _run([INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])

    assert out[0]["result"]["serverInfo"]["name"] == "bridge-ask"
    tools = out[1]["result"]["tools"]
    assert tools[0]["name"] == "ask_user"
    assert "question" in tools[0]["inputSchema"]["properties"]
    assert "options" in tools[0]["inputSchema"]["properties"]


def test_notifications_produce_no_response():
    out = _run([INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    assert len(out) == 2


def test_call_returns_stop_instruction():
    out = _run([
        INIT,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "ask_user", "arguments": {"question": "Chọn DB?"}}},
    ])
    result = out[1]["result"]
    assert result["isError"] is False
    assert "End your turn" in result["content"][0]["text"]


def test_unknown_tool_is_an_error():
    out = _run([
        INIT,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}},
    ])
    assert out[1]["result"]["isError"] is True


def test_unknown_method_returns_jsonrpc_error():
    out = _run([INIT, {"jsonrpc": "2.0", "id": 9, "method": "does/not/exist"}])
    assert out[1]["error"]["code"] == -32601
