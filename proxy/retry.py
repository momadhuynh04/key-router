import httpx
from typing import Callable, List
from config.logging import logger, log_error, log_warn
from models.events import SSEEvent
from proxy.responses_ingress import has_native_tool_call, stream_text, looks_like_action_narration, AGENTIC_NUDGE

AGENTIC_RETRY_ATTEMPTS = 3


def _short_preview(text: str, n: int = 120) -> str:
    t = text.strip().replace("\n", " ")
    return (t[:n] + "…") if len(t) > n else t


async def retry_generate(
    anthropic_req,
    provider,
    provider_body: dict,
    is_agentic: bool,
    should_retry_fn: Callable,
    max_attempts: int = AGENTIC_RETRY_ATTEMPTS,
):
    for attempt in range(max_attempts):
        response = await provider.generate(provider_body)
        should_retry = should_retry_fn(response, attempt)
        if not should_retry or attempt == max_attempts - 1:
            return response, attempt
        cur = getattr(anthropic_req, "_retry_max_tokens", None) or anthropic_req.max_tokens or 4096
        nxt = min(cur * 2 if cur * 2 > cur else cur + 2048, 16384)
        anthropic_req._retry_max_tokens = nxt  # type: ignore
        logger.info(f"[🔁] Generate retry {attempt+1}/{max_attempts} bumping max_tokens to {nxt}")
        if isinstance(anthropic_req.system, list):
            anthropic_req.system.append({"type": "text", "text": AGENTIC_NUDGE})
        else:
            anthropic_req.system = ((anthropic_req.system or "") + "\n\n" + AGENTIC_NUDGE).strip()
        provider_body = await provider.translate_request(anthropic_req)
    raise RuntimeError("unreachable")


async def collect_stream_with_retry(
    anthropic_req,
    provider,
    provider_body: dict,
    is_agentic: bool,
    should_retry_fn: Callable,
    tool_schemas: dict | None = None,
    max_attempts: int = AGENTIC_RETRY_ATTEMPTS,
):
    base_max = anthropic_req.max_tokens
    last_buf: List[SSEEvent] = []
    for attempt in range(max_attempts):
        buf: List[SSEEvent] = []
        truncated = False
        try:
            async for ev in provider.stream(provider_body):
                buf.append(ev)
                if ev.data.get("type") == "message_delta" and ev.data.get("delta", {}).get("stop_reason") == "max_tokens":
                    truncated = True
        except httpx.HTTPStatusError as e:
            raise
        except Exception:
            raise

        text = stream_text(buf)
        saw_stop = any(ev.data.get("type") == "message_stop" for ev in buf)
        premature = not buf or not saw_stop
        has_tool = has_native_tool_call(buf)

        def _is_empty_or_narration(t: str, ht: bool) -> bool:
            if ht:
                return False
            if not t or not t.strip():
                return True
            return looks_like_action_narration(t)

        should_retry = should_retry_fn(
            buf=buf, text=text, has_tool=has_tool, truncated=truncated,
            premature=premature, empty_or_narration=_is_empty_or_narration(text, has_tool),
            is_agentic=is_agentic,
        )
        if not should_retry or attempt == max_attempts - 1:
            return buf, attempt, truncated, premature, has_tool, text

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
            logger.info(f"[🔁] Truncated → bumping max_tokens to {nxt} before retry {attempt+2}/{max_attempts} (was {cur})")
        logger.info(f"[🔁] {reason}, retrying {attempt+1}/{max_attempts} text_preview={_short_preview(text)!r}")
        if isinstance(anthropic_req.system, list):
            anthropic_req.system.append({"type": "text", "text": AGENTIC_NUDGE})
        else:
            anthropic_req.system = ((anthropic_req.system or "") + "\n\n" + AGENTIC_NUDGE).strip()
        provider_body = await provider.translate_request(anthropic_req)
        last_buf = buf

    return last_buf, max_attempts - 1, False, False, False, ""
