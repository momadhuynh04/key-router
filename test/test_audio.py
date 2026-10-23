import base64
import io
import json
import os
import wave

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from audio.drivers.base import AudioDriver, DriverStatus
from audio.jobs import audio_job_store
from config.settings import settings
from proxy.server import app

client = TestClient(app)


def _wav_bytes(seconds=1.0, rate=44100, channels=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * int(rate * seconds))
    return buf.getvalue()


WAV = _wav_bytes()
VIDEO = b"\x00\x00\x00\x18ftypmp42fake-video-bytes"
AUDIO_CFG = {
    "alias": "adcraft-audio",
    "driver": "modal_gateway",
    "app": "thinksound",
    "family": "thinksound_light",
    "quality_tier": "default",
    "max_video_seconds": 15,
    "response_format": "wav",
    "extra": {},
}


class FakeAudioDriver(AudioDriver):
    name = "modal_gateway"

    def __init__(self, data=WAV):
        super().__init__(AUDIO_CFG)
        self._data = data
        self.submitted = []
        self.cancelled = []

    def submit(self, job):
        self.submitted.append(job)
        return "call_audio_1"

    def poll(self, driver_job_id):
        return DriverStatus(
            state="succeeded",
            progress=1.0,
            usage={"gpu": "L40S", "gpu_seconds": 12.0},
            output_meta={"sample_rate": 44100, "channels": 2},
        )

    def fetch(self, driver_job_id):
        return self._data

    def cancel(self, driver_job_id):
        self.cancelled.append(driver_job_id)


@pytest.fixture
def audio_env(tmp_path, monkeypatch):
    audio_job_store.configure(str(tmp_path))
    fake = FakeAudioDriver()
    monkeypatch.setattr("proxy.routers.audio.resolve_audio_driver", lambda alias: (fake, AUDIO_CFG))
    monkeypatch.setattr("proxy.routers.audio.probe_video_duration", AsyncMock(return_value=5.0))
    monkeypatch.setattr(settings, "keyrouter_api_key", "")
    monkeypatch.setattr(settings, "modal_audio_gateway_url", "")
    return fake


