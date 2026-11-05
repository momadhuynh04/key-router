"""Personal tools hub.

Each tool is a small app published in its own GitHub repo with a ``tool.json``
manifest. Installing downloads the tarball into ``tools/<id>`` in this repository;
starting spawns it on a loopback port with the credentials the manifest asks
for. The dashboard embeds the tool through an iframe.

The hub never touches the LLM data path: it only manages processes and files.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx

from config import secrets as secrets_store
from config import tools as tools_store
from config.settings import settings
from proxy.process_utils import kill_tree, pid_alive, reap, spawn_kwargs, tail_file

GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$", re.IGNORECASE)
LEGACY_TOOLS_ROOT = os.path.join(os.path.expanduser("~"), ".key-router", "tools")
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
HEALTH_TIMEOUT = 1.0
START_WAIT_SECONDS = 10.0

TOOL_KEY_LABELS = {"github": "GitHub token"}

# Env values the hub resolves itself. `tool:<name>` means "credential", but these
# three are placeholders, not secrets, so they must not appear as credential rows.
ENV_PLACEHOLDERS = {"keyrouter:url", "generated:token", "tool:dir", "tool:data"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _github_headers(accept: str = "application/vnd.github+json") -> Dict[str, str]:
    headers = {"Accept": accept, "User-Agent": "key-router-tools"}
    token = secrets_store.get_tool_key("github")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ---------------------------------------------------------------- install

async def install_tool(
    repo: str = "",
    branch: str = "",
    local_path: str = "",
) -> Dict[str, Any]:
    if local_path:
        directory = os.path.abspath(os.path.expanduser(local_path))
        if not os.path.isdir(directory):
            raise FileNotFoundError(f"Folder not found: {directory}")
        manifest = tools_store.load_manifest(directory)
        source: Dict[str, Any] = {"type": "local", "path": directory}
        repo, branch = _git_remote_repo(directory)
        if repo:
            source.update({"repo": repo, "branch": branch or "main"})
        entry = _entry_from_manifest(manifest, directory, source)
        _save_entry(entry)
        return tool_snapshot(entry)

    if not repo:
        raise ValueError("Provide a GitHub repo (owner/name) or a local folder")

    if not GITHUB_REPO_RE.match(repo):
        raise ValueError("Repo must look like owner/name")

    archive, extract_root = await _download_and_extract(repo, branch or "main")
    try:
        manifest = tools_store.load_manifest(extract_root)
        destination = tools_store.tool_dir(manifest["id"])
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if os.path.exists(destination):
            shutil.rmtree(destination)
        shutil.move(extract_root, destination)
        manifest = tools_store.load_manifest(destination)
        entry = _entry_from_manifest(
            manifest,
            destination,
            {"type": "github", "repo": repo, "branch": branch or "main"},
        )
        _save_entry(entry)
        return tool_snapshot(entry)
    finally:
        _cleanup_download(archive, extract_root)


async def _download_and_extract(repo: str, branch: str) -> Tuple[str, str]:
    url = f"https://api.github.com/repos/{repo}/tarball/{branch}"
    fd, archive = tempfile.mkstemp(prefix="key-router-tool-", suffix=".tar.gz")
    handle = os.fdopen(fd, "wb")
    try:
        total = 0
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=_github_headers()) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")[:500]
                    raise RuntimeError(f"GitHub returned {response.status_code}: {body}")
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("Tool package is larger than 100 MB")
                    handle.write(chunk)
        handle.close()
    except Exception:
        try:
            handle.close()
        except OSError:
            pass
        try:
            os.remove(archive)
        except OSError:
            pass
        raise

    try:
        root = _extract_package(archive)
    except Exception:
        try:
            os.remove(archive)
        except OSError:
            pass
        raise
    return archive, root


def _extract_package(archive: str) -> str:
    temp_root = tempfile.mkdtemp(prefix="key-router-tool-extract-")
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _safe_extract(tar, temp_root)
        entries = [
            os.path.join(temp_root, name)
            for name in os.listdir(temp_root)
            if not name.startswith(".")
        ]
        if len(entries) != 1 or not os.path.isdir(entries[0]):
            raise ValueError("Tool package must contain exactly one top-level folder")
        return entries[0]
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _safe_extract(tar: tarfile.TarFile, destination: str) -> None:
    root = os.path.realpath(destination)
    for member in tar.getmembers():
        target = os.path.realpath(os.path.join(root, member.name))
        if target != root and not target.startswith(root + os.sep):
            raise ValueError(f"Unsafe path in tool package: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Links are not allowed in tool packages: {member.name}")
    tar.extractall(root)


def _cleanup_download(archive: str, extract_root: str) -> None:
    try:
        os.remove(archive)
    except OSError:
        pass
    if extract_root:
        shutil.rmtree(os.path.dirname(extract_root), ignore_errors=True)


def _git_remote_repo(directory: str) -> Tuple[str, str]:
    """Infer owner/repo and current branch from a tool's git remote (if any)."""
    if not directory or not os.path.isdir(directory):
        return "", ""
    try:
        remote = subprocess.run(
            ["git", "-C", directory, "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return "", ""
    if remote.returncode != 0:
        return "", ""
    match = GITHUB_REMOTE_RE.search(remote.stdout.strip())
    if not match:
        return "", ""
    repo = f"{match.group('owner')}/{match.group('repo')}"
    branch = ""
    try:
        head = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if head.returncode == 0 and head.stdout.strip() not in ("", "HEAD"):
            branch = head.stdout.strip()
    except Exception:
        pass
    return repo, branch


RUNTIME_KEYS = ("pid", "session_token", "last_started_at", "port")


def _runtime_entry(tool_id: str) -> Dict[str, Any]:
    return tools_store.load_runtime()["tools"].get(tool_id, {})


def _migrate_runtime() -> None:
    """Move pid/token/port out of the tracked registry into the ignored runtime file."""
    registry = tools_store.registry_copy()
    runtime = tools_store.load_runtime()
    changed_registry = False
    changed_runtime = False
    for tool_id, entry in registry["tools"].items():
        moved = {key: entry.pop(key) for key in RUNTIME_KEYS if key in entry}
        if not moved:
            continue
        target = runtime["tools"].setdefault(tool_id, {})
        for key, value in moved.items():
            target.setdefault(key, value)
        changed_registry = True
        changed_runtime = True
    if changed_registry:
        tools_store.save_registry(registry)
    if changed_runtime:
        tools_store.save_runtime(runtime)


def _migrate_install_dirs() -> None:
    """Move registry entries that still point at the old ~/.key-router/tools root."""
    registry = tools_store.registry_copy()
    legacy = os.path.realpath(LEGACY_TOOLS_ROOT)
    changed = False
    for tool_id, entry in registry["tools"].items():
        install_dir = entry.get("install_dir")
        if not isinstance(install_dir, str) or not install_dir:
            continue
        resolved = os.path.realpath(install_dir)
        if resolved == legacy or resolved.startswith(legacy + os.sep):
            entry["install_dir"] = tools_store.tool_dir(tool_id)
            changed = True
    if changed:
        tools_store.save_registry(registry)


def _backfill_sources() -> None:
    """Record the GitHub repo of local tools so a missing folder can be reinstalled."""
    registry = tools_store.registry_copy()
    changed = False
    for entry in registry["tools"].values():
        source = entry.get("source")
        if not isinstance(source, dict):
            source = {}
        if source.get("repo"):
            continue
        repo, branch = _git_remote_repo(entry.get("install_dir", ""))
        if not repo:
            continue
        source["repo"] = repo
        source["branch"] = source.get("branch") or branch or "main"
        entry["source"] = source
        changed = True
    if changed:
        tools_store.save_registry(registry)


def _entry_from_manifest(
    manifest: Dict[str, Any], directory: str, source: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "description": manifest["description"],
        "install_dir": directory,
        "source": source,
        "health": manifest["health"],
        "installed_at": _now(),
    }


def _save_entry(entry: Dict[str, Any]) -> None:
    registry = tools_store.registry_copy()
    registry["tools"][entry["id"]] = entry
    tools_store.save_registry(registry)


# ---------------------------------------------------------------- lifecycle

def _expand(token: str, tool_dir: str, port: int) -> str:
    return (
        token.replace("{python}", sys.executable)
        .replace("{port}", str(port))
        .replace("{tool_dir}", tool_dir)
    )


def _build_env(
    manifest: Dict[str, Any], tool_dir: str, port: int, session_token: str
) -> Dict[str, str]:
    env = os.environ.copy()
    env["KEY_ROUTER_TOOL_ID"] = manifest["id"]
    for name, spec in manifest.get("env", {}).items():
        value = spec
        if spec == "keyrouter:url":
            value = f"http://127.0.0.1:{settings.port}"
        elif spec == "generated:token":
            value = session_token
        elif spec == "tool:dir":
            value = tool_dir
        elif spec == "tool:data":
            value = tools_store.data_dir(manifest["id"])
            os.makedirs(value, exist_ok=True)
        elif spec.startswith("tool:"):
            value = secrets_store.get_tool_key(spec.split(":", 1)[1])
        if value:
            env[name] = value
    return env


def allocate_port(default: int) -> int:
    for port in range(max(1024, int(default)), 65535):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free loopback port available")


def _wait_healthy(port: int, path: str, pid: int) -> bool:
    deadline = time.time() + START_WAIT_SECONDS
    while time.time() < deadline:
        if not pid_alive(pid):
            return False
        try:
            response = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=HEALTH_TIMEOUT)
            if response.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def start_tool(tool_id: str, session_token: str = "") -> Dict[str, Any]:
    registry = tools_store.registry_copy()
    entry = registry["tools"].get(tool_id)
    if not entry:
        raise FileNotFoundError(f"No tool '{tool_id}'")

    directory = entry.get("install_dir", "")
    manifest = tools_store.load_manifest(directory)

    runtime = _runtime_entry(tool_id)
    pid = runtime.get("pid") or entry.get("pid")
    if pid and pid_alive(int(pid)):
        return tool_snapshot(entry, manifest)

    port = allocate_port(int(runtime.get("port") or entry.get("port") or manifest["default_port"]))
    session_token = session_token or secrets.token_urlsafe(24)
    argv = [_expand(token, directory, port) for token in manifest["start"]]
    env = _build_env(manifest, directory, port, session_token)

    logs_dir = tools_store.logs_dir()
    os.makedirs(logs_dir, exist_ok=True)
    log_file = Path(tools_store.log_path(tool_id))
    with open(log_file, "ab") as handle:
        handle.write(f"\n=== start {_now()} ===\n".encode("utf-8"))
        handle.flush()
        proc = subprocess.Popen(
            argv,
            cwd=directory,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            **spawn_kwargs(),
        )

    runtime = tools_store.load_runtime()
    runtime["tools"][tool_id] = {
        "pid": proc.pid,
        "port": port,
        "session_token": session_token,
        "last_started_at": _now(),
    }
    tools_store.save_runtime(runtime)

    if not _wait_healthy(port, manifest["health"], proc.pid):
        kill_tree(proc.pid)
        reap(proc.pid)
        runtime = tools_store.load_runtime()
        runtime["tools"].pop(tool_id, None)
        tools_store.save_runtime(runtime)
        raise RuntimeError(
            "Tool did not become healthy.\n" + tail_file(log_file, 40)
        )
    return tool_snapshot(entry, manifest)


def stop_tool(tool_id: str) -> Dict[str, Any]:
    registry = tools_store.registry_copy()
    entry = registry["tools"].get(tool_id)
    if not entry:
        raise FileNotFoundError(f"No tool '{tool_id}'")

    runtime = tools_store.load_runtime()
    pid = runtime["tools"].get(tool_id, {}).get("pid") or entry.get("pid")
    if pid:
        if pid_alive(int(pid)):
            kill_tree(int(pid), signal.SIGTERM)
            for _ in range(20):
                if not pid_alive(int(pid)):
                    break
                time.sleep(0.25)
            if pid_alive(int(pid)):
                kill_tree(int(pid), signal.SIGKILL)
                time.sleep(0.5)
        reap(int(pid))

    current = runtime["tools"].get(tool_id, {})
    current.pop("pid", None)
    current.pop("session_token", None)
    if current:
        runtime["tools"][tool_id] = current
    else:
        runtime["tools"].pop(tool_id, None)
    tools_store.save_runtime(runtime)
    return {"status": "stopped"}


def restart_tool(tool_id: str) -> Dict[str, Any]:
    # Keep the session token so an embedded iframe survives the restart.
    runtime = _runtime_entry(tool_id)
    entry = tools_store.load_registry()["tools"].get(tool_id)
    token = runtime.get("session_token") or (entry.get("session_token", "") if entry else "")
    stop_tool(tool_id)
    return start_tool(tool_id, session_token=token)


def run_setup(tool_id: str) -> Dict[str, Any]:
    registry = tools_store.load_registry()
    entry = registry["tools"].get(tool_id)
    if not entry:
        raise FileNotFoundError(f"No tool '{tool_id}'")

    directory = entry.get("install_dir", "")
    manifest = tools_store.load_manifest(directory)
    if not manifest["setup"]:
        return {"status": "no-setup", "exit_code": 0, "output": ""}

    runtime = _runtime_entry(tool_id)
    port = int(runtime.get("port") or entry.get("port") or manifest["default_port"])
    env = _build_env(manifest, directory, port, runtime.get("session_token") or entry.get("session_token", ""))
    argv = [_expand(token, directory, port) for token in manifest["setup"]]
    try:
        proc = subprocess.run(
            argv,
            cwd=directory,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Setup timed out after 10 minutes") from exc

    output = ((proc.stdout or "") + (proc.stderr or ""))[-6000:]
    log_file = Path(tools_store.log_path(tool_id))
    os.makedirs(log_file.parent, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as handle:
        handle.write(f"\n=== setup {_now()} (exit {proc.returncode}) ===\n{output}\n")
    return {"status": "ok" if proc.returncode == 0 else "failed", "exit_code": proc.returncode, "output": output}


def uninstall_tool(tool_id: str) -> Dict[str, Any]:
    registry = tools_store.registry_copy()
    entry = registry["tools"].get(tool_id)
    if not entry:
        raise FileNotFoundError(f"No tool '{tool_id}'")

    pid = _runtime_entry(tool_id).get("pid") or entry.get("pid")
    if pid:
        stop_tool(tool_id)
        registry = tools_store.registry_copy()

    directory = entry.get("install_dir", "")
    root = os.path.realpath(tools_store.tools_root())
    resolved = os.path.realpath(directory)
    if resolved.startswith(root + os.sep):
        shutil.rmtree(resolved, ignore_errors=True)

    try:
        os.remove(tools_store.log_path(tool_id))
    except OSError:
        pass

    registry["tools"].pop(tool_id, None)
    tools_store.save_registry(registry)

    runtime = tools_store.load_runtime()
    runtime["tools"].pop(tool_id, None)
    tools_store.save_runtime(runtime)
    return {"status": "uninstalled"}


# ---------------------------------------------------------------- status

def tool_snapshot(
    entry: Dict[str, Any], manifest: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    tool_id = entry.get("id", "")
    error = ""
    if manifest is None:
        try:
            manifest = tools_store.load_manifest(entry.get("install_dir", ""))
        except (OSError, ValueError) as exc:
            manifest = None
            error = str(exc)

    runtime = _runtime_entry(tool_id)
    pid = runtime.get("pid") or entry.get("pid")
    running = bool(pid and pid_alive(int(pid)))
    port = int(runtime.get("port") or entry.get("port") or (manifest or {}).get("default_port", 0) or 0)
    healthy = False
    if running and port:
        health = (manifest or {}).get("health", entry.get("health", "/health"))
        try:
            response = httpx.get(f"http://127.0.0.1:{port}{health}", timeout=HEALTH_TIMEOUT)
            healthy = response.status_code < 500
        except Exception:
            healthy = False

    url = f"http://127.0.0.1:{port}" if port else ""
    token = runtime.get("session_token") or entry.get("session_token", "")
    frame_url = f"{url}/?token={token}" if running and url and token else ""

    return {
        "id": tool_id,
        "name": entry.get("name") or (manifest or {}).get("name", tool_id),
        "version": entry.get("version") or (manifest or {}).get("version", ""),
        "description": entry.get("description") or (manifest or {}).get("description", ""),
        "install_dir": entry.get("install_dir", ""),
        "installed": bool(entry.get("install_dir")) and os.path.isdir(entry.get("install_dir", "")),
        "source": entry.get("source", {}),
        "port": port,
        "health": (manifest or {}).get("health", entry.get("health", "/health")),
        "has_setup": bool((manifest or {}).get("setup")),
        "running": running,
        "healthy": healthy,
        "pid": pid if running else None,
        "url": url,
        "frame_url": frame_url,
        "installed_at": entry.get("installed_at", ""),
        "last_started_at": runtime.get("last_started_at") or entry.get("last_started_at", ""),
        "error": error,
    }


def installed_tools() -> List[Dict[str, Any]]:
    _migrate_install_dirs()
    _migrate_runtime()
    _backfill_sources()
    registry = tools_store.load_registry()
    return [tool_snapshot(entry) for entry in registry["tools"].values()]


async def reinstall_tool(tool_id: str, repo: str = "", branch: str = "") -> Dict[str, Any]:
    """Download a tool again from its recorded GitHub repo (after its folder was deleted)."""
    registry = tools_store.registry_copy()
    entry = registry["tools"].get(tool_id)
    if not entry:
        raise FileNotFoundError(f"No tool '{tool_id}'")

    source = entry.get("source") or {}
    target_repo = (repo or source.get("repo") or "").strip()
    target_branch = (branch or source.get("branch") or "main").strip()
    if not target_repo:
        raise ValueError("No source repo recorded for this tool; provide owner/repo")
    if not GITHUB_REPO_RE.match(target_repo):
        raise ValueError("Repo must look like owner/name")

    pid = _runtime_entry(tool_id).get("pid") or entry.get("pid")
    if pid and pid_alive(int(pid)):
        stop_tool(tool_id)

    result = await install_tool(repo=target_repo, branch=target_branch)
    if result.get("id") != tool_id:
        raise RuntimeError(f"Repo contains tool '{result.get('id')}', expected '{tool_id}'")
    return result


def tool_logs(tool_id: str, lines: int = 60) -> Dict[str, str]:
    registry = tools_store.load_registry()
    if tool_id not in registry["tools"]:
        raise FileNotFoundError(f"No tool '{tool_id}'")
    path = Path(tools_store.log_path(tool_id))
    return {"path": str(path), "lines": tail_file(path, lines)}


def credential_specs(manifest: Dict[str, Any]) -> Dict[str, str]:
    """Credential rows a tool declares in its manifest (`tool:<name>`), name → label."""
    specs: Dict[str, str] = {}
    for spec in manifest.get("env", {}).values():
        if spec.startswith("tool:") and spec not in ENV_PLACEHOLDERS:
            name = spec.split(":", 1)[1]
            specs[name] = TOOL_KEY_LABELS.get(name, name.replace("_", " ").title())
    return specs


def _manifest_for(tool_id: str) -> Dict[str, Any]:
    entry = tools_store.load_registry()["tools"].get(tool_id)
    if not entry:
        raise FileNotFoundError(f"No tool '{tool_id}'")
    return tools_store.load_manifest(entry.get("install_dir", ""))


def tool_credentials(tool_id: str) -> List[Dict[str, Any]]:
    specs = credential_specs(_manifest_for(tool_id))
    stored = secrets_store.list_tool_keys()
    return [
        {
            "name": name,
            "label": label,
            "has_key": name in stored,
            "preview": stored.get(name, {}).get("preview", ""),
        }
        for name, label in specs.items()
    ]


def ensure_declared_credential(tool_id: str, name: str) -> None:
    specs = credential_specs(_manifest_for(tool_id))
    if name not in specs:
        raise ValueError(f"Tool '{tool_id}' does not declare a credential named '{name}'")


def get_credential(tool_id: str, name: str) -> str:
    ensure_declared_credential(tool_id, name)
    return secrets_store.get_tool_key(name)


def set_credential(tool_id: str, name: str, value: str) -> None:
    ensure_declared_credential(tool_id, name)
    secrets_store.set_tool_key(name, value)


def delete_credential(tool_id: str, name: str) -> bool:
    ensure_declared_credential(tool_id, name)
    return secrets_store.delete_tool_key(name)
