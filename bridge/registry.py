"""Build the CLI argv for one AgentSpec.

All knowledge about CLI flags lives here so it is not scattered around the bridge.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from bridge.config import AgentSpec, BridgeConfig, resolve_workdir

# Claude Code: permission mode -> flag
CLAUDE_PERMISSION = {
    "plan": ["--permission-mode", "plan"],
    "edit": ["--permission-mode", "acceptEdits"],
    "auto": ["--permission-mode", "auto"],
    "ci": ["--permission-mode", "dontAsk"],
    "bypass": ["--permission-mode", "bypassPermissions"],
}

# Codex: permission mode -> sandbox / approval flags
CODEX_PERMISSION = {
    "plan": ["-s", "read-only"],
    "edit": ["-s", "workspace-write"],
    "auto": ["-s", "workspace-write"],
    "ci": ["-s", "workspace-write"],
    "bypass": ["--dangerously-bypass-approvals-and-sandbox"],
}

# Claude Code: tools profile -> --tools
TOOL_PROFILES = {
    "readonly": "Read,Grep,Glob",
    "edit": "Read,Grep,Glob,Edit,Write",
}

# Codex only understands low/medium/high
CODEX_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def claude_argv(agent: AgentSpec, session_id: Optional[str], resume: bool) -> List[str]:
    argv = [
        "claude",
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        # never wait for a human to answer a permission prompt -> never hang
        "--permission-prompts", "none",
    ]
    argv += CLAUDE_PERMISSION[agent.permission]
    argv += ["--effort", agent.effort]
    if agent.model:
        argv += ["--model", agent.model]
    if agent.fallback_model:
        argv += ["--fallback-model", agent.fallback_model]
    if agent.max_budget_usd is not None:
        argv += ["--max-budget-usd", str(agent.max_budget_usd)]
    if agent.append_system_prompt:
        argv += ["--append-system-prompt", agent.append_system_prompt]
    profile = TOOL_PROFILES.get(agent.tools)
    if profile:
        argv += ["--tools", profile]
    if agent.restricted:
        argv += ["--restricted"]
    if session_id and resume:
        argv += ["--resume", session_id]
    elif session_id:
        argv += ["--session-id", session_id]

    # Variadic options go last. Each stops consuming at the next "--" token, and
    # nothing follows the final group, so no value can be swallowed by accident.
    for directory in agent.add_dirs:
        argv += ["--add-dir", directory]
    if agent.allowed_tools:
        argv += ["--allowedTools", *agent.allowed_tools]
    if agent.disallowed_tools:
        argv += ["--disallowedTools", *agent.disallowed_tools]
    return argv


def codex_argv(agent: AgentSpec, workdir: Path, session_id: Optional[str], resume: bool) -> List[str]:
    argv = ["codex", "exec"]
    # `resume` is a subcommand of `exec`, so it must come right after it.
    if session_id and resume:
        argv += ["resume", session_id]
    argv += CODEX_PERMISSION[agent.permission]
    argv += ["--json"]
    argv += ["-C", str(workdir)]
    if agent.model:
        argv += ["-m", agent.model]
    argv += ["-c", f'model_reasoning_effort="{CODEX_EFFORT[agent.effort]}"']
    for directory in agent.add_dirs:
        argv += ["--add-dir", directory]
    return argv


def build_invocation(
    agent: AgentSpec,
    cfg: BridgeConfig,
    session_id: Optional[str] = None,
    resume: bool = False,
) -> Tuple[List[str], Path]:
    """Return (argv, cwd).

    The prompt is NOT part of argv: it is piped through stdin. `--tools`, `--add-dir`,
    `--allowedTools` and `--disallowedTools` are variadic options that swallow every
    following token, so a trailing prompt would be eaten. Going through stdin also
    removes the argv length limit and any risk of a prompt being parsed as a flag.
    """
    workdir = resolve_workdir(agent, cfg)
    if agent.cli == "claude":
        argv = claude_argv(agent, session_id, resume)
    else:
        argv = codex_argv(agent, workdir, session_id, resume)
    return argv, workdir
