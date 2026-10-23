"""
KeyRouter audio app — ThinkSound video-to-SFX (ambience/foley) on Modal.

Model: ThinkSound (Apache 2.0, weights: FunAudioLLM/ThinkSound).
Pipeline: extract_latents.py (video features) -> eval_batch.py (flow matching)
-> demo.wav at 44.1 kHz.

Same shape as modal/video_app.py:
  * GPU class with min_containers=0, max_containers=1 (serialize GPU jobs).
  * Weights in a Modal Volume, downloaded once by a CPU-only function.
  * CPU-only gateway that spawns GPU jobs; key-router polls.

Deploy:
    modal run modal/audio_app.py::download_models
    modal deploy modal/audio_app.py

Gateway contract (consumed by audio/drivers/modal_gateway.py):
    POST /generate          -> {"call_id": "..."}
    GET  /status/{call_id}  -> {"status","progress","error","usage","output"}
    GET  /content/{call_id} -> audio/wav bytes
    POST /cancel/{call_id}
    GET  /health
The gateway is unauthenticated (same as the video app).
"""

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import wave
from pathlib import Path

import modal

# --------------------------------------------------------------------------- #
# Config (pinned)
# --------------------------------------------------------------------------- #
APP_NAME = "keyrouter-thinksound-light"
VOLUME_NAME = "keyrouter-thinksound-models"

GPU = "L40S"                 # ThinkSound-light fits comfortably; benchmark if needed
SCALEDOWN_SECONDS = 60
MAX_GPU_CONTAINERS = 1       # one GPU job at a time -> queue is implicit in Modal
GEN_TIMEOUT = 25 * 60

THINKSOUND_REPO = "https://github.com/liuhuadai/ThinkSound"
THINKSOUND_SHA = "4e2cfd57efaaf40c90460d8111b6a3c1e7dfb2f0"

WEIGHTS_REPO = "FunAudioLLM/ThinkSound"
WEIGHTS_REV = "7f152d1a088f91b21307337060216f0dec8d4a8f"
MODEL_FILES = [
    "thinksound_light.ckpt",          # default `ckpt_dir` in the repo's defaults.ini
    "vae.ckpt",                       # pretransform_ckpt_path
    "synchformer_state_dict.pth",     # extract_latents.py default
]

APP_DIR = "/opt/ThinkSound"
CKPT_DIR = f"{APP_DIR}/ckpts"
DATASET_CONFIG = "ThinkSound/configs/multimodal_dataset_demo.json"
MODEL_CONFIG = "ThinkSound/configs/model_configs/thinksound.json"

DEFAULT_PROMPT = (
    "diegetic sound effects and ambience synchronized to the action; "
    "no speech, no dialogue, no music"
)


