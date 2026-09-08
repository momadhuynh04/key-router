"""
OpenAI Chat Completions ingress — lets OpenAI-native CLI agents (Codex, etc.)
talk to the same provider pipeline that Claude Code uses.

Flow:
  OpenAI /v1/chat/completions request
    → AnthropicRequest (internal format)
    → ProviderRouter → provider adapter
    → AnthropicResponse / Anthropic SSE events
    → OpenAI Chat Completions response / SSE chunks
"""

import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from models.anthropic import AnthropicRequest, AnthropicResponse, Message
from models.events import SSEEvent
from models.openai_compat import OpenAIRequest


# ---------------------------------------------------------------------------
# Request translation: OpenAI Chat Completions → Anthropic Messages
# ---------------------------------------------------------------------------

def _content_to_text(content: Any) -> str:
    """Flatten OpenAI message content (string or content-part array) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return "\n".join(parts)
    return str(content)


def _safe_json_loads(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _strip_toolu(tool_id: str) -> str:
    """Remove the toolu_ prefix added on egress so OpenAI clients see their own ids."""
    return tool_id[6:] if tool_id.startswith("toolu_") else tool_id


def _openai_tools_to_anthropic(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    result = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function", {})
        if not fn.get("name"):
            continue
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}
        })
    return result if result else None


def _openai_tool_choice_to_anthropic(tool_choice: Any) -> Optional[Dict[str, Any]]:
    if isinstance(tool_choice, str):
        if tool_choice == "required":
            return {"type": "any"}
        if tool_choice == "auto":
            return {"type": "auto"}
        return None  # "none" — omit entirely
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        return {"type": "tool", "name": tool_choice.get("function", {}).get("name", "")}
    return None


def openai_chat_to_anthropic(req: OpenAIRequest) -> AnthropicRequest:
    system_parts: List[str] = []
    messages: List[Message] = []

    for msg in req.messages:
        role = msg.role
        text = _content_to_text(msg.content)

        if role in ("system", "developer"):
            system_parts.append(text)

        elif role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in getattr(msg, "tool_calls", None) or []:
                fn = tc.get("function", {})
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or f"toolu_{uuid.uuid4().hex}",
                    "name": fn.get("name", ""),
                    "input": _safe_json_loads(fn.get("arguments"))
                })
            messages.append(Message(role="assistant", content=blocks or [{"type": "text", "text": ""}]))

        elif role == "tool":
            messages.append(Message(role="user", content=[{
                "type": "tool_result",
                "tool_use_id": getattr(msg, "tool_call_id", "") or "",
                "content": text
            }]))

        else:  # user
            messages.append(Message(role="user", content=text))

    max_tokens = req.max_tokens or getattr(req, "max_completion_tokens", None)

    stop = req.stop
    if isinstance(stop, str):
        stop = [stop]

    anthropic_req = AnthropicRequest(
        model=req.model,
        messages=messages,
        system="\n".join(system_parts) if system_parts else None,
        max_tokens=max_tokens,
        stream=bool(req.stream),
        temperature=req.temperature,
        top_p=req.top_p,
        stop_sequences=stop,
        tools=_openai_tools_to_anthropic(req.tools),
        tool_choice=_openai_tool_choice_to_anthropic(req.tool_choice),
    )

    # Preserve reasoning-effort hints from Codex so providers receive them verbatim.
    effort = getattr(req, "reasoning_effort", None)
    if effort:
        anthropic_req.reasoning_effort = effort

    return anthropic_req


# ---------------------------------------------------------------------------
# Response translation: Anthropic Messages → OpenAI Chat Completions
# ---------------------------------------------------------------------------

def _message_content_to_openai(resp: AnthropicResponse) -> Dict[str, Any]:
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for block in resp.content:
        block_type = block.get("type")
        if block_type == "text" and block.get("text"):
            text_parts.append(block["text"])
        elif block_type == "tool_use":
            tool_calls.append({
                "id": _strip_toolu(block.get("id", "")),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}))
                }
            })

    message: Dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else None,
        "refusal": None
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def anthropic_response_to_openai_chat(resp: AnthropicResponse, requested_model: str) -> Dict[str, Any]:
    finish_reason = "tool_calls" if resp.stop_reason == "tool_use" else "stop"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [{
            "index": 0,
            "message": _message_content_to_openai(resp),
            "finish_reason": finish_reason
        }],
        "usage": {
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens
        }
    }


# ---------------------------------------------------------------------------
# Streaming translation: Anthropic SSE events → OpenAI chunk lines
# ---------------------------------------------------------------------------

def _sse_data(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def openai_error_line(message: str) -> str:
    return _sse_data({"error": {"message": message, "type": "api_error", "code": None}})


def _chunk(chat_id: str, model: str, delta: Dict[str, Any],
           finish_reason: Optional[str] = None, usage: Optional[Dict[str, Any]] = None) -> str:
    payload: Dict[str, Any] = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    }
    if usage is not None:
        payload["usage"] = usage
    return _sse_data(payload)


async def anthropic_events_to_openai_stream(
    events: AsyncIterator[SSEEvent],
    requested_model: str,
) -> AsyncIterator[str]:
    """Consume Anthropic SSEEvents from a provider and emit OpenAI SSE chunk lines."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    prompt_tokens = 0
    completion_tokens = 0
    finish_reason: Optional[str] = None
    tool_index = -1

    yield _chunk(chat_id, requested_model, {"role": "assistant", "content": ""})

    async for event in events:
        data = event.data
        event_type = data.get("type")

        if event_type == "message_start":
            usage = data.get("message", {}).get("usage", {})
            prompt_tokens = usage.get("input_tokens", 0)

        elif event_type == "content_block_start":
            block = data.get("content_block", {})
            if block.get("type") == "tool_use":
                tool_index += 1
                yield _chunk(chat_id, requested_model, {
                    "tool_calls": [{
                        "index": tool_index,
                        "id": _strip_toolu(block.get("id", "")),
                        "type": "function",
                        "function": {"name": block.get("name", ""), "arguments": ""}
                    }]
                })

        elif event_type == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                yield _chunk(chat_id, requested_model, {"content": delta.get("text", "")})
            elif delta.get("type") == "input_json_delta":
                yield _chunk(chat_id, requested_model, {
                    "tool_calls": [{
                        "index": tool_index,
                        "function": {"arguments": delta.get("partial_json", "")}
                    }]
                })

        elif event_type == "message_delta":
            stop_reason = data.get("delta", {}).get("stop_reason")
            if stop_reason:
                finish_reason = "tool_calls" if stop_reason == "tool_use" else "stop"
            completion_tokens = data.get("usage", {}).get("output_tokens", completion_tokens)

        elif event_type == "error":
            yield openai_error_line(data.get("error", {}).get("message", "Unknown provider error"))
            yield "data: [DONE]\n\n"
            return

        elif event_type == "message_stop":
            break

    total = prompt_tokens + completion_tokens
    yield _chunk(chat_id, requested_model, {}, finish_reason=finish_reason,
                 usage={"prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total})
    yield "data: [DONE]\n\n"
