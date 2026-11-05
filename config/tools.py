"""Registry and manifest helpers for personal tools.

Everything the hub persists lives inside the repository under ``.key-router/``:
``tools.json`` (tracked registry/catalog with each tool's source repo),
``runtime.json`` (gitignored pid/session/port), ``logs/<id>.log`` and
``data/<id>/`` (gitignored). Installed tool code lives in ``tools/<id>/``
(gitignored). Tool credentials stay in ``secrets.json``.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from functools import lru_cache
from typing import Any, Dict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pre-0.1.35 location; migrated into the repository on first use (see _ensure_state).
LEGACY_STATE_ROOT = os.path.join(os.path.expanduser("~"), ".key-router")

TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MANIFEST_NAME = "tool.json"


def _state_root() -> str:
    return os.path.join(BASE_DIR, ".key-router")


def _migrate_legacy_state() -> None:
    """One-time move of the old ~/.key-router tree into the repository."""
    target = _state_root()
    source = os.path.realpath(LEGACY_STATE_ROOT)
    if os.path.realpath(target) == source or not os.path.isdir(source):
        return
    try:
        os.makedirs(target, exist_ok=True)
        for name in ("tools.json",):
            src = os.path.join(source, name)
            dst = os.path.join(target, name)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.move(src, dst)
        for name in ("logs", "data"):
            src = os.path.join(source, name)
            dst = os.path.join(target, name)
            if os.path.isdir(src) and not os.path.exists(dst):
                shutil.move(src, dst)
        os.rmdir(source)
    except OSError:
        pass


_migrated = False


def _ensure_state() -> None:
    global _migrated
    if _migrated:
        return
    _migrated = True
    _migrate_legacy_state()


def _get_tools_root() -> str:
    return os.path.join(BASE_DIR, "tools")


def _get_registry_path() -> str:
    _ensure_state()
    return os.path.join(_state_root(), "tools.json")


def _get_logs_dir() -> str:
    _ensure_state()
    return os.path.join(_state_root(), "logs")


def _get_data_root() -> str:
    _ensure_state()
    return os.path.join(_state_root(), "data")


def tools_root() -> str:
    return _get_tools_root()


def logs_dir() -> str:
    return _get_logs_dir()


def registry_path() -> str:
    return _get_registry_path()


def tool_dir(tool_id: str) -> str:
    return os.path.join(_get_tools_root(), tool_id)


def log_path(tool_id: str) -> str:
    return os.path.join(_get_logs_dir(), f"{tool_id}.log")


def data_dir(tool_id: str) -> str:
    return os.path.join(_get_data_root(), tool_id)


def _empty_registry() -> Dict[str, Any]:
    return {"tools": {}}


def _read_registry() -> Dict[str, Any]:
    path = _get_registry_path()
    if not os.path.exists(path):
        return _empty_registry()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty_registry()
    if not isinstance(data, dict):
        return _empty_registry()

    registry = _empty_registry()
    tools = data.get("tools")
    if isinstance(tools, dict):
        registry["tools"] = {
            key: value for key, value in tools.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
    return registry


@lru_cache(maxsize=1)
def _load_registry_cached() -> Dict[str, Any]:
    return _read_registry()


def load_registry() -> Dict[str, Any]:
    return _load_registry_cached()


def invalidate_tools_cache() -> None:
    _load_registry_cached.cache_clear()


def save_registry(registry: Dict[str, Any]) -> None:
    path = _get_registry_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tools-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    invalidate_tools_cache()


def registry_copy() -> Dict[str, Any]:
    return copy.deepcopy(load_registry())


def _get_runtime_path() -> str:
    _ensure_state()
    return os.path.join(_state_root(), "runtime.json")


def _empty_runtime() -> Dict[str, Any]:
    return {"tools": {}}


def load_runtime() -> Dict[str, Any]:
    path = _get_runtime_path()
    if not os.path.exists(path):
        return _empty_runtime()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty_runtime()
    if not isinstance(data, dict):
        return _empty_runtime()
    tools = data.get("tools")
    if not isinstance(tools, dict):
        return _empty_runtime()
    return {
        "tools": {
            key: value for key, value in tools.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
    }


def save_runtime(runtime: Dict[str, Any]) -> None:
    path = _get_runtime_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".runtime-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(runtime, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def validate_manifest(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("tool.json must contain a JSON object")

    tool_id = data.get("id")
    if not isinstance(tool_id, str) or not TOOL_ID_RE.match(tool_id):
        raise ValueError(
            "tool.json id must be lowercase letters, digits or dashes (2-41 chars)"
        )

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool.json name is required")

    start = data.get("start")
    if not isinstance(start, list) or not start or not all(isinstance(x, str) and x for x in start):
        raise ValueError("tool.json start must be a non-empty list of strings")

    setup = data.get("setup", [])
    if not isinstance(setup, list) or not all(isinstance(x, str) and x for x in setup):
        raise ValueError("tool.json setup must be a list of strings")

    health = data.get("health", "/health")
    if not isinstance(health, str) or not health.startswith("/"):
        raise ValueError("tool.json health must be a path starting with '/'")

    default_port = data.get("default_port", 9100)
    if not isinstance(default_port, int) or not (1024 <= default_port <= 65535):
        raise ValueError("tool.json default_port must be a port number")

    env = data.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and k and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("tool.json env must map variable names to strings")

    version = data.get("version", "")
    if not isinstance(version, str):
        raise ValueError("tool.json version must be a string")

    description = data.get("description", "")
    if not isinstance(description, str):
        raise ValueError("tool.json description must be a string")

    return {
        "id": tool_id,
        "name": name.strip(),
        "version": version.strip(),
        "description": description.strip(),
        "start": list(start),
        "setup": list(setup),
        "health": health,
        "default_port": default_port,
        "env": dict(env),
    }


def load_manifest(directory: str) -> Dict[str, Any]:
    path = os.path.join(directory, MANIFEST_NAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{MANIFEST_NAME} not found in {directory}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {MANIFEST_NAME}: {exc}") from exc
    return validate_manifest(raw)
