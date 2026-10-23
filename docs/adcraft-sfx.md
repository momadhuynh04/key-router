# AdCraft SFX route (`adcraft-audio`) — KeyRouter side

Video-to-SFX: AdCraft sends a video, KeyRouter runs a V2A model and returns a
WAV with **diegetic sound effects / ambience — no speech, no dialogue, no music**.

```
AdCraft ─► KeyRouter /v1/audio/video-to-sfx ─► audio/drivers/modal_gateway.py
                                                    │  (base64 video inline)
                                                    ▼
                                          Modal CPU gateway ──spawn──► GPU ThinkSound
```

## Contract (async)

```
POST /v1/audio/video-to-sfx
Authorization: Bearer <KEYROUTER_API_KEY>      # only if configured
Idempotency-Key: <uuid>                         # optional, recommended
{
  "model": "adcraft-audio",
  "video_url": "http://127.0.0.1:8000/media/...",   // or "video_b64"
  "prompt": "footsteps on wet pavement, cloth rustle, distant room tone",
  "duration_seconds": 7.0,
  "seed": 123,
  "response_format": "wav"
}
→ 202 { "id": "aud_...", "object": "audio", "status": "queued", ... }
```

```
GET  /v1/audio/video-to-sfx/{id}           → status/output/usage/error
GET  /v1/audio/video-to-sfx/{id}/content   → audio/wav bytes
POST /v1/audio/video-to-sfx/{id}/cancel
```

Statuses: `queued | starting | loading | running | succeeded | failed | cancelled`.

Finished:

```json
{
  "status": "succeeded",
  "output": { "url": "http://127.0.0.1:8082/v1/audio/video-to-sfx/aud_.../content",
              "media_type": "audio/wav", "duration_seconds": 7.0,
              "sample_rate": 44100, "channels": 2, "size_bytes": 1234567 },
  "usage": { "gpu": "L40S", "generation_seconds": 42.1, "gpu_seconds": 42.1 }
}
```

Errors: non-2xx + `{"error": {"code": "...", "message": "..."}}`.
Codes: `invalid_request`, `unknown_model`, `idempotency_conflict`, `unauthorized`,
`audio_not_configured`, `audio_not_found`, `audio_not_ready`, `video_url_invalid`,
`video_url_not_allowed`, `video_unreachable`, `video_too_large`, `audio_too_large`,
`invalid_audio`, `submit_failed`, `upstream_error`, `upstream_timeout`.

## Video input (important)

The plan assumed the Modal worker could fetch AdCraft's `127.0.0.1:8000` URL. It
cannot — Modal containers are in the cloud. So **key-router (which runs locally)
fetches the video** and forwards the bytes inline (base64) to the gateway.

- `video_url` must match `AUDIO_VIDEO_ALLOW_HOSTS` (default
  `127.0.0.1:8000,localhost:8000,127.0.0.1:8082`); other hosts → `403`.
- Fetch: no redirects, 30s timeout, max `AUDIO_MAX_VIDEO_BYTES` (default 100MB).
- Duration is read with `ffprobe` and clamped to `min(client, video, 15s)`.

## Limits / policy

- Video 1–15s (AdCraft sends 5–10s); prompt ≤ 2000 chars.
- Output must be WAV (RIFF/WAVE), 44.1kHz, mono/stereo, ≤ 50MB.
- The prompt always gets `"; no speech, no dialogue, no music"` appended if missing.
  This is **best-effort** (prompt-level); do a listening check before shipping.
- Idempotency: same key + same normalized payload (incl. video sha256) → same job.

## Configuration

`config.json`:

```json
"audio_models": {
  "adcraft-audio": {
    "driver": "modal_gateway",
    "app": "thinksound",
    "family": "thinksound_light",
    "quality_tier": "default",
    "max_video_seconds": 15,
    "response_format": "wav"
  }
}
```

`.env`:

```env
KEYROUTER_API_KEY=""                 # guards /v1/audio* (and /v1/videos*) when set
MODAL_AUDIO_GATEWAY_URL=""           # printed by `modal deploy modal/audio_app.py`
MODAL_AUDIO_GATEWAY_TOKEN=""         # optional; must match the Modal side if used
AUDIO_VIDEO_ALLOW_HOSTS="127.0.0.1:8000,localhost:8000,127.0.0.1:8082"
```

## Modal app (ThinkSound)

Model: **ThinkSound light** — license **Apache 2.0** (verify `LICENSE.md` in the
weights repo before commercial use). Weights: `FunAudioLLM/ThinkSound`
(`thinksound_light.ckpt` 5.7GB + `vae.ckpt` 2.5GB + `synchformer_state_dict.pth`
0.95GB ≈ 9.2GB).

```bash
venv/bin/modal run modal/audio_app.py::download_models
venv/bin/modal deploy modal/audio_app.py
# then set MODAL_AUDIO_GATEWAY_URL in key-router/.env and restart
```

The worker runs the upstream pipeline per job:
`extract_latents.py` (features) → `eval_batch.py` (flow matching) → `demo.wav`
@ 44.1kHz, and returns it base64 through the gateway.

## WebUI / management API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/adcraft-audio/status` | alias, driver/family, endpoints, gateway, allowlist |
| GET | `/api/adcraft-audio/models` | audio aliases |
| GET | `/api/adcraft-audio/jobs` | recent jobs (advances pending ones) |
| GET | `/api/adcraft-audio/jobs/{id}` | advance + status |
| GET | `/api/adcraft-audio/jobs/{id}/content` | WAV preview (no client auth) |
| POST | `/api/adcraft-audio/test` | use the newest completed video (or a 2s clip) and run SFX |

WebUI tab **AdCraft SFX**: status, endpoint/allowlist, test button with audio
player, recent jobs.

## Isolation

New code: `audio/` package, `models/audio.py`, `proxy/routers/audio.py`,
`modal/audio_app.py`, `test/test_audio.py`, this doc.
Touched: `proxy/app.py` (router + handler), `config/settings.py`,
`config.json` (`audio_models`), `webui/src/App.tsx`, `video/jobs.py` (generic
db/media/id-prefix params — video behaviour unchanged).

## Known limitations

- One video reference per job; no video+audio references.
- ThinkSound output length follows `duration_seconds` (clamped to the video).
- Cold start + model load dominate short clips; `max_containers=1` serializes jobs.
- Spark/music leakage is possible on some material — manual review for now.
