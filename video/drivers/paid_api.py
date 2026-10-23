"""Placeholder driver for hosted video APIs (Seedance / Veo / Replicate video).

Kept in the registry so aliases like `adcraft-video-hero` resolve without wiring
a second backend yet. Implement `submit/poll/fetch/cancel` when a hosted provider
is chosen (Phase 3 in adcraft_video.md).
"""

from typing import Any, Dict

from video.drivers.base import DriverStatus, VideoDriver
from video.errors import VideoUpstreamError


class PaidApiDriver(VideoDriver):
    name = "paid_api"

    def _not_implemented(self):
        raise VideoUpstreamError(
            "paid_api video driver is not implemented yet. Configure a hosted provider first.",
            status=501,
            code="driver_not_implemented",
        )

    def submit(self, job: Dict[str, Any]) -> str:
        self._not_implemented()

    def poll(self, driver_job_id: str) -> DriverStatus:
        self._not_implemented()

    def fetch(self, driver_job_id: str) -> bytes:
        self._not_implemented()

    def cancel(self, driver_job_id: str) -> None:
        self._not_implemented()
