"""Driver abstraction for the video subsystem.

A driver is a thin, synchronous adapter over one generation backend. Methods are
sync so the routes can call them through `asyncio.to_thread` without pulling in
an async client per backend.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class DriverStatus:
    state: str
    progress: float = 0.0
    error: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
    output_meta: Optional[Dict[str, Any]] = None


class VideoDriver:
    name = "base"
    supports_remote_lookup = True

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or {}

    def submit(self, job: Dict[str, Any]) -> str:
        raise NotImplementedError

    def poll(self, driver_job_id: str) -> DriverStatus:
        raise NotImplementedError

    def fetch(self, driver_job_id: str) -> bytes:
        raise NotImplementedError

    def cancel(self, driver_job_id: str) -> None:
        raise NotImplementedError
