# AdCraft Video route (`adcraft-video`) — KeyRouter side

Async video generation for AdCraft. AdCraft only ever talks to KeyRouter with
`model: "adcraft-video"`; the real backend (Modal + ComfyUI, or a hosted API)
lives behind the isolated `video/` package.

```
AdCraft ─► KeyRouter /v1/videos*  ─► video/registry.py ─► video/drivers/*
                                                              │
                                                Modal CPU gateway ─► GPU ComfyUI
```

## Contract

Unlike the image route (sync), video is **async**: submit → poll → fetch.

```
POST /v1/videos
Authorization: Bearer <KEYROUTER_API_KEY>       # only if the key is configured
Idempotency-Key: <uuid>                          # optional, recommended
{
  "model": "adcraft-video",
  "prompt": "...",
  "duration_seconds": 5,
  "resolution": "720p",
  "aspect_ratio": "16:9",
  "generate_audio": false,
  "seed": 12345,
  "image_url": "https://...",        // optional first frame
  "image_urls": ["...","..."],       // optional first/last or refs
  "image_mode": "first_frame|first_last|reference",
  "quality": "draft|standard|hero",
  "workflow_overrides": {"steps": 4}
}
→ 202 { "id": "vid_...", "object": "video", "status": "queued", "progress": 0, ... }
```

```
GET  /v1/videos/{id}           → status/progress/output/usage/error
GET  /v1/videos/{id}/content   → video/mp4 bytes
POST /v1/videos/{id}/cancel
```

Statuses: `queued | starting | loading | running | completed | failed | cancelled`.

When finished:

```json
{
  "status": "completed",
  "output": { "url": "http://127.0.0.1:8082/v1/videos/vid_.../content",
              "duration_seconds": 5, "resolution": "832x480", "seed": 12345,
              "media_type": "video/mp4", "size_bytes": 7340032 },
  "usage": { "gpu": "L40S", "cold_start_seconds": 48, "generation_seconds": 96,
             "gpu_seconds": 150, "estimated_cost_usd": 0.081 }
}
```

Errors: non-2xx + `{"error": {"code": "...", "message": "..."}}`.
Codes: `invalid_request`, `unknown_model`, `idempotency_conflict`, `unauthorized`,
`video_not_configured`, `video_not_found`, `video_not_ready`, `submit_failed`,
`upstream_error`, `upstream_timeout`, `video_too_large`, `driver_not_implemented`.

### Idempotency

Same `Idempotency-Key` + same payload → the **same** job is returned (HTTP 200,
no new GPU job). Same key with a different payload → `409 idempotency_conflict`.
The request hash covers every normalized field (model, prompt, duration,
resolution, aspect ratio, audio, seed, images, quality, overrides).

## How it works (lazy polling)

There is **no background worker**. Job state lives in SQLite
(`data/video_jobs.db`) and is advanced on each `GET /v1/videos/{id}`:

1. If the job is in a final state → return as-is.
2. Otherwise poll the driver once and persist `status/progress/usage`.
3. On `completed` → download the MP4 to `data/videos/<id>.mp4` and set `output.url`.
4. On `failed` → persist the error.

Because `driver_job_id` is durable, a server restart simply resumes polling — the
Modal `call_id` is still valid. AdCraft's own `async_file` poll loop drives it.

## Configuration

Aliases live in `config.json → video_models` (a separate namespace — `ModelMapper`
and the text/image routes are untouched, and no alias can hit the gpt/codex
fallback).

```json
"video_models": {
  "adcraft-video":          { "driver": "modal_gateway", "family": "wan22_i2v_14b_4step", "quality_tier": "standard" },
  "adcraft-video-draft":    { "driver": "modal_gateway", "family": "wan22_ti2v_5b",       "quality_tier": "draft" },
  "adcraft-video-draft-i2v":{ "driver": "modal_gateway", "family": "wan22_i2v_5b",         "quality_tier": "draft" },
  "adcraft-video-standard": { "driver": "modal_gateway", "family": "wan22_i2v_14b_4step",  "quality_tier": "standard" },
  "adcraft-video-hero":     { "driver": "paid_api",      "provider": "replicate",          "quality_tier": "hero" }
}
```

`adcraft-video` là alias đích AdCraft gọi; 4 dòng còn lại là preset (WebUI dropdown).
`adcraft-video-draft` tự chuyển sang `wan22_i2v_5b` khi request có ảnh
(`fallback_with_image`); preset `-draft-i2v` để ép chọn tường minh.

