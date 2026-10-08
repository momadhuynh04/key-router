"""FastAPI app for agent-bridge.

The API surface is OpenAI-compatible but serves exactly one model: `key-router-agents`.
Which agent actually runs is decided by a directive in the message
(`/start`, `/stop`, `/agents`).
"""
from __future__ import annotations

import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from bridge import directives, registry, sse
from bridge.config import MODEL_ID, AgentSpec, BridgeConfig, ensure_config, find_agent
from bridge.runner import TURNS, RunError, TurnTimeout, run_cli
from bridge.sessions import GLOBAL_KEY, store

logger = logging.getLogger("agent-bridge")


class Limiter:
    """Counts running turns. The event loop is single-threaded, so no lock is needed."""

    def __init__(self) -> None:
        self.limit = 2
        self.active = 0

    def configure(self, limit: int) -> None:
        self.limit = max(1, int(limit))

    def try_acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


LIMITER = Limiter()


def _error(status: int, message: str, err_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "code": status}},
    )


def _authorized(request: Request, cfg: BridgeConfig) -> bool:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(header[7:].strip(), cfg.token)


def _chat_key(request: Request) -> str:
    return request.headers.get("x-openwebui-chat-id") or GLOBAL_KEY


def _last_user_text(messages: Any) -> str:
    for msg in reversed(messages or []):
        if not isinstance(msg, dict) or (msg.get("role") or "") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") in ("text", "input_text")
            ]
            return "\n".join(x for x in parts if x)
    return ""


def agents_text(cfg: BridgeConfig) -> str:
    if not cfg.agents:
        return (
            "No agents configured yet.\n\n"
            "Open the key-router dashboard -> **Agents** tab to add one."
        )
    lines = ["**Configured agents:**", ""]
    for agent in cfg.agents:
        mark = " · default" if agent.id == cfg.default_agent else ""
        lines.append(f"- `{agent.id}`{mark} — {agent.name or agent.id}")
        lines.append(
            f"  - cli: {agent.cli} · model: {agent.model or '(default)'} · "
            f"permission: {agent.permission} · effort: {agent.effort} · tools: {agent.tools}"
        )
        lines.append(f"  - workdir: `{agent.workdir}`")
    lines += ["", "Start one with `/start <id>`, stop it with `/stop`."]
    return "\n".join(lines)


def start_text(agent: AgentSpec, cfg: BridgeConfig, had_session: bool) -> str:
    state = "resuming existing session" if had_session else "new session"
    lines = [
        f"✅ Bound agent `{agent.id}` ({state}).",
        "",
        f"- cli: **{agent.cli}** · model: `{agent.model or '(default)'}`",
        f"- permission: **{agent.permission}** · effort: **{agent.effort}** · tools: **{agent.tools}**",
        f"- workdir: `{agent.workdir}`",
        "",
        "Send your next message to start working. Use `/stop` to stop.",
    ]
    return "\n".join(lines)


def _concurrency_notice(limit: int) -> str:
    return f"{limit} turns are already running (the configured limit). Try again once one finishes."


async def _resolve_agent(
    parsed: directives.Parsed, chat_key: str, cfg: BridgeConfig
) -> tuple[Optional[AgentSpec], List[str], str]:
    """Return (agent, text to emit first, stop reason)."""
    if parsed.kind == "start":
        agent_id = parsed.agent_id or cfg.default_agent
        if not agent_id:
            return None, ["No agent selected. Use `/start <id>`.\n\n" + agents_text(cfg)], "no-agent"
        agent = find_agent(cfg, agent_id)
        if agent is None:
            return None, [f"No agent named `{agent_id}`.\n\n" + agents_text(cfg)], "unknown-agent"
        entry = store.get(chat_key)
        had = bool(entry and entry.get("agent_id") == agent.id and entry.get("session_id"))
        session_id = entry.get("session_id") if had else None
        store.bind(chat_key, agent.id, session_id)
        return agent, [start_text(agent, cfg, had)], "started"

    # kind == "prompt"
    entry = store.get(chat_key)
    if entry is None:
        if not cfg.default_agent:
            return None, ["No agent bound.\n\n" + agents_text(cfg)], "no-agent"
        agent = find_agent(cfg, cfg.default_agent)
        if agent is None:
            return None, ["`default_agent` does not exist.\n\n" + agents_text(cfg)], "no-agent"
        store.bind(chat_key, agent.id, None)
        return agent, [], "auto-started"

    agent = find_agent(cfg, entry["agent_id"])
    if agent is None:
        store.unbind(chat_key)
        return None, [
            f"Agent `{entry['agent_id']}` was deleted. Use `/start <id>` to pick another.\n\n"
            + agents_text(cfg)
        ], "agent-removed"
    return agent, [], "resumed"


async def _attempt(
    cfg: BridgeConfig,
    agent: AgentSpec,
    prompt: str,
    chat_key: str,
    session_id: Optional[str],
    state: sse.TurnState,
) -> AsyncIterator[str]:
    """Run one CLI process, feeding events into `state` and yielding reply text."""
    argv, cwd = registry.build_invocation(
        agent, cfg, session_id=session_id, resume=bool(session_id)
    )
    logger.info(
        "spawn agent=%s cli=%s cwd=%s resume=%s session=%s argv=%s",
        agent.id, agent.cli, cwd, bool(session_id), session_id, argv,
    )
    async for event in run_cli(cfg, argv, cwd, prompt, chat_key, agent.id, TURNS):
        for piece in sse.feed(event, state):
            yield piece


