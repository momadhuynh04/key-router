"""Schemas for the isolated /v1/videos subsystem (AdCraft video route)."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class VideoStatus(str, Enum):
    queued = "queued"
    starting = "starting"
    loading = "loading"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


FINAL_STATUSES = {
    VideoStatus.completed.value,
    VideoStatus.failed.value,
    VideoStatus.cancelled.value,
}


class VideoUsage(BaseModel):
    gpu: Optional[str] = None
    cold_start_seconds: Optional[float] = None
    generation_seconds: Optional[float] = None
    gpu_seconds: Optional[float] = None
    estimated_cost_usd: Optional[float] = None


class VideoOutput(BaseModel):
    url: Optional[str] = None
    duration_seconds: Optional[float] = None
    resolution: Optional[str] = None
    seed: Optional[int] = None
    media_type: str = "video/mp4"
    size_bytes: Optional[int] = None


class VideoGenerationRequest(BaseModel):
    model: Optional[str] = None
    prompt: Optional[str] = None
    duration_seconds: Optional[float] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    generate_audio: Optional[bool] = False
    seed: Optional[int] = None
    image_url: Optional[str] = None
    image_urls: Optional[List[str]] = None
    image_mode: Optional[str] = None
    quality: Optional[str] = None
    workflow_overrides: Optional[Dict[str, Any]] = None
    # Unknown fields from AdCraft are silently ignored.
    model_config = {"extra": "ignore"}


class VideoJobResponse(BaseModel):
    id: str
    object: str = "video"
    status: str
    progress: float = 0.0
    model: Optional[str] = None
    created_at: Optional[str] = None
    output: Optional[VideoOutput] = None
    usage: Optional[VideoUsage] = None
    error: Optional[Dict[str, Any]] = None
