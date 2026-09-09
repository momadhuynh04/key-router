import json
import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from config.logging import logger, log_error, log_warn
from models.openai_compat import ResponsesRequest
from proxy.router import provider_router
from proxy.errors import openai_error
from proxy.responses_ingress import (
    responses_to_anthropic,
    anthropic_response_to_responses_object,
    anthropic_events_to_responses_stream,
    has_native_tool_call,
    stream_text,
    stream_events,
    response_text,
    looks_like_action_narration,
    AGENTIC_NUDGE,
    _unwrap_additional_tools,
    _is_garbled,
)
from proxy.retry import AGENTIC_RETRY_ATTEMPTS

router = APIRouter()


@router.post("/v1/responses")
async def handle_responses(request: ResponsesRequest):
    logger.info(f"[🤖] Received Responses API request from Codex for model: {request.model}")
    try:
        raw_tools: list = []
        raw_tools.extend(request.tools or [])
        if getattr(request, "additional_tools", None):
            raw_tools.extend(request.additional_tools or [])
        extra = getattr(request, "__pydantic_extra__", None) or {}
        if extra.get("additional_tools"):
            raw_tools.extend(extra["additional_tools"] or [])
        if isinstance(request.input, list):
            for _item in request.input:
                if isinstance(_item, dict) and _item.get("type") == "additional_tools":
                    raw_tools.extend(_item.get("tools", []) or [])
        flat_tools = _unwrap_additional_tools(raw_tools)
        tool_schemas = {
            t.get("name"): (t.get("parameters") or t.get("input_schema") or {})
            for t in flat_tools
            if isinstance(t, dict) and t.get("name")
        }
        if flat_tools:
            logger.info(f"[🔧] Codex tools unwrapped: {list(tool_schemas.keys())}")

        anthropic_request = responses_to_anthropic(request)
        provider = provider_router.get_provider_for_openai(anthropic_request.model)
        provider_request_body = await provider.translate_request(anthropic_request)

        if request.stream:
            async def event_generator():
                nonlocal provider_request_body
                for attempt in range(AGENTIC_RETRY_ATTEMPTS):
                    events_buffer = []
                    try:
                        async for ev in provider.stream(provider_request_body):
                            events_buffer.append(ev)
                    except httpx.HTTPStatusError as e:
                        error_detail = e.response.text if hasattr(e, 'response') else str(e)
                        error_msg = f"Provider API Error {e.response.status_code}: {error_detail[:2000]}"
                        log_error(error_msg)
                        yield f'event: response.failed\ndata: {json.dumps({"type": "response.failed", "response": {"error": {"code": "api_error", "message": error_msg}}})}\n\n'
                        return
                    except Exception as e:
                        log_error(str(e)[:2000])
                        yield f'event: response.failed\ndata: {json.dumps({"type": "response.failed", "response": {"error": {"code": "api_error", "message": str(e)[:2000]}}})}\n\n'
                        return

                    text = stream_text(events_buffer)
                    saw_stop = any(ev.data.get("type") == "message_stop" for ev in events_buffer)
                    premature_cut = not events_buffer or not saw_stop
                    truncated = any(
                        ev.data.get("type") == "message_delta" and ev.data.get("delta", {}).get("stop_reason") == "max_tokens"
                        for ev in events_buffer
                    )
                    empty_with_tools = bool(tool_schemas) and not text.strip() and not has_native_tool_call(events_buffer)
                    narrated = (
                        tool_schemas
                        and not has_native_tool_call(events_buffer)
                        and looks_like_action_narration(text)
                    )
                    garbled = bool(tool_schemas and not has_native_tool_call(events_buffer) and _is_garbled(text))
                    should_retry = (narrated or premature_cut or truncated or empty_with_tools or garbled)
                    if not should_retry or attempt == AGENTIC_RETRY_ATTEMPTS - 1:
                        if premature_cut:
                            log_warn(f"[⚠️] Upstream stream ended prematurely (no message_stop) after {attempt + 1} attempt(s)")
                        if truncated:
                            log_warn(f"[⚠️] Upstream truncated (max_tokens) after {attempt + 1} attempt(s)")
                        logger.info(f"[🔁] Attempt {attempt + 1}/{AGENTIC_RETRY_ATTEMPTS}")
                        async for line in anthropic_events_to_responses_stream(
                            stream_events(events_buffer), request.model, tool_schemas
                        ):
                            yield line
                        return
                    if garbled:
                        reason = "garbled output without tool call"
                    elif truncated:
                        reason = "truncated (max_tokens)"
                    elif premature_cut:
                        reason = "premature stream cut"
                    else:
                        reason = "narration without tool call"
                    logger.info(f"[🔁] {reason}, retrying ({attempt + 1}/{AGENTIC_RETRY_ATTEMPTS})")
                    anthropic_request.system = (
                        (anthropic_request.system or "") + "\n\n" + AGENTIC_NUDGE
                    ).strip()
                    provider_request_body = await provider.translate_request(anthropic_request)

            return StreamingResponse(event_generator(), media_type="text/event-stream")
        else:
            for attempt in range(AGENTIC_RETRY_ATTEMPTS):
                response = await provider.generate(provider_request_body)
                _rt = response_text(response)
                _has_tool = any(b.get("type") == "tool_use" for b in response.content)
                narrated = tool_schemas and not _has_tool and looks_like_action_narration(_rt)
                garbled_ns = bool(tool_schemas and not _has_tool and _is_garbled(_rt))
                truncated = response.stop_reason == "max_tokens"
                should_retry = narrated or truncated or garbled_ns
                if not should_retry or attempt == AGENTIC_RETRY_ATTEMPTS - 1:
                    if truncated:
                        log_warn(f"[⚠️] Codex non-stream truncated (max_tokens)")
                    return anthropic_response_to_responses_object(response, request.model, tool_schemas)
                if garbled_ns:
                    reason = "garbled output without tool call"
                else:
                    reason = "truncated (max_tokens)" if truncated else "narrated without tool call"
                logger.info(f"[🔁] Codex {reason}, retrying ({attempt + 1}/{AGENTIC_RETRY_ATTEMPTS})")
                anthropic_request.system = (
                    (anthropic_request.system or "") + "\n\n" + AGENTIC_NUDGE
                ).strip()
                provider_request_body = await provider.translate_request(anthropic_request)

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text if hasattr(e, 'response') else str(e)
        error_msg = f"Provider API Error {e.response.status_code}: {error_detail[:2000]}"
        log_error(error_msg)
        return openai_error(e.response.status_code, error_msg)
    except Exception as e:
        return openai_error(500, str(e)[:2000])
