"""HTTP endpoints for the isolated /v1/audio/video-to-sfx subsystem.

AdCraft sends a `video_url` pointing at its local media server. Because the Modal
worker cannot reach AdCraft's loopback, key-router (which runs locally) fetches
the video bytes and forwards them inline (base64) to the gateway.
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import tempfile
import wave
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse, JSONResponse

from audio.auth import require_audio_auth
from audio.config import DEFAULT_PROMPT
from audio.errors import AudioApiError, AudioIdempotencyConflict
from audio.jobs import audio_job_store
from audio.registry import resolve_audio_driver
from config.logging import logger
from config.settings import settings
from models.audio import FINAL_STATUSES, AudioGenerationRequest

router = APIRouter(dependencies=[Depends(require_audio_auth)])

DEFAULT_ALIAS = "adcraft-audio"
PROMPT_MAX_CHARS = 2000


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def audio_api_error_handler(request: Request, exc: AudioApiError) -> JSONResponse:
    return error_response(exc.status, exc.code, exc.message)


def _content_url(job_id: str) -> str:
    return f"http://{settings.host}:{settings.port}/v1/audio/video-to-sfx/{job_id}/content"


def _load_json_field(row: Dict[str, Any], key: str) -> Optional[Any]:
    raw = row.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def serialize_job(row: Dict[str, Any]) -> Dict[str, Any]:
    created = row.get("created_at")
    return {
        "id": row["id"],
        "object": "audio",
        "status": row.get("status"),
        "progress": row.get("progress") or 0.0,
        "model": row.get("model"),
        "created_at": datetime.fromtimestamp(created, tz=timezone.utc).isoformat() if created else None,
        "output": _load_json_field(row, "output_json"),
        "usage": _load_json_field(row, "usage_json"),
        "error": _load_json_field(row, "error_json"),
    }


# ---------------------------------------------------------------------------
# Video input helpers (SSRF-safe fetch + probe + WAV metadata)
# ---------------------------------------------------------------------------

def _allowed_hosts() -> set[str]:
    raw = settings.audio_video_allow_hosts or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


async def fetch_video(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AudioApiError(400, "video_url_invalid", "'video_url' must be an http(s) URL.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    hostname = parsed.hostname.lower()
    allowed = _allowed_hosts()
    if f"{hostname}:{port}" not in allowed and hostname not in allowed:
        raise AudioApiError(
            403, "video_url_not_allowed",
            f"Host '{hostname}:{port}' is not in the media allowlist.",
        )

    max_bytes = int(settings.audio_max_video_bytes)
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise AudioApiError(502, "video_unreachable", f"Video server returned HTTP {resp.status_code}.")
                declared = resp.headers.get("content-length")
                if declared and int(declared) > max_bytes:
                    raise AudioApiError(413, "video_too_large", f"Video exceeds {max_bytes} bytes.")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise AudioApiError(413, "video_too_large", f"Video exceeds {max_bytes} bytes.")
                return bytes(buf)
    except AudioApiError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise AudioApiError(502, "video_unreachable", f"Failed to fetch video: {exc}")


def _ffprobe_duration(path: str) -> Optional[float]:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


async def probe_video_duration(data: bytes) -> Optional[float]:
    def _run() -> Optional[float]:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
            fh.write(data)
            path = fh.name
        try:
            return _ffprobe_duration(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    return await asyncio.to_thread(_run)


def wav_metadata(data: bytes) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate = wav.getframerate()
            frames = wav.getnframes()
            channels = wav.getnchannels()
            return (frames / rate if rate else None), rate, channels
    except (wave.Error, EOFError):
        return None, None, None


def _normalize_prompt(prompt: Optional[str]) -> str:
    text = (prompt or DEFAULT_PROMPT).strip()
    if len(text) > PROMPT_MAX_CHARS:
        raise AudioApiError(400, "invalid_request", f"'prompt' exceeds {PROMPT_MAX_CHARS} characters.")
    if "no speech" not in text.lower():
        text = f"{text}; no speech, no dialogue, no music"
    return text


# ---------------------------------------------------------------------------
# Job submission / lazy polling
# ---------------------------------------------------------------------------

async def submit_audio_job(
    request_model: AudioGenerationRequest,
    *,
    idempotency_key: Optional[str] = None,
    video_bytes: Optional[bytes] = None,
) -> Tuple[Dict[str, Any], bool]:
    model = (request_model.model or "").strip()
    if not model:
        raise AudioApiError(400, "invalid_request", "'model' is required.")

    try:
        driver, cfg = resolve_audio_driver(model)
    except ValueError as exc:
        raise AudioApiError(404, "unknown_model", str(exc))

    await asyncio.to_thread(audio_job_store.cleanup_expired)

    if video_bytes is None:
        if request_model.video_b64:
            try:
                video_bytes = base64.b64decode(request_model.video_b64, validate=True)
            except Exception:
                raise AudioApiError(400, "invalid_request", "'video_b64' is not valid base64.")
        elif request_model.video_url:
            video_bytes = await fetch_video(request_model.video_url)
        else:
            raise AudioApiError(400, "invalid_request", "'video_url' or 'video_b64' is required.")

    if not video_bytes:
        raise AudioApiError(400, "invalid_request", "Video is empty.")
    if len(video_bytes) > int(settings.audio_max_video_bytes):
        raise AudioApiError(413, "video_too_large", "Video exceeds the configured size limit.")

    video_duration = await probe_video_duration(video_bytes)
    client_duration = request_model.duration_seconds
    candidates = [d for d in (client_duration, video_duration) if d]
    duration = min(candidates) if candidates else 5.0
    duration = max(1.0, min(float(duration), float(cfg.get("max_video_seconds") or 15)))

    normalized = {
        "model": model,
        "prompt": _normalize_prompt(request_model.prompt),
        "duration_seconds": duration,
        "seed": request_model.seed,
        "response_format": (request_model.response_format or "wav").lower(),
        "video_sha256": hashlib.sha256(video_bytes).hexdigest(),
        "video_bytes": len(video_bytes),
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    try:
        job, created = await asyncio.to_thread(
            audio_job_store.create_or_get,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            model=model,
            driver=driver.name,
            request=normalized,
            ttl_seconds=int(settings.audio_job_ttl_seconds),
        )
    except AudioIdempotencyConflict:
        raise AudioApiError(
            409, "idempotency_conflict",
            "This Idempotency-Key was already used with a different payload.",
        )

    if created:
        job_id = job["id"]
        try:
            call_id = await asyncio.to_thread(
                driver.submit,
                {
                    "id": job_id,
                    "model": model,
                    "config": cfg,
                    "request": normalized,
                    "video_b64": base64.b64encode(video_bytes).decode("ascii"),
                },
            )
            job = await asyncio.to_thread(
                audio_job_store.update, job_id, driver_job_id=call_id, status="starting"
            )
        except AudioApiError as exc:
            await asyncio.to_thread(
                audio_job_store.update, job_id, status="failed",
                error_json=json.dumps({"code": exc.code, "message": exc.message}),
            )
            raise
        except Exception as exc:  # noqa: BLE001
            await asyncio.to_thread(
                audio_job_store.update, job_id, status="failed",
                error_json=json.dumps({"code": "submit_failed", "message": str(exc)[:300]}),
            )
            raise AudioApiError(502, "submit_failed", str(exc)[:300])

    logger.info(f"[🔊] audio submit model={model} driver={driver.name} job={job['id']} created={created}")
    return job, created


async def advance_job(row: Dict[str, Any]) -> Dict[str, Any]:
    if row.get("status") in FINAL_STATUSES or not row.get("driver_job_id"):
        return row

    try:
        driver, _ = resolve_audio_driver(row["model"])
    except ValueError as exc:
        return await asyncio.to_thread(
            audio_job_store.update, row["id"], status="failed",
            error_json=json.dumps({"code": "driver_unavailable", "message": str(exc)[:300]}),
        )

    try:
        status = await asyncio.to_thread(driver.poll, row["driver_job_id"])
    except AudioApiError as exc:
        logger.warning(f"[🔊] poll failed job={row['id']}: {exc.code} {exc.message}")
        if exc.code == "driver_job_not_found":
            return await asyncio.to_thread(
                audio_job_store.update, row["id"], status="failed",
                error_json=json.dumps({"code": exc.code, "message": exc.message}),
            )
        return row

    updates: Dict[str, Any] = {"status": status.state, "progress": float(status.progress or 0.0)}
    if status.error:
        updates["error_json"] = json.dumps(status.error)
    elif status.state == "failed":
        updates["error_json"] = json.dumps({"code": "generation_failed", "message": "Upstream reported failure."})
    if status.usage:
        updates["usage_json"] = json.dumps(status.usage)

    if status.state == "succeeded":
        path = audio_job_store.video_path(row["id"])
        if not os.path.exists(path):
            try:
                data = await asyncio.to_thread(driver.fetch, row["driver_job_id"])
            except AudioApiError as exc:
                return await asyncio.to_thread(
                    audio_job_store.update, row["id"], status="failed",
                    error_json=json.dumps({"code": exc.code, "message": exc.message}),
                )
            if not (data.startswith(b"RIFF") and data[8:12] == b"WAVE"):
                return await asyncio.to_thread(
                    audio_job_store.update, row["id"], status="failed",
                    error_json=json.dumps({"code": "invalid_audio", "message": "Upstream returned non-WAV audio."}),
                )
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)

        with open(path, "rb") as fh:
            data = fh.read()
        duration, rate, channels = wav_metadata(data)
        updates["output_json"] = json.dumps({
            "url": _content_url(row["id"]),
            "media_type": "audio/wav",
            "duration_seconds": duration,
            "sample_rate": rate,
            "channels": channels,
            "size_bytes": len(data),
        })
        updates["progress"] = 1.0
        logger.info(f"[🔊] audio succeeded job={row['id']} bytes={len(data)} duration={duration}")

    return await asyncio.to_thread(audio_job_store.update, row["id"], **updates)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/v1/audio/video-to-sfx")
async def create_sfx_job(
    request: AudioGenerationRequest,
    idempotency_key: Optional[str] = Header(default=None),
):
    job, created = await submit_audio_job(request, idempotency_key=idempotency_key)
    return JSONResponse(status_code=202 if created else 200, content=serialize_job(job))


@router.get("/v1/audio/video-to-sfx/{job_id}")
async def get_sfx_job(job_id: str):
    job = await asyncio.to_thread(audio_job_store.get, job_id)
    if job is None:
        raise AudioApiError(404, "audio_not_found", f"No audio job '{job_id}'.")
    job = await advance_job(job)
    return serialize_job(job)


@router.get("/v1/audio/video-to-sfx/{job_id}/content")
async def get_sfx_content(job_id: str):
    job = await asyncio.to_thread(audio_job_store.get, job_id)
    if job is None:
        raise AudioApiError(404, "audio_not_found", f"No audio job '{job_id}'.")
    job = await advance_job(job)
    if job.get("status") != "succeeded":
        raise AudioApiError(409, "audio_not_ready", f"Audio is '{job.get('status')}', not ready yet.")
    path = audio_job_store.video_path(job_id)
    if not os.path.exists(path):
        raise AudioApiError(404, "audio_not_found", "Audio file is no longer available.")
    return FileResponse(path, media_type="audio/wav", filename=f"{job_id}.wav")


@router.post("/v1/audio/video-to-sfx/{job_id}/cancel")
async def cancel_sfx_job(job_id: str):
    job = await asyncio.to_thread(audio_job_store.get, job_id)
    if job is None:
        raise AudioApiError(404, "audio_not_found", f"No audio job '{job_id}'.")
    if job.get("status") in FINAL_STATUSES:
        return serialize_job(job)
    if job.get("driver_job_id"):
        try:
            driver, _ = resolve_audio_driver(job["model"])
            await asyncio.to_thread(driver.cancel, job["driver_job_id"])
        except AudioApiError:
            pass
    job = await asyncio.to_thread(audio_job_store.update, job_id, status="cancelled")
    return serialize_job(job)