async def _run_turn(
    cfg: BridgeConfig, agent: AgentSpec, prompt: str, chat_key: str, session_id: Optional[str]
) -> AsyncIterator[str]:
    if agent.cli != "claude":
        yield (
            f"⚠️ Agent `{agent.id}` uses the **{agent.cli}** CLI — Codex support lands in "
            "Phase 2; only Claude Code runs today."
        )
        return

    resuming = bool(session_id)
    state = sse.TurnState()
    try:
        async for piece in _attempt(cfg, agent, prompt, chat_key, session_id, state):
            yield piece
    except RunError:
        if not resuming:
            raise
        # A stale session id (transcript deleted, workdir moved) must not brick the chat:
        # drop it and start a fresh session once.
        logger.warning(
            "resume failed chat=%s agent=%s session=%s — starting a new session",
            chat_key, agent.id, session_id,
        )
        store.bind(chat_key, agent.id, None)
        state = sse.TurnState()
        yield "_(could not resume the previous session — started a new one)_\n\n"
        async for piece in _attempt(cfg, agent, prompt, chat_key, None, state):
            yield piece

    if state.session_id:
        if resuming and state.session_id != session_id:
            logger.warning(
                "session changed during resume chat=%s old=%s new=%s",
                chat_key, session_id, state.session_id,
            )
        store.set_session_id(chat_key, state.session_id)


async def produce(
    parsed: directives.Parsed, chat_key: str, cfg: BridgeConfig
) -> AsyncIterator[str]:
    """Yield the reply text for one chat turn."""
    if parsed.kind == "agents":
        yield agents_text(cfg)
        return

    if parsed.kind == "stop":
        killed = TURNS.kill(chat_key)
        store.unbind(chat_key)
        yield "🛑 Agent stopped." if killed else "Nothing was running. Agent unbound."
        return

    agent, prefix, _reason = await _resolve_agent(parsed, chat_key, cfg)
    for piece in prefix:
        yield piece
    if agent is None:
        return

    # `/start` with no prompt only confirms the binding; nothing runs yet.
    if parsed.kind == "start" and not parsed.text.strip():
        return

    prompt = parsed.text.strip()
    if not prompt:
        return

    if not LIMITER.try_acquire():
        yield "\n\n" + _concurrency_notice(LIMITER.limit)
        return
    try:
        entry = store.get(chat_key) or {}
        session_id = entry.get("session_id")
        try:
            if prefix:
                yield "\n\n"
            async for piece in _run_turn(cfg, agent, prompt, chat_key, session_id):
                yield piece
        except TurnTimeout as e:
            yield f"\n\n⚠️ {e}"
        except RunError as e:
            yield f"\n\n⚠️ {e}"
        except ValueError as e:
            yield f"\n\n⚠️ Invalid agent configuration: {e}"
        except Exception as e:  # noqa: BLE001 — surface the error instead of an empty 500
            yield f"\n\n⚠️ Unexpected error: {e}"
    finally:
        LIMITER.release()


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        from bridge.runner import kill_all

        kill_all()

    app = FastAPI(title="agent-bridge", lifespan=lifespan)

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        try:
            cfg = ensure_config()
            count = len(cfg.agents)
            LIMITER.configure(cfg.max_concurrent_runs)
            port = cfg.port
        except Exception:
            count, port = 0, None
        return {
            "status": "ok",
            "agents": count,
            "port": port,
            "running_turns": len(TURNS.all()),
        }

    @app.get("/v1/models")
    async def list_models(request: Request):
        cfg = ensure_config()
        if not _authorized(request, cfg):
            return _error(401, "Invalid API key", "authentication_error")
        return {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "key-router",
                }
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        cfg = ensure_config()
        if not _authorized(request, cfg):
            return _error(401, "Invalid API key", "authentication_error")
        try:
            body = await request.json()
        except Exception:
            return _error(400, "Request body is not valid JSON")

        chat_key = _chat_key(request)
        messages = body.get("messages") or []
        parsed = directives.parse(_last_user_text(messages))
        want_stream = bool(body.get("stream"))
        cid = f"chatcmpl-{uuid.uuid4().hex}"

        if not want_stream:
            parts: List[str] = []
            async for piece in produce(parsed, chat_key, cfg):
                parts.append(piece)
            return JSONResponse(sse.completion(cid, MODEL_ID, "".join(parts)))

        async def event_stream() -> AsyncIterator[str]:
            yield sse.role_chunk(cid, MODEL_ID)
            try:
                async for piece in produce(parsed, chat_key, cfg):
                    yield sse.text_chunk(cid, MODEL_ID, piece)
            except Exception as e:  # noqa: BLE001
                yield sse.text_chunk(cid, MODEL_ID, f"\n\n⚠️ {e}")
            yield sse.finish_chunk(cid, MODEL_ID)
            yield sse.DONE_LINE

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app