def _payload(**overrides):
    payload = {
        "model": "adcraft-audio",
        "video_b64": base64.b64encode(VIDEO).decode(),
        "prompt": "footsteps on gravel, cloth rustle",
        "duration_seconds": 5,
        "response_format": "wav",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

def test_submit_poll_and_content(audio_env):
    resp = client.post("/v1/audio/video-to-sfx", json=_payload())
    assert resp.status_code == 202
    job = resp.json()
    assert job["object"] == "audio"
    assert job["status"] == "starting"
    job_id = job["id"]

    status = client.get(f"/v1/audio/video-to-sfx/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "succeeded"
    assert body["output"]["sample_rate"] == 44100
    assert body["output"]["channels"] == 2
    assert body["output"]["media_type"] == "audio/wav"

    content = client.get(f"/v1/audio/video-to-sfx/{job_id}/content")
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("audio/wav")
    assert content.content == WAV


def test_requires_video_source(audio_env):
    resp = client.post("/v1/audio/video-to-sfx", json={"model": "adcraft-audio", "prompt": "x"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_ssrf_rejects_unknown_host(audio_env):
    resp = client.post("/v1/audio/video-to-sfx", json={
        "model": "adcraft-audio",
        "video_url": "http://evil.example.com/video.mp4",
        "prompt": "x",
    })
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "video_url_not_allowed"


def test_video_too_large(audio_env, monkeypatch):
    monkeypatch.setattr(settings, "audio_max_video_bytes", 10)
    resp = client.post("/v1/audio/video-to-sfx", json=_payload())
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "video_too_large"


def test_prompt_policy_appended(audio_env):
    client.post("/v1/audio/video-to-sfx", json=_payload(prompt="footsteps"))
    request = audio_env.submitted[0]["request"]
    assert "no speech" in request["prompt"].lower()


def test_invalid_audio_marks_failed(tmp_path, monkeypatch):
    audio_job_store.configure(str(tmp_path))
    fake = FakeAudioDriver(data=b"not a wav file")
    monkeypatch.setattr("proxy.routers.audio.resolve_audio_driver", lambda alias: (fake, AUDIO_CFG))
    monkeypatch.setattr("proxy.routers.audio.probe_video_duration", AsyncMock(return_value=5.0))
    monkeypatch.setattr(settings, "keyrouter_api_key", "")

    resp = client.post("/v1/audio/video-to-sfx", json=_payload())
    job_id = resp.json()["id"]
    status = client.get(f"/v1/audio/video-to-sfx/{job_id}").json()
    assert status["status"] == "failed"
    assert status["error"]["code"] == "invalid_audio"


# ---------------------------------------------------------------------------
# Idempotency + auth
# ---------------------------------------------------------------------------

def test_idempotency_replay(audio_env):
    headers = {"Idempotency-Key": "sfx-1"}
    first = client.post("/v1/audio/video-to-sfx", json=_payload(), headers=headers)
    second = client.post("/v1/audio/video-to-sfx", json=_payload(), headers=headers)
    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(audio_env.submitted) == 1


def test_idempotency_conflict(audio_env):
    headers = {"Idempotency-Key": "sfx-2"}
    client.post("/v1/audio/video-to-sfx", json=_payload(prompt="a"), headers=headers)
    resp = client.post("/v1/audio/video-to-sfx", json=_payload(prompt="b"), headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "idempotency_conflict"


def test_auth_enforced(audio_env, monkeypatch):
    monkeypatch.setattr(settings, "keyrouter_api_key", "sekret")
    assert client.post("/v1/audio/video-to-sfx", json=_payload()).status_code == 401
    ok = client.post("/v1/audio/video-to-sfx", json=_payload(),
                     headers={"Authorization": "Bearer sekret"})
    assert ok.status_code == 202


def test_unknown_alias_404(tmp_path, monkeypatch):
    audio_job_store.configure(str(tmp_path))
    monkeypatch.setattr(settings, "keyrouter_api_key", "")
    resp = client.post("/v1/audio/video-to-sfx", json=_payload(model="nope"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_model"


def test_gateway_not_configured_503(tmp_path, monkeypatch):
    audio_job_store.configure(str(tmp_path))
    monkeypatch.setattr(settings, "keyrouter_api_key", "")
    monkeypatch.setattr(settings, "modal_audio_gateway_url", "")
    resp = client.post("/v1/audio/video-to-sfx", json=_payload())
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "audio_not_configured"


# ---------------------------------------------------------------------------
# Management endpoints
# ---------------------------------------------------------------------------

def test_audio_status_and_models():
    status = client.get("/api/adcraft-audio/status")
    assert status.status_code == 200
    assert status.json()["alias"] == "adcraft-audio"
    assert status.json()["endpoint"].endswith("/v1/audio/video-to-sfx")

    models = client.get("/api/adcraft-audio/models")
    assert models.status_code == 200
    ids = [item["id"] for item in models.json()["models"]]
    assert "adcraft-audio" in ids


def test_audio_test_endpoint_uses_latest_video(audio_env, tmp_path):
    from video.jobs import job_store as video_store

    video_store.configure(str(tmp_path))
    row, _ = video_store.create_or_get(
        idempotency_key=None, request_hash="vh", model="adcraft-video",
        driver="modal_gateway", request={}, ttl_seconds=3600,
    )
    video_store.update(row["id"], status="completed")
    os.makedirs(os.path.dirname(video_store.video_path(row["id"])), exist_ok=True)
    with open(video_store.video_path(row["id"]), "wb") as fh:
        fh.write(VIDEO)

    resp = client.post("/api/adcraft-audio/test", json={"wait_seconds": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"]["id"].startswith("aud_")
    assert data["video_source"] == f"video_job:{row['id']}"
