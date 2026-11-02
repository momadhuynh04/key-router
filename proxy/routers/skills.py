"""Skills management API used by the Launcher → Skills panel."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import skills
from config.logging import log_warn

router = APIRouter()

MARKETPLACE_URL = "https://skills.sh/api/search"
MARKETPLACE_TTL = 60.0
SUGGESTIONS = ["react", "python", "typescript", "testing", "security", "git", "docker", "nextjs"]

_marketplace_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_install_lock = asyncio.Lock()


class InstallRequest(BaseModel):
    source: str
    skill: str = ""
    scope: Literal["global", "project"] = "global"
    project: Optional[str] = None
    providers: List[Literal["claude", "codex"]] = ["claude", "codex"]


class ToggleRequest(BaseModel):
    provider: Literal["claude", "codex"]
    scope: Literal["global", "project"]
    project: Optional[str] = None
    id: str
    enabled: bool


def _project(raw: Optional[str]) -> Tuple[Optional[Path], Dict[str, Any]]:
    if not raw or not raw.strip():
        return None, {"path": "", "valid": False, "allowed": False, "error": "No project selected"}
    try:
        resolved = skills.resolve_project_path(raw)
        return resolved, {"path": str(resolved), "valid": True, "allowed": True, "error": None}
    except ValueError as exc:
        return None, {"path": raw, "valid": False, "allowed": False, "error": str(exc)}


def _scope_project(scope: str, raw: Optional[str]) -> Optional[Path]:
    if scope != "project":
        return None
    return skills.resolve_project_path(raw or "")


async def _fetch_marketplace(q: str, limit: int) -> Tuple[List[Dict[str, Any]], bool, List[str]]:
    key = q.lower()
    now = time.time()
    cached = _marketplace_cache.get(key)
    if cached and now - cached[0] < MARKETPLACE_TTL:
        return cached[1][:limit], True, []

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(MARKETPLACE_URL, params={"q": q})
            resp.raise_for_status()
            payload = resp.json()
        raw_items = payload.get("skills") or payload.get("data") or []
        items: List[Dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            name = item.get("name")
            if not isinstance(source, str) or not isinstance(name, str):
                continue
            try:
                installs = int(item.get("installs") or 0)
            except (TypeError, ValueError):
                installs = 0
            items.append({
                "id": item.get("id") or f"{source}/{name}",
                "source": source,
                "skillId": item.get("skillId") or item.get("slug"),
                "name": name,
                "installs": installs,
            })
        _marketplace_cache[key] = (now, items)
        return items[:limit], False, []
    except Exception as exc:
        log_warn(f"skills.sh search failed: {exc}")
        stale = _marketplace_cache.get(key)
        warning = f"skills.sh unavailable: {exc}"
        if stale:
            return stale[1][:limit], True, [warning]
        return [], False, [warning]


@router.get("/api/skills")
async def list_skills(project: Optional[str] = None):
    project_path, project_info = _project(project)
    data, env = await asyncio.gather(
        asyncio.to_thread(skills.scan_all, project_path),
        asyncio.to_thread(skills.environment),
    )
    return {
        "project": project_info,
        "environment": env,
        "skills": data["skills"],
        "warnings": data["warnings"],
    }


@router.get("/api/skills/marketplace")
async def search_marketplace(q: str = "", limit: int = 30):
    query = (q or "").strip()
    limit = max(1, min(limit, 50))
    if len(query) < 2:
        return {"skills": [], "suggestions": SUGGESTIONS, "cached": False, "warnings": []}
    items, cached, warnings = await _fetch_marketplace(query, limit)
    return {"skills": items, "suggestions": SUGGESTIONS, "cached": cached, "warnings": warnings}


@router.post("/api/skills/install")
async def install_skill(request: InstallRequest):
    async with _install_lock:
        try:
            result = await asyncio.to_thread(
                skills.install_skill,
                request.source,
                request.skill,
                list(request.providers),
                request.scope,
                request.project or "",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)[:2000])
    return result


@router.post("/api/skills/toggle")
async def toggle_skill(request: ToggleRequest):
    try:
        project_path = _scope_project(request.scope, request.project)
        info = await asyncio.to_thread(
            skills.toggle_skill,
            request.provider,
            request.scope,
            project_path,
            request.id,
            request.enabled,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "skill": info.to_dict()}


@router.delete("/api/skills/{provider}/{scope}/{skill_id}")
async def delete_skill(
    provider: str,
    scope: str,
    skill_id: str,
    project: Optional[str] = None,
):
    try:
        project_path = _scope_project(scope, project)
        info = await asyncio.to_thread(skills.find_skill, provider, scope, project_path, skill_id)
        if not info.writable:
            raise ValueError("skill is read-only (symlink/system/plugin); manage it with npx skills")
        async with _install_lock:
            result = await asyncio.to_thread(
                skills.remove_skill, provider, scope, project_path, skill_id
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:2000])
    return {"status": "success", **result}


@router.get("/api/skills/content")
async def skill_content(
    provider: str,
    scope: str,
    id: str,
    project: Optional[str] = None,
):
    try:
        project_path = _scope_project(scope, project)
        result = await asyncio.to_thread(
            skills.read_skill_content, provider, scope, project_path, id
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result
