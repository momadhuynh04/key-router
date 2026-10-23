"""HTTP endpoints for the isolated /v1/videos subsystem.

AdCraft only ever calls `POST /v1/videos` with `model: "adcraft-video"` and then
polls — the whole backend (Modal/ComfyUI, hosted APIs) lives behind the
`video/` package. Job state is durable (SQLite) and advanced by **lazy polling**
on GET, so a server restart resumes polling instead of losing the job.
"""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse, JSONResponse

from config.logging import logger
from config.settings import settings
from models.video import FINAL_STATUSES, VideoGenerationRequest
from video.auth import require_video_auth
from video.errors import IdempotencyConflict, VideoApiError
from video.jobs import job_store
from video.registry import resolve_driver

router = APIRouter(dependencies=[Depends(require_video_auth)])

DEFAULT_ALIAS = "adcraft-video"


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def video_api_error_handler(request: Request, exc: VideoApiError) -> JSONResponse:
    return error_response(exc.status, exc.code, exc.message)


def _content_url(job_id: str) -> str:
    return f"http://{settings.host}:{settings.port}/v1/videos/{job_id}/content"


def _normalize_request(request: VideoGenerationRequest, cfg: Dict[str, Any]) -> Dict[str, Any]:
    low, high = (cfg.get("duration_range") or [1, 15])[:2]
    duration = request.duration_seconds
    duration = 5.0 if duration is None else float(duration)
    duration = max(float(low), min(float(high), duration))
    return {
        "model": request.model,
        "prompt": request.prompt,
        "duration_seconds": duration,
        "resolution": request.resolution or "720p",
        "aspect_ratio": request.aspect_ratio or "16:9",
        "generate_audio": bool(request.generate_audio),
        "seed": request.seed,
        "image_url": request.image_url,
        "image_urls": request.image_urls or [],
        "image_mode": request.image_mode,
        "quality": request.quality or cfg.get("quality_tier") or "auto",
        "workflow_overrides": request.workflow_overrides or {},
    }


def _request_hash(normalized: Dict[str, Any]) -> str:
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        "object": "video",
        "status": row.get("status"),
        "progress": row.get("progress") or 0.0,
        "model": row.get("model"),
        "created_at": datetime.fromtimestamp(created, tz=timezone.utc).isoformat() if created else None,
        "output": _load_json_field(row, "output_json"),
        "usage": _load_json_field(row, "usage_json"),
        "error": _load_json_field(row, "error_json"),
    }


