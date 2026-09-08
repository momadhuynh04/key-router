"""
OpenAI Responses API ingress (/v1/responses) — native protocol of Codex CLI.

Codex (>= 0.149) removed support for wire_api = "chat", so key-router speaks
the Responses API directly:

  Responses request
    → AnthropicRequest (internal format)
    → ProviderRouter → provider adapter
    → AnthropicResponse / Anthropic SSE events
    → Responses API response object / typed SSE event stream
"""

import json
import re
from config.logging import log_warn
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

from models.anthropic import AnthropicRequest, AnthropicResponse, Message
from models.events import SSEEvent
from models.openai_compat import ResponsesRequest

_TEXT_PART_TYPES = {"input_text", "output_text", "summary_text", "text"}

# Matches weak models that imitate tool syntax as plain text, e.g.:
#   commentary: I'll explore...
#   functions.exec:
#   {"cmd": "ls -la", "cwd": "/tmp"}
_TEXT_TOOL_CALL_RE = re.compile(
    r"(?:^|\n)\s*(?:commentary:\s*)?(?:functions\.)?([A-Za-z_][\w.]*)\s*:\s*",
)

# Llama / Nemotron style, often mangled by weak models. Observed variants:
#   <tool_call> FUNCTION exec_command <parameter name="cmd">v</parameter> </tool_call>
#   <tool_call> <function=functions.exec> <parameter=cmd>v </parameter> </function> </tool_call>
#   <tool_call> FUNCTION exec_command <parameter name="cmd> v </parameter> ...  (broken attr!)
_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE
)
_FUNCTION_NAME_PATTERNS = [
    re.compile(r"FUNCTION\s+([A-Za-z_][\w.]*)", re.IGNORECASE),
    re.compile(r"<function\s*=\s*\"?([A-Za-z_][\w.]*)", re.IGNORECASE),
]
_PARAM_PATTERNS = [
    re.compile(
        r'<parameter[^>]*?name\s*=\s*"?([\w.-]+)"?[^>]*>(.*?)</parameter>',
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(
        r'<parameter\s*=\s*"?([\w.-]+)"?[^>]*>(.*?)</parameter>',
        re.DOTALL | re.IGNORECASE,
    ),
]

# Llama 3 / Qwen style: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
_JSON_TOOL_CALL_RE = re.compile(
    r"<(?:tool_call|function_call)>\s*(\{.*?\})\s*</(?:tool_call|function_call)>",
    re.DOTALL,
)


def _coerce_value(value: str, prop_schema: Dict[str, Any]) -> Any:
    """Coerce an XML parameter string to the type declared by the tool schema."""
    if not isinstance(value, str):
        return value
    ptype = (prop_schema or {}).get("type")
    stripped = value.strip()
    if ptype == "integer" and re.fullmatch(r"-?\d+", stripped):
        return int(stripped)
    if ptype == "number" and re.fullmatch(r"-?\d+(\.\d+)?", stripped):
        return float(stripped)
    if ptype == "boolean" and stripped.lower() in ("true", "false"):
        return stripped.lower() == "true"
    if ptype in ("object", "array"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    # XML-imitated params carry padding whitespace — strip it
    return value.strip()


def _resolve_tool_alias(name: str, tool_names: Set[str]) -> Optional[str]:
    """Map an imitated tool name to the real tool (e.g. 'exec' → 'exec_command')."""
    if name in tool_names:
        return name
    candidates = [t for t in tool_names if t.startswith(name)]
    return candidates[0] if len(candidates) == 1 else None


def _extract_text_tool_calls(
    text: str, tool_schemas: Dict[str, Dict[str, Any]]
) -> List[Tuple[str, Dict[str, Any]]]:
    """Find tool calls that a weak model wrote as plain text instead of natively.

    Handles the common imitation formats:
      - `tool_name: {json}` (with optional functions. prefix / commentary line)
      - `<tool_call> FUNCTION name <parameter name="k">v</parameter> ... </tool_call>`
      - `<tool_call>{"name": ..., "arguments": {...}}</tool_call>`

    Only names resolvable against the request's tool list are accepted and the
    payload must parse — otherwise normal prose mentioning tools is untouched.
    """
    calls: List[Tuple[str, Dict[str, Any]]] = []
    decoder = json.JSONDecoder()

    # 1. name: {json}
    for match in _TEXT_TOOL_CALL_RE.finditer(text):
        raw_name = match.group(1).split(".")[-1]
        resolved = _resolve_tool_alias(raw_name, set(tool_schemas))
        if not resolved:
            continue
        brace = text.find("{", match.end())
        if brace == -1 or text[match.end():brace].strip():
            continue
        try:
            payload, _ = decoder.raw_decode(text, brace)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            calls.append((resolved, payload))

    # 2. <tool_call> ... </tool_call> blocks — loose match tolerating every
    #    observed mangling: multi-line tags, broken attributes (name="cmd>),
    #    attribute-style tags (<function=x>, <parameter=k>).
    for block in _TOOL_CALL_BLOCK_RE.finditer(text):
        inner = block.group(1)

        resolved = None
        for name_pattern in _FUNCTION_NAME_PATTERNS:
            name_match = name_pattern.search(inner)
            if name_match:
                resolved = _resolve_tool_alias(
                    name_match.group(1).split(".")[-1], set(tool_schemas)
                )
                if resolved:
                    break
        if not resolved:
            continue

        props = (tool_schemas.get(resolved) or {}).get("properties") or {}
        params: Dict[str, Any] = {}
        for param_pattern in _PARAM_PATTERNS:
            found = param_pattern.findall(inner)
            if found:
                for pname, pvalue in found:
                    params[pname] = _coerce_value(pvalue, props.get(pname, {}))
                break
        if params:
            calls.append((resolved, params))

    # 3. <tool_call>{"name": ..., "arguments": {...}}</tool_call>
    for match in _JSON_TOOL_CALL_RE.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        raw_name = payload.get("name", "")
        resolved = _resolve_tool_alias(str(raw_name).split(".")[-1], set(tool_schemas))
        if not resolved:
            continue
        args = payload.get("arguments", payload.get("parameters", {}))
        if isinstance(args, dict):
            calls.append((resolved, args))

    return calls


def _parts_to_text(content: Any) -> str:
    """Flatten Responses content (string or list of typed parts) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in _TEXT_PART_TYPES:
                parts.append(part.get("text", ""))
        return "\n".join(parts)
    return str(content)


def _strip_toolu(tool_id: str) -> str:
    return tool_id[6:] if tool_id.startswith("toolu_") else tool_id


def _new_item_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


# ---------------------------------------------------------------------------
# Request translation: Responses → Anthropic Messages
# ---------------------------------------------------------------------------

def _unwrap_additional_tools(items: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Flatten Codex additional_tools / namespace / custom grouping into flat function-like dicts."""
    flat: List[Dict[str, Any]] = []
    for t in items or []:
        if not isinstance(t, dict):
            continue
        ttype = t.get("type")
        if ttype == "function" and t.get("name"):
            flat.append(t)
        elif ttype == "namespace":
            flat.extend(_unwrap_additional_tools(t.get("tools", [])))
        elif ttype == "custom":
            name = t.get("name", "")
            if name == "exec":
                name = "exec_command"
            if not name:
                continue
            flat.append({
                "type": "function",
                "name": name,
                "description": t.get("description", ""),
                "parameters": t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}},
            })
        elif ttype == "additional_tools":
            flat.extend(_unwrap_additional_tools(t.get("tools", [])))
    return flat


