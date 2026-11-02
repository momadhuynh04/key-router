"""Translate CLI JSONL into text, and text into OpenAI-style SSE.

The final answer is markdown text: tool calls are rendered inline as blockquote lines,
so Open WebUI can display them without needing client-side `tool_calls` support.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

MAX_TOOL_PREVIEW = 200
MAX_RESULT_PREVIEW = 160
MAX_DENIALS = 5
MAX_STEP_INPUT = 160


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
    ask: Optional[dict] = None
    ask_tool_ids: Set[str] = field(default_factory=set)
    steps: List[dict] = field(default_factory=list)


def feed(event: Dict[str, Any], state: TurnState) -> List[str]:
    """Take one JSONL event and return the text pieces to append to the reply."""
    etype = event.get("type")
    sid = event.get("session_id")
    if sid:
        state.session_id = sid

    if etype == "assistant":
        return _feed_assistant(event, state)
    if etype == "user":
        return _feed_user(event, state)
    if etype == "result":
        return _feed_result(event, state)
    return []


def _ask_from_input(raw: Any) -> dict:
    """Normalize the `ask_user` tool input into {question, options, multi_select}."""
    data = raw if isinstance(raw, dict) else {}
    question = str(data.get("question") or data.get("prompt") or "").strip()
    options: List[dict] = []
    for item in data.get("options") or []:
        if isinstance(item, str):
            label, description = item, ""
        elif isinstance(item, dict):
            label = item.get("label") or item.get("value") or item.get("text") or ""
            description = item.get("description") or item.get("detail") or ""
        else:
            continue
        label = str(label).strip()
        if label:
            options.append({"label": label, "description": str(description).strip()})
    return {
        "question": question,
        "options": options,
        "multi_select": bool(data.get("multi_select")),
    }


def _render_ask(ask: dict) -> str:
    lines = [f"\n\n❓ **{ask.get('question') or 'The agent has a question.'}**\n"]
    options = ask.get("options") or []
    if options:
        for index, option in enumerate(options, 1):
            suffix = f" — {option['description']}" if option.get("description") else ""
            lines.append(f"{index}. **{option['label']}**{suffix}")
        lines.append("")
        hint = "one or more numbers/labels" if ask.get("multi_select") else "a number or label"
        lines.append(f"_Reply with {hint}, or type your own answer._")
    else:
        lines.append("_Reply to continue._")
    return "\n".join(lines) + "\n\n"


def _is_ask_tool(name: str) -> bool:
    return name.endswith("ask_user")


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
            if _is_ask_tool(name):
                tool_id = block.get("id")
                if tool_id:
                    state.ask_tool_ids.add(tool_id)
                state.ask = _ask_from_input(block.get("input"))
                state.emitted = True
                out.append(_render_ask(state.ask))
            else:
                state.steps.append({
                    "tool": name,
                    "input": _clip(block.get("input"), MAX_STEP_INPUT),
                })
                out.append(f"\n\n**▸ {name}** `{_clip(block.get('input'), MAX_TOOL_PREVIEW)}`\n\n")
    return out


def _feed_user(event: Dict[str, Any], state: TurnState) -> List[str]:
    out: List[str] = []
    message = event.get("message") or {}
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if block.get("tool_use_id") in state.ask_tool_ids:
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
