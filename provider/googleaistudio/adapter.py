import json
import uuid
import httpx
from typing import AsyncIterator, Dict, Any, List, Optional

from provider.base import BaseProvider
from models.anthropic import AnthropicRequest, AnthropicResponse, AnthropicUsage
from models.events import SSEEvent
from config.settings import settings
from config.logging import log_warn


def _strip_toolu(tid: str) -> str:
    return tid[6:] if tid.startswith("toolu_") else tid


def _ensure_toolu(tid: str) -> str:
    return tid if tid.startswith("toolu_") else f"toolu_{tid}"


def _is_valid_b64(s: str) -> bool:
    return isinstance(s, str) and len(s.strip()) > 10


_THOUGHT_SIG_CACHE: dict[str, str] = {}


def _cache_sig(tool_id: str, sig: str) -> None:
    if not tool_id or not sig:
        return
    _THOUGHT_SIG_CACHE[tool_id] = sig
    _THOUGHT_SIG_CACHE[_strip_toolu(tool_id)] = sig
    _THOUGHT_SIG_CACHE[_ensure_toolu(tool_id)] = sig


def _lookup_sig(tool_id: str) -> str:
    if not tool_id:
        return ""
    return _THOUGHT_SIG_CACHE.get(tool_id) or _THOUGHT_SIG_CACHE.get(_strip_toolu(tool_id)) or _THOUGHT_SIG_CACHE.get(_ensure_toolu(tool_id)) or ""


_DROP_FIELDS = {
    "additionalProperties", "propertyNames", "prefixItems",
    "const", "anyOf", "oneOf", "allOf", "not", "if", "then", "else",
    "contains", "unevaluatedProperties", "unevaluatedItems", "dependentRequired",
    "dependentSchemas", "patternProperties", "exclusiveMinimum", "exclusiveMaximum",
    "$schema", "$id", "$defs", "definitions", "contentEncoding", "contentMediaType",
    "encrypted",
}


def _sanitize_schema_for_gemini(obj: Any) -> Any:
    if isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in obj.items():
            if k in _DROP_FIELDS:
                continue
            cv = _sanitize_schema_for_gemini(v)
            if k == "exclusiveMinimum" and isinstance(cv, bool):
                continue
            if k == "exclusiveMaximum" and isinstance(cv, bool):
                continue
            cleaned[k] = cv
        # Gemini: array must have `items`; bare {"type":"array"} is invalid (missing items.items)
        if cleaned.get("type") == "array" and "items" not in cleaned:
            cleaned["items"] = {"type": "string"}
        # Also fix nested case: items: {"type":"array"} with no inner items
        if cleaned.get("type") == "array" and isinstance(cleaned.get("items"), dict) and cleaned["items"].get("type") == "array" and "items" not in cleaned["items"]:
            cleaned["items"]["items"] = {"type": "string"}
        return cleaned
    elif isinstance(obj, list):
        return [_sanitize_schema_for_gemini(x) for x in obj]
    else:
        return obj


