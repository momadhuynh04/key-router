"""Schemas for the isolated /v1/audio/video-to-sfx subsystem (AdCraft SFX)."""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel


class AudioStatus(str, Enum):
    queued = "queued"
    starting = "starting"
    loading = "loading"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


FINAL_STATUSES = {
    AudioStatus.succeeded.value,
    AudioStatus.failed.value,
    AudioStatus.cancelled.value,
}


class AudioUsage(BaseModel):
    gpu: Optional[str] = None
    cold_start_seconds: Optional[float] = None
    generation_seconds: Optional[float] = None
    gpu_seconds: Optional[float] = None
    estimated_cost_usd: Optional[float] = None


class AudioOutput(BaseModel):
    url: Optional[str] = None
    media_type: str = "audio/wav"
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    size_bytes: Optional[int] = None


class AudioGenerationRequest(BaseModel):
    model: Optional[str] = None
    # One of video_url (fetched by key-router from an allowlisted host) or
    # video_b64 (inline, used by the internal test endpoint).
    video_url: Optional[str] = None
    video_b64: Optional[str] = None
    prompt: Optional[str] = None
    duration_seconds: Optional[float] = None
    seed: Optional[int] = None
    response_format: Optional[str] = "wav"
    # Unknown fields from AdCraft are silently ignored.
    model_config = {"extra": "ignore"}
