"""Parse agent-control directives out of a chat message.

Open WebUI only sees a single model, so the agent is chosen with `/start <agent-id>`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_DIRECTIVE_RE = re.compile(
    r"^/(start|stop|agents|list|help)\b[ \t]*(.*)$", re.IGNORECASE | re.DOTALL
)


@dataclass
class Parsed:
    kind: str                 # "start" | "stop" | "agents" | "prompt"
    agent_id: str | None
    text: str                 # remaining prompt text after the directive


def parse(message: str) -> Parsed:
    text = (message or "").strip()
    m = _DIRECTIVE_RE.match(text)
    if not m:
        return Parsed(kind="prompt", agent_id=None, text=message or "")

    cmd = m.group(1).lower()
    rest = m.group(2).strip()

    if cmd in ("agents", "list", "help"):
        return Parsed(kind="agents", agent_id=None, text=rest)

    if cmd == "stop":
        return Parsed(kind="stop", agent_id=rest.split()[0] if rest else None, text="")

    # /start [agent-id] [remaining prompt]
    if not rest:
        return Parsed(kind="start", agent_id=None, text="")
    parts = rest.split(None, 1)
    return Parsed(
        kind="start",
        agent_id=parts[0],
        text=parts[1] if len(parts) > 1 else "",
    )
