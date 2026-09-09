import json
import httpx
from config.logging import log_warn
from typing import AsyncIterator, Dict, Any, List
from provider.base import BaseProvider
from models.anthropic import AnthropicRequest, AnthropicResponse, AnthropicUsage
from models.events import SSEEvent
import uuid


def _anthropic_content_to_openai(role: str, content: Any) -> List[Dict[str, Any]]:
    """
    Convert an Anthropic message (role + content) to a list of OpenAI-format messages.
    Handles text, thinking, tool_use (assistant calling tools), and tool_result (user returning results).
    Preserves reasoning_content for thinking-mode models (DeepSeek R1 etc.).
    """
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    messages = []
    text_parts: List[str] = []
    thinking_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for block in content:
        block_type = block.get("type")

        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "thinking":
            thinking_parts.append(block.get("thinking", block.get("text", "")))

        elif block_type == "tool_use":
            raw_id = block.get("id", f"call_{uuid.uuid4().hex}")
            tc_id = raw_id[6:] if raw_id.startswith("toolu_") else raw_id
            tool_calls.append({
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}))
                }
            })

        elif block_type == "tool_result":
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                result_content = "\n".join(
                    c.get("text", "") for c in result_content if c.get("type") == "text"
                )
            raw_id = block.get("tool_use_id", "")
            tc_id = raw_id[6:] if raw_id.startswith("toolu_") else raw_id
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_content
            })

    if role == "assistant" and (text_parts or thinking_parts or tool_calls):
        msg: Dict[str, Any] = {"role": "assistant"}
        # THINKING OFF: không gửi reasoning_content nữa — DeepSeek V4 lỗi khi echo
        msg["content"] = "\n".join(text_parts) if text_parts else ""
        if msg["content"] is None:
            msg["content"] = ""
        if tool_calls:
            msg["tool_calls"] = tool_calls
        messages.insert(0, msg)
    elif role == "user" and (text_parts or thinking_parts):
        content = "\n".join(text_parts or thinking_parts)
        messages.insert(0, {"role": "user", "content": content})

    return messages


def _sanitize_json_schema(node, is_cohere: bool = False):
    if isinstance(node, dict):
        if "pattern" in node:
            pat = node.get("pattern")
            if not isinstance(pat, str):
                node.pop("pattern", None)
            else:
                import re as _re2
                try:
                    _re2.compile(pat)
                except re.error:
                    node.pop("pattern", None)
                if is_cohere:
                    node.pop("pattern", None)
        for v in list(node.values()):
            _sanitize_json_schema(v, is_cohere)
    elif isinstance(node, list):
        for item in node:
            _sanitize_json_schema(item, is_cohere)


def _anthropic_tools_to_openai(tools: List[Dict[str, Any]], is_cohere: bool = False) -> List[Dict[str, Any]]:
    """Convert Anthropic tool definitions to OpenAI function-calling format."""
    result = []
    for tool in tools:
        schema = tool.get("input_schema", {}) or {}
        if isinstance(schema, dict):
            import copy as _cp
            schema = _cp.deepcopy(schema)
            _sanitize_json_schema(schema, is_cohere=is_cohere)
        result.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": schema
            }
        })
    return result