def _responses_tools_to_anthropic(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    flat = _unwrap_additional_tools(tools)
    result = []
    for tool in flat:
        if not tool.get("name"):
            continue
        result.append({
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters") or {"type": "object", "properties": {}}
        })
    return result if result else None


def _responses_tool_choice_to_anthropic(tool_choice: Any) -> Optional[Dict[str, Any]]:
    if tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "required":
        return {"type": "any"}
    if tool_choice == "none":
        return None
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        return {"type": "tool", "name": tool_choice.get("name", "")}
    return None


def responses_to_anthropic(req: ResponsesRequest) -> AnthropicRequest:
    system_parts: List[str] = []
    if req.instructions:
        system_parts.append(req.instructions)

    messages: List[Message] = []

    def add_system(text: str):
        if text:
            system_parts.append(text)

    def add_user(text: str):
        if text:
            messages.append(Message(role="user", content=text))

    def add_assistant(text: str):
        blocks = [{"type": "text", "text": text}] if text else [{"type": "text", "text": ""}]
        messages.append(Message(role="assistant", content=blocks))

    pending_tools: List[Dict[str, Any]] = []
    extra_tools = getattr(req, "__pydantic_extra__", None) or {}
    if getattr(req, "additional_tools", None):
        pending_tools.extend(req.additional_tools or [])
    if extra_tools.get("additional_tools"):
        pending_tools.extend(extra_tools["additional_tools"] or [])

    pending_thinking: List[str] = []

    items = req.input
    if isinstance(items, str):
        add_user(items)
    else:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "message")

            if item_type == "message":
                role = item.get("role", "user")
                text = _parts_to_text(item.get("content"))
                if role in ("system", "developer"):
                    add_system(text)
                elif role == "assistant":
                    add_assistant(text)
                else:
                    add_user(text)

            elif item_type == "function_call":
                thinking_blocks = []
                if pending_thinking:
                    for t in pending_thinking:
                        thinking_blocks.append({"type": "thinking", "thinking": t})
                    pending_thinking.clear()
                blocks = []
                blocks.extend(thinking_blocks)
                blocks.append({
                    "type": "tool_use",
                    "id": item.get("call_id") or item.get("id") or _new_item_id("fc"),
                    "name": item.get("name", ""),
                    "input": _safe_arguments(item.get("arguments"))
                })
                messages.append(Message(role="assistant", content=blocks))

            elif item_type == "function_call_output":
                output = item.get("output", "")
                if not isinstance(output, str):
                    output = json.dumps(output)
                messages.append(Message(role="user", content=[{
                    "type": "tool_result",
                    "tool_use_id": item.get("call_id", ""),
                    "content": output
                }]))

            elif item_type == "reasoning":
                summary = item.get("summary") or item.get("text") or item.get("content") or ""
                text = ""
                if isinstance(summary, list) and summary and isinstance(summary[0], dict):
                    text = summary[0].get("text","") or summary[0].get("summary","")
                    if not text and "content" in item:
                        c = item.get("content")
                        text = c[0].get("text","") if isinstance(c, list) and c and isinstance(c[0], dict) else str(c) if isinstance(c, str) else ""
                else:
                    text = str(summary) if summary else ""
                if not text:
                    c2 = item.get("content", "")
                    if isinstance(c2, list) and c2:
                        text = c2[0].get("text","") if isinstance(c2[0], dict) else str(c2[0])
                    elif isinstance(c2, str):
                        text = c2
                if text and text.strip():
                    pending_thinking.append(text)
            elif item_type == "additional_tools":
                pending_tools.extend(item.get("tools", []) or [])
            else:
                if pending_thinking:
                    for t in pending_thinking:
                        messages.append(Message(role="assistant", content=[{"type": "thinking", "thinking": t}]))
                    pending_thinking.clear()

            # other types → flush orphan thinking above, ignored

    if pending_thinking:
        for t in pending_thinking:
            messages.append(Message(role="assistant", content=[{"type": "thinking", "thinking": t}]))
        pending_thinking.clear()

    all_tools: List[Dict[str, Any]] = []
    all_tools.extend(req.tools or [])
    all_tools.extend(pending_tools)

    max_tokens = req.max_output_tokens

    effort = (req.reasoning or {}).get("effort")

    anthropic_req = AnthropicRequest(
        model=req.model,
        messages=messages,
        system="\n\n".join(system_parts) if system_parts else None,
        max_tokens=max_tokens,
        stream=bool(req.stream),
        tools=_responses_tools_to_anthropic(all_tools if all_tools else None),
        tool_choice=_responses_tool_choice_to_anthropic(req.tool_choice),
    )
    if effort:
        anthropic_req.reasoning_effort = effort
    return anthropic_req