async def submit_job(
    request: VideoGenerationRequest,
    *,
    idempotency_key: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Create (or replay) a job and submit it to the driver. Returns (row, created)."""
    model = (request.model or "").strip()
    if not model:
        raise VideoApiError(400, "invalid_request", "'model' is required.")
    if not request.prompt or not request.prompt.strip():
        raise VideoApiError(400, "invalid_request", "'prompt' is required.")

    await asyncio.to_thread(job_store.cleanup_expired)

    try:
        driver, cfg = resolve_driver(model)
    except ValueError as exc:
        raise VideoApiError(404, "unknown_model", str(exc))

    normalized = _normalize_request(request, cfg)
    request_hash = _request_hash(normalized)

    try:
        job, created = await asyncio.to_thread(
            job_store.create_or_get,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            model=model,
            driver=driver.name,
            request=normalized,
            ttl_seconds=int(settings.video_job_ttl_seconds),
        )
    except IdempotencyConflict:
        raise VideoApiError(
            409, "idempotency_conflict",
            "This Idempotency-Key was already used with a different payload.",
        )

    if created:
        job_id = job["id"]
        try:
            call_id = await asyncio.to_thread(
                driver.submit,
                {"id": job_id, "model": model, "config": cfg, "request": normalized},
            )
            job = await asyncio.to_thread(
                job_store.update, job_id, driver_job_id=call_id, status="starting"
            )
        except VideoApiError as exc:
            await asyncio.to_thread(
                job_store.update, job_id, status="failed",
                error_json=json.dumps({"code": exc.code, "message": exc.message}),
            )
            raise
        except Exception as exc:  # noqa: BLE001 - surface any driver bug as a job failure
            await asyncio.to_thread(
                job_store.update, job_id, status="failed",
                error_json=json.dumps({"code": "submit_failed", "message": str(exc)[:300]}),
            )
            raise VideoApiError(502, "submit_failed", str(exc)[:300])

    logger.info(f"[🎬] video submit model={model} driver={driver.name} job={job['id']} created={created}")
    return job, created


async def advance_job(row: Dict[str, Any]) -> Dict[str, Any]:
    """Poll the driver once and persist the result (lazy polling)."""
    if row.get("status") in FINAL_STATUSES or not row.get("driver_job_id"):
        return row

    try:
        driver, _ = resolve_driver(row["model"])
    except ValueError as exc:
        return await asyncio.to_thread(
            job_store.update, row["id"], status="failed",
            error_json=json.dumps({"code": "driver_unavailable", "message": str(exc)[:300]}),
        )

    try:
        status = await asyncio.to_thread(driver.poll, row["driver_job_id"])
    except VideoApiError as exc:
        logger.warning(f"[🎬] poll failed job={row['id']}: {exc.code} {exc.message}")
        if exc.code == "driver_job_not_found":
            # Wrong gateway URL / lost call: fail it instead of hanging at 50% forever.
            return await asyncio.to_thread(
                job_store.update, row["id"], status="failed",
                error_json=json.dumps({"code": exc.code, "message": exc.message}),
            )
        return row  # transient upstream issue: leave state as-is, try again next poll

    updates: Dict[str, Any] = {"status": status.state, "progress": float(status.progress or 0.0)}
    if status.error:
        updates["error_json"] = json.dumps(status.error)
    elif status.state == "failed":
        updates["error_json"] = json.dumps({"code": "generation_failed", "message": "Upstream reported failure."})
    if status.usage:
        updates["usage_json"] = json.dumps(status.usage)

    if status.state == "completed":
        path = job_store.video_path(row["id"])
        if not os.path.exists(path):
            try:
                data = await asyncio.to_thread(driver.fetch, row["driver_job_id"])
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(data)
            except VideoApiError as exc:
                logger.warning(f"[🎬] fetch failed job={row['id']}: {exc.code} {exc.message}")
                return await asyncio.to_thread(
                    job_store.update, row["id"], status="failed",
                    error_json=json.dumps({"code": exc.code, "message": exc.message}),
                )
        meta = status.output_meta or {}
        output: Dict[str, Any] = {
            "url": _content_url(row["id"]),
            "media_type": "video/mp4",
            "size_bytes": os.path.getsize(path) if os.path.exists(path) else None,
        }
        for key in ("duration_seconds", "resolution", "seed"):
            if meta.get(key) is not None:
                output[key] = meta[key]
        updates["output_json"] = json.dumps(output)
        updates["progress"] = 1.0
        logger.info(f"[🎬] video completed job={row['id']} bytes={output['size_bytes']}")

    return await asyncio.to_thread(job_store.update, row["id"], **updates)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/v1/videos")
async def create_video(
    request: VideoGenerationRequest,
    idempotency_key: Optional[str] = Header(default=None),
):
    job, created = await submit_job(request, idempotency_key=idempotency_key)
    return JSONResponse(status_code=202 if created else 200, content=serialize_job(job))


@router.get("/v1/videos/{job_id}")
async def get_video(job_id: str):
    job = await asyncio.to_thread(job_store.get, job_id)
    if job is None:
        raise VideoApiError(404, "video_not_found", f"No video job '{job_id}'.")
    job = await advance_job(job)
    return serialize_job(job)


@router.get("/v1/videos/{job_id}/content")
async def get_video_content(job_id: str):
    job = await asyncio.to_thread(job_store.get, job_id)
    if job is None:
        raise VideoApiError(404, "video_not_found", f"No video job '{job_id}'.")
    job = await advance_job(job)
    if job.get("status") != "completed":
        raise VideoApiError(409, "video_not_ready", f"Video is '{job.get('status')}', not ready yet.")
    path = job_store.video_path(job_id)
    if not os.path.exists(path):
        raise VideoApiError(404, "video_not_found", "Video file is no longer available.")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")


@router.post("/v1/videos/{job_id}/cancel")
async def cancel_video(job_id: str):
    job = await asyncio.to_thread(job_store.get, job_id)
    if job is None:
        raise VideoApiError(404, "video_not_found", f"No video job '{job_id}'.")
    if job.get("status") in FINAL_STATUSES:
        return serialize_job(job)
    if job.get("driver_job_id"):
        try:
            driver, _ = resolve_driver(job["model"])
            await asyncio.to_thread(driver.cancel, job["driver_job_id"])
        except VideoApiError:
            pass
    job = await asyncio.to_thread(job_store.update, job_id, status="cancelled")
    return serialize_job(job)
