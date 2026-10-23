import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse, JSONResponse
from config.logging import logger, log_error, log_warn
from models.openai_compat import OpenAIRequest
from proxy.router import provider_router
from proxy.errors import openai_error
from proxy.openai_ingress import (
    openai_chat_to_anthropic,
    anthropic_response_to_openai_chat,
    anthropic_events_to_openai_stream,
    openai_error_line,
)
from proxy.retry import AGENTIC_RETRY_ATTEMPTS
from proxy.responses_ingress import (
    has_native_tool_call,
    stream_text,
    looks_like_action_narration,
    response_text,
    AGENTIC_NUDGE,
    _is_garbled,
)

router = APIRouter()


def _short_preview(text: str, n: int = 120) -> str:
    t = text.strip().replace("\n", " ")
    return (t[:n] + "…") if len(t) > n else t


def _is_empty_or_narration(text: str, has_tool_use: bool) -> bool:
    if has_tool_use:
        return False
    if not text or not text.strip():
        return True
    return looks_like_action_narration(text)


def _strip_response_format(body) -> bool:
    """Drop a response_format the upstream rejected. Returns True if one was removed."""
    if isinstance(body, dict) and body.pop("response_format", None) is not None:
        return True
    return False


def _bump_max_tokens(anthropic_req, base_max, cap: int) -> int:
    cur = getattr(anthropic_req, "_retry_max_tokens", None) or base_max or 4096
    nxt = cur * 2
    if nxt <= cur:
        nxt = cur + 2048
    nxt = min(nxt, cap)
    anthropic_req._retry_max_tokens = nxt  # type: ignore
    return nxt


def _append_nudge(anthropic_req) -> None:
    if isinstance(anthropic_req.system, list):
        anthropic_req.system.append({"type": "text", "text": AGENTIC_NUDGE})
    else:
        anthropic_req.system = ((anthropic_req.system or "") + "\n\n" + AGENTIC_NUDGE).strip()


async def _replay(events):
    for ev in events:
        yield ev


