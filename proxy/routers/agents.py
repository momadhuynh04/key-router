"""Management API for agent-bridge, used by the key-router dashboard.

These endpoints only *manage* things: they read/write `agents.json` and control the
bridge process lifecycle. The proxy data path (`/v1/messages`, `/v1/responses`,
`/v1/chat/completions`) is untouched.
"""
from __future__ import annotations

import asyncio
import os
import platform
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bridge.config import (
    LOG_PATH,
    PID_PATH,
    REPO_ROOT,
    AgentSpec,
    BridgeConfig,
    ensure_config,
    new_token,
    save_config,
    validate_agent,
    validate_config,
)

router = APIRouter()

HEALTH_TIMEOUT = 2.0


def _kill_tree(pid: int, sig: int = signal.SIGTERM) -> None:
    if platform.system() == "Windows":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
        return
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid() -> Optional[int]:
    try:
        raw = PID_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _write_pid(pid: int) -> None:
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(pid), encoding="utf-8")


def _clear_pid() -> None:
    try:
        PID_PATH.unlink()
    except OSError:
        pass


class AgentPayload(BaseModel):
    id: str
    name: str = ""
    cli: str = "claude"
    model: str = ""
    workdir: str
    permission: str = "edit"
    effort: str = "medium"
    tools: str = "edit"
    restricted: bool = False
    add_dirs: List[str] = []
    append_system_prompt: str = ""
    fallback_model: str = ""
    max_budget_usd: Optional[float] = None
    allowed_tools: List[str] = []
    disallowed_tools: List[str] = []


class SettingsPayload(BaseModel):
    allowed_roots: Optional[List[str]] = None
    default_agent: Optional[str] = None
    max_concurrent_runs: Optional[int] = None
    turn_timeout_sec: Optional[int] = None
    allow_bypass: Optional[bool] = None
    port: Optional[int] = None
    key_router_url: Optional[str] = None


class PathPayload(BaseModel):
    path: str


def _settings(cfg: BridgeConfig) -> Dict:
    return {
        "host": cfg.host,
        "port": cfg.port,
        "token": cfg.token,
        "key_router_url": cfg.key_router_url,
        "allowed_roots": cfg.allowed_roots,
        "default_agent": cfg.default_agent,
        "max_concurrent_runs": cfg.max_concurrent_runs,
        "turn_timeout_sec": cfg.turn_timeout_sec,
        "allow_bypass": cfg.allow_bypass,
    }


def _load() -> BridgeConfig:
    try:
        return ensure_config()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/agents")
async def list_agents():
    cfg = _load()
    warnings: List[str] = []
    try:
        validate_config(cfg)
    except ValueError as e:
        warnings.append(str(e))
    return {
        "agents": [a.model_dump() for a in cfg.agents],
        "settings": _settings(cfg),
        "warnings": warnings,
    }


@router.post("/api/agents")
async def upsert_agent(payload: AgentPayload):
    cfg = _load()
    agent = AgentSpec(**payload.model_dump())
    try:
        validate_agent(agent, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cfg.agents = [a for a in cfg.agents if a.id != agent.id] + [agent]
    if not cfg.default_agent:
        cfg.default_agent = agent.id
    save_config(cfg)
    return {"status": "success", "agents": [a.model_dump() for a in cfg.agents]}


@router.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str):
    cfg = _load()
    remaining = [a for a in cfg.agents if a.id != agent_id]
    if len(remaining) == len(cfg.agents):
        raise HTTPException(status_code=404, detail=f"No agent '{agent_id}'")
    cfg.agents = remaining
    if cfg.default_agent == agent_id:
        cfg.default_agent = remaining[0].id if remaining else None
    save_config(cfg)
    return {"status": "success", "agents": [a.model_dump() for a in cfg.agents]}


