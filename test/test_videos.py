import base64
import json
import os

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from config.settings import settings
from proxy.server import app
from video.drivers.base import DriverStatus, VideoDriver
from video.drivers.modal_gateway import ModalGatewayDriver
from video.errors import VideoNotConfigured
from video.jobs import VideoJobStore, job_store

client = TestClient(app)

MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"fake-mp4-payload"

CFG = {
    "alias": "adcraft-video",
    "driver": "modal_gateway",
    "app": "wan22",
    "family": "wan22_ti2v_5b",
    "quality_tier": "auto",
    "duration_range": [1, 15],
    "resolutions": ["480p", "720p", "1080p"],
    "aspect_ratios": ["16:9", "9:16", "1:1"],
    "max_references": 9,
    "supports_native_audio": False,
    "extra": {},
}


class FakeDriver(VideoDriver):
    name = "modal_gateway"

    def __init__(self, cfg=None, states=None):
        super().__init__(cfg)
        self._states = list(states or ["running", "completed"])
        self._idx = 0
        self.submitted = []
        self.cancelled = []

    def submit(self, job):
        self.submitted.append(job)
        return "call_fake_1"

    def poll(self, driver_job_id):
        state = self._states[min(self._idx, len(self._states) - 1)]
        self._idx += 1
        return DriverStatus(
            state=state,
            progress=0.5 if state == "running" else 1.0,
            usage={"gpu": "L40S", "gpu_seconds": 12.0},
            output_meta={"duration_seconds": 5, "resolution": "1280x720", "seed": 42},
        )

    def fetch(self, driver_job_id):
        return MP4_BYTES

    def cancel(self, driver_job_id):
        self.cancelled.append(driver_job_id)


@pytest.fixture
def video_env(tmp_path, monkeypatch):
    """Isolated job store + fake driver + no auth."""
    job_store.configure(str(tmp_path))
    fake = FakeDriver()
    monkeypatch.setattr("proxy.routers.videos.resolve_driver", lambda alias: (fake, CFG))
    monkeypatch.setattr(settings, "keyrouter_api_key", "")
    monkeypatch.setattr(settings, "modal_video_gateway_url", "")
    return fake


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

def test_missing_model_returns_400(video_env):
    resp = client.post("/v1/videos", json={"prompt": "hello"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_missing_prompt_returns_400(video_env):
    resp = client.post("/v1/videos", json={"model": "adcraft-video"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_submit_poll_and_content(video_env):
    resp = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "ocean waves", "duration_seconds": 5})
    assert resp.status_code == 202
    job = resp.json()
    assert job["object"] == "video"
    assert job["status"] == "starting"
    job_id = job["id"]

    first = client.get(f"/v1/videos/{job_id}")
    assert first.status_code == 200
    assert first.json()["status"] == "running"

    second = client.get(f"/v1/videos/{job_id}")
    body = second.json()
    assert body["status"] == "completed"
    assert body["progress"] == 1.0
    assert body["output"]["url"].endswith(f"/v1/videos/{job_id}/content")
    assert body["usage"]["gpu"] == "L40S"

    content = client.get(f"/v1/videos/{job_id}/content")
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("video/mp4")
    assert content.content == MP4_BYTES


def test_content_not_ready_returns_409(video_env):
    resp = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "x"})
    job_id = resp.json()["id"]
    content = client.get(f"/v1/videos/{job_id}/content")
    assert content.status_code == 409
    assert content.json()["error"]["code"] == "video_not_ready"


def test_unknown_job_returns_404(video_env):
    resp = client.get("/v1/videos/vid_does_not_exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "video_not_found"


def test_cancel(video_env):
    resp = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "x"})
    job_id = resp.json()["id"]
    cancel = client.post(f"/v1/videos/{job_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    assert video_env.cancelled == ["call_fake_1"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_idempotency_replay_same_job(video_env):
    headers = {"Idempotency-Key": "key-1"}
    payload = {"model": "adcraft-video", "prompt": "same prompt"}
    first = client.post("/v1/videos", json=payload, headers=headers)
    second = client.post("/v1/videos", json=payload, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(video_env.submitted) == 1


def test_idempotency_conflict(video_env):
    headers = {"Idempotency-Key": "key-2"}
    client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "a"}, headers=headers)
    resp = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "b"}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "idempotency_conflict"


# ---------------------------------------------------------------------------
# Auth (only /v1/videos)
# ---------------------------------------------------------------------------

def test_auth_enforced_when_key_set(video_env, monkeypatch):
    monkeypatch.setattr(settings, "keyrouter_api_key", "sekret")
    unauth = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "x"})
    assert unauth.status_code == 401
    assert unauth.json()["error"]["code"] == "unauthorized"

    wrong = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "x"},
                        headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401

    ok = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "x"},
                     headers={"Authorization": "Bearer sekret"})
    assert ok.status_code == 202


def test_other_routes_unaffected_by_video_auth(monkeypatch):
    monkeypatch.setattr(settings, "keyrouter_api_key", "sekret")
    # /api/health must stay unauthenticated
    assert client.get("/api/health").status_code == 200


# ---------------------------------------------------------------------------
# Configuration / durability
# ---------------------------------------------------------------------------

def test_gateway_not_configured_returns_503(tmp_path, monkeypatch):
    job_store.configure(str(tmp_path))
    monkeypatch.setattr(settings, "keyrouter_api_key", "")
    monkeypatch.setattr(settings, "modal_video_gateway_url", "")
    monkeypatch.setattr(settings, "modal_h3_gateway_url", "")
    resp = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "x"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "video_not_configured"