def _openai_tool_calls_to_anthropic(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI tool_calls in a response to Anthropic tool_use content blocks."""
    blocks = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        try:
            input_data = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            input_data = {}
            
        raw_id = tc.get("id", f"call_{uuid.uuid4().hex}")
        tc_id = raw_id if raw_id.startswith("toolu_") else f"toolu_{raw_id}"
        
        blocks.append({
            "type": "tool_use",
            "id": tc_id,
            "name": fn.get("name", ""),
            "input": input_data
        })
    return blocks


class OpenAIBaseProvider(BaseProvider):
    supports_openai: bool = True
    """
    Shared base provider for any OpenAI Chat Completions compatible API.
    Handles full Anthropic ↔ OpenAI translation including tool use.
    """
    def __init__(self, target_model: str, base_url: str, api_key: str):
        super().__init__(target_model)
        self.base_url = base_url
        self.api_key = api_key

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def translate_request(self, anthropic_request: AnthropicRequest) -> Dict[str, Any]:
        """Convert Anthropic Messages request to OpenAI Chat Completions request."""
        messages = []

        if anthropic_request.system:
            system_content = anthropic_request.system
            if isinstance(system_content, list):
                system_content = "\n".join(
                    m.get("text", "") for m in system_content if m.get("type") == "text"
                )
            messages.append({"role": "system", "content": system_content})

        for msg in anthropic_request.messages:
            converted = _anthropic_content_to_openai(msg.role, msg.content)
            messages.extend(converted)

        body: Dict[str, Any] = {
            "model": self.target_model,
            "messages": messages,
            "stream": anthropic_request.stream,
        }

        is_nemotron = "nemotron" in self.target_model.lower()
        if is_nemotron:
            eb = body.get("extra_body") or {}
            eb.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
            eb["enable_thinking"] = False
            eb["force_nonempty_content"] = True
            body["extra_body"] = eb
            if "max_tokens" not in body and not getattr(anthropic_request, "_retry_max_tokens", None):
                body["max_tokens"] = 8192
            anthropic_request.thinking = None  # type: ignore
        # Respect explicit max_tokens; on agentic retry we bump it if truncated (see server.py)
        if anthropic_request.model_fields_set and "max_tokens" in anthropic_request.model_fields_set:
            if anthropic_request.max_tokens is not None:
                body["max_tokens"] = anthropic_request.max_tokens
        # Allow server retry to inject _retry_max_tokens when previous turn was truncated
        if hasattr(anthropic_request, "_retry_max_tokens") and getattr(anthropic_request, "_retry_max_tokens"):
            body["max_tokens"] = getattr(anthropic_request, "_retry_max_tokens")
        if anthropic_request.temperature is not None:
            body["temperature"] = anthropic_request.temperature
        if anthropic_request.top_p is not None:
            body["top_p"] = anthropic_request.top_p
        if anthropic_request.stop_sequences:
            body["stop"] = anthropic_request.stop_sequences

        if not is_nemotron and "deepseek" in self.target_model.lower():
            body["extra_body"] = {"thinking": {"type": "disabled"}}
            body["thinking"] = {"type": "disabled"}
            body.pop("reasoning_effort", None)
        elif not is_nemotron:
            if anthropic_request.thinking and anthropic_request.thinking.type == "enabled":
                budget = anthropic_request.thinking.budget_tokens or 4000
                if budget > 16000:
                    body["reasoning_effort"] = "xhigh"
                elif budget > 8000:
                    body["reasoning_effort"] = "high"
                elif budget > 2000:
                    body["reasoning_effort"] = "medium"
                else:
                    body["reasoning_effort"] = "low"

        if anthropic_request.tools:
            _is_cohere = "cohere" in self.target_model.lower() or "north" in self.target_model.lower()
            body["tools"] = _anthropic_tools_to_openai(anthropic_request.tools, is_cohere=_is_cohere)
            tc = anthropic_request.tool_choice
            if isinstance(tc, dict):
                t = tc.get("type")
                if t == "any":
                    body["tool_choice"] = "required"
                elif t == "auto":
                    body["tool_choice"] = "auto"
                elif t == "tool" and tc.get("name"):
                    body["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
            body["parallel_tool_calls"] = True

        # thinking mode OFF — không inject reasoning_content nữa

        return body

    @staticmethod
    def _map_finish_reason(openai_reason: str | None, has_tool_calls: bool) -> str | None:
        # Always surface truncation — even when tool_calls present (truncated mid-JSON).
        if openai_reason == "length":
            return "max_tokens"
        if has_tool_calls:
            return "tool_use"
        m = {"tool_calls": "tool_use", "content_filter": "stop_sequence", "stop": "end_turn", "end_turn": "end_turn"}
        return m.get(openai_reason, openai_reason) if openai_reason else None

    async def translate_response(self, provider_response: Dict[str, Any]) -> AnthropicResponse:
        choice = provider_response.get("choices", [{}])[0]
        message = choice.get("message", {})
        raw_finish = choice.get("finish_reason")

        content_blocks: List[Dict[str, Any]] = []

        reasoning_raw = message.get("reasoning_content")
        if reasoning_raw is None:
            reasoning_raw = message.get("reasoning")
        reasoning_text = ""
        if isinstance(reasoning_raw, str):
            reasoning_text = reasoning_raw
        elif isinstance(reasoning_raw, list):
            reasoning_text = "\n".join(str(p.get("text", "") if isinstance(p, dict) else str(p)) for p in reasoning_raw if p)
        elif isinstance(reasoning_raw, dict):
            reasoning_text = reasoning_raw.get("text", "") or reasoning_raw.get("content", "") or ""
        if reasoning_text and reasoning_text.strip():
            content_blocks.append({"type": "thinking", "thinking": reasoning_text})

        text = message.get("content")
        if isinstance(text, list):
            text = "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in text)
        if isinstance(text, str):
            import re as _re
            m = _re.search(r"<think>(.*?)</think>", text, _re.DOTALL | _re.IGNORECASE)
            if m and m.group(1).strip() and not text.replace(m.group(0), "").strip():
                text = ""
                if not reasoning_text.strip():
                    reasoning_text = m.group(1).strip()
                    if not any(b.get("type") == "thinking" for b in content_blocks):
                        content_blocks.insert(0, {"type": "thinking", "thinking": reasoning_text})
            elif m and m.group(1).strip():
                inner = m.group(1).strip()
                if not any(b.get("type") == "thinking" for b in content_blocks):
                    content_blocks.insert(0, {"type": "thinking", "thinking": inner})
                text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL | _re.IGNORECASE).strip()
        reasoning_for_fallback = ""
        for b in content_blocks:
            if b.get("type") == "thinking" and b.get("thinking", "").strip():
                reasoning_for_fallback = b["thinking"]
                break
        if (not text or not text.strip()) and reasoning_for_fallback and reasoning_for_fallback.strip():
            text = reasoning_for_fallback
        if text and text.strip():
            content_blocks.append({"type": "text", "text": text})

        tool_calls = message.get("tool_calls")
        if tool_calls:
            content_blocks.extend(_openai_tool_calls_to_anthropic(tool_calls))

        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})

        finish_reason = self._map_finish_reason(raw_finish, bool(tool_calls))
        if not finish_reason:
            finish_reason = "end_turn" if not tool_calls else "tool_use"
        if finish_reason == "max_tokens":
            log_warn(f"[⚠️] Upstream truncated (finish_reason=length → max_tokens) model={self.target_model}")

        provider_usage = provider_response.get("usage", {})
        usage = AnthropicUsage(
            input_tokens=provider_usage.get("prompt_tokens", 0),
            output_tokens=provider_usage.get("completion_tokens", 0)
        )

        return AnthropicResponse(
            id=f"msg_{provider_response.get('id', uuid.uuid4().hex)}",
            model=self.target_model,
            content=content_blocks,
            stop_reason=finish_reason,
            usage=usage
        )

    async def generate(self, request_body: Dict[str, Any]) -> AnthropicResponse:
        if "deepseek" in self.target_model.lower() and "v4" in self.target_model.lower():
            import pathlib
            try:
                pathlib.Path("/tmp/key-router-deepseek-last-request.json").write_text(json.dumps(request_body, ensure_ascii=False, indent=2))
            except Exception:
                pass
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=request_body,
            )
            if resp.status_code != 200 and "deepseek" in self.target_model.lower():
                import pathlib
                try:
                    pathlib.Path("/tmp/key-router-deepseek-last-error.txt").write_text(resp.text[:5000])
                except Exception:
                    pass
            resp.raise_for_status()
            return await self.translate_response(resp.json())

    async def stream(self, request_body: Dict[str, Any]) -> AsyncIterator[SSEEvent]:
        request_body["stream"] = True
        msg_id = f"msg_{uuid.uuid4().hex}"

        yield SSEEvent(event="message_start", data={
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant",
                "content": [], "model": self.target_model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0}
            }
        })

        text_block_open = False
        thinking_block_open = False
        thinking_index: int | None = None
        tool_blocks: dict[int, dict[str, Any]] = {}
        block_index = 0
        reasoning_buffer = ""
        upstream_finish: str | None = None
        upstream_usage: dict[str, Any] = {}
        seen_data = False

        if "deepseek" in self.target_model.lower() and "v4" in self.target_model.lower():
            import pathlib
            try:
                pathlib.Path("/tmp/key-router-deepseek-last-stream-request.json").write_text(json.dumps(request_body, ensure_ascii=False, indent=2))
            except Exception:
                pass
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=request_body,
            ) as response:
                if response.status_code != 200:
                    err = (await response.aread()).decode()[:5000] if hasattr(response, 'aread') else ""
                    if "deepseek" in self.target_model.lower():
                        import pathlib
                        try:
                            pathlib.Path("/tmp/key-router-deepseek-last-stream-error.txt").write_text(err)
                            pathlib.Path("/tmp/key-router-deepseek-last-stream-request.json").write_text(json.dumps(request_body, ensure_ascii=False, indent=2))
                        except Exception:
                            pass
                    response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if choices:
                        seen_data = True
                        fr = choices[0].get("finish_reason")
                        if fr:
                            upstream_finish = fr
                    else:
                        # usage-only chunk is still progress
                        if chunk.get("usage"):
                            seen_data = True
                            upstream_usage = chunk.get("usage", {})
                        continue
                    delta = choices[0].get("delta", {})
                    if not delta:
                        if chunk.get("usage"):
                            upstream_usage = chunk.get("usage", {})
                        continue

                    reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(reasoning_delta, list):
                        reasoning_delta = "".join(str(p.get("text","") if isinstance(p, dict) else str(p)) for p in reasoning_delta)
                    elif isinstance(reasoning_delta, dict):
                        reasoning_delta = reasoning_delta.get("text","") or reasoning_delta.get("content","") or ""
                    if reasoning_delta and "</think>" in reasoning_delta:
                        import re as _re_rd
                        if reasoning_delta.strip().startswith("</think>"):
                            reasoning_delta = reasoning_delta.split("</think>", 1)[-1]
                        reasoning_delta = _re_rd.sub(r"<think>.*?</think>", "", reasoning_delta, flags=_re_rd.DOTALL | _re_rd.IGNORECASE)
                        if not reasoning_delta.strip():
                            reasoning_delta = ""
                    if reasoning_delta and reasoning_delta.strip():
                        if not thinking_block_open:
                            yield SSEEvent(event="content_block_start", data={
                                "type": "content_block_start", "index": block_index,
                                "content_block": {"type": "thinking", "thinking": ""}
                            })
                            thinking_block_open = True
                            thinking_index = block_index
                            block_index += 1
                        yield SSEEvent(event="content_block_delta", data={
                            "type": "content_block_delta", "index": thinking_index,
                            "delta": {"type": "thinking_delta", "thinking": reasoning_delta}
                        })
                        reasoning_buffer += reasoning_delta

                    text_delta = delta.get("content")
                    if isinstance(text_delta, list):
                        text_delta = "".join(p.get("text","") if isinstance(p, dict) else str(p) for p in text_delta)
                    if isinstance(text_delta, str) and "</think>" in text_delta:
                        import re as _re2
                        if text_delta.strip().startswith("</think>") or text_delta.lstrip().startswith("</think>"):
                            _, _, tail = text_delta.partition("</think>")
                            text_delta = tail
                        text_delta = _re2.sub(r"<think>.*?</think>", "", text_delta, flags=_re2.DOTALL | _re2.IGNORECASE)
                    if text_delta:
                        if not text_block_open:
                            yield SSEEvent(event="content_block_start", data={
                                "type": "content_block_start", "index": block_index,
                                "content_block": {"type": "text", "text": ""}
                            })
                            text_block_open = True
                        yield SSEEvent(event="content_block_delta", data={
                            "type": "content_block_delta", "index": block_index,
                            "delta": {"type": "text_delta", "text": text_delta}
                        })

                    for tc_chunk in delta.get("tool_calls", []):
                        tc_index = tc_chunk.get("index", 0)

                        if tc_index not in tool_blocks:
                            if text_block_open:
                                yield SSEEvent(event="content_block_stop", data={
                                    "type": "content_block_stop", "index": block_index
                                })
                                text_block_open = False
                                block_index += 1

                            fn_info = tc_chunk.get("function", {})
                            raw_id = tc_chunk.get("id", f"call_{uuid.uuid4().hex}")
                            tool_id = raw_id if raw_id.startswith("toolu_") else f"toolu_{raw_id}"
                            tool_name = fn_info.get("name", "")
                            tool_blocks[tc_index] = {"id": tool_id, "name": tool_name, "args_buf": ""}

                            yield SSEEvent(event="content_block_start", data={
                                "type": "content_block_start", "index": block_index + tc_index,
                                "content_block": {
                                    "type": "tool_use",
                                    "id": tool_id,
                                    "name": tool_name,
                                    "input": {}
                                }
                            })

                        args_delta = tc_chunk.get("function", {}).get("arguments", "")
                        if args_delta:
                            tool_blocks[tc_index]["args_buf"] += args_delta
                            yield SSEEvent(event="content_block_delta", data={
                                "type": "content_block_delta",
                                "index": block_index + tc_index,
                                "delta": {"type": "input_json_delta", "partial_json": args_delta}
                            })

                    if chunk.get("usage"):
                        upstream_usage = chunk.get("usage", {})

        if not seen_data or upstream_finish is None:
            log_warn(f"[⚠️] Upstream stream ended without finish_reason (seen_data={seen_data}) model={self.target_model} — will surface as incomplete")

        if text_block_open:
            yield SSEEvent(event="content_block_stop", data={
                "type": "content_block_stop", "index": block_index
            })
        if thinking_block_open:
            yield SSEEvent(event="content_block_stop", data={
                "type": "content_block_stop", "index": thinking_index
            })
        for tc_index in tool_blocks:
            yield SSEEvent(event="content_block_stop", data={
                "type": "content_block_stop", "index": block_index + tc_index
            })

        if not seen_data or upstream_finish is None:
            upstream_finish = "length"
            log_warn(f"[⚠️] No finish_reason from upstream — treating as truncated (incomplete) for retry")
        mapped = self._map_finish_reason(upstream_finish, bool(tool_blocks))
        if not mapped:
            mapped = "tool_use" if tool_blocks else "end_turn"
        if mapped == "max_tokens":
            log_warn(f"[⚠️] Upstream truncated mid-stream (finish_reason={upstream_finish} → max_tokens) model={self.target_model}")
        out_tokens = upstream_usage.get("completion_tokens", 0) if upstream_usage else 0
        yield SSEEvent(event="message_delta", data={
            "type": "message_delta",
            "delta": {"stop_reason": mapped, "stop_sequence": None},
            "usage": {"output_tokens": out_tokens}
        })
        yield SSEEvent(event="message_stop", data={"type": "message_stop"})
