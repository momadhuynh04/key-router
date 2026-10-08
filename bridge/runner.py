"""Spawn the CLI headless and read its JSONL stream back.

Every turn is its own process (spawn + resume). Each process is placed in a new
session so the whole process group can be killed — this prevents orphaned agents
from continuing to edit the repo after a timeout or a Stop.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

from bridge.config import BridgeConfig

STDERR_LINES = 40
KILL_GRACE_SEC = 5.0


class TurnTimeout(RuntimeError):
    pass


class RunError(RuntimeError):
    pass


def proxy_env(cfg: BridgeConfig) -> Dict[str, str]:
    """Environment for the CLI so it calls back into key-router.

    Declared here instead of importing from `proxy/` on purpose: the bridge has no
    code-level dependency on key-router, so it can be split into its own repo later.
    """
    env = os.environ.copy()
    base = cfg.key_router_url.rstrip("/")
    env["ANTHROPIC_BASE_URL"] = base
    env["ANTHROPIC_API_KEY"] = "key-router"
    env["OPENAI_BASE_URL"] = f"{base}/v1"
    env["KEY_ROUTER_API_KEY"] = "key-router"
    return env


def kill_tree(pid: int, sig: int = signal.SIGTERM) -> None:
    if platform.system() == "Windows":
        try:
            os.kill(pid, sig)
        except OSError:
            pass
        return
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


@dataclass
class RunningTurn:
    chat_key: str
    pid: int
    agent_id: str
    started: float = field(default_factory=time.time)
    process: Optional[asyncio.subprocess.Process] = None

    def age(self) -> float:
        return max(0.0, time.time() - self.started)

    def kill(self, sig: int = signal.SIGTERM) -> None:
        kill_tree(self.pid, sig)
        if self.process is not None:
            try:
                self.process.kill()
            except (ProcessLookupError, OSError):
                pass


class ActiveTurns:
    """Turns currently running, so `/stop` and the dashboard can kill them."""

    def __init__(self) -> None:
        self._turns: Dict[str, RunningTurn] = {}

    def add(self, turn: RunningTurn) -> None:
        self._turns[turn.chat_key] = turn

    def remove(self, chat_key: str) -> None:
        self._turns.pop(chat_key, None)

    def get(self, chat_key: str) -> Optional[RunningTurn]:
        return self._turns.get(chat_key)

    def kill(self, chat_key: str) -> bool:
        turn = self._turns.get(chat_key)
        if turn is None:
            return False
        turn.kill()
        return True

    def all(self) -> Dict[str, RunningTurn]:
        return dict(self._turns)


async def _drain(stream: Optional[asyncio.StreamReader], sink: Deque[str]) -> None:
    if stream is None:
        return
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            sink.append(line.decode("utf-8", "replace").rstrip())
    except (asyncio.CancelledError, OSError):
        pass


async def run_cli(
    cfg: BridgeConfig,
    argv: List[str],
    cwd: Path,
    prompt: str,
    chat_key: str,
    agent_id: str,
    turns: ActiveTurns,
) -> AsyncIterator[Dict[str, Any]]:
    """Spawn the CLI and yield each JSON event. Cleans up the process on every exit path."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=proxy_env(cfg),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        raise RunError(f"CLI {argv[0]!r} not found in PATH") from e

    turn = RunningTurn(chat_key=chat_key, pid=proc.pid, agent_id=agent_id, process=proc)
    turns.add(turn)
    err_sink: Deque[str] = deque(maxlen=STDERR_LINES)
    err_task = asyncio.create_task(_drain(proc.stderr, err_sink))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg.turn_timeout_sec
    timed_out = False

    try:
        # The prompt goes through stdin (see bridge/registry.py for why argv is not used).
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = True
                raise TurnTimeout(
                    f"Turn exceeded {cfg.turn_timeout_sec}s and was stopped"
                )
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), remaining)
            except asyncio.TimeoutError as e:
                timed_out = True
                raise TurnTimeout(
                    f"Turn exceeded {cfg.turn_timeout_sec}s and was stopped"
                ) from e
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue

        await proc.wait()
        if proc.returncode and not timed_out:
            detail = "\n".join(err_sink)[-800:]
            raise RunError(
                f"{argv[0]} exited with code {proc.returncode}"
                + (f": {detail}" if detail else "")
            )
    finally:
        turns.remove(chat_key)
        if proc.returncode is None:
            kill_tree(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), KILL_GRACE_SEC)
            except asyncio.TimeoutError:
                kill_tree(proc.pid, signal.SIGKILL)
                try:
                    await asyncio.wait_for(proc.wait(), KILL_GRACE_SEC)
                except asyncio.TimeoutError:
                    pass
        if not err_task.done():
            err_task.cancel()
        try:
            await err_task
        except (asyncio.CancelledError, Exception):
            pass


# Shared registry for the whole app: /stop, /health and shutdown all look at this.
TURNS = ActiveTurns()


def kill_all() -> int:
    """Kill every running turn — used when the bridge shuts down."""
    count = 0
    for key in list(TURNS.all().keys()):
        if TURNS.kill(key):
            count += 1
    return count
