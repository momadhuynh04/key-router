import asyncio
import sys

import pytest

from bridge import runner
from bridge.config import BridgeConfig
from bridge.runner import ActiveTurns, RunError, run_cli


def _cfg():
    return BridgeConfig(turn_timeout_sec=15)


def test_long_jsonl_line_is_read(tmp_path):
    """A base64 image makes one JSONL event far larger than asyncio's 64 KiB default."""
    script = (
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'type': 'result', 'pad': 'a' * 200000}) + '\\n')\n"
        "sys.stdout.flush()\n"
    )

    async def consume():
        events = []
        async for event in run_cli(
            _cfg(),
            [sys.executable, "-c", script],
            tmp_path,
            "",
            "chat",
            "a1",
            ActiveTurns(),
        ):
            events.append(event)
        return events

    events = asyncio.run(consume())
    assert len(events) == 1
    assert len(events[0]["pad"]) == 200_000


def test_over_limit_line_raises_run_error(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_LINE_BYTES", 4096)
    script = "import sys; sys.stdout.write('x' * 20000 + '\\n')"

    async def consume():
        async for _ in run_cli(
            _cfg(),
            [sys.executable, "-c", script],
            tmp_path,
            "",
            "chat",
            "a1",
            ActiveTurns(),
        ):
            pass

    with pytest.raises(RunError):
        asyncio.run(consume())
