"""Session state keyed by chat (the `X-OpenWebUI-Chat-Id` header).

Because the bridge re-spawns the CLI on every turn, a "session" here is just a small
record: which agent a chat is bound to, and the CLI session id needed for `--resume`.
"""
from __future__ import annotations

import json
import threading
from typing import Dict, Optional

from bridge.config import STATE_PATH

GLOBAL_KEY = "__global__"


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            self._data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _save(self) -> None:
        try:
            STATE_PATH.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def get(self, chat_key: str) -> Optional[dict]:
        with self._lock:
            entry = self._data.get(chat_key)
            return dict(entry) if entry else None

    def bind(self, chat_key: str, agent_id: str, session_id: Optional[str] = None) -> dict:
        with self._lock:
            entry = {"agent_id": agent_id, "session_id": session_id}
            self._data[chat_key] = entry
            self._save()
            return dict(entry)

    def set_session_id(self, chat_key: str, session_id: str) -> None:
        with self._lock:
            entry = self._data.get(chat_key)
            if entry is None:
                return
            entry["session_id"] = session_id
            self._save()

    def unbind(self, chat_key: str) -> Optional[dict]:
        with self._lock:
            entry = self._data.pop(chat_key, None)
            self._save()
            return entry


store = SessionStore()