@router.post("/v1/chat/completions")
async def handle_chat_completions(request: OpenAIRequest):
    logger.info(f"[🤖] Received request from OpenAI-compatible client (Codex) for model: {request.model}")
    try:
        anthropic_request = openai_chat_to_anthropic(request)
        provider = provider_router.get_provider_for_openai(anthropic_request.model)
        provider_request_body = await provider.translate_request(anthropic_request)
        # AdCraft-style requests offer tools → treat as agentic and retry when the
        # model narrates or returns nothing instead of calling the tool.
        is_agentic = bool(anthropic_request.tools)

        if request.stream:
            async def event_generator():
                nonlocal provider_request_body, anthropic_request
                base_max = anthropic_request.max_tokens
                rf_rejected = False
                for attempt in range(AGENTIC_RETRY_ATTEMPTS):
                    buf: list = []
                    truncated = False
                    try:
                        async for ev in provider.stream(provider_request_body):
                            buf.append(ev)
                            if ev.data.get("type") == "message_delta" and ev.data.get("delta", {}).get("stop_reason") == "max_tokens":
                                truncated = True
                    except httpx.HTTPStatusError as e:
                        status = e.response.status_code if e.response is not None else 0
                        if status >= 400 and not rf_rejected and _strip_response_format(provider_request_body):
                            rf_rejected = True
                            log_warn(f"[↩️] Upstream rejected response_format ({status}) — retrying without it")
                            continue
                        error_detail = e.response.text if hasattr(e, 'response') else str(e)
                        error_msg = f"Provider API Error {status}: {error_detail[:2000]}"
                        log_error(error_msg)
                        yield openai_error_line(error_msg)
                        yield "data: [DONE]\n\n"
                        return
                    except Exception as e:
                        log_error(f"chat.completions stream failed: {e}", e)
                        yield openai_error_line(str(e)[:2000])
                        yield "data: [DONE]\n\n"
                        return

                    saw_stop = any(ev.data.get("type") == "message_stop" for ev in buf)
                    premature = not buf or not saw_stop
                    has_tool = has_native_tool_call(buf)
                    text = stream_text(buf)
                    garbled = not has_tool and _is_garbled(text)
                    should_retry = is_agentic and (
                        (not has_tool and (premature or truncated or _is_empty_or_narration(text, has_tool) or garbled))
                        or truncated
                        or garbled
                    )
                    if not should_retry or attempt == AGENTIC_RETRY_ATTEMPTS - 1:
                        if premature:
                            log_warn(f"[⚠️] chat.completions stream premature cut (no message_stop) attempt {attempt+1} text_preview={_short_preview(text)!r} has_tool={has_tool}")
                        if truncated:
                            log_warn(f"[⚠️] chat.completions stream truncated (max_tokens) attempt {attempt+1} text_preview={_short_preview(text)!r}")
                        elif not premature and not has_tool and text:
                            logger.info(f"[📝] chat.completions stop={_short_preview(text)!r} (no tool, will NOT retry)")
                        async for line in anthropic_events_to_openai_stream(_replay(buf), request.model):
                            yield line
                        return

                    reason = "garbled without tool call" if garbled else (
                        "premature cut" if premature else ("truncated" if truncated else "empty/narration without tool call")
                    )
                    if truncated:
                        nxt = _bump_max_tokens(anthropic_request, base_max, 64000)
                        logger.info(f"[🔁] chat.completions truncated → bumping max_tokens to {nxt} before retry {attempt+2}/{AGENTIC_RETRY_ATTEMPTS}")
                    logger.info(f"[🔁] chat.completions {reason}, retrying {attempt+1}/{AGENTIC_RETRY_ATTEMPTS} text_preview={_short_preview(text)!r}")
                    _append_nudge(anthropic_request)
                    provider_request_body = await provider.translate_request(anthropic_request)
                    if rf_rejected:
                        _strip_response_format(provider_request_body)

            return StreamingResponse(event_generator(), media_type="text/event-stream")
        else:
            base_max = anthropic_request.max_tokens
            rf_rejected = False
            for attempt in range(AGENTIC_RETRY_ATTEMPTS):
                try:
                    response = await provider.generate(provider_request_body)
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code if e.response is not None else 0
                    if status >= 400 and not rf_rejected and _strip_response_format(provider_request_body):
                        rf_rejected = True
                        log_warn(f"[↩️] Upstream rejected response_format ({status}) — retrying without it")
                        continue
                    raise

                has_tool = any(b.get("type") == "tool_use" for b in response.content)
                text = response_text(response)
                truncated = response.stop_reason == "max_tokens"
                garbled = not has_tool and _is_garbled(text)
                should_retry = is_agentic and (
                    (not has_tool and (truncated or _is_empty_or_narration(text, has_tool) or garbled))
                    or truncated
                    or garbled
                )
                if not should_retry or attempt == AGENTIC_RETRY_ATTEMPTS - 1:
                    if truncated:
                        log_warn(f"[⚠️] chat.completions non-stream truncated (max_tokens) — surfacing to client text_preview={_short_preview(text)!r}")
                    elif not has_tool and text and is_agentic:
                        logger.info(f"[📝] chat.completions stop (no tool) sr={response.stop_reason} text_preview={_short_preview(text)!r}")
                    return anthropic_response_to_openai_chat(response, request.model)

                if truncated:
                    nxt = _bump_max_tokens(anthropic_request, base_max, 16384)
                    logger.info(f"[🔁] chat.completions truncated non-stream → bumping max_tokens to {nxt}")
                reason = "garbled without tool call" if garbled else ("truncated" if truncated else "empty/narration")
                logger.info(f"[🔁] chat.completions {reason} without tool call, retrying {attempt+1}/{AGENTIC_RETRY_ATTEMPTS} text_preview={_short_preview(text)!r}")
                _append_nudge(anthropic_request)
                provider_request_body = await provider.translate_request(anthropic_request)
                if rf_rejected:
                    _strip_response_format(provider_request_body)

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text if hasattr(e, 'response') else str(e)
        error_msg = f"Provider API Error {e.response.status_code}: {error_detail[:2000]}"
        log_error(error_msg)
        return openai_error(e.response.status_code, error_msg)
    except Exception as e:
        return openai_error(500, str(e)[:2000])
