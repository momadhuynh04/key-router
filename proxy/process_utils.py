"""Small cross-platform process helpers shared by the bridge and tools hub."""
from __future__ import annotations

import os
import platform
import signal
import subprocess
from pathlib import Path


def kill_tree(pid: int, sig: int = signal.SIGTERM) -> None:
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


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    if platform.system() != "Windows":
        # A stopped child stays as a zombie (state Z) until reaped; kill(pid, 0)
        # still succeeds for it, so treat it as dead and let reap() clean it up.
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            state = stat.rsplit(")", 1)[1].split()[0]
            if state == "Z":
                return False
        except (OSError, IndexError):
            pass
    return True


def reap(pid: int) -> None:
    """Collect a dead child of this process so it does not linger as a zombie."""
    if platform.system() == "Windows":
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


def spawn_kwargs() -> dict:
    """Keep the child in its own group so a stop can kill the whole tree."""
    if platform.system() == "Windows":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def tail_file(path: Path, lines: int) -> str:
    if not path.exists():
        return "(no log yet)"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(could not read the log: {exc})"
    return "\n".join(content[-lines:]) if content else "(log is empty)"
