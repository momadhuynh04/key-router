"""Headless Test lab: run Claude Code or Codex once and verify the result.

This lives in the proxy on purpose: the dashboard is already served here, and a
test run does not need the agent-bridge. It reuses the bridge's process runner and
argv builders so process-group cleanup, timeouts and CLI flags stay in one place.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import platform
import re
import shutil
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from bridge import config as bridge_config
from bridge import registry as bridge_registry
from bridge import runner as bridge_runner
from bridge import sse as bridge_sse

SCENARIO_ID = "quick-check"
OUTPUT_DIR = ".key-router-testlab"
HTML_NAME = "key-router-test.html"
MD_NAME = "key-router-review.md"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 100 * 1024
SHELL_LIMIT = 10
DEFAULT_TIMEOUT_SEC = 300
MAX_TIMEOUT_SEC = 1800
MAX_KEEP_RUNS = 20

CLI_IDS = ("claude", "codex")

IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

LANGUAGE_PROBES: Dict[str, tuple[str, List[str]]] = {
    "Python": ("python", ["python3", "python"]),
    "Node.js": ("node", ["node", "npm"]),
    "Go": ("go", ["go"]),
    "Rust": ("rust", ["rustc", "cargo"]),
    "Java": ("java", ["java", "javac"]),
    ".NET": ("dotnet", ["dotnet"]),
    "C": ("gcc", ["gcc", "clang"]),
    "C++": (r"c\+\+", ["g++", "clang++"]),
    "PHP": ("php", ["php"]),
    "Ruby": ("ruby", ["ruby"]),
    "Lua": ("lua", ["lua", "luajit"]),
    "Deno": ("deno", ["deno"]),
    "Bun": ("bun", ["bun"]),
}


@dataclass
class TestRun:
    id: str
    cli: str
    model: str
    workdir: str
    run_dir: str
    status: str = "running"
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    has_image: bool = False
    steps: List[Dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    transcript: str = ""
    session_id: str = ""
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    shell_count: int = 0
    checks: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    extra_files: List[Dict[str, Any]] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    denials: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class _CodexState:
    shell_ids: Set[str] = field(default_factory=set)
    failed: bool = False
    failure: str = ""
    last_error: str = ""


RUNS: "OrderedDict[str, TestRun]" = OrderedDict()
TASKS: Dict[str, asyncio.Task] = {}
ACTIVE_RUN_ID: Optional[str] = None


def cli_available(cli: str) -> bool:
    return shutil.which(cli) is not None


def detect_languages() -> List[str]:
    return [
        label
        for label, (_, commands) in LANGUAGE_PROBES.items()
        if any(shutil.which(command) for command in commands)
    ]


def build_prompt(run_id: str, has_image: bool) -> str:
    image_note = (
        "An image is attached to this message."
        if has_image
        else "No image is attached."
    )
    return f"""You are running a fast capability self-check inside the current working directory.
Use at most {SHELL_LIMIT} shell commands. Do not ask questions and do not install anything.

{image_note}
Output directory (already created): {OUTPUT_DIR}/{run_id}

Tasks:
1. If an image is attached, describe exactly what it shows in 1-2 sentences. If no image is attached, reply exactly NO_IMAGE.
2. Create `{HTML_NAME}` inside the output directory: a small, valid, self-contained HTML page with a heading and some CSS.
3. Identify the current OS and machine configuration: OS name/version, architecture, CPU, memory.
4. List the programming languages and toolchains currently installed and available on PATH (for example Python, Node, Go, Rust, Java, C/C++, .NET, PHP, Ruby).
5. Review the HTML file you just created and summarize the OS/language findings, then write `{MD_NAME}` inside the output directory. The review must mention `{HTML_NAME}`.