class GoogleAIStudioProvider(BaseProvider):
    def __init__(self, target_model: str):
        super().__init__(target_model)
        self.api_key = settings.google_api_key
        self.base_url = settings.google_base_url.rstrip("/")

    def _get_headers(self) -> Dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _url(self, action: str) -> str:
        model = getattr(self, "_effective_model", self.target_model)
        return f"{self.base_url}/models/{model}:{action}"

    @staticmethod
    def _map_finish_reason(gemini_reason: str | None, has_tool: bool) -> str | None:
        if gemini_reason == "MAX_TOKENS":
            return "max_tokens"
        if has_tool:
            return "tool_use"
        m = {
            "STOP": "end_turn",
            "SAFETY": "stop_sequence",
            "RECITATION": "stop_sequence",
            "OTHER": "end_turn",
            "FINISH_REASON_UNSPECIFIED": "end_turn",
        }
        if gemini_reason in m:
            return m[gemini_reason]
        return m.get(gemini_reason, gemini_reason) if gemini_reason else None

    async def translate_request(self, req: AnthropicRequest) -> Dict[str, Any]:
        contents: List[Dict[str, Any]] = []
        tool_id_to_name: Dict[str, str] = {}

        for msg in req.messages:
            role = msg.role
            content = msg.content

            if isinstance(content, str):
                gem_role = "model" if role == "assistant" else "user"
                contents.append({"role": gem_role, "parts": [{"text": content}]})
                continue

            text_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            tool_results: List[Dict[str, Any]] = []

            for block in content:
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    text_parts.append(block["text"])
                elif btype == "tool_use":
                    tid = block.get("id", "")
                    if tid:
                        tool_id_to_name[tid] = block.get("name", "")
                    tool_calls.append(block)
                elif btype == "tool_result":
                    tool_results.append(block)

            if role == "assistant":
                parts: List[Dict[str, Any]] = []
                for block in content:
                    btype = block.get("type")
                    if btype == "text" and block.get("text"):
                        part: Dict[str, Any] = {"text": block["text"]}
                        sig = block.get("thought_signature") or block.get("thoughtSignature") or _lookup_sig(block.get("id", "")) or ""
                        if sig and _is_valid_b64(sig):
                            part["thoughtSignature"] = sig
                        parts.append(part)
                    elif btype == "tool_use":
                        args = block.get("input", {})
                        if not isinstance(args, dict):
                            args = {}
                        fc: Dict[str, Any] = {"name": block.get("name", ""), "args": args}
                        sig2 = block.get("thought_signature") or block.get("thoughtSignature") or _lookup_sig(block.get("id", "")) or ""
                        if sig2 and _is_valid_b64(sig2):
                            parts.append({"functionCall": fc, "thoughtSignature": sig2})
                        else:
                            parts.append({"functionCall": fc})
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif role == "user":
                if text_parts:
                    contents.append({"role": "user", "parts": [{"text": "\n".join(text_parts)}]})
                for tr in tool_results:
                    tid = tr.get("tool_use_id", "")
                    name = tool_id_to_name.get(tid, "") or tool_id_to_name.get(_strip_toolu(tid), "") or "unknown"
                    if tid in tool_id_to_name:
                        name = tool_id_to_name[tid]
                    elif _strip_toolu(tid) in { _strip_toolu(k) for k in tool_id_to_name }:
                        for k, v in tool_id_to_name.items():
                            if _strip_toolu(k) == _strip_toolu(tid):
                                name = v
                                break
                    raw = tr.get("content", "")
                    if isinstance(raw, list):
                        raw = "\n".join(c.get("text", "") for c in raw if isinstance(c, dict))
                    if not isinstance(raw, str):
                        raw = json.dumps(raw)
                    contents.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": name, "response": {"content": raw}}}],
                    })

        merged: List[Dict[str, Any]] = []
        for c in contents:
            has_fn_response = any("functionResponse" in p for p in c.get("parts", []))
            prev_has_fn_response = bool(merged and any("functionResponse" in p for p in merged[-1].get("parts", [])))
            # Don't merge user turns that contain functionResponse with plain text —
            # Gemini expects functionResponse turn isolated and rejects mixed parts.
            if merged and merged[-1]["role"] == c["role"] and not has_fn_response and not prev_has_fn_response:
                merged[-1]["parts"].extend(c["parts"])
            else:
                merged.append(c)

        if not merged:
            merged = [{"role": "user", "parts": [{"text": "Hello"}]}]

        # Gemini alternation: must start with user
        if merged and merged[0]["role"] == "model":
            merged.insert(0, {"role": "user", "parts": [{"text": "Hello"}]})

        effective_model = self.target_model

        body: Dict[str, Any] = {"contents": merged}
        self._effective_model = effective_model

        if req.system:
            sys_text = req.system
            if isinstance(sys_text, list):
                sys_text = "\n".join(m.get("text", "") for m in sys_text if isinstance(m, dict) and m.get("text"))
            if isinstance(sys_text, str) and sys_text.strip():
                body["systemInstruction"] = {"parts": [{"text": sys_text.strip()}]}

        if req.tools:
            decls = []
            for t in req.tools:
                raw = t.get("input_schema") or {"type": "object", "properties": {}}
                params = _sanitize_schema_for_gemini(raw)
                # If sanitizing stripped a union (anyOf/oneOf) down to {}, Gemini rejects empty.
                # Fall back to a minimal generic schema instead of invalid {}.
                if not params or (params == {} or (isinstance(params, dict) and not params.get("type") and not params.get("properties"))):
                    params = {"type": "object", "properties": {}}
                # Gemini also requires `type` at top level
                if isinstance(params, dict) and "type" not in params:
                    params["type"] = "object"
                decls.append({
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": params,
                })
            body["tools"] = [{"functionDeclarations": decls}]

            tc = req.tool_choice
            if isinstance(tc, dict):
                t = tc.get("type")
                if t == "any":
                    body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
                elif t == "auto":
                    body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
                elif t == "tool" and tc.get("name"):
                    body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tc["name"]]}}

        gen: Dict[str, Any] = {}
        if req.temperature is not None:
            gen["temperature"] = req.temperature
        if req.top_p is not None:
            gen["topP"] = req.top_p
        if req.top_k is not None:
            gen["topK"] = req.top_k
        if req.stop_sequences:
            gen["stopSequences"] = req.stop_sequences
        if req.model_fields_set and "max_tokens" in req.model_fields_set and req.max_tokens is not None:
            gen["maxOutputTokens"] = req.max_tokens
        effort = getattr(req, "reasoning_effort", None)
        if effort and effort in ("low", "medium", "high", "xhigh"):
            budget = {"low": 1024, "medium": 2048, "high": 8192, "xhigh": 16384}.get(effort, 1024)
            # Gemini rejects thinkingBudget:0 — map "low" to minimal valid budget
            if budget > 0:
                gen["thinkingConfig"] = {"thinkingBudget": budget}
        if gen:
            body["generationConfig"] = gen

        return body

    async def translate_response(self, data: Dict[str, Any]) -> AnthropicResponse:
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            reason = feedback.get("blockReason", "BLOCKED")
            raise ValueError(f"Gemini blocked prompt: {reason}")

        candidates = data.get("candidates") or []
        if not candidates:
            raise ValueError(f"Gemini returned no candidates: {json.dumps(data)[:500]}")

        cand = candidates[0]
        raw_finish = cand.get("finishReason")
        content = cand.get("content") or {}
        parts = content.get("parts") or []

        blocks: List[Dict[str, Any]] = []
        for part in parts:
            if "text" in part and part["text"]:
                sig_t = part.get("thoughtSignature") or part.get("thought_signature") or ""
                block: Dict[str, Any] = {"type": "text", "text": part["text"]}
                if sig_t:
                    block["thought_signature"] = sig_t
                blocks.append(block)
            elif "functionCall" in part:
                fc = part["functionCall"]
                raw_id = fc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                sig = fc.get("thoughtSignature") or fc.get("thought_signature") or part.get("thoughtSignature") or part.get("thought_signature") or ""
                raw_sig = fc.get("thought_signature") or part.get("thoughtSignature") or sig
                block2: Dict[str, Any] = {
                    "type": "tool_use",
                    "id": _ensure_toolu(raw_id),
                    "name": fc.get("name", ""),
                    "input": fc.get("args") if isinstance(fc.get("args"), dict) else {},
                }
                if raw_sig:
                    sig_to_store = raw_sig
                    block2["thought_signature"] = sig_to_store
                    _cache_sig(raw_id, sig_to_store)
                    _cache_sig(block2["id"], sig_to_store)
                blocks.append(block2)
            elif "inlineData" in part:
                pass

        if not blocks:
            blocks.append({"type": "text", "text": ""})

        has_tool = any(b["type"] == "tool_use" for b in blocks)
        finish = self._map_finish_reason(raw_finish, has_tool)
        if not finish:
            finish = "tool_use" if has_tool else "end_turn"
        if finish == "max_tokens":
            log_warn(f"[⚠️] Gemini truncated (MAX_TOKENS) model={self.target_model}")

        usage = data.get("usageMetadata") or {}
        anth_usage = AnthropicUsage(
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
        )

        return AnthropicResponse(
            id=f"msg_{data.get('modelVersion', uuid.uuid4().hex)}",
            model=self.target_model,
            content=blocks,
            stop_reason=finish,
            usage=anth_usage,
        )

    async def generate(self, request_body: Dict[str, Any]) -> AnthropicResponse:
        if not self.api_key:
            raise ValueError("Missing GOOGLE_API_KEY for googleaistudio provider — set it in .env")
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            resp = await client.post(
                self._url("generateContent"),
                headers=self._get_headers(),
                json=request_body,
            )
            if resp.status_code != 200:
                body_txt = resp.text[:3000]
                log_warn(f"[❌] Gemini generateContent {resp.status_code}: {body_txt}")
                # also dump request shape for debugging
                import pathlib
                dbg = pathlib.Path("/tmp/key-router-gemini-last-request.json")
                try:
                    dbg.write_text(json.dumps(request_body, ensure_ascii=False, indent=2))
                    log_warn(f"[❌] Request dumped to {dbg} (tools={len(request_body.get('tools',[]))} contents={len(request_body.get('contents',[]))})")
                except Exception:
                    pass
            resp.raise_for_status()
            return await self.translate_response(resp.json())

    async def stream(self, request_body: Dict[str, Any]) -> AsyncIterator[SSEEvent]:
        if not self.api_key:
            from config.settings import settings as _s
            self.api_key = _s.google_api_key
        if not self.api_key:
            raise ValueError("Missing GOOGLE_API_KEY for googleaistudio provider — set it in .env")

        msg_id = f"msg_{uuid.uuid4().hex}"
        yield SSEEvent(event="message_start", data={
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant",
                "content": [], "model": self.target_model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        })

        text_open = False
        tool_blocks: Dict[int, Dict[str, Any]] = {}
        block_index = 0
        pending_text = ""
        upstream_finish: Optional[str] = None
        upstream_usage: Dict[str, Any] = {}
        seen_data = False
        all_tool_calls: List[Dict[str, Any]] = []

        url = self._url("streamGenerateContent") + "?alt=sse"

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream("POST", url, headers=self._get_headers(), json=request_body) as response:
                if response.status_code != 200:
                    err_body = (await response.aread()).decode()[:3000]
                    log_warn(f"[❌] Gemini streamGenerateContent {response.status_code}: {err_body}")
                    import pathlib
                    dbg = pathlib.Path("/tmp/key-router-gemini-last-request.json")
                    try:
                        dbg.write_text(json.dumps(request_body, ensure_ascii=False, indent=2))
                        log_warn(f"[❌] Stream request dumped to {dbg}")
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

                    candidates = chunk.get("candidates") or []
                    if chunk.get("usageMetadata"):
                        upstream_usage = chunk["usageMetadata"]
                    if not candidates:
                        if chunk.get("usageMetadata"):
                            seen_data = True
                        continue

                    cand = candidates[0]
                    seen_data = True
                    fr = cand.get("finishReason")
                    if fr:
                        upstream_finish = fr

                    content = cand.get("content") or {}
                    parts = content.get("parts") or []
                    for part in parts:
                        if "text" in part and part["text"]:
                            text = part["text"]
                            if not text_open:
                                yield SSEEvent(event="content_block_start", data={
                                    "type": "content_block_start", "index": block_index,
                                    "content_block": {"type": "text", "text": ""},
                                })
                                text_open = True
                            pending_text += text
                            yield SSEEvent(event="content_block_delta", data={
                                "type": "content_block_delta", "index": block_index,
                                "delta": {"type": "text_delta", "text": text},
                            })
                        elif "functionCall" in part:
                            if text_open:
                                yield SSEEvent(event="content_block_stop", data={
                                    "type": "content_block_stop", "index": block_index,
                                })
                                text_open = False
                                block_index += 1
                            fc = part["functionCall"]
                            name = fc.get("name", "")
                            raw_id = fc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                            tid = _ensure_toolu(raw_id)
                            sig_stream = fc.get("thought_signature") or fc.get("thoughtSignature") or part.get("thoughtSignature") or part.get("thought_signature") or ""
                            if sig_stream:
                                _cache_sig(raw_id, sig_stream)
                                _cache_sig(tid, sig_stream)
                            idx = len(all_tool_calls)
                            all_tool_calls.append({"id": tid, "name": name, "args": fc.get("args", {}), "thought_signature": sig_stream})
                            tool_blocks[idx] = {"id": tid, "name": name, "thought_signature": sig_stream}
                            yield SSEEvent(event="content_block_start", data={
                                "type": "content_block_start", "index": block_index + idx,
                                "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}, "thought_signature": sig_stream},
                            })
                            args_obj = fc.get("args", {})
                            if isinstance(args_obj, dict) and args_obj:
                                args_json = json.dumps(args_obj)
                                yield SSEEvent(event="content_block_delta", data={
                                    "type": "content_block_delta", "index": block_index + idx,
                                    "delta": {"type": "input_json_delta", "partial_json": args_json},
                                })

                    if chunk.get("usageMetadata"):
                        upstream_usage = chunk["usageMetadata"]

        if text_open:
            yield SSEEvent(event="content_block_stop", data={
                "type": "content_block_stop", "index": block_index,
            })
        for idx in list(tool_blocks.keys()):
            yield SSEEvent(event="content_block_stop", data={
                "type": "content_block_stop", "index": block_index + idx,
            })

        if not seen_data or upstream_finish is None:
            upstream_finish = "MAX_TOKENS"
            log_warn(f"[⚠️] Gemini stream no finishReason — treating as truncated for retry model={self.target_model}")

        has_tool = bool(tool_blocks)
        mapped = self._map_finish_reason(upstream_finish, has_tool)
        if not mapped:
            mapped = "tool_use" if has_tool else "end_turn"
        if mapped == "max_tokens":
            log_warn(f"[⚠️] Gemini truncated MAX_TOKENS model={self.target_model}")

        out_tokens = upstream_usage.get("candidatesTokenCount", 0) if upstream_usage else 0
        yield SSEEvent(event="message_delta", data={
            "type": "message_delta",
            "delta": {"stop_reason": mapped, "stop_sequence": None},
            "usage": {"output_tokens": out_tokens},
        })
        yield SSEEvent(event="message_stop", data={"type": "message_stop"})