def _safe_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


# ---------------------------------------------------------------------------
# Response object translation: Anthropic → Responses
# ---------------------------------------------------------------------------

def _content_blocks_to_output_items(content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    text_buffer: List[str] = []

    def flush_text():
        if text_buffer:
            items.append({
                "type": "message",
                "id": _new_item_id("msg"),
                "status": "completed",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": "\n".join(text_buffer),
                    "annotations": []
                }],
            })
            text_buffer.clear()

    for block in content:
        block_type = block.get("type")
        if block_type == "thinking":
            continue
        if block_type == "text" and block.get("text"):
            text_buffer.append(block["text"])
        elif block_type == "tool_use":
            flush_text()
            items.append({
                "type": "function_call",
                "id": _new_item_id("fc"),
                "call_id": _strip_toolu(block.get("id", "")),
                "name": block.get("name", ""),
                "arguments": json.dumps(block.get("input", {})),
                "status": "completed"
            })
    flush_text()
    return items


def anthropic_response_to_responses_object(
    resp: AnthropicResponse,
    requested_model: str,
    tool_schemas: Optional[Dict[str, Dict[str, Any]]] = None,
    response_id: Optional[str] = None,
) -> Dict[str, Any]:
    input_tokens = resp.usage.input_tokens
    output_tokens = resp.usage.output_tokens

    output = _content_blocks_to_output_items(resp.content)

    if tool_schemas and not any(i["type"] == "function_call" for i in output):
        full_text = "\n".join(
            part["text"]
            for item in output if item["type"] == "message"
            for part in item["content"]
        )
        for name, payload in _extract_text_tool_calls(full_text, tool_schemas):
            output.append({
                "type": "function_call",
                "id": _new_item_id("fc"),
                "call_id": f"call_{uuid.uuid4().hex[:24]}",
                "name": name,
                "arguments": json.dumps(payload),
                "status": "completed"
            })

    truncated = resp.stop_reason in ("max_tokens", "length")
    empty_no_tool = len(output) == 1 and output[0]["type"] == "message" and not output[0]["content"][0]["text"].strip() and tool_schemas
    if truncated or empty_no_tool:
        if empty_no_tool:
            log_warn(f"[⚠️] Empty output with tools offered — marking incomplete (likely truncated)")

    response = {
        "id": response_id or _new_item_id("resp"),
        "object": "response",
        "created_at": int(time.time()),
        "status": "incomplete" if (truncated or empty_no_tool) else "completed",
        "model": requested_model,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    if truncated or empty_no_tool:
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    return response


# ---------------------------------------------------------------------------
# Streaming translation: Anthropic SSE events → Responses typed SSE events
# ---------------------------------------------------------------------------

def _sse(event_name: str, payload: Dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


class _ResponsesStreamState:
    def __init__(self, requested_model: str, tool_schemas: Optional[Dict[str, Dict[str, Any]]] = None):
        self.model = requested_model
        self.tool_schemas = tool_schemas or {}
        self.response_id = _new_item_id("resp")
        self.created_at = int(time.time())
        self.output_index = -1
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.collected_items: List[Dict[str, Any]] = []
        self.saw_native_tool_call = False
        self.all_text_parts: List[str] = []
        # active item tracking
        self.item_type: Optional[str] = None
        self.item_id: Optional[str] = None
        self.item_header: Dict[str, Any] = {}
        self.text_buffer = ""
        self.args_buffer = ""

    def next_output_index(self) -> int:
        self.output_index += 1
        return self.output_index

    def base_response(self, status: str = "in_progress") -> Dict[str, Any]:
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "model": self.model,
            "output": [],
            "usage": None,
        }

    def final_response(self, status: str = "completed") -> Dict[str, Any]:
        total = self.prompt_tokens + self.completion_tokens
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "model": self.model,
            "output": self.collected_items,
            "usage": {
                "input_tokens": self.prompt_tokens,
                "output_tokens": self.completion_tokens,
                "total_tokens": total,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }


async def anthropic_events_to_responses_stream(
    events: AsyncIterator[SSEEvent],
    requested_model: str,
    tool_schemas: Optional[Dict[str, Dict[str, Any]]] = None,
) -> AsyncIterator[str]:
    state = _ResponsesStreamState(requested_model, tool_schemas)

    yield _sse("response.created", {
        "type": "response.created",
        "sequence_number": 0,
        "response": state.base_response(),
    })

    seq = 1

    async for event in events:
        data = event.data
        event_type = data.get("type")

        if event_type == "message_start":
            usage = data.get("message", {}).get("usage", {})
            state.prompt_tokens = usage.get("input_tokens", 0)

        elif event_type == "content_block_start":
            block = data.get("content_block", {})
            block_type = block.get("type")
            if block_type == "thinking":
                continue
            index = state.next_output_index()

            if block_type == "text":
                state.item_type = "message"
                state.item_id = _new_item_id("msg")
                state.text_buffer = ""
                header = {
                    "type": "message", "id": state.item_id,
                    "role": "assistant", "status": "in_progress", "content": []
                }
            elif block_type == "tool_use":
                state.item_type = "function_call"
                state.item_id = _new_item_id("fc")
                state.args_buffer = ""
                state.saw_native_tool_call = True
                header = {
                    "type": "function_call", "id": state.item_id,
                    "call_id": _strip_toolu(block.get("id", "")),
                    "name": block.get("name", ""),
                    "arguments": "", "status": "in_progress"
                }
            else:
                continue

            state.item_header = header
            yield _sse("response.output_item.added", {
                "type": "response.output_item.added",
                "sequence_number": seq, "output_index": index, "item": header,
            }); seq += 1

            if state.item_type == "message":
                yield _sse("response.content_part.added", {
                    "type": "response.content_part.added",
                    "sequence_number": seq, "item_id": state.item_id,
                    "output_index": index, "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }); seq += 1

        elif event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type")
            if delta_type == "thinking_delta":
                continue
            index = state.output_index

            if delta_type == "text_delta":
                text = delta.get("text", "")
                state.text_buffer += text
                yield _sse("response.output_text.delta", {
                    "type": "response.output_text.delta",
                    "sequence_number": seq, "item_id": state.item_id,
                    "output_index": index, "content_index": 0, "delta": text,
                }); seq += 1

            elif delta_type == "input_json_delta":
                args = delta.get("partial_json", "")
                state.args_buffer += args
                yield _sse("response.function_call_arguments.delta", {
                    "type": "response.function_call_arguments.delta",
                    "sequence_number": seq, "item_id": state.item_id,
                    "output_index": index, "delta": args,
                }); seq += 1

        elif event_type == "content_block_stop":
            if state.item_type is None and state.output_index == -1:
                continue
            index = state.output_index

            if state.item_type == "message":
                final_text = state.text_buffer
                state.all_text_parts.append(final_text)
                final_part = {"type": "output_text", "text": final_text, "annotations": []}

                yield _sse("response.output_text.done", {
                    "type": "response.output_text.done",
                    "sequence_number": seq, "item_id": state.item_id,
                    "output_index": index, "content_index": 0, "text": final_text,
                }); seq += 1
                yield _sse("response.content_part.done", {
                    "type": "response.content_part.done",
                    "sequence_number": seq, "item_id": state.item_id,
                    "output_index": index, "content_index": 0, "part": final_part,
                }); seq += 1
                done_item = {
                    "type": "message", "id": state.item_id, "status": "completed",
                    "role": "assistant", "content": [final_part]
                }
            elif state.item_type == "function_call":
                final_args = state.args_buffer
                yield _sse("response.function_call_arguments.done", {
                    "type": "response.function_call_arguments.done",
                    "sequence_number": seq, "item_id": state.item_id,
                    "output_index": index, "arguments": final_args,
                }); seq += 1
                done_item = dict(state.item_header)
                done_item["arguments"] = final_args
                done_item["status"] = "completed"
            else:
                continue

            yield _sse("response.output_item.done", {
                "type": "response.output_item.done",
                "sequence_number": seq, "output_index": index, "item": done_item,
            }); seq += 1
            state.collected_items.append(done_item)
            state.item_type = None

        elif event_type == "message_delta":
            state.completion_tokens = data.get("usage", {}).get(
                "output_tokens", state.completion_tokens)
            # remember upstream truncation so we can surface incomplete
            sr = data.get("delta", {}).get("stop_reason")
            if sr in ("max_tokens", "length"):
                state._truncated = True  # type: ignore

        elif event_type == "error":
            yield _sse("response.failed", {
                "type": "response.failed",
                "sequence_number": seq,
                "response": {
                    **state.final_response(),
                    "status": "failed",
                    "error": {"code": "api_error", "message": data.get("error", {}).get("message", "Unknown provider error")},
                },
            })
            return

        elif event_type == "message_stop":
            break

    # Rescue pass: if the model never emitted a native tool call, check whether
    # it wrote one as plain text (weak models imitate "functions.exec: {...}" or
    # "<tool_call> FUNCTION ...") and re-emit it as a real function_call item so
    # Codex can execute it.
    if state.tool_schemas and not state.saw_native_tool_call:
        full_text = "\n".join(state.all_text_parts)
        for name, payload in _extract_text_tool_calls(full_text, state.tool_schemas):
            index = state.next_output_index()
            item_id = _new_item_id("fc")
            args_json = json.dumps(payload)
            header = {
                "type": "function_call", "id": item_id,
                "call_id": f"call_{uuid.uuid4().hex[:24]}",
                "name": name, "arguments": "", "status": "in_progress"
            }
            yield _sse("response.output_item.added", {
                "type": "response.output_item.added",
                "sequence_number": seq, "output_index": index, "item": header,
            }); seq += 1
            yield _sse("response.function_call_arguments.delta", {
                "type": "response.function_call_arguments.delta",
                "sequence_number": seq, "item_id": item_id,
                "output_index": index, "delta": args_json,
            }); seq += 1
            yield _sse("response.function_call_arguments.done", {
                "type": "response.function_call_arguments.done",
                "sequence_number": seq, "item_id": item_id,
                "output_index": index, "arguments": args_json,
            }); seq += 1
            done_item = dict(header)
            done_item["arguments"] = args_json
            done_item["status"] = "completed"
            yield _sse("response.output_item.done", {
                "type": "response.output_item.done",
                "sequence_number": seq, "output_index": index, "item": done_item,
            }); seq += 1
            state.collected_items.append(done_item)

    truncated = bool(getattr(state, "_truncated", False))
    # empty output with tools = likely truncation even if no explicit flag
    if not truncated and state.tool_schemas and not state.collected_items:
        truncated = True
    status = "incomplete" if truncated else "completed"
    final = state.final_response(status=status)
    if truncated:
        final["incomplete_details"] = {"reason": "max_output_tokens"}
        log_warn(f"[⚠️] Responses stream truncated — surfacing incomplete")
    yield _sse("response.completed", {
        "type": "response.completed",
        "sequence_number": seq,
        "response": final,
    })


# ---------------------------------------------------------------------------
# Agentic retry — free models sometimes narrate ("let me check the files…")
# instead of emitting a tool call. When tools were offered and the model
# replies with action-intent narration only, the proxy retries (with a nudge)
# instead of letting the turn die.
# ---------------------------------------------------------------------------

AGENTIC_NUDGE = (
    "CRITICAL: You are an autonomous coding agent. If you intend to inspect, "
    "run, create or change anything, you MUST emit the corresponding tool call "
    "in this reply. Do NOT describe what you are about to do and stop — act by "
    "calling the tool now."
)

_INTENT_RE = re.compile(
    r"(?i)\b("
    r"i'll|i will|i'm going to|let me|shall i|may i|"
    r"để mình|mình sẽ|mình đang|mình thử|tôi sẽ|tôi đang|cho mình|"
    r"bắt đầu|khám phá|xem thử|chạy thử|demo"
    r")\b"
)


def looks_like_action_narration(text: str) -> bool:
    """True when the reply announces an action instead of taking it."""
    if not text:
        return False
    return bool(_INTENT_RE.search(text))


def stream_events(events: List[SSEEvent]) -> AsyncIterator[SSEEvent]:
    async def _gen():
        for ev in events:
            yield ev
    return _gen()


def has_native_tool_call(events: List[SSEEvent]) -> bool:
    return any(
        ev.data.get("type") == "content_block_start"
        and ev.data.get("content_block", {}).get("type") == "tool_use"
        for ev in events
    )


def stream_text(events: List[SSEEvent]) -> str:
    return "".join(
        ev.data.get("delta", {}).get("text", "")
        for ev in events
        if ev.data.get("type") == "content_block_delta"
        and ev.data.get("delta", {}).get("type") == "text_delta"
    )


def response_text(resp: AnthropicResponse) -> str:
    return "\n".join(
        b.get("text", "") for b in resp.content if b.get("type") == "text"
    )