Be concise and finish quickly.
"""


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value or "")
    text = " ".join(text.split())
    return text[:limit] + "…" if len(text) > limit else text


def _read_text(path: Path, limit: int = MAX_ARTIFACT_BYTES) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", "replace")
    except OSError:
        return ""


def _check(check_id: str, label: str, status: str, detail: str) -> Dict[str, Any]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _validate_workdir(raw: str, cfg: bridge_config.BridgeConfig) -> Path:
    if not raw or not raw.strip():
        raise ValueError("workdir is required")
    agent = bridge_config.AgentSpec(
        id="testlab",
        cli="claude",
        workdir=raw.strip(),
        permission="ci",
    )
    try:
        resolved = bridge_config.resolve_workdir(agent, cfg)
    except bridge_config.AgentConfigError as exc:
        raise ValueError(str(exc)) from exc
    if not resolved.is_dir():
        raise ValueError(f"workdir is not a directory: {resolved}")
    return resolved


def _decode_image(media_type: str, data_base64: str, run_dir: Path):
    media_type = (media_type or "").strip().lower()
    if media_type not in IMAGE_EXTENSIONS:
        raise ValueError("image must be png, jpeg, webp or gif")
    if not data_base64:
        raise ValueError("image data is empty")
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image data is not valid base64") from exc
    if not raw:
        raise ValueError("image data is empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MiB")
    path = run_dir / f"image.{IMAGE_EXTENSIONS[media_type]}"
    path.write_bytes(raw)
    return path


def _evict_runs() -> None:
    while len(RUNS) > MAX_KEEP_RUNS:
        oldest_id, oldest = next(iter(RUNS.items()))
        if oldest.status == "running":
            return
        RUNS.pop(oldest_id, None)


def _shell_step_id(step: Dict[str, Any]) -> str:
    return str(step.get("id") or "")


def _upsert_shell_step(run: TestRun, item_id: str, step: Dict[str, Any]) -> None:
    for index, existing in enumerate(run.steps):
        if _shell_step_id(existing) == item_id:
            run.steps[index] = step
            return
    run.steps.append(step)


def _refresh_claude(run: TestRun, state: bridge_sse.TurnState) -> None:
    run.session_id = state.session_id or run.session_id
    run.cost_usd = state.cost_usd
    run.duration_ms = state.duration_ms
    run.final_text = state.final_text or run.final_text
    run.denials = [d for d in state.denials if isinstance(d, dict)]
    run.steps = [
        {
            "kind": "tool",
            "label": step.get("tool") or "tool",
            "detail": step.get("input") or "",
        }
        for step in state.steps
    ]
    run.shell_count = sum(1 for step in state.steps if step.get("tool") == "Bash")


def _feed_codex(run: TestRun, event: Dict[str, Any], state: _CodexState) -> None:
    event_type = event.get("type")
    if event_type == "thread.started":
        run.session_id = str(event.get("thread_id") or run.session_id)
        return
    if event_type == "turn.failed":
        error = event.get("error") or {}
        state.failed = True
        state.failure = str(error.get("message") or "turn failed")
        return
    if event_type == "error":
        message = str(event.get("message") or "")
        if message and not message.startswith("Reconnecting"):
            state.last_error = message
            run.steps.append({"kind": "status", "label": "error", "detail": message})
        return
    if event_type not in ("item.started", "item.updated", "item.completed"):
        return

    item = event.get("item") or {}
    item_type = item.get("type")
    item_id = str(item.get("id") or "")

    if item_type == "agent_message" and event_type == "item.completed":
        text = str(item.get("text") or "")
        if text:
            run.final_text = f"{run.final_text}\n\n{text}".strip() if run.final_text else text
            run.transcript = f"{run.transcript}\n\n{text}".strip() if run.transcript else text
        return
    if item_type == "reasoning" and event_type == "item.completed":
        run.steps.append({"kind": "reasoning", "label": "reasoning", "detail": _clip(item.get("text"), 400)})
        return
    if item_type == "command_execution":
        command = _clip(item.get("command"), 300)
        if event_type == "item.started":
            _upsert_shell_step(run, item_id, {
                "id": item_id,
                "kind": "shell",
                "label": command,
                "detail": "",
                "status": "running",
            })
            return
        if event_type == "item.completed":
            exit_code = item.get("exit_code")
            status = "ok" if exit_code == 0 else "failed"
            output = _clip(item.get("aggregated_output"), 800)
            _upsert_shell_step(run, item_id, {
                "id": item_id,
                "kind": "shell",
                "label": command,
                "detail": output,
                "status": status,
                "exit_code": exit_code,
            })
            if item_id and item_id not in state.shell_ids:
                state.shell_ids.add(item_id)
                run.shell_count += 1
            run.transcript = (
                f"{run.transcript}\n$ {command}\n{output}".strip()
                if run.transcript else f"$ {command}\n{output}"
            )
        return
    if item_type == "file_change" and event_type == "item.completed":
        changes = item.get("changes") or []
        paths = ", ".join(
            f"{change.get('kind') or 'change'}:{change.get('path') or '?'}"
            for change in changes if isinstance(change, dict)
        )
        run.steps.append({"kind": "file", "label": "file change", "detail": paths})
        return
    if item_type in ("mcp_tool_call", "web_search", "todo_list", "error") and event_type == "item.completed":
        detail = item.get("tool") or item.get("query") or item.get("message") or ""
        run.steps.append({"kind": item_type, "label": item_type, "detail": _clip(detail, 300)})


def _build_checks(run: TestRun) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    run_dir = Path(run.run_dir)

    if not run.has_image:
        checks.append(_check("image", "Image understanding", "skip", "No image attached"))
    else:
        text = (run.final_text or "").strip()
        if text and "NO_IMAGE" not in text.upper():
            checks.append(_check("image", "Image understanding", "pass", "Agent described the attached image"))
        else:
            checks.append(_check("image", "Image understanding", "fail", "No image description returned"))

    html_path = run_dir / HTML_NAME
    html = _read_text(html_path)
    if html_path.exists() and len(html) >= 50 and ("<html" in html.lower() or "<!doctype" in html.lower()):
        checks.append(_check("html", "HTML file", "pass", f"{HTML_NAME} · {len(html)} chars"))
    else:
        checks.append(_check("html", "HTML file", "fail", f"{HTML_NAME} missing or not valid HTML"))

    md_path = run_dir / MD_NAME
    markdown = _read_text(md_path)
    if md_path.exists() and len(markdown) >= 50 and HTML_NAME.lower() in markdown.lower():
        checks.append(_check("markdown", "Markdown review", "pass", f"{MD_NAME} · {len(markdown)} chars"))
    else:
        checks.append(_check("markdown", "Markdown review", "fail", f"{MD_NAME} missing or does not mention {HTML_NAME}"))

    transcript = run.transcript.lower()
    os_tokens = [platform.system(), platform.machine(), platform.release()]
    os_hits = [token for token in os_tokens if token and len(token) >= 2 and token.lower() in transcript]
    if os_hits:
        checks.append(_check("os", "OS and machine info", "pass", f"Found: {', '.join(os_hits)}"))
    else:
        checks.append(_check("os", "OS and machine info", "fail", "Transcript does not mention the host OS/arch"))

    if not run.languages:
        checks.append(_check("languages", "Installed languages", "skip", "No known language detected on PATH"))
    else:
        language_hits = []
        for label in run.languages:
            pattern, commands = LANGUAGE_PROBES[label]
            tokens = [pattern, *commands]
            if any(re.search(rf"\b{re.escape(token)}\b", transcript) for token in tokens):
                language_hits.append(label)
        if language_hits:
            checks.append(_check("languages", "Installed languages", "pass", f"Found: {', '.join(language_hits)}"))
        else:
            checks.append(_check(
                "languages",
                "Installed languages",
                "fail",
                f"Transcript did not mention: {', '.join(run.languages)}",
            ))

    if run.shell_count <= SHELL_LIMIT:
        checks.append(_check("shell_budget", "Shell budget", "pass", f"{run.shell_count} shell command(s) (limit {SHELL_LIMIT})"))
    else:
        checks.append(_check("shell_budget", "Shell budget", "warn", f"{run.shell_count} shell command(s), over the limit of {SHELL_LIMIT}"))

    if run.cli == "claude":
        if run.denials:
            names = ", ".join(str(d.get("tool_name") or "?") for d in run.denials[:5])
            checks.append(_check("permissions", "Permissions", "warn", f"Blocked: {names}"))
        else:
            checks.append(_check("permissions", "Permissions", "pass", "No permission denials"))
    return checks


def _collect_artifacts(run: TestRun) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    run_dir = Path(run.run_dir)
    artifacts: List[Dict[str, Any]] = []
    extra_files: List[Dict[str, Any]] = []
    for name in (HTML_NAME, MD_NAME):
        path = run_dir / name
        if path.is_file():
            artifacts.append({
                "name": name,
                "path": str(path),
                "size": path.stat().st_size,
                "content": _read_text(path),
            })
    known = {HTML_NAME, MD_NAME}
    if run_dir.is_dir():
        for path in sorted(run_dir.iterdir()):
            if not path.is_file() or path.name in known or path.name.startswith("image."):
                continue
            extra_files.append({"name": path.name, "path": str(path), "size": path.stat().st_size})
    return artifacts, extra_files


async def _execute(
    run: TestRun,
    cfg: bridge_config.BridgeConfig,
    argv: List[str],
    cwd: Path,
    stdin_payload: str,
) -> None:
    global ACTIVE_RUN_ID
    run_cfg = cfg.model_copy(update={"turn_timeout_sec": run.timeout_sec})
    claude_state = bridge_sse.TurnState() if run.cli == "claude" else None
    codex_state = _CodexState() if run.cli == "codex" else None
    try:
        async for event in bridge_runner.run_cli(
            run_cfg, argv, cwd, stdin_payload, run.id, "testlab", bridge_runner.TURNS
        ):
            if claude_state is not None:
                pieces = bridge_sse.feed(event, claude_state)
                if pieces:
                    run.transcript += "".join(pieces)
                _refresh_claude(run, claude_state)
            elif codex_state is not None:
                _feed_codex(run, event, codex_state)
        if claude_state is not None and claude_state.is_error:
            run.status = "error"
            run.error = claude_state.error_text or claude_state.final_text or "the agent returned an error"
        elif codex_state is not None and codex_state.failed:
            run.status = "error"
            run.error = codex_state.failure
        else:
            run.status = "complete"
    except asyncio.CancelledError:
        run.status = "stopped"
    except Exception as exc:  # RunError, TurnTimeout — keep the partial run
        run.status = "error"
        if codex_state is not None:
            run.error = codex_state.failure or codex_state.last_error or str(exc)
        elif claude_state is not None and (claude_state.error_text or claude_state.final_text):
            run.error = claude_state.error_text or claude_state.final_text
        else:
            run.error = str(exc)
    finally:
        if claude_state is not None:
            _refresh_claude(run, claude_state)
        run.finished = time.time()
        try:
            run.checks = _build_checks(run)
            run.artifacts, run.extra_files = _collect_artifacts(run)
        except Exception as exc:
            run.error = run.error or f"Could not read artifacts: {exc}"
        TASKS.pop(run.id, None)
        if ACTIVE_RUN_ID == run.id:
            ACTIVE_RUN_ID = None


def start_run(
    cli: str,
    model: str,
    workdir: str,
    timeout_sec: Optional[int] = None,
    image_media_type: Optional[str] = None,
    image_base64: Optional[str] = None,
) -> TestRun:
    global ACTIVE_RUN_ID
    if ACTIVE_RUN_ID and ACTIVE_RUN_ID in TASKS and not TASKS[ACTIVE_RUN_ID].done():
        raise RuntimeError("A test run is already active — stop it first")
    cli = (cli or "").strip().lower()
    if cli not in CLI_IDS:
        raise ValueError(f"cli must be one of {', '.join(CLI_IDS)}")
    if not cli_available(cli):
        raise ValueError(f"{cli!r} was not found in PATH")

    cfg = bridge_config.ensure_config()
    resolved = _validate_workdir(workdir, cfg)
    timeout = int(timeout_sec or DEFAULT_TIMEOUT_SEC)
    timeout = max(30, min(timeout, MAX_TIMEOUT_SEC))

    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = resolved / OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    has_image = bool(image_base64)
    image_path: Optional[Path] = None
    image_block: Optional[Dict[str, Any]] = None
    try:
        if has_image:
            image_path = _decode_image(image_media_type or "", image_base64 or "", run_dir)
            image_block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": (image_media_type or "").strip().lower(),
                    "data": image_base64,
                },
            }
        if cli == "codex":
            from config.codex_config import setup_codex_config
            setup_codex_config(base_url=f"{cfg.key_router_url.rstrip('/')}/v1")
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise

    prompt = build_prompt(run_id, has_image)
    if cli == "claude":
        stdin_payload = bridge_registry.claude_stdin(prompt, [image_block] if image_block else [])
        stream_input = has_image
    else:
        stdin_payload = prompt
        stream_input = False

    agent = bridge_config.AgentSpec(
        id="testlab",
        cli=cli,
        model=(model or "").strip(),
        workdir=str(resolved),
        permission="ci",
        effort="high",
        tools="full",
        allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
    )
    argv, cwd = bridge_registry.build_invocation(
        agent,
        cfg,
        stream_input=stream_input,
        image_paths=[str(image_path)] if (image_path and cli == "codex") else None,
    )

    run = TestRun(
        id=run_id,
        cli=cli,
        model=(model or "").strip(),
        workdir=str(resolved),
        run_dir=str(run_dir),
        timeout_sec=timeout,
        has_image=has_image,
        languages=detect_languages(),
    )
    RUNS[run.id] = run
    _evict_runs()
    TASKS[run.id] = asyncio.create_task(_execute(run, cfg, argv, cwd, stdin_payload))
    ACTIVE_RUN_ID = run.id
    return run


def get_run(run_id: str) -> Optional[TestRun]:
    return RUNS.get(run_id)


def run_to_dict(run: TestRun) -> Dict[str, Any]:
    data = asdict(run)
    end = run.finished or time.time()
    data["elapsed_sec"] = round(max(0.0, end - run.started), 1)
    data["active"] = run.status == "running"
    return data


def run_summary(run: TestRun) -> Dict[str, Any]:
    data = run_to_dict(run)
    data.pop("steps", None)
    data.pop("transcript", None)
    data.pop("artifacts", None)
    data.pop("extra_files", None)
    data["passed"] = sum(1 for check in run.checks if check.get("status") == "pass")
    data["failed"] = sum(1 for check in run.checks if check.get("status") == "fail")
    return data


def list_runs() -> List[Dict[str, Any]]:
    return [run_summary(run) for run in reversed(RUNS.values())]


def stop_run(run_id: str) -> Optional[TestRun]:
    run = RUNS.get(run_id)
    if run is None:
        return None
    bridge_runner.TURNS.kill(run_id)
    task = TASKS.get(run_id)
    if task is not None and not task.done():
        task.cancel()
    if run.status == "running":
        run.status = "stopped"
        run.finished = time.time()
    return run


def meta() -> Dict[str, Any]:
    cfg = bridge_config.ensure_config()
    return {
        "scenario": {
            "id": SCENARIO_ID,
            "label": "Quick capability check",
            "shell_limit": SHELL_LIMIT,
            "checks": [
                "Image understanding",
                "HTML file",
                "OS and machine info",
                "Installed languages",
                "Markdown review",
                f"≤ {SHELL_LIMIT} shell commands",
            ],
        },
        "clis": [
            {"id": "claude", "label": "Claude Code", "available": cli_available("claude")},
            {"id": "codex", "label": "Codex", "available": cli_available("codex")},
        ],
        "allowed_roots": cfg.allowed_roots,
        "default_timeout_sec": DEFAULT_TIMEOUT_SEC,
        "languages": detect_languages(),
        "html_name": HTML_NAME,
        "md_name": MD_NAME,
    }