@router.post("/api/agents/settings")
async def update_settings(payload: SettingsPayload):
    cfg = _load()
    data = payload.model_dump(exclude_unset=True)
    if "default_agent" in data and not data["default_agent"]:
        data["default_agent"] = None
    for key, value in data.items():
        setattr(cfg, key, value)
    try:
        validate_config(cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    save_config(cfg)
    return {"status": "success", "settings": _settings(cfg)}


@router.post("/api/agents/token")
async def regenerate_token():
    cfg = _load()
    cfg.token = new_token()
    save_config(cfg)
    return {"status": "success", "token": cfg.token}


@router.post("/api/agents/validate-path")
async def validate_path(payload: PathPayload):
    cfg = _load()
    from pathlib import Path as _Path

    resolved = _Path(payload.path).expanduser().resolve()
    roots = [_Path(r).expanduser().resolve() for r in cfg.allowed_roots]
    allowed = any(resolved == r or resolved.is_relative_to(r) for r in roots)
    return {
        "path": str(resolved),
        "exists": resolved.is_dir(),
        "allowed": allowed,
        "allowed_roots": [str(r) for r in roots],
    }


# ------------------------------------------------------------ bridge lifecycle

@router.get("/api/bridge/status")
async def bridge_status():
    cfg = _load()
    pid = _read_pid()
    alive = bool(pid and _pid_alive(pid))
    if not alive and pid:
        _clear_pid()
        pid = None

    healthy = False
    if alive:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{cfg.port}/health", timeout=HEALTH_TIMEOUT
                )
                healthy = resp.status_code == 200
        except Exception:
            healthy = False

    uptime = None
    if pid and PID_PATH.exists():
        try:
            uptime = int(time.time() - PID_PATH.stat().st_mtime)
        except OSError:
            uptime = None

    return {
        "running": alive,
        "healthy": healthy,
        "pid": pid,
        "port": cfg.port,
        "uptime_sec": uptime,
        "url": f"http://{cfg.host}:{cfg.port}/v1",
    }


@router.post("/api/bridge/start")
async def bridge_start():
    cfg = _load()
    pid = _read_pid()
    if pid and _pid_alive(pid):
        return {"status": "already-running", "pid": pid}

    try:
        validate_config(cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Configuration is not valid: {e}")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(LOG_PATH, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "bridge"],
            cwd=str(REPO_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        log_file.close()
        raise HTTPException(status_code=500, detail=f"Could not spawn the bridge: {e}")
    finally:
        try:
            log_file.close()
        except Exception:
            pass

    _write_pid(proc.pid)
    await asyncio.sleep(1.5)

    if proc.poll() is not None:
        _clear_pid()
        tail = await _tail_log(40)
        raise HTTPException(
            status_code=500,
            detail=f"Bridge exited right after starting. Log:\n{tail}",
        )
    return {"status": "success", "pid": proc.pid}


@router.post("/api/bridge/stop")
async def bridge_stop():
    pid = _read_pid()
    if not pid:
        return {"status": "not-running"}
    if not _pid_alive(pid):
        _clear_pid()
        return {"status": "not-running"}

    _kill_tree(pid, signal.SIGTERM)
    for _ in range(20):
        if not _pid_alive(pid):
            break
        await asyncio.sleep(0.25)
    if _pid_alive(pid):
        _kill_tree(pid, signal.SIGKILL)
        await asyncio.sleep(0.5)
    _clear_pid()
    return {"status": "success"}


async def _tail_log(lines: int) -> str:
    def _read() -> str:
        if not LOG_PATH.exists():
            return "(no log yet)"
        try:
            content = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return f"(could not read the log: {e})"
        return "\n".join(content[-lines:]) if content else "(log is empty)"

    return await asyncio.to_thread(_read)


@router.get("/api/bridge/logs")
async def bridge_logs(lines: int = 60):
    lines = max(1, min(lines, 500))
    return {"path": str(LOG_PATH), "lines": await _tail_log(lines)}