def _load_root_env() -> dict:
    """Read the repo-root .env at deploy time (inside the container it's absent)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if value:
                env[key.strip()] = value
    return env


_ENV = _load_root_env()
# Always exactly ONE secret object (empty if HF_TOKEN is absent), so local and
# container dependency lists match. Used by download_models only.
HF_SECRET = modal.Secret.from_dict({k: v for k, v in _ENV.items() if k == "HF_TOKEN" and v})

downloader_image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install("huggingface_hub[hf_xet]>=0.30")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

gpu_image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0")
    # ThinkSound calls torch.load() without weights_only; torch 2.6 defaults to
    # weights_only=True, so restore the legacy behaviour.
    .env({"TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1"})
    .run_commands(
        f"git clone {THINKSOUND_REPO} {APP_DIR}",
        f"cd {APP_DIR} && git checkout {THINKSOUND_SHA}",
        #ThinkSound pins torch==2.6.0 but leaves torchaudio open, so pip pulls a
        # newer torchaudio built for CUDA 13 -> "libcudart.so.13: cannot open".
        # Pin the matching trio before and after installing thinksound.
        'pip install "torch==2.6.0" "torchvision==0.21.0" "torchaudio==2.6.0"',
        "pip install thinksound prefigure",
        'pip install "torch==2.6.0" "torchvision==0.21.0" "torchaudio==2.6.0"',
        # transformers' audio utils import soxr at load time; the T5 tokenizer used
        # by FeatureUtils needs tiktoken; scipy is used by the training losses.
        "pip install soxr soundfile scipy tiktoken",
        # ThinkSound targets transformers 4.x. In 5.x the slow tokenizers were
        # removed and T5 is converted through tiktoken, which fails on spiece.model.
        'pip install "transformers==4.53.3"',
        # thinksound pins wandb==0.15.4, whose generated protobuf code breaks on
        # protobuf 5 (Modal's runtime requires protobuf 5.x). Modern wandb is fine.
        'pip install "wandb==0.18.7"',
        # Modal's runtime needs protobuf 5.x (EnumTypeWrapper.ValueType).
        'pip install "protobuf==5.29.5"',
        # thinksound pins sentencepiece==0.1.99, whose bundled
        # sentencepiece_model_pb2.py was generated with protoc < 3.19 and raises
        # "TypeError: Descriptors cannot be created directly" on protobuf 5.
        # sentencepiece >= 0.2.0 ships regenerated code and works with protobuf 5.
        'pip install "sentencepiece==0.2.1"',
    )
)

gateway_image = modal.Image.debian_slim(python_version="3.10").pip_install("fastapi[standard]")

app = modal.App(APP_NAME)
models_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


# --------------------------------------------------------------------------- #
# One-off: weights -> Volume (CPU only)
# --------------------------------------------------------------------------- #
@app.function(image=downloader_image, volumes={"/ckpts": models_volume}, secrets=[HF_SECRET], timeout=3 * 3600)
def download_models():
    from huggingface_hub import hf_hub_download

    token = os.environ.get("HF_TOKEN") or None
    Path("/ckpts").mkdir(parents=True, exist_ok=True)
    for filename in MODEL_FILES:
        target = Path("/ckpts") / filename
        if target.exists():
            print(f"[skip] {target}")
            continue
        print(f"[download] {WEIGHTS_REPO}/{filename} @ {WEIGHTS_REV}")
        hf_hub_download(WEIGHTS_REPO, filename, revision=WEIGHTS_REV, token=token, local_dir="/ckpts")
        models_volume.commit()
    models_volume.commit()
    print("done")


# --------------------------------------------------------------------------- #
# GPU worker: ThinkSound pipeline (no server; run the repo scripts per job)
# --------------------------------------------------------------------------- #
@app.cls(
    image=gpu_image,
    gpu=GPU,
    volumes={CKPT_DIR: models_volume},
    secrets=[HF_SECRET],
    timeout=GEN_TIMEOUT,
    scaledown_window=SCALEDOWN_SECONDS,
    min_containers=0,
    max_containers=MAX_GPU_CONTAINERS,
)
class ThinkSoundWorker:
    GPU_NAME = GPU

    @staticmethod
    def _run(cmd, timeout: int) -> str:
        proc = subprocess.run(cmd, cwd=APP_DIR, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
            raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{tail}")
        return proc.stdout or ""

    @modal.method()
    def generate(self, payload: dict) -> dict:
        t0 = time.time()
        if not payload.get("video_b64"):
            raise RuntimeError("video_b64 is required")
        video_bytes = base64.b64decode(payload["video_b64"])
        duration = max(1.0, float(payload.get("duration_seconds") or 5))
        prompt = (payload.get("prompt") or DEFAULT_PROMPT).replace('"', "'")
        use_half = bool(payload.get("use_half"))

        work = tempfile.mkdtemp(prefix="sfx_")
        try:
            videos_dir = os.path.join(work, "videos")
            cot_dir = os.path.join(work, "cot")
            features_dir = os.path.join(work, "results", "features")
            audio_dir = os.path.join(work, "results", "audios")
            for path in (videos_dir, cot_dir, features_dir, audio_dir):
                os.makedirs(path, exist_ok=True)

            # Normalize to mp4 (ComfyUI/decord-friendly), like the upstream demo.
            src = os.path.join(work, "input.bin")
            Path(src).write_bytes(video_bytes)
            target = os.path.join(videos_dir, "demo.mp4")
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", src, "-c:v", "libx264",
                 "-preset", "fast", "-c:a", "aac", "-strict", "experimental", target],
                capture_output=True, timeout=300,
            )
            if proc.returncode != 0 or not os.path.exists(target):
                raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode()[-800:]}")

            csv_path = os.path.join(cot_dir, "cot.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("id,caption,caption_cot\n")
                fh.write(f'demo,{payload.get("alias") or "adcraft-audio"},"{prompt}"\n')

            # 1) video/audio features
            extract = [
                "python", "extract_latents.py",
                "--root", videos_dir,
                "--tsv_path", csv_path,
                "--save-dir", features_dir,
                "--duration_sec", str(duration),
            ]
            if use_half:
                extract.append("--use_half")
            self._run(extract, timeout=1200)

            # 2) flow-matching inference
            self._run(
                [
                    "python", "eval_batch.py",
                    "--dataset-config", DATASET_CONFIG,
                    "--model-config", MODEL_CONFIG,
                    "--duration-sec", str(duration),
                    "--results-dir", features_dir,
                    "--save-dir", audio_dir,
                    "--test-batch-size", "1",
                ],
                timeout=1800,
            )

            wavs = sorted(Path(audio_dir).rglob("demo.wav"))
            if not wavs:
                raise RuntimeError("ThinkSound did not produce demo.wav")
            data = wavs[0].read_bytes()

            with wave.open(io.BytesIO(data), "rb") as wav:
                rate = wav.getframerate()
                frames = wav.getnframes()
                channels = wav.getnchannels()
                actual_duration = frames / rate if rate else None

            elapsed = round(time.time() - t0, 1)
            return {
                "audio_b64": base64.b64encode(data).decode("ascii"),
                "duration_seconds": actual_duration,
                "sample_rate": rate,
                "channels": channels,
                "bytes": len(data),
                "generation_seconds": elapsed,
                "cold_start_seconds": 0,
                "gpu_seconds": elapsed,
                "gpu": self.GPU_NAME,
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Public gateway (CPU only): submit -> poll -> fetch
# --------------------------------------------------------------------------- #
@app.function(image=gateway_image, timeout=300)
@modal.asgi_app()
def api():
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.responses import Response

    web = FastAPI(title="KeyRouter Audio Gateway")

    def auth(authorization: str = Header(default="")):
        # Unauthenticated by default (matches the other Modal apps here).
        key = os.environ.get("MODAL_AUDIO_GATEWAY_TOKEN", "")
        if not key:
            return
        if authorization != f"Bearer {key}":
            raise HTTPException(401, "invalid gateway token")

    def _get_call(call_id: str):
        try:
            return modal.FunctionCall.from_id(call_id)
        except Exception:
            raise HTTPException(404, "unknown call_id")

    @web.post("/generate", dependencies=[Depends(auth)])
    def generate(payload: dict):
        call = ThinkSoundWorker().generate.spawn(payload)
        return {"call_id": call.object_id}

    @web.get("/status/{call_id}", dependencies=[Depends(auth)])
    def status(call_id: str):
        call = _get_call(call_id)
        try:
            out = call.get(timeout=2.0)
        except TimeoutError:
            return {"status": "running", "progress": 0.5}
        except Exception as exc:
            return {"status": "failed", "progress": 0.0,
                    "error": {"code": "generation_failed", "message": str(exc)[:500]}}
        return {
            "status": "completed",
            "progress": 1.0,
            "output": {
                "duration_seconds": out.get("duration_seconds"),
                "sample_rate": out.get("sample_rate"),
                "channels": out.get("channels"),
            },
            "usage": {
                "gpu": out.get("gpu"),
                "generation_seconds": out.get("generation_seconds"),
                "gpu_seconds": out.get("gpu_seconds"),
            },
        }

    @web.get("/content/{call_id}", dependencies=[Depends(auth)])
    def content(call_id: str):
        call = _get_call(call_id)
        try:
            out = call.get(timeout=2.0)
        except TimeoutError:
            raise HTTPException(409, "not ready")
        return Response(content=base64.b64decode(out["audio_b64"]), media_type="audio/wav")

    @web.post("/cancel/{call_id}", dependencies=[Depends(auth)])
    def cancel(call_id: str):
        modal.FunctionCall.from_id(call_id).cancel()
        return {"cancelled": True}

    @web.get("/health")
    def health():
        return {"ok": True}

    return web
