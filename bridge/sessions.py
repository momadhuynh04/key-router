"""Session state keyed by chat (the `X-OpenWebUI-Chat-Id` header).

Because the bridge re-spawns the CLI on every turn, a "session" here is just a small
record: which agent a chat is bound to, and the CLI session id needed for `--resume`.

`_meta` holds chat-scoped extras that are independent of the binding: a pending
`ask_user` question and queued sub-agent handoffs. Keeping them separate means
`/call` works even before the chat has been bound to an agent.
"""
from __future__ import annotations

import json
import threading
from typing import Dict, Optional

from bridge.config import STATE_PATH

GLOBAL_KEY = "__global__"
META_KEY = "__meta__"


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}
        self._meta: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(raw, dict):
            return

        meta = raw.pop(META_KEY, {})
        self._meta = meta if isinstance(meta, dict) else {}

        for key, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            # Older state stored pending/subruns inside the binding entry.
            pending = entry.pop("pending", None)
            subruns = entry.pop("subruns", None)
            if pending or subruns:
                slot = self._meta.setdefault(key, {})
                if pending:
                    slot["pending"] = pending
                if subruns:
                    slot["subruns"] = subruns
            self._data[key] = entry

    def _save(self) -> None:
        try:
            payload: Dict[str, dict] = dict(self._data)
            if self._meta:
                payload[META_KEY] = self._meta
            STATE_PATH.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
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
            self._drop_pending(chat_key)
            self._save()
            return dict(entry)

    def _drop_pending(self, chat_key: str) -> None:
        meta = self._meta.get(chat_key)
        if not meta:
            return
        meta.pop("pending", None)
        if not meta:
            self._meta.pop(chat_key, None)

    def get_pending(self, chat_key: str) -> Optional[dict]:
        with self._lock:
            pending = (self._meta.get(chat_key) or {}).get("pending")
            return dict(pending) if pending else None

    def set_pending(self, chat_key: str, pending: Optional[dict]) -> None:
        """Remember (or clear) a question the agent asked the user."""
        with self._lock:
            if pending:
                self._meta.setdefault(chat_key, {})["pending"] = pending
            else:
                self._drop_pending(chat_key)
            self._save()

    def add_subrun(self, chat_key: str, record: dict) -> bool:
        """Queue a one-off sub-agent result for the next main-agent turn."""
        with self._lock:
            self._meta.setdefault(chat_key, {}).setdefault("subruns", []).append(record)
            self._save()
            return True

    def take_subruns(self, chat_key: str) -> list:
        """Return queued sub-agent results and clear them."""
        with self._lock:
            meta = self._meta.get(chat_key)
            if not meta:
                return []
            runs = meta.pop("subruns", []) or []
            if not meta:
                self._meta.pop(chat_key, None)
            if runs:
                self._save()
            return runs

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
            self._meta.pop(chat_key, None)
            self._save()
            return entry


store = SessionStore()
