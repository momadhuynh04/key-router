import httpx
import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from config.logging import logger, log_error, log_warn
from models.anthropic import AnthropicRequest
from proxy.router import provider_router
from proxy.errors import anthropic_error
from proxy.retry import AGENTIC_RETRY_ATTEMPTS
from proxy.responses_ingress import has_native_tool_call, stream_text, looks_like_action_narration, AGENTIC_NUDGE

router = APIRouter()


def _short_preview(text: str, n: int = 120) -> str:
    t = text.strip().replace("\n", " ")
    return (t[:n] + "…") if len(t) > n else t


@router.post("/v1/messages")
async def handle_messages(request: AnthropicRequest):
    logger.info(f"[🚀] Received request from Claude Code for model: {request.model} (tools={len(request.tools or [])} max_tokens={request.max_tokens} stream={request.stream})")
    try:
        provider = provider_router.get_provider_for_anthropic(request.model)
        anthropic_req = request
        provider_request_body = await provider.translate_request(anthropic_req)
        is_agentic = bool(anthropic_req.tools)

        def _is_empty_or_narration(text: str, has_tool_use: bool) -> bool:
            if has_tool_use:
                return False
            if not text or not text.strip():
                return True
            return looks_like_action_narration(text)

        if request.stream:
            async def event_generator():
                nonlocal provider_request_body, anthropic_req
                base_max = anthropic_req.max_tokens
                for attempt in range(AGENTIC_RETRY_ATTEMPTS):
                    buf: list = []
                    truncated = False
                    try:
                        async for ev in provider.stream(provider_request_body):
                            buf.append(ev)
                            if ev.data.get("type") == "message_delta" and ev.data.get("delta", {}).get("stop_reason") == "max_tokens":
                                truncated = True
                    except httpx.HTTPStatusError as e:
                        error_detail = e.response.text if hasattr(e, 'response') else str(e)
                        error_msg = f"Provider API Error {e.response.status_code}: {error_detail[:2000]}"
                        log_error(error_msg)
                        yield f'event: error\ndata: {{"type": "error", "error": {{"type": "api_error", "message": {json.dumps(error_msg)}}}}}\n\n'
                        return
                    except Exception as e:
                        error_msg = str(e)[:2000]
                        log_error(error_msg)
                        yield f'event: error\ndata: {{"type": "error", "error": {{"type": "api_error", "message": {json.dumps(error_msg)}}}}}\n\n'
                        return

                    saw_stop = any(ev.data.get("type") == "message_stop" for ev in buf)
                    premature = not buf or not saw_stop
                    has_tool = has_native_tool_call(buf)
                    text = stream_text(buf)
                    should_retry = is_agentic and (
                        (not has_tool and (premature or truncated or _is_empty_or_narration(text, has_tool)))
                        or truncated
                    )
                    if not should_retry or attempt == AGENTIC_RETRY_ATTEMPTS - 1:
                        if premature:
                            log_warn(f"[⚠️] Claude Code stream premature cut (no message_stop) attempt {attempt+1} text_preview={_short_preview(text)!r} has_tool={has_tool}")
                        if truncated:
                            log_warn(f"[⚠️] Claude Code stream truncated (max_tokens) attempt {attempt+1} text_preview={_short_preview(text)!r}")
                        elif not premature and not has_tool and text:
                            logger.info(f"[📝] Claude Code stop={_short_preview(text)!r} (no tool, will NOT retry)")
                        for ev in buf:
                            yield ev.format()
                        return
                    reason = "premature cut" if premature else ("truncated" if truncated else "empty/narration without tool call")
                    if truncated:
                        cur = getattr(anthropic_req, "_retry_max_tokens", None) or base_max or 4096
                        nxt = cur * 2
                        if nxt <= cur:
                            nxt = cur + 2048
                        nxt = min(nxt, 64000)
                        if nxt < cur:
                            nxt = cur
                        anthropic_req._retry_max_tokens = nxt  # type: ignore
                        logger.info(f"[🔁] Claude Code truncated → bumping max_tokens to {nxt} before retry {attempt+2}/{AGENTIC_RETRY_ATTEMPTS} (was {cur})")
                    logger.info(f"[🔁] Claude Code {reason}, retrying {attempt+1}/{AGENTIC_RETRY_ATTEMPTS} text_preview={_short_preview(text)!r}")
                    if isinstance(anthropic_req.system, list):
                        anthropic_req.system.append({"type": "text", "text": AGENTIC_NUDGE})
                    else:
                        anthropic_req.system = ((anthropic_req.system or "") + "\n\n" + AGENTIC_NUDGE).strip()
                    provider_request_body = await provider.translate_request(anthropic_req)

            return StreamingResponse(event_generator(), media_type="text/event-stream")
        else:
            from proxy.responses_ingress import response_text
            base_max_ns = anthropic_req.max_tokens
            for attempt in range(AGENTIC_RETRY_ATTEMPTS):
                response = await provider.generate(provider_request_body)
                has_tool = any(b.get("type") == "tool_use" for b in response.content)
                text = response_text(response)
                truncated = response.stop_reason == "max_tokens"
                should_retry = is_agentic and (
                    (not has_tool and (truncated or _is_empty_or_narration(text, has_tool))) or truncated
                )
                if not should_retry or attempt == AGENTIC_RETRY_ATTEMPTS - 1:
                    if truncated:
                        log_warn(f"[⚠️] Claude Code non-stream truncated (max_tokens) — surfacing to client text_preview={_short_preview(text)!r}")
                    elif not has_tool and text and is_agentic:
                        logger.info(f"[📝] Claude Code stop (no tool) sr={response.stop_reason} text_preview={_short_preview(text)!r}")
                    return response.model_dump(exclude_none=True)
                if truncated:
                    nxt = (getattr(anthropic_req, "_retry_max_tokens", None) or base_max_ns or 4096) * 2
                    nxt = min(nxt, 16384)
                    anthropic_req._retry_max_tokens = nxt  # type: ignore
                    logger.info(f"[🔁] Claude Code truncated non-stream → bumping max_tokens to {nxt}")
                reason = "truncated" if truncated else "empty/narration"
                logger.info(f"[🔁] Claude Code {reason} without tool call, retrying {attempt+1}/{AGENTIC_RETRY_ATTEMPTS} text_preview={_short_preview(text)!r}")
                if isinstance(anthropic_req.system, list):
                    anthropic_req.system.append({"type": "text", "text": AGENTIC_NUDGE})
                else:
                    anthropic_req.system = ((anthropic_req.system or "") + "\n\n" + AGENTIC_NUDGE).strip()
                provider_request_body = await provider.translate_request(anthropic_req)

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text if hasattr(e, 'response') else str(e)
        error_msg = f"Provider API Error {e.response.status_code}: {error_detail[:2000]}"
        log_error(error_msg)
        return anthropic_error(e.response.status_code, error_msg)
    except Exception as e:
        return anthropic_error(500, str(e)[:2000])


@router.post("/v1/messages/count_tokens")
@router.post("/v1/messages/count_tokens/")
async def count_tokens(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    token_count = 0
    messages = body.get("messages", [])
    system = body.get("system", "")
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            token_count += len(content.split()) * 2
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    token_count += len(text.split()) * 2
    if isinstance(system, str):
        token_count += len(system.split()) * 2
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                token_count += len(block.get("text", "").split()) * 2
    if token_count == 0:
        token_count = 100
    return {"input_tokens": token_count}
