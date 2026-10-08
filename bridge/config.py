"""Loading and validation for `agents.json`.

The single source of truth is `agents.json` at the repo root. The key-router dashboard
is the normal way to edit it, but the file is re-read on every request, so a dashboard
change takes effect immediately without restarting the bridge.
"""
from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_DIR = Path(__file__).resolve().parent
AGENTS_PATH = REPO_ROOT / "agents.json"
STATE_PATH = BRIDGE_DIR / "state.json"
PID_PATH = BRIDGE_DIR / ".pid"
LOG_PATH = REPO_ROOT / "bridge.log"

AGENT_ID_RE = re.compile(r"^[a-z0-9_-]{2,32}$")

CLI_VALUES = ("claude", "codex")
PERMISSION_VALUES = ("plan", "edit", "auto", "ci", "bypass")
EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
TOOL_VALUES = ("readonly", "edit", "full")

MODEL_ID = "key-router-agents"


class AgentSpec(BaseModel):
    id: str
    name: str = ""
    cli: str = "claude"
    model: str = ""
    workdir: str
    permission: str = "edit"
    effort: str = "medium"
    tools: str = "edit"
    restricted: bool = False
    add_dirs: List[str] = Field(default_factory=list)
    append_system_prompt: str = ""
    fallback_model: str = ""
    max_budget_usd: Optional[float] = None
    allowed_tools: List[str] = Field(default_factory=list)
    disallowed_tools: List[str] = Field(default_factory=list)


class BridgeConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8083
    token: str = ""
    key_router_url: str = "http://127.0.0.1:8082"
    allowed_roots: List[str] = Field(default_factory=list)
    default_agent: Optional[str] = None
    max_concurrent_runs: int = 2
    turn_timeout_sec: int = 1800
    allow_bypass: bool = False
    agents: List[AgentSpec] = Field(default_factory=list)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_config() -> BridgeConfig:
    """Read `agents.json`, creating it (with a fresh token) when missing."""
    if not AGENTS_PATH.exists():
        cfg = BridgeConfig(token=new_token())
        save_config(cfg)
        return cfg
    try:
        raw = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"agents.json is not valid JSON: {e}") from e
    cfg = BridgeConfig(**raw)
    if not cfg.token:
        cfg.token = new_token()
        save_config(cfg)
    return cfg


def save_config(cfg: BridgeConfig) -> None:
    AGENTS_PATH.write_text(
        json.dumps(cfg.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def find_agent(cfg: BridgeConfig, agent_id: str) -> Optional[AgentSpec]:
    for agent in cfg.agents:
        if agent.id == agent_id:
            return agent
    return None


def resolve_workdir(agent: AgentSpec, cfg: BridgeConfig) -> Path:
    """Return the resolved workdir, raising when it falls outside `allowed_roots`.

    `.resolve()` is deliberate: it follows symlinks, so a symlink pointing outside
    the allowed roots cannot be used to escape them.
    """
    path = Path(agent.workdir).expanduser()
    if not path.is_absolute():
        raise ValueError(f"workdir must be an absolute path: {agent.workdir!r}")
    resolved = path.resolve()
    roots = [Path(r).expanduser().resolve() for r in cfg.allowed_roots]
    if not roots:
        raise ValueError(
            "allowed_roots is empty — add at least one root folder in the Agents tab."
        )
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        raise ValueError(f"workdir {resolved} is outside allowed_roots")
    return resolved


def validate_agent(agent: AgentSpec, cfg: BridgeConfig) -> None:
    if not AGENT_ID_RE.match(agent.id or ""):
        raise ValueError(
            f"invalid id {agent.id!r} — use a-z, 0-9, '-', '_' (2-32 characters)"
        )
    if agent.cli not in CLI_VALUES:
        raise ValueError(f"invalid cli {agent.cli!r} — pick one of {', '.join(CLI_VALUES)}")
    if agent.permission not in PERMISSION_VALUES:
        raise ValueError(
            f"invalid permission {agent.permission!r} — pick one of {', '.join(PERMISSION_VALUES)}"
        )
    if agent.effort not in EFFORT_VALUES:
        raise ValueError(
            f"invalid effort {agent.effort!r} — pick one of {', '.join(EFFORT_VALUES)}"
        )
    if agent.tools not in TOOL_VALUES:
        raise ValueError(
            f"invalid tools profile {agent.tools!r} — pick one of {', '.join(TOOL_VALUES)}"
        )
    if agent.permission == "bypass" and not cfg.allow_bypass:
        raise ValueError(
            "permission 'bypass' is refused — enable allow_bypass in Bridge settings first"
        )
    if agent.max_budget_usd is not None and agent.max_budget_usd <= 0:
        raise ValueError("max_budget_usd must be greater than 0")
    resolve_workdir(agent, cfg)


def validate_config(cfg: BridgeConfig) -> None:
    ids = [a.id for a in cfg.agents]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate agent ids: {', '.join(sorted(duplicates))}")
    for agent in cfg.agents:
        validate_agent(agent, cfg)
    if cfg.default_agent and cfg.default_agent not in ids:
        raise ValueError(f"default_agent {cfg.default_agent!r} does not exist")
    if cfg.max_concurrent_runs < 1:
        raise ValueError("max_concurrent_runs must be >= 1")
    if cfg.turn_timeout_sec < 1:
        raise ValueError("turn_timeout_sec must be >= 1")
