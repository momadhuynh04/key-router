"""Driver that talks to the Modal CPU-only gateway (see modal/video_app.py).

Distinct from the Model Context Protocol style used elsewhere: this is a plain
HTTP contract owned by this repo, so key-router stays the single source of truth
for job state. Retries once on 5xx / transport errors.
"""

from typing import Any, Dict, Optional

import httpx

from config.logging import log_warn
from config.settings import settings
from video.drivers.base import DriverStatus, VideoDriver
from video.errors import VideoNotConfigured, VideoUpstreamError


class ModalGatewayDriver(VideoDriver):
    name = "modal_gateway"

    # -- helpers -----------------------------------------------------------
    def _base(self) -> str:
        app = str((self.cfg or {}).get("app") or "").lower()
        if app in ("minimax_h3", "h3"):
            url = (settings.modal_h3_gateway_url or "").rstrip("/")
            if not url:
                raise VideoNotConfigured(
                    "MODAL_H3_GATEWAY_URL is not set. Deploy minimaxh3.py, then set the gateway URL."
                )
            return url
        url = (settings.modal_video_gateway_url or "").rstrip("/")
        if not url:
            raise VideoNotConfigured(
                "MODAL_VIDEO_GATEWAY_URL is not set. Deploy modal/video_app.py, then set the gateway URL."
            )
        return url

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.modal_video_gateway_token:
            headers["Authorization"] = f"Bearer {settings.modal_video_gateway_token}"
        return headers

    @staticmethod
    def _payload(job: Dict[str, Any]) -> Dict[str, Any]:
        cfg = job.get("config") or {}
        req = job.get("request") or {}
        payload = {
            "alias": job.get("model"),
            "app": cfg.get("app"),
            "family": cfg.get("family"),
            "quality_tier": cfg.get("quality_tier"),
            "prompt": req.get("prompt"),
            "duration_seconds": req.get("duration_seconds"),
            "resolution": req.get("resolution"),
            "aspect_ratio": req.get("aspect_ratio"),
            "generate_audio": req.get("generate_audio"),
            "seed": req.get("seed"),
            "image_url": req.get("image_url"),
            "image_urls": req.get("image_urls"),
            "image_mode": req.get("image_mode"),
            "quality": req.get("quality"),
            "workflow_overrides": req.get("workflow_overrides"),
            "extra": cfg.get("extra") or {},
        }
        return {k: v for k, v in payload.items() if v is not None}

    def _request(self, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None,
                 timeout: Optional[float] = None) -> httpx.Response:
        url = f"{self._base()}{path}"
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=timeout or settings.video_poll_timeout_seconds) as client:
                    resp = client.request(method, url, headers=self._headers(), json=json_body)
                if resp.status_code >= 500 and attempt == 0:
                    last_error = None
                    continue
                return resp
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt == 0:
                    continue
        raise VideoUpstreamError(
            f"Modal gateway unreachable: {last_error}", status=504, code="upstream_timeout"
        )

    # -- VideoDriver -------------------------------------------------------
    def submit(self, job: Dict[str, Any]) -> str:
        resp = self._request(
            "POST", "/generate", json_body=self._payload(job),
            timeout=settings.video_submit_timeout_seconds,
        )
        if resp.status_code >= 400:
            code = "submit_failed" if resp.status_code < 500 else "upstream_error"
            raise VideoUpstreamError(
                f"Gateway rejected job (HTTP {resp.status_code}): {resp.text[:300]}",
                status=502 if resp.status_code >= 500 else 400,
                code=code,
            )
        call_id = (resp.json() or {}).get("call_id")
        if not call_id:
            raise VideoUpstreamError("Gateway did not return a call_id.")
        return str(call_id)

    def poll(self, driver_job_id: str) -> DriverStatus:
        resp = self._request("GET", f"/status/{driver_job_id}")
        if resp.status_code == 404:
            raise VideoUpstreamError("Gateway job not found.", status=404, code="driver_job_not_found")
        if resp.status_code >= 400:
            raise VideoUpstreamError(
                f"Gateway status error (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        data = resp.json() or {}
        return DriverStatus(
            state=data.get("status") or "running",
            progress=float(data.get("progress") or 0.0),
            error=data.get("error"),
            usage=data.get("usage"),
            output_meta=data.get("output"),
        )

    def fetch(self, driver_job_id: str) -> bytes:
        resp = self._request(
            "GET", f"/content/{driver_job_id}", timeout=settings.video_content_timeout_seconds
        )
        if resp.status_code >= 400:
            raise VideoUpstreamError(f"Gateway content error (HTTP {resp.status_code}).")
        data = resp.content
        if len(data) > settings.video_max_output_bytes:
            raise VideoUpstreamError(
                "Generated video exceeds the configured size limit.",
                status=413, code="video_too_large",
            )
        return data

    def cancel(self, driver_job_id: str) -> None:
        try:
            self._request("POST", f"/cancel/{driver_job_id}")
        except VideoUpstreamError as exc:
            log_warn(f"[🎬] cancel failed for {driver_job_id}: {exc}")