**Reference images:** mọi family Wan2.2 hiện tại nhận **đúng 1 ảnh** (start frame;
`Wan22ImageToVideoLatent.start_image` / `WanImageToVideo.start_image`). API vẫn
nhận `image_url` và `image_urls`, nhưng chỉ ảnh đầu tiên được dùng; các ảnh sau bị
bỏ qua và có cảnh báo trong log của worker. `max_references = 1` trong config.

Per-alias keys: `driver`, `app`, `family`, `quality_tier`, `provider`,
`upstream_model`, `max_references`, `duration_range`, `resolutions`,
`aspect_ratios`, `supports_native_audio`, `extra`.

Drivers: `modal_gateway` (Modal + ComfyUI, see `modal/README.md`) and `paid_api`
(hosted APIs — stub until a provider is chosen).

GPU tiers on the Wan Modal app: `draft`/`auto` → L40S (Wan2.2 TI2V-5B),
`standard` → A100-80GB (Wan2.2 I2V-A14B 4-step). Families live in
`modal/workflows/` + `_param_map.json`.

### MiniMax H3 (FL2VA + Ref2VA)

Separate Modal app (`minimaxh3.py`), same gateway contract and `modal_gateway`
driver. Two GPU workers share one weights Volume:

| Alias | Family | Max refs | Mode |
| --- | --- | --- | --- |
| `adcraft-video-h3-fl2va` | `minimax_h3_fl2va` | 2 | T2V / first frame / first+last frame |
| `adcraft-video-h3-ref2va` | `minimax_h3_ref2va` | 9 | omni-reference (images) |
| `adcraft-video-hero` | `minimax_h3_ref2va` | 9 | hero tier, points at Ref2VA |

- Output: 4–15s, 24fps, native 32kHz stereo audio, canvas capped ~1344x768.
- Reference images may be `data:` URLs or public `http(s)` URLs reachable from
  Modal. Loopback/private URLs are rejected — send data URLs for local media.
- Both workers run on A100-80GB (`max_containers=1` each, scale-to-zero).

## ENV (`.env`)

```
KEYROUTER_API_KEY=""                 # set -> /v1/videos* requires Bearer auth
MODAL_VIDEO_GATEWAY_URL=""           # printed by `modal deploy modal/video_app.py`
MODAL_VIDEO_GATEWAY_TOKEN=""         # ALSO used by minimaxh3.py; enforced when non-empty
MODAL_H3_GATEWAY_URL=""              # printed by `modal deploy minimaxh3.py`
```

If `KEYROUTER_API_KEY` is empty the proxy stays unauthenticated (current
behaviour); the key only guards `/v1/videos*`.

## WebUI / management API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/adcraft-video/status` | resolved profile, endpoints, auth + gateway status |
| GET | `/api/adcraft-video/models` | available profiles (aliases) |
| POST | `/api/adcraft-video/model` | `{ "target": "adcraft-video-standard" }` — repoint `adcraft-video` |
| POST | `/api/adcraft-video/test` | `{ "prompt": ..., "wait_seconds": 75 }` — submit + bounded poll |
| GET | `/api/adcraft-video/jobs` | recent jobs |

WebUI tab **AdCraft video**: profile selector, endpoint/API-key/gateway info,
test button with player preview, recent jobs list.

## Isolated by design

- New code: `video/` package, `proxy/routers/videos.py`, `modal/`, `scripts/video-bench/`.
- Touched existing files: `proxy/app.py` (include router + one exception handler),
  `config/settings.py`, `config.json`, `.gitignore`, `.env.example`, `webui/src/App.tsx`,
  `proxy/routers/management.py`.
- No new runtime dependency (stdlib `sqlite3` + existing `httpx`).

## Deploy & benchmark

1. Fill Phase 0 values in `modal/video_app.py` (GPU, SHAs, `MODEL_FILES`, workflows).
2. `modal run modal/video_app.py::download_models && modal deploy modal/video_app.py`.
3. `modal run minimaxh3.py::download_models && modal deploy minimaxh3.py` (H3).
4. Set `MODAL_VIDEO_GATEWAY_URL`, `MODAL_H3_GATEWAY_URL` and
   `MODAL_VIDEO_GATEWAY_TOKEN` in `.env`, restart key-router.
5. `python scripts/video-bench/bench.py --gpu L40S --runs 3` → `results.md`.

## AdCraft side (Phase 2 — not in this repo)

AdCraft adds a `keyrouter_video_native` transport: submit with `Idempotency-Key`,
poll `GET /v1/videos/{id}`, then download `GET /v1/videos/{id}/content`
(cap the download size, e.g. 64MB). Note its `_normalize_provider_base_url` only
accepts `https`, so `http://127.0.0.1:8082` must be configured via `.env` (or add
a loopback-http policy on the AdCraft side).
