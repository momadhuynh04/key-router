#!/usr/bin/env python3
"""Minimal MCP stdio server that gives the agent one tool: `ask_user`.

Claude Code's own AskUserQuestion tool is unavailable under `-p`, so the bridge
registers this server with `--mcp-config`.

The tool is intentionally non-blocking: it returns immediately and the bridge
renders the question in the chat. The agent is told to end its turn, and the
next user message is treated as the answer. Keeping the CLI process alive across
two HTTP requests would be the alternative, but Open WebUI is turn-based.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

PROTOCOL_VERSION = "2024-11-05"

TOOL: Dict[str, Any] = {
    "name": "ask_user",
    "description": (
        "Ask the user a question, optionally with choices. Use this instead of guessing "
        "or asking in plain text when you are blocked on a decision.\n"
        "After calling it, STOP immediately and end your turn: the question is rendered "
        "in the chat and the user's next message is the answer. Never invent an answer."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to show the user.",
            },
            "options": {
                "type": "array",
                "description": "Optional choices (2-6). The user may still reply with free text.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Short choice text."},
                        "description": {
                            "type": "string",
                            "description": "Optional one-line explanation of the choice.",
                        },
                    },
                    "required": ["label"],
                },
            },
            "multi_select": {
                "type": "boolean",
                "description": "True when the user may pick more than one option.",
                "default": False,
            },
        },
        "required": ["question"],
    },
}

RESULT_TEXT = (
    "Question shown to the user in the chat. End your turn now — do not continue, "
    "do not assume an answer. The user's next message is the answer."
)


def _reply(request_id: Any, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> None:
    message: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result or {}
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(request: Dict[str, Any]) -> None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        _reply(request_id, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "bridge-ask", "version": "1.0.0"},
        })
        return

    if method == "notifications/initialized":
        return

    if method == "ping":
        _reply(request_id, {})
        return

    if method == "tools/list":
        _reply(request_id, {"tools": [TOOL]})
        return

    if method == "tools/call":
        if params.get("name") != TOOL["name"]:
            _reply(request_id, {
                "content": [{"type": "text", "text": f"unknown tool: {params.get('name')}"}],
                "isError": True,
            })
            return
        _reply(request_id, {
            "content": [{"type": "text", "text": RESULT_TEXT}],
            "isError": False,
        })
        return

    if request_id is not None:
        _reply(request_id, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(request, dict):
            _handle(request)


if __name__ == "__main__":
    main()
