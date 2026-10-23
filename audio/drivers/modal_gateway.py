"""Driver that talks to the Modal audio gateway (see modal/audio_app.py).

The video bytes are sent inline as base64 (`video_b64`), because the Modal
container cannot reach AdCraft's loopback media server.
"""

from typing import Any, Dict, Optional

import httpx

from audio.drivers.base import AudioDriver, DriverStatus
from audio.errors import AudioNotConfigured, AudioUpstreamError
from config.logging import log_warn
from config.settings import settings


class ModalAudioGatewayDriver(AudioDriver):
    name = "modal_gateway"

    def _base(self) -> str:
        url = (settings.modal_audio_gateway_url or "").rstrip("/")
        if not url:
            raise AudioNotConfigured(
                "MODAL_AUDIO_GATEWAY_URL is not set. Deploy modal/audio_app.py, then set the gateway URL."
            )
        return url

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.modal_audio_gateway_token:
            headers["Authorization"] = f"Bearer {settings.modal_audio_gateway_token}"
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
            "seed": req.get("seed"),
            "response_format": req.get("response_format") or "wav",
            "video_b64": job.get("video_b64"),
            "extra": cfg.get("extra") or {},
        }
        return {k: v for k, v in payload.items() if v is not None}

    def _request(self, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None,
                 timeout: Optional[float] = None) -> httpx.Response:
        url = f"{self._base()}{path}"
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=timeout or settings.audio_poll_timeout_seconds) as client:
                    resp = client.request(method, url, headers=self._headers(), json=json_body)
                if resp.status_code >= 500 and attempt == 0:
                    last_error = None
                    continue
                return resp
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt == 0:
                    continue
        raise AudioUpstreamError(
            f"Modal audio gateway unreachable: {last_error}", status=504, code="upstream_timeout"
        )

    def submit(self, job: Dict[str, Any]) -> str:
        resp = self._request(
            "POST", "/generate", json_body=self._payload(job),
            timeout=settings.audio_submit_timeout_seconds,
        )
        if resp.status_code >= 400:
            raise AudioUpstreamError(
                f"Gateway rejected job (HTTP {resp.status_code}): {resp.text[:300]}",
                status=502 if resp.status_code >= 500 else 400,
                code="submit_failed",
            )
        call_id = (resp.json() or {}).get("call_id")
        if not call_id:
            raise AudioUpstreamError("Gateway did not return a call_id.")
        return str(call_id)

    def poll(self, driver_job_id: str) -> DriverStatus:
        resp = self._request("GET", f"/status/{driver_job_id}")
        if resp.status_code == 404:
            raise AudioUpstreamError("Gateway job not found.", status=404, code="driver_job_not_found")
        if resp.status_code >= 400:
            raise AudioUpstreamError(f"Gateway status error (HTTP {resp.status_code}): {resp.text[:300]}")
        data = resp.json() or {}
        state = data.get("status") or "running"
        if state == "completed":  # gateway vocabulary -> audio vocabulary
            state = "succeeded"
        return DriverStatus(
            state=state,
            progress=float(data.get("progress") or 0.0),
            error=data.get("error"),
            usage=data.get("usage"),
            output_meta=data.get("output"),
        )

    def fetch(self, driver_job_id: str) -> bytes:
        resp = self._request("GET", f"/content/{driver_job_id}", timeout=settings.audio_content_timeout_seconds)
        if resp.status_code >= 400:
            raise AudioUpstreamError(f"Gateway content error (HTTP {resp.status_code}).")
        data = resp.content
        if len(data) > settings.audio_max_output_bytes:
            raise AudioUpstreamError(
                "Generated audio exceeds the configured size limit.",
                status=413, code="audio_too_large",
            )
        return data

    def cancel(self, driver_job_id: str) -> None:
        try:
            self._request("POST", f"/cancel/{driver_job_id}")
        except AudioUpstreamError as exc:
            log_warn(f"[🔊] cancel failed for {driver_job_id}: {exc}")
