"""SQLite-backed job store for the video subsystem.

Design notes:
- Sync sqlite3 connection guarded by a lock; callers hit it from the event loop
  via `asyncio.to_thread`, so nothing blocks the server.
- State is durable, which is what makes lazy polling work: after a restart the
  job row (with `driver_job_id`) is still there and polling simply resumes.
- Idempotency is enforced by a UNIQUE(idempotency_key, request_hash) index.
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from video.errors import IdempotencyConflict

_COLUMNS = (
    "id", "idempotency_key", "request_hash", "model", "driver", "driver_job_id",
    "status", "progress", "request_json", "output_json", "usage_json", "error_json",
    "created_at", "updated_at", "expires_at",
)

_MUTABLE = {
    "driver_job_id", "status", "progress", "output_json", "usage_json", "error_json",
    "updated_at", "expires_at", "model", "driver",
}


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VideoJobStore:
    def __init__(self, data_dir: Optional[str] = None, db_filename: str = "video_jobs.db",
                 media_subdir: str = "videos", media_ext: str = ".mp4", id_prefix: str = "vid_"):
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self.data_dir = data_dir
        self.db_filename = db_filename
        self.media_subdir = media_subdir
        self.media_ext = media_ext
        self.id_prefix = id_prefix

    # -- paths -------------------------------------------------------------
    def configure(self, data_dir: str) -> None:
        """Repoint the store (mainly for tests)."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None
            self.data_dir = data_dir

    @property
    def dir(self) -> str:
        base = self.data_dir or settings.video_output_dir or os.path.join(_repo_root(), "data")
        return os.path.abspath(base)

    @property
    def videos_dir(self) -> str:
        return os.path.join(self.dir, self.media_subdir)

    def video_path(self, job_id: str) -> str:
        return os.path.join(self.videos_dir, f"{job_id}{self.media_ext}")

    # -- connection --------------------------------------------------------
    def _connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                os.makedirs(self.dir, exist_ok=True)
                os.makedirs(self.videos_dir, exist_ok=True)
                conn = sqlite3.connect(os.path.join(self.dir, self.db_filename), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        idempotency_key TEXT,
                        request_hash TEXT,
                        model TEXT,
                        driver TEXT,
                        driver_job_id TEXT,
                        status TEXT,
                        progress REAL DEFAULT 0,
                        request_json TEXT,
                        output_json TEXT,
                        usage_json TEXT,
                        error_json TEXT,
                        created_at REAL,
                        updated_at REAL,
                        expires_at REAL
                    )
                    """
                )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idem ON jobs(idempotency_key, request_hash)"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")
                conn.commit()
                self._conn = conn
            return self._conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    # -- writes ------------------------------------------------------------
    def create_or_get(
        self,
        *,
        idempotency_key: Optional[str],
        request_hash: str,
        model: str,
        driver: str,
        request: Dict[str, Any],
        ttl_seconds: int,
    ) -> Tuple[Dict[str, Any], bool]:
        """Return (job, created). Raises IdempotencyConflict on key reuse w/ new payload."""
        now = time.time()
        with self._lock:
            conn = self._connection()
            if idempotency_key:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if row is not None:
                    existing = self._row_to_dict(row)
                    if existing.get("request_hash") == request_hash:
                        return existing, False
                    raise IdempotencyConflict()

            job_id = f"{self.id_prefix}{uuid.uuid4().hex[:24]}"
            row_values = (
                job_id, idempotency_key, request_hash, model, driver, None,
                "queued", 0.0, json.dumps(request, ensure_ascii=False), None, None, None,
                now, now, now + ttl_seconds,
            )
            placeholders = ", ".join("?" for _ in _COLUMNS)
            try:
                conn.execute(f"INSERT INTO jobs ({', '.join(_COLUMNS)}) VALUES ({placeholders})", row_values)
                conn.commit()
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ? AND request_hash = ?",
                    (idempotency_key, request_hash),
                ).fetchone()
                if row is None:
                    raise
                return self._row_to_dict(row), False
            return self.get_unlocked(conn, job_id), True

    def update(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        updates = {k: v for k, v in fields.items() if k in _MUTABLE}
        if not updates:
            return self.get(job_id)
        updates.setdefault("updated_at", time.time())
        columns = ", ".join(f"{k} = ?" for k in updates)
        with self._lock:
            conn = self._connection()
            conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*updates.values(), job_id))
            conn.commit()
            return self.get_unlocked(conn, job_id)

    def delete(self, job_id: str) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()

    # -- reads -------------------------------------------------------------
    def get_unlocked(self, conn: sqlite3.Connection, job_id: str) -> Optional[Dict[str, Any]]:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.get_unlocked(self._connection(), job_id)

    def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connection()
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def cleanup_expired(self) -> List[str]:
        """Delete expired rows and their MP4 files. Returns removed job ids."""
        now = time.time()
        with self._lock:
            conn = self._connection()
            rows = conn.execute("SELECT id FROM jobs WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)).fetchall()
            removed = [r["id"] for r in rows]
            if removed:
                conn.executemany("DELETE FROM jobs WHERE id = ?", [(jid,) for jid in removed])
                conn.commit()
        for job_id in removed:
            path = self.video_path(job_id)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        return removed


job_store = VideoJobStore()