def test_unknown_alias_returns_404(tmp_path, monkeypatch):
    job_store.configure(str(tmp_path))
    monkeypatch.setattr(settings, "keyrouter_api_key", "")
    resp = client.post("/v1/videos", json={"model": "nope-video", "prompt": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_model"


def test_job_persisted_and_resumable(video_env, tmp_path):
    resp = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "x"})
    job_id = resp.json()["id"]

    # Simulate a server restart: a brand-new store on the same dir sees the job.
    fresh = VideoJobStore(str(tmp_path))
    row = fresh.get(job_id)
    assert row is not None
    assert row["driver_job_id"] == "call_fake_1"

    # Polling resumes from the durable state.
    resumed = client.get(f"/v1/videos/{job_id}")
    assert resumed.json()["status"] == "running"


def test_ttl_cleanup_removes_row_and_file(video_env, tmp_path):
    resp = client.post("/v1/videos", json={"model": "adcraft-video", "prompt": "x"})
    job_id = resp.json()["id"]
    client.get(f"/v1/videos/{job_id}")
    client.get(f"/v1/videos/{job_id}")  # completes -> writes file

    path = job_store.video_path(job_id)
    assert os.path.exists(path)

    job_store.update(job_id, expires_at=0)  # force expiry
    removed = job_store.cleanup_expired()
    assert job_id in removed
    assert not os.path.exists(path)
    assert job_store.get(job_id) is None


# ---------------------------------------------------------------------------
# Modal gateway driver (httpx mocked)
# ---------------------------------------------------------------------------

@patch("httpx.Client")
def test_modal_driver_roundtrip(mock_client_cls, monkeypatch):
    monkeypatch.setattr(settings, "modal_video_gateway_url", "http://gw.local")
    monkeypatch.setattr(settings, "modal_video_gateway_token", "tok")

    instance = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = instance
    seen = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        seen.setdefault("calls", []).append((method, url, json))
        seen["headers"] = headers
        resp = MagicMock()
        if url.endswith("/generate"):
            resp.status_code = 200
            resp.json.return_value = {"call_id": "c1"}
            resp.text = ""
        elif "/status/" in url:
            resp.status_code = 200
            resp.json.return_value = {"status": "running", "progress": 0.4, "usage": {"gpu": "H100"}}
        elif "/content/" in url:
            resp.status_code = 200
            resp.content = MP4_BYTES
        elif "/cancel/" in url:
            resp.status_code = 200
            resp.json.return_value = {"cancelled": True}
        else:
            resp.status_code = 404
        return resp

    instance.request.side_effect = fake_request
    driver = ModalGatewayDriver(CFG)

    call_id = driver.submit({"id": "vid_1", "model": "adcraft-video", "config": CFG,
                             "request": {"prompt": "hi", "duration_seconds": 5}})
    assert call_id == "c1"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    submit_body = seen["calls"][0][2]
    assert submit_body["alias"] == "adcraft-video"
    assert submit_body["family"] == "wan22_ti2v_5b"
    assert submit_body["prompt"] == "hi"

    status = driver.poll("c1")
    assert status.state == "running"
    assert status.progress == 0.4
    assert status.usage["gpu"] == "H100"

    assert driver.fetch("c1") == MP4_BYTES
    driver.cancel("c1")
    assert any(method == "POST" and url.endswith("/cancel/c1") for method, url, _ in seen["calls"])


def test_modal_driver_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "modal_video_gateway_url", "")
    driver = ModalGatewayDriver(CFG)
    with pytest.raises(VideoNotConfigured):
        driver.submit({"id": "vid_1", "model": "adcraft-video", "config": CFG, "request": {"prompt": "hi"}})


# ---------------------------------------------------------------------------
# Management endpoints
# ---------------------------------------------------------------------------

def test_video_status_and_models_endpoints():
    status = client.get("/api/adcraft-video/status")
    assert status.status_code == 200
    assert status.json()["alias"] == "adcraft-video"
    assert status.json()["endpoint"].endswith("/v1/videos")

    models = client.get("/api/adcraft-video/models")
    assert models.status_code == 200
    ids = [item["id"] for item in models.json()["models"]]
    # `adcraft-video` is the destination alias, not a selectable preset.
    assert "adcraft-video" not in ids
    assert "adcraft-video-draft" in ids
    assert "adcraft-video-draft-i2v" in ids
    assert "adcraft-video-standard" in ids


def test_video_test_endpoint(video_env):
    resp = client.post("/api/adcraft-video/test", json={"wait_seconds": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"]["id"].startswith("vid_")
    assert data["driver"] == "modal_gateway"


def test_video_test_endpoint_with_reference(video_env):
    resp = client.post("/api/adcraft-video/test", json={"wait_seconds": 0, "with_reference": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["with_reference"] is True

    row = job_store.get(data["job"]["id"])
    request = json.loads(row["request_json"])
    assert request["image_url"].startswith("data:image/png;base64,")


def test_reference_test_image_is_valid_256_png():
    from proxy.routers.images import reference_test_image_data_url

    raw = base64.b64decode(reference_test_image_data_url().split(",", 1)[1])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert int.from_bytes(raw[16:20], "big") == 256
    assert int.from_bytes(raw[20:24], "big") == 256
