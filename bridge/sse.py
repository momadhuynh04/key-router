"""Translate CLI JSONL into text, and text into OpenAI-style SSE.

The final answer is markdown text: tool calls are rendered inline as blockquote lines,
so Open WebUI can display them without needing client-side `tool_calls` support.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MAX_TOOL_PREVIEW = 200
MAX_RESULT_PREVIEW = 160
MAX_DENIALS = 5


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(str(text).split())
    return text[:limit] + "…" if len(text) > limit else text


@dataclass
class TurnState:
    session_id: Optional[str] = None
    emitted: bool = False
    final_text: str = ""
    denials: List[dict] = field(default_factory=list)
    is_error: bool = False
    error_text: str = ""
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None


def feed(event: Dict[str, Any], state: TurnState) -> List[str]:
    """Take one JSONL event and return the text pieces to append to the reply."""
    etype = event.get("type")
    sid = event.get("session_id")
    if sid:
        state.session_id = sid

    if etype == "assistant":
        return _feed_assistant(event, state)
    if etype == "user":
        return _feed_user(event)
    if etype == "result":
        return _feed_result(event, state)
    return []


def _feed_assistant(event: Dict[str, Any], state: TurnState) -> List[str]:
    out: List[str] = []
    message = event.get("message") or {}
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text") or ""
            if text:
                state.emitted = True
                out.append(text)
        elif btype == "tool_use":
            name = block.get("name") or "tool"
            out.append(f"\n\n**▸ {name}** `{_clip(block.get('input'), MAX_TOOL_PREVIEW)}`\n\n")
    return out


def _feed_user(event: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    message = event.get("message") or {}
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        content = block.get("content")
        if isinstance(content, list):
            content = "\n".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        preview = _clip(content, MAX_RESULT_PREVIEW)
        if preview:
            out.append(f"> ↩ {preview}\n\n")
    return out


def _feed_result(event: Dict[str, Any], state: TurnState) -> List[str]:
    out: List[str] = []
    state.is_error = bool(event.get("is_error"))
    state.denials = event.get("permission_denials") or []
    state.cost_usd = event.get("total_cost_usd")
    state.duration_ms = event.get("duration_ms")
    state.num_turns = event.get("num_turns")
    state.final_text = event.get("result") or ""
    state.error_text = event.get("error") or ""

    if state.is_error:
        msg = state.error_text or state.final_text or "the agent returned an error"
        state.emitted = True
        out.append(f"⚠️ **Error:** {_clip(msg, 300)}")
    elif not state.emitted and state.final_text:
        state.emitted = True
        out.append(state.final_text)

    footer = _footer(state)
    if footer:
        out.append(footer)
    return out


def _footer(state: TurnState) -> str:
    lines: List[str] = []
    if state.denials:
        items = []
        for d in state.denials[:MAX_DENIALS]:
            if not isinstance(d, dict):
                continue
            name = d.get("tool_name") or "?"
            items.append(f"`{name}` {_clip(d.get('tool_input') or '', 80)}")
        more = len(state.denials) - MAX_DENIALS
        suffix = f" (+{more})" if more > 0 else ""
        lines.append("⛔ **Blocked by permissions:** " + "; ".join(items) + suffix)

    bits = []
    if state.cost_usd:
        bits.append(f"${state.cost_usd:.4f}")
    if state.duration_ms:
        bits.append(f"{state.duration_ms / 1000:.1f}s")
    if state.num_turns:
        bits.append(f"{state.num_turns} turn")
    if bits:
        lines.append(" · ".join(bits))

    if not lines:
        return ""
    return "\n\n---\n" + "\n\n".join(lines) + "\n"


# ---------------------------------------------------------------- SSE (OpenAI)

def chunk(cid: str, model: str, delta: Dict[str, Any], finish: Optional[str] = None) -> str:
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def role_chunk(cid: str, model: str) -> str:
    return chunk(cid, model, {"role": "assistant", "content": ""})


def text_chunk(cid: str, model: str, text: str) -> str:
    return chunk(cid, model, {"content": text})


def finish_chunk(cid: str, model: str) -> str:
    return chunk(cid, model, {}, "stop")


DONE_LINE = "data: [DONE]\n\n"


def completion(cid: str, model: str, text: str) -> Dict[str, Any]:
    return {
        "id": cid,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
