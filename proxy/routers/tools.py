"""Dashboard API for personal tools (install, run, credentials).

Only management endpoints live here. Tool traffic goes to the tool's own
loopback process; nothing is added to the LLM ingress paths.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import tools as tools_store
from proxy import tools_hub

router = APIRouter()


class InstallRequest(BaseModel):
    repo: Optional[str] = None
    branch: Optional[str] = None
    local_path: Optional[str] = None


class CredentialValue(BaseModel):
    value: str


class ReinstallRequest(BaseModel):
    repo: Optional[str] = None
    branch: Optional[str] = None


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, httpx.HTTPError):
        return HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")
    return HTTPException(status_code=500, detail=str(exc))


async def _snapshot() -> Dict[str, Any]:
    installed = await asyncio.to_thread(tools_hub.installed_tools)
    return {
        "installed": installed,
        "tools_root": tools_store.tools_root(),
    }


@router.get("/api/tools")
async def list_tools():
    return await _snapshot()


@router.post("/api/tools/install")
async def install_tool(payload: InstallRequest):
    try:
        tool = await tools_hub.install_tool(
            repo=payload.repo or "",
            branch=payload.branch or "",
            local_path=payload.local_path or "",
        )
    except Exception as exc:
        raise _fail(exc) from exc
    snapshot = await _snapshot()
    snapshot["installed_tool"] = tool
    return snapshot


@router.post("/api/tools/{tool_id}/reinstall")
async def reinstall_tool(tool_id: str, payload: ReinstallRequest):
    try:
        tool = await tools_hub.reinstall_tool(tool_id, payload.repo or "", payload.branch or "")
    except Exception as exc:
        raise _fail(exc) from exc
    snapshot = await _snapshot()
    snapshot["installed_tool"] = tool
    return snapshot


def _require_tool_token(tool_id: str, request: Request) -> None:
    """Tool processes authenticate with the per-start token from their env."""
    entry = tools_store.load_registry()["tools"].get(tool_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"No tool '{tool_id}'")
    runtime = tools_store.load_runtime()["tools"].get(tool_id, {})
    expected = runtime.get("session_token") or entry.get("session_token") or ""
    supplied = request.headers.get("x-tool-token", "")
    if not expected or supplied != expected:
        raise HTTPException(status_code=401, detail="Invalid tool token")


@router.get("/api/tools/{tool_id}/credentials")
async def list_tool_credentials(tool_id: str, request: Request):
    _require_tool_token(tool_id, request)
    try:
        return {"credentials": tools_hub.tool_credentials(tool_id)}
    except Exception as exc:
        raise _fail(exc) from exc


@router.get("/api/tools/{tool_id}/credentials/{name}")
async def get_tool_credential(tool_id: str, name: str, request: Request):
    _require_tool_token(tool_id, request)
    try:
        return {"name": name, "value": tools_hub.get_credential(tool_id, name)}
    except Exception as exc:
        raise _fail(exc) from exc


@router.put("/api/tools/{tool_id}/credentials/{name}")
async def save_tool_credential(tool_id: str, name: str, payload: CredentialValue, request: Request):
    _require_tool_token(tool_id, request)
    try:
        tools_hub.set_credential(tool_id, name, payload.value)
        return {"credentials": tools_hub.tool_credentials(tool_id)}
    except Exception as exc:
        raise _fail(exc) from exc


@router.delete("/api/tools/{tool_id}/credentials/{name}")
async def remove_tool_credential(tool_id: str, name: str, request: Request):
    _require_tool_token(tool_id, request)
    try:
        if not tools_hub.delete_credential(tool_id, name):
            raise HTTPException(status_code=404, detail=f"No credential '{name}'")
        return {"credentials": tools_hub.tool_credentials(tool_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _fail(exc) from exc


@router.post("/api/tools/{tool_id}/setup")
async def setup_tool(tool_id: str):
    try:
        return await asyncio.to_thread(tools_hub.run_setup, tool_id)
    except Exception as exc:
        raise _fail(exc) from exc


@router.post("/api/tools/{tool_id}/start")
async def start_tool(tool_id: str):
    try:
        return await asyncio.to_thread(tools_hub.start_tool, tool_id)
    except Exception as exc:
        raise _fail(exc) from exc


@router.post("/api/tools/{tool_id}/stop")
async def stop_tool(tool_id: str):
    try:
        return await asyncio.to_thread(tools_hub.stop_tool, tool_id)
    except Exception as exc:
        raise _fail(exc) from exc


@router.post("/api/tools/{tool_id}/restart")
async def restart_tool(tool_id: str):
    try:
        return await asyncio.to_thread(tools_hub.restart_tool, tool_id)
    except Exception as exc:
        raise _fail(exc) from exc


@router.get("/api/tools/{tool_id}/logs")
async def tool_logs(tool_id: str, lines: int = 60):
    lines = max(1, min(lines, 500))
    try:
        return await asyncio.to_thread(tools_hub.tool_logs, tool_id, lines)
    except Exception as exc:
        raise _fail(exc) from exc


@router.delete("/api/tools/{tool_id}")
async def uninstall_tool(tool_id: str):
    try:
        return await asyncio.to_thread(tools_hub.uninstall_tool, tool_id)
    except Exception as exc:
        raise _fail(exc) from exc
