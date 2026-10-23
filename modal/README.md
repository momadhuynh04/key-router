# KeyRouter video app (Modal + ComfyUI)

Scale-to-zero video backend for the KeyRouter `/v1/videos` route. Model:
**Wan2.2** (Apache-2.0) using **ComfyUI core** Wan nodes (no custom node pack
required). Weights are the ComfyUI-ready single-file repackaged safetensors.

```
AdCraft ─► KeyRouter /v1/videos ─► video/drivers/modal_gateway.py
                                        │  (HTTP, bearer)
                                        ▼
                              Modal CPU gateway  ──spawn──►  GPU ComfyUI
                                                               ├─ WanDraftWorker    (L40S)
                                                               └─ WanStandardWorker (A100-80GB)
```

## What's already wired (no placeholders)

| Thing | Value |
| --- | --- |
| ComfyUI | `Comfy-Org/ComfyUI` @ `e3bee7ff…` (0.42.0) |
| Weights | `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` @ `ee6f4a40…` |
| GPU tiers | `draft`/`auto` → L40S, `standard`/`hero` → A100-80GB |
| Families | `wan22_ti2v_5b`, `wan22_i2v_5b`, `wan22_i2v_14b_4step` |

Families are data-driven: `modal/workflows/<family>.json` (ComfyUI **API** format,
committed and validated against ComfyUI 0.42 node schemas) + a node-id map in
`modal/workflows/_param_map.json`.

The worker auto-switches family when the input doesn't match:
- no image + a T2V family → stays T2V;
- image + T2V family (`fallback_with_image`) → `wan22_i2v_5b`;
- no image + `requires_image` family → `fallback_without_image`.

## Tune (optional)

In `video_app.py`:

- `DRAFT_GPU` / `STANDARD_GPU` — change after the Phase 0 benchmark.
- `SCALEDOWN_SECONDS`, `MAX_GPU_CONTAINERS` — spend/latency knobs.
- `MODEL_FILES` — add/remove weights (each entry is `(repo, remote_path, subdir, revision)`).

## Deploy (3 commands)

```bash
venv/bin/modal token new                                  # once, normal Modal auth
venv/bin/modal run modal/video_app.py::download_models    # once: weights -> Volume (CPU, no GPU billing)
venv/bin/modal deploy modal/video_app.py                  # prints the gateway URL
```

That is the whole deploy. The gateway is **unauthenticated** (same as the other
apps in this workspace). `download_models` is the same Volume pattern as
`minimaxh3.py`: weights live in a Volume instead of being baked into the image, so
they are fetched once by a CPU-only function.

`HF_TOKEN` (optional): if it is set in the repo-root `.env`, it is passed to the
downloader as a Modal secret, which gives higher Hugging Face rate limits and
faster downloads. The Wan repackaged repo is public, so it is not required.
The secret object is created unconditionally, so an absent token just produces an
empty secret (never wrap Modal objects in `if`, or dependency matching breaks).

Then add the URL printed by `modal deploy` to key-router's `.env`:

```env
MODAL_VIDEO_GATEWAY_URL="https://<workspace>--keyrouter-video-api.modal.run"
```

Then restart key-router. The WebUI **AdCraft video** tab should show
`gateway_configured: true`, and the Test button should produce an mp4.

> The gateway is **unauthenticated**, same as the other apps in this workspace.
> Its URL is public, so anyone with the link can spend GPU credits. To add bearer
> auth later, create a Modal secret and wire it into `api` (see the comment in
> `auth()`).

## Gateway contract

| Method | Path | Returns |
| --- | --- | --- |
| POST | `/generate` | `{"call_id": "..."}` (spawns the GPU job; routes by `quality_tier`) |
| GET | `/status/{call_id}` | `{"status","progress","error","usage","output"}` |
| GET | `/content/{call_id}` | `video/mp4` bytes |
| POST | `/cancel/{call_id}` | `{"cancelled": true}` |
| GET | `/health` | `{"ok": true}` |

The gateway is **unauthenticated** by default (matching the other Modal apps in
this workspace). `status` is
`running | completed | failed` (the gateway can't see ComfyUI progress, so a
running job reports `progress: 0.5`).

## Adding a family

1. Export/draft the workflow in ComfyUI **API** format → `modal/workflows/<name>.json`.
2. Add an entry to `_param_map.json`:
   ```json
   "<name>": { "workflow": "<name>.json", "output_node": "10", "fps": 24, "steps": 30,
               "nodes": { "prompt": {"node":"4","input":"text"}, ... } }
   ```
   `nodes` values may use `"input"` (set `inputs[input] = values[logical]`) or
   `"inputs"` (map several inputs to other logical params, e.g. a second sampler stage).
3. Point an alias at it in `config.json → video_models`: `"family": "<name>"`.
4. If it needs a different GPU, add another `@app.cls` and map the tier in the
   gateway's `WORKERS` dict.

## Audio app (video-to-SFX, ThinkSound)

Separate app, same deploy pattern:

```bash
venv/bin/modal run modal/audio_app.py::download_models    # ~9.2GB (ThinkSound light + VAE + Synchformer)
venv/bin/modal deploy modal/audio_app.py                  # prints the gateway URL
```

Then set in key-router `.env`:

```env
MODAL_AUDIO_GATEWAY_URL="https://<workspace>--keyrouter-audio-api.modal.run"
```

Contract and details: `docs/adcraft-sfx.md`. The worker runs the upstream
ThinkSound pipeline (`extract_latents.py` → `eval_batch.py`) and returns a
44.1kHz WAV. GPU: L40S, `max_containers=1`.

## Cost notes

- `min_containers=0` → no idle GPU; `scaledown_window` is billed after the last request.
- `max_containers` caps parallel spend.
- Weights live in the Volume (downloaded once, CPU-only).
- Run `scripts/video-bench/bench.py` to pick the GPU by
  *generated video-seconds per GPU-dollar*.
