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
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from bridge import directives, registry, sse
from bridge.config import (
    MODEL_ID,
    AgentConfigError,
    AgentSpec,
    BridgeConfig,
    ensure_config,
    find_agent,
)
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


def _image_block(value: Any) -> Optional[Dict[str, Any]]:
    """Turn an OpenAI/Responses image part into an Anthropic image block."""
    if isinstance(value, dict):
        value = value.get("url") or value.get("image_url") or value.get("file_id")
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()

    if url.startswith("data:"):
        header, sep, data = url.partition(",")
        if not sep or ";base64" not in header or not data:
            return None
        media_type = header[len("data:"):].split(";", 1)[0] or "image/png"
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}

    if url.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": url}}

    return None


def _last_user_content(messages: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """Return the text and image blocks of the last user message."""
    for msg in reversed(messages or []):
        if not isinstance(msg, dict) or (msg.get("role") or "") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content, []
        if isinstance(content, list):
            texts: List[str] = []
            images: List[Dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype in ("text", "input_text"):
                    text = part.get("text") or ""
                    if text:
                        texts.append(text)
                elif ptype in ("image_url", "input_image"):
                    block = _image_block(part.get("image_url") or part.get("url"))
                    if block:
                        images.append(block)
            return "\n".join(texts), images
    return "", []


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
    lines += [
        "",
        "Start one with `/start <id>`, stop it with `/stop`.",
        "Run a one-off helper without rebinding the chat with `/call <id> <prompt>`.",
    ]
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


HISTORY_MAX_MESSAGES = 80
HISTORY_MAX_CHARS = 24000
HANDOFF_RESULT_CHARS = 4000


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("text", "input_text"):
                text = (part.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif ptype in ("image_url", "input_image", "image"):
                parts.append("[image attached]")
        return "\n".join(parts)
    return ""


def _history_text(
    messages: Any,
    max_messages: int = HISTORY_MAX_MESSAGES,
    max_chars: int = HISTORY_MAX_CHARS,
) -> str:
    """Format the Open WebUI chat history for a one-off sub-agent prompt."""
    rows: List[str] = []
    for msg in list(messages or [])[-max_messages:]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _content_text(msg.get("content"))
        if text:
            rows.append(f"{role}: {text}")
    if not rows:
        return ""
    out = "\n\n".join(rows)
    if len(out) > max_chars:
        out = "…(older messages truncated)…\n\n" + out[-max_chars:]
    return out


def _subagent_prompt(agent: AgentSpec, task: str, history: str) -> str:
    lines = [
        f"You are running as a one-off sub-agent `{agent.id}` invoked by a slash command.",
        "You are NOT bound to this chat; another agent owns it. Do your task, then end your turn.",
        "This run is independent: use the chat history below only as context.",
        "End with a short summary of what you did — the main agent only sees that summary.",
        "",
    ]
    if history:
        lines += ["[Chat history — may be truncated]", history, ""]
    lines += ["[Task from the user]", task]
    return "\n".join(lines)


def _handoff_prefix(runs: List[dict]) -> str:
    lines = [
        "[Sub-agent handoff — runs you did not see. They are finished one-off processes, "
        "not live peer sessions; do not message them.]"
    ]
    for run in runs:
        status = "ok" if run.get("ok", True) else "error"
        workdir = run.get("workdir") or ""
        head = f"- `{run.get('agent_id')}` ({status}"
        head += f" · workdir `{workdir}`" if workdir else ""
        head += f") · task: {(run.get('task') or '').strip()}"
        lines.append(head)

        steps = run.get("steps") or []
        if steps:
            rendered = [
                f"`{s.get('tool')}` {s.get('input')}"
                for s in steps
                if isinstance(s, dict)
            ]
            lines.append("  Steps: " + " · ".join(rendered))

        result = (run.get("result") or "").strip()
        if result:
            if len(result) > HANDOFF_RESULT_CHARS:
                result = result[:HANDOFF_RESULT_CHARS] + "…"
            lines.append("  Result: " + result.replace("\n", "\n  "))
    lines.append("[End sub-agent handoff]")
    return "\n".join(lines)


def _answer_prefix(pending: dict, answer: str) -> str:
    """Frame the next user message as the answer to the agent's `ask_user` call."""
    lines = ["[User answered AskUserQuestion]"]
    question = (pending.get("question") or "").strip()
    if question:
        lines.append(f"Question: {question}")
    options = [
        o.get("label") for o in (pending.get("options") or []) if o.get("label")
    ]
    if options:
        lines.append("Options: " + "; ".join(f"{i}) {label}" for i, label in enumerate(options, 1)))
    lines.append(f"Answer: {answer}")
    return "\n".join(lines)


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
    images: Optional[List[Dict[str, Any]]] = None,
) -> AsyncIterator[str]:
    """Run one CLI process, feeding events into `state` and yielding reply text."""
    images = images or []
    argv, cwd = registry.build_invocation(
        agent,
        cfg,
        session_id=session_id,
        resume=bool(session_id),
        stream_input=bool(images),
    )
    payload = registry.claude_stdin(prompt, images) if images else prompt
    logger.info(
        "spawn agent=%s cli=%s cwd=%s resume=%s session=%s images=%d argv=%s",
        agent.id, agent.cli, cwd, bool(session_id), session_id, len(images), argv,
    )
    async for event in run_cli(cfg, argv, cwd, payload, chat_key, agent.id, TURNS):
        for piece in sse.feed(event, state):
            yield piece


async def _run_turn(
    cfg: BridgeConfig,
    agent: AgentSpec,
    prompt: str,
    chat_key: str,
    session_id: Optional[str],
    images: Optional[List[Dict[str, Any]]] = None,
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
        async for piece in _attempt(
            cfg, agent, prompt, chat_key, session_id, state, images
        ):
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
        async for piece in _attempt(
            cfg, agent, prompt, chat_key, None, state, images
        ):
            yield piece

    if state.session_id:
        if resuming and state.session_id != session_id:
            logger.warning(
                "session changed during resume chat=%s old=%s new=%s",
                chat_key, session_id, state.session_id,
            )
        store.set_session_id(chat_key, state.session_id)

    if state.ask:
        store.set_pending(chat_key, state.ask)


async def _run_sub_turn(
    cfg: BridgeConfig,
    agent: AgentSpec,
    prompt: str,
    chat_key: str,
    task: str,
    session_id: Optional[str] = None,
    fallback_prompt: Optional[str] = None,
) -> AsyncIterator[str]:
    """Run a one-off sub-agent. Never touches the chat's binding or main session."""
    resuming = bool(session_id)
    state = sse.TurnState()
    chunks: List[str] = []
    error_text = ""

    try:
        try:
            async for piece in _attempt(
                cfg, agent, prompt, chat_key, session_id, state
            ):
                chunks.append(piece)
                yield piece
        except RunError as e:
            if not resuming:
                raise
            logger.warning(
                "sub-agent resume failed chat=%s agent=%s session=%s — starting fresh",
                chat_key, agent.id, session_id,
            )
            state = sse.TurnState()
            yield "_(could not resume the sub-agent session — started fresh)_\n\n"
            async for piece in _attempt(
                cfg, agent, fallback_prompt or prompt, chat_key, None, state
            ):
                chunks.append(piece)
                yield piece
    except Exception as e:  # noqa: BLE001 — record the failure for the main agent
        error_text = str(e)
        raise
    finally:
        if state.ask:
            pending = dict(state.ask)
            pending.update({
                "sub_agent_id": agent.id,
                "sub_session_id": state.session_id,
                "task": task,
            })
            store.set_pending(chat_key, pending)
        else:
            result = (state.final_text or "").strip() or "".join(chunks).strip()
            store.add_subrun(chat_key, {
                "agent_id": agent.id,
                "workdir": agent.workdir,
                "task": task,
                "steps": state.steps,
                "result": error_text or result,
                "ok": not error_text and not state.is_error,
            })


async def _produce_call(
    parsed: directives.Parsed,
    chat_key: str,
    cfg: BridgeConfig,
    messages: Any,
) -> AsyncIterator[str]:
    if not parsed.agent_id:
        yield "Usage: `/call <agent-id> <prompt>`\n\n" + agents_text(cfg)
        return

    agent = find_agent(cfg, parsed.agent_id)
    if agent is None:
        yield f"No agent named `{parsed.agent_id}`.\n\n" + agents_text(cfg)
        return

    task = parsed.text.strip()
    if not task:
        yield "Usage: `/call <agent-id> <prompt>` — the prompt is required."
        return

    if agent.cli != "claude":
        yield (
            f"⚠️ Agent `{agent.id}` uses the **{agent.cli}** CLI — Codex support lands in "
            "Phase 2; only Claude Code runs today."
        )
        return

    if not LIMITER.try_acquire():
        yield _concurrency_notice(LIMITER.limit)
        return

    history = _history_text((messages or [])[:-1])
    prompt = _subagent_prompt(agent, task, history)
    try:
        yield f"🧩 **{agent.id}** · one-off sub-agent\n\n"
        async for piece in _run_sub_turn(cfg, agent, prompt, chat_key, task):
            yield piece
    except TurnTimeout as e:
        yield f"\n\n⚠️ {e}"
    except RunError as e:
        yield f"\n\n⚠️ {e}"
    except AgentConfigError as e:
        yield f"\n\n⚠️ Invalid agent configuration: {e}"
    except Exception as e:  # noqa: BLE001
        yield f"\n\n⚠️ Unexpected error: {e}"
    finally:
        LIMITER.release()


async def _produce_sub_answer(
    parsed: directives.Parsed,
    pending: dict,
    chat_key: str,
    cfg: BridgeConfig,
    messages: Any,
) -> AsyncIterator[str]:
    """Continue a sub-agent that stopped on ask_user. The main binding is untouched."""
    sub = find_agent(cfg, pending.get("sub_agent_id") or "")
    store.set_pending(chat_key, None)
    if sub is None or sub.cli != "claude":
        yield f"Sub-agent `{pending.get('sub_agent_id')}` is no longer available. Use `/call` again."
        return

    task = pending.get("task") or ""
    answer = parsed.text.strip()
    prompt = _answer_prefix(pending, answer)
    fallback = (
        _subagent_prompt(sub, task, _history_text((messages or [])[:-1]))
        + "\n\n"
        + prompt
    )

    if not LIMITER.try_acquire():
        yield _concurrency_notice(LIMITER.limit)
        return
    try:
        async for piece in _run_sub_turn(
            cfg,
            sub,
            prompt,
            chat_key,
            task,
            session_id=pending.get("sub_session_id"),
            fallback_prompt=fallback,
        ):
            yield piece
    except TurnTimeout as e:
        yield f"\n\n⚠️ {e}"
    except RunError as e:
        yield f"\n\n⚠️ {e}"
    except AgentConfigError as e:
        yield f"\n\n⚠️ Invalid agent configuration: {e}"
    except Exception as e:  # noqa: BLE001
        yield f"\n\n⚠️ Unexpected error: {e}"
    finally:
        LIMITER.release()


async def produce(
    parsed: directives.Parsed,
    chat_key: str,
    cfg: BridgeConfig,
    images: Optional[List[Dict[str, Any]]] = None,
    messages: Any = None,
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

    if parsed.kind == "call":
        async for piece in _produce_call(parsed, chat_key, cfg, messages):
            yield piece
        return

    # A sub-agent stopped on ask_user: the reply continues that run, not the main agent.
    pending = store.get_pending(chat_key)
    if parsed.kind == "prompt" and pending and pending.get("sub_agent_id"):
        async for piece in _produce_sub_answer(parsed, pending, chat_key, cfg, messages):
            yield piece
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
    if parsed.kind == "prompt":
        runs = store.take_subruns(chat_key)
        if runs:
            prompt = _handoff_prefix(runs) + "\n\n" + prompt
        if pending:
            prompt = _answer_prefix(pending, prompt)
            store.set_pending(chat_key, None)

    if not prompt and not images:
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
            async for piece in _run_turn(
                cfg, agent, prompt, chat_key, session_id, images
            ):
                yield piece
        except TurnTimeout as e:
            yield f"\n\n⚠️ {e}"
        except RunError as e:
            yield f"\n\n⚠️ {e}"
        except AgentConfigError as e:
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
        text, images = _last_user_content(messages)
        last = messages[-1] if isinstance(messages, list) and messages else {}
        last_content = last.get("content") if isinstance(last, dict) else None
        part_types = (
            [p.get("type") for p in last_content if isinstance(p, dict)]
            if isinstance(last_content, list)
            else [type(last_content).__name__]
        )
        logger.info(
            "request chat=%s parts=%s images=%d files=%d",
            chat_key,
            part_types,
            len(images),
            len(last.get("files") or []) if isinstance(last, dict) else 0,
        )
        parsed = directives.parse(text)
        want_stream = bool(body.get("stream"))
        cid = f"chatcmpl-{uuid.uuid4().hex}"

        if not want_stream:
            parts: List[str] = []
            async for piece in produce(parsed, chat_key, cfg, images, messages):
                parts.append(piece)
            return JSONResponse(sse.completion(cid, MODEL_ID, "".join(parts)))

        async def event_stream() -> AsyncIterator[str]:
            yield sse.role_chunk(cid, MODEL_ID)
            try:
                async for piece in produce(parsed, chat_key, cfg, images, messages):
                    yield sse.text_chunk(cid, MODEL_ID, piece)
            except Exception as e:  # noqa: BLE001
                yield sse.text_chunk(cid, MODEL_ID, f"\n\n⚠️ {e}")
            yield sse.finish_chunk(cid, MODEL_ID)
            yield sse.DONE_LINE

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app
