"""
KeyRouter video app — MiniMax H3 (GGUF) on Modal, two workers.

Workers:
  * H3FL2VA  — first-and-last-frame mode: T2V (no image), first frame (1 image),
               first+last frame (2 images).
  * H3Ref2VA — omni-reference mode: up to 9 reference images.

Gateway contract (consumed by video/drivers/modal_gateway.py):
    POST /generate          -> {"call_id": "..."}
    GET  /status/{call_id}  -> {"status","progress","error","usage","output"}
    GET  /content/{call_id} -> video/mp4 bytes
    POST /cancel/{call_id}
    GET  /health

Routing: the key-router alias sets `family`:
    minimax_h3_fl2va -> H3FL2VA
    minimax_h3_ref2va -> H3Ref2VA
`image_mode == "reference"` also selects Ref2VA.

Deploy:
    modal run minimaxh3.py::download_models
    modal deploy minimaxh3.py
"""

import base64
import ipaddress
import json
import os
import random
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import modal

# --------------------------------------------------------------------------- #
# Config (pinned)
# --------------------------------------------------------------------------- #
APP_NAME = "keyrouter-minimax-h3"
VOLUME_NAME = "keyrouter-minimax-h3-models"

GPU = "A100-80GB"           # 32B text encoder + ~21GB UNet; 40GB is too tight
SCALEDOWN_SECONDS = 60
MAX_GPU_CONTAINERS = 1      # per worker class -> at most 2 A100s (FL2VA + Ref2VA)
GEN_TIMEOUT = 30 * 60

COMFYUI_REPO = "https://github.com/Comfy-Org/ComfyUI"
COMFYUI_SHA = "d0ef06b6f6a5a6d9c5e861a438ae2eb4acf0aaaa"
GGUF_NODES_REPO = "https://github.com/molbal/ComfyUI-GGUF"
GGUF_NODES_SHA = "b77b9d8124656aa1a548d95c617da4d16cab1a2e"

UNET_REPO = "molbal/MiniMax-H3-GGUF"
UNET_FL2VA = "minimax_h3_fl2va_pruned_fp8_Q8_0.gguf"
UNET_REF2VA = "minimax-h3-ref2va-Q8_0.gguf"

COMFY_REPO = "Comfy-Org/MiniMax-H3"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
TEXT_ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"

MODELS_DIR = "/models"
COMFY_DIR = "/opt/ComfyUI"
OUT_DIR = "/tmp/comfy_out"
COMFY_PORT = 8188

MAX_REF_IMAGES = 9
MAX_REF_IMAGE_BYTES = 16 * 1024 * 1024
MAX_CANVAS_AREA = 1344 * 768
MAX_SHORTER_SIDE = {"480p": 480, "720p": 768, "1080p": 1080}

DEFAULT_PROMPT = "cinematic video, natural motion, consistent scene"


# --------------------------------------------------------------------------- #
# Local .env (deploy time only)
# --------------------------------------------------------------------------- #
def _repo_root() -> Path:
    """Find the repo root that owns .env, regardless of where this file lives."""
    here = Path(__file__).resolve().parent
    for candidate in (here, here.parent, here.parent.parent):
        if (candidate / ".env").exists():
            return candidate
    return here


def _load_root_env() -> dict:
    env = {}
    path = _repo_root() / ".env"
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            env[key.strip()] = value
    return env


_ENV = _load_root_env()
# Always exactly one secret object, so local/container dependency lists match.
secret = modal.Secret.from_dict(
    {k: v for k, v in _ENV.items() if k in ("HF_TOKEN", "API_KEY", "MODAL_VIDEO_GATEWAY_TOKEN")}
)

app = modal.App(APP_NAME)
models_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

downloader_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface_hub[hf_xet]>=0.30")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

gateway_image = modal.Image.debian_slim(python_version="3.12").pip_install("fastapi[standard]")

gpu_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0")
    .pip_install("torch", "torchvision", "torchaudio", "requests")
    .run_commands(
        f"git clone {COMFYUI_REPO} {COMFY_DIR}",
        f"cd {COMFY_DIR} && git checkout {COMFYUI_SHA}",
        f"pip install -r {COMFY_DIR}/requirements.txt",
        f"git clone {GGUF_NODES_REPO} {COMFY_DIR}/custom_nodes/ComfyUI-GGUF",
        f"cd {COMFY_DIR}/custom_nodes/ComfyUI-GGUF && git checkout {GGUF_NODES_SHA}",
        f"pip install -r {COMFY_DIR}/custom_nodes/ComfyUI-GGUF/requirements.txt",
    )
)

EXTRA_PATHS_YAML = f"""keyrouter_h3:
  base_path: {MODELS_DIR}
  diffusion_models: diffusion_models
  vae: vae
  text_encoders: text_encoders
"""


# --------------------------------------------------------------------------- #
# One-off: weights -> Volume (CPU only, no GPU billing)
# --------------------------------------------------------------------------- #
@app.function(
    image=downloader_image,
    volumes={MODELS_DIR: models_volume},
    secrets=[secret],
    timeout=3 * 3600,
)
def download_models():
    from huggingface_hub import hf_hub_download

    token = os.environ.get("HF_TOKEN") or None
    Path(MODELS_DIR, "diffusion_models").mkdir(parents=True, exist_ok=True)

    def fetch(repo: str, filename: str, local_dir: str) -> None:
        target = Path(local_dir) / filename
        if target.exists():
            print(f"[skip] {target}")
            return
        print(f"[download] {repo}/{filename}")
        hf_hub_download(repo, filename, local_dir=local_dir, token=token)
        models_volume.commit()

    fetch(UNET_REPO, UNET_FL2VA, f"{MODELS_DIR}/diffusion_models")
    fetch(UNET_REPO, UNET_REF2VA, f"{MODELS_DIR}/diffusion_models")
    fetch(COMFY_REPO, f"vae/{VIDEO_VAE}", MODELS_DIR)
    fetch(COMFY_REPO, f"vae/{AUDIO_VAE}", MODELS_DIR)
    fetch(COMFY_REPO, f"text_encoders/{TEXT_ENCODER}", MODELS_DIR)
    models_volume.commit()
    print("done")


# --------------------------------------------------------------------------- #
# Pure helpers (also unit-testable without Modal)
# --------------------------------------------------------------------------- #
def _snap_length(seconds: float) -> int:
    """Frames on the model's 17k+5 grid @24fps (same formula as the official workflow)."""
    n = max(5, round(seconds * 24))
    return n + (5 - n % 17) % 17


def _round32(value: float, *, mode: str = "nearest") -> int:
    if mode == "floor":
        return max(32, int(value) // 32 * 32)
    return max(32, int(round(value / 32)) * 32)


def _parse_ratio(aspect_ratio: str | None) -> float:
    try:
        a, b = (aspect_ratio or "16:9").split(":", 1)
        ratio = float(a) / float(b)
        return ratio if ratio > 0 else 16 / 9
    except (ValueError, ZeroDivisionError):
        return 16 / 9


def _h3_dims(resolution: str | None, aspect_ratio: str | None) -> tuple[int, int]:
    """Map (resolution, aspect) -> H3 canvas on a /32 grid, capped at ~1344x768."""
    base = MAX_SHORTER_SIDE.get((resolution or "720p").lower(), 768)
    ratio = _parse_ratio(aspect_ratio)
    if ratio >= 1:
        height, width = base, base * ratio
    else:
        width, height = base, base / ratio
    if width * height > MAX_CANVAS_AREA:
        scale = (MAX_CANVAS_AREA / (width * height)) ** 0.5
        width, height = width * scale, height * scale
    width, height = _round32(width), _round32(height)
    while width * height > MAX_CANVAS_AREA:
        if width >= height:
            width -= 32
        else:
            height -= 32
    return width, height


def _detect_ext(raw: bytes, fallback: str = ".png") -> str:
    if raw.startswith(b"\x89PNG"):
        return ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return ".wav"
    if raw.startswith(b"fLaC"):
        return ".flac"
    if raw.startswith(b"OggS"):
        return ".ogg"
    if raw.startswith(b"ID3") or (len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
        return ".mp3"
    if raw.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    if len(raw) >= 12 and raw[4:8] == b"ftyp":
        return ".mp4"
    return fallback


def _collect_images(payload: dict) -> list[str]:
    images: list[str] = []
    if payload.get("image_url"):
        images.append(str(payload["image_url"]))
    for value in payload.get("image_urls") or []:
        if value:
            images.append(str(value))
    return list(dict.fromkeys(images))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise ValueError("redirects are not allowed for reference URLs")


def _assert_public_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported reference URL scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise ValueError("reference URL has no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve reference host '{host}': {exc}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError(
                "reference URL resolves to a private/loopback address; Modal cannot fetch it. "
                "Send a data: URL instead."
            )


def _image_to_b64(value: str) -> str:
    value = value.strip()
    if value.startswith("data:"):
        header, sep, data = value.partition(",")
        if not sep or "base64" not in header:
            raise ValueError("unsupported data URL (expected data:<mime>;base64,...)")
        return data
    _assert_public_url(value)
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(value, headers={"User-Agent": "keyrouter-h3/1.0"})
    with opener.open(req, timeout=15) as resp:
        raw = resp.read(MAX_REF_IMAGE_BYTES + 1)
    if len(raw) > MAX_REF_IMAGE_BYTES:
        raise ValueError(f"reference image too large ({len(raw)} bytes, max {MAX_REF_IMAGE_BYTES})")
    return base64.b64encode(raw).decode("ascii")


def _fetch_images_b64(values: list[str]) -> list[str]:
    if not values:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(values))) as pool:
        return list(pool.map(_image_to_b64, values))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _build_worker_payload(payload: dict) -> dict:
    """Translate a key-router video payload into a worker payload."""
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")

    family = str(payload.get("family") or "").lower()
    mode = str(payload.get("mode") or "").lower()
    if mode not in ("fl2va", "ref2va"):
        if str(payload.get("image_mode") or "").lower() == "reference" or "ref2" in family:
            mode = "ref2va"
        else:
            mode = "fl2va"

    images = _collect_images(payload)
    if mode == "ref2va" and not images and not payload.get("ref_images_b64"):
        # Ref2VA needs at least one reference; fall back to text-to-video.
        mode = "fl2va"

    overrides = payload.get("workflow_overrides") or {}
    tier = str(payload.get("quality_tier") or "standard").lower()
    default_steps = {"draft": 12, "standard": 20, "hero": 24}.get(tier, 20)
    steps = int(overrides.get("steps") or payload.get("steps") or default_steps)
    steps = int(_clamp(steps, 4, 50))

    duration = _clamp(float(payload.get("duration_seconds") or payload.get("duration") or 5), 4.0, 15.0)

    if payload.get("width") and payload.get("height"):
        width, height = _round32(payload["width"]), _round32(payload["height"])
    else:
        width, height = _h3_dims(payload.get("resolution"), payload.get("aspect_ratio"))
    if width * height > MAX_CANVAS_AREA * 1.05:
        raise ValueError(f"canvas {width}x{height} exceeds the ~1344x768 limit")

    seed = payload.get("seed")
    seed = random.randint(0, 2**63 - 1) if seed is None else int(seed)

    if mode == "ref2va":
        refs = payload.get("ref_images_b64")
        if refs:
            refs_b64 = [str(v) for v in refs][:MAX_REF_IMAGES]
        else:
            refs_b64 = _fetch_images_b64(images[:MAX_REF_IMAGES])
        first_b64 = payload.get("first_frame_b64")
        last_b64 = payload.get("last_frame_b64")
    else:
        first_b64 = payload.get("first_frame_b64")
        last_b64 = payload.get("last_frame_b64")
        if not first_b64 and images:
            first_b64 = _image_to_b64(images[0])
        if not last_b64 and len(images) > 1:
            last_b64 = _image_to_b64(images[1])
        refs_b64 = []

    return {
        "mode": mode,
        "prompt": prompt,
        "duration": duration,
        "width": width,
        "height": height,
        "steps": steps,
        "seed": seed,
        "sampler_name": str(overrides.get("sampler_name") or overrides.get("sampler") or payload.get("sampler_name") or "res_multistep"),
        "scheduler": str(overrides.get("scheduler") or payload.get("scheduler") or "simple"),
        "shift_video": float(_clamp(float(overrides.get("shift_video") or payload.get("shift_video") or 12.0), 0.01, 100.0)),
        "shift_audio": float(_clamp(float(overrides.get("shift_audio") or payload.get("shift_audio") or 3.0), 0.01, 100.0)),
        "ref_image_size": str(overrides.get("ref_image_size") or payload.get("ref_image_size") or "match"),
        "first_frame_b64": first_b64,
        "last_frame_b64": last_b64,
        "ref_images_b64": refs_b64,
    }


# --------------------------------------------------------------------------- #
# ComfyUI API-format workflow (FL2VA + Ref2VA)
# --------------------------------------------------------------------------- #
def build_prompt(
    *,
    mode: str,
    prompt: str,
    width: int,
    height: int,
    seconds: float,
    steps: int,
    seed: int,
    sampler_name: str = "res_multistep",
    scheduler: str = "simple",
    shift_video: float = 12.0,
    shift_audio: float = 3.0,
    first_image: str | None = None,
    last_image: str | None = None,
    ref_images: list[str] | None = None,
    ref_image_size: str = "match",
) -> dict:
    is_ref2va = mode == "ref2va"
    unet_file = UNET_REF2VA if is_ref2va else UNET_FL2VA
    if is_ref2va and not (Path(MODELS_DIR) / "diffusion_models" / UNET_REF2VA).exists():
        raise RuntimeError(
            f"Ref2VA weights ({UNET_REF2VA}) not found in volume. Run: modal run minimaxh3.py::download_models"
        )
    if not is_ref2va and not (Path(MODELS_DIR) / "diffusion_models" / UNET_FL2VA).exists():
        raise RuntimeError(
            f"FL2VA weights ({UNET_FL2VA}) not found in volume. Run: modal run minimaxh3.py::download_models"
        )

    wf: dict = {
        "1": {"class_type": "UnetLoaderGGUFDynamicVRAM", "inputs": {"unet_name": unet_file}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "minimax", "device": "default"}},
        "15": {
            "class_type": "MiniMaxH3SigmaShift",
            "inputs": {
                "model": ["1", 0],
                "shift_video": float(shift_video),
                "shift_audio": float(shift_audio),
            },
        },
        "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "7": {"class_type": "BasicGuider", "inputs": {"model": ["15", 0], "conditioning": ["5", 0]}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler_name}},
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["15", 0], "scheduler": scheduler, "steps": steps, "denoise": 1.0},
        },
        "10": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["6", 0],
                "guider": ["7", 0],
                "sampler": ["8", 0],
                "sigmas": ["9", 0],
                "latent_image": ["5", 1],
            },
        },
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["2", 0]}},
        "12": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "13": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "fps": 24, "audio": ["12", 0]}},
        "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": "video/h3"}},
    }

    if is_ref2va:
        cond = {
            "clip": ["4", 0],
            "vae": ["2", 0],
            "audio_vae": ["3", 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": _snap_length(seconds),
            "ref_image_size": ref_image_size if ref_image_size in ("match", "max") else "match",
        }
        for idx, img_file in enumerate((ref_images or [])[:MAX_REF_IMAGES]):
            node_id = str(100 + idx)
            wf[node_id] = {"class_type": "LoadImage", "inputs": {"image": img_file}}
            cond[f"ref_images.ref_image_{idx}"] = [node_id, 0]
        wf["5"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": cond}
    else:
        cond = {
            "clip": ["4", 0],
            "vae": ["2", 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": _snap_length(seconds),
        }
        if first_image:
            wf["20"] = {"class_type": "LoadImage", "inputs": {"image": first_image}}
            cond["first_frame"] = ["20", 0]
        if last_image:
            wf["21"] = {"class_type": "LoadImage", "inputs": {"image": last_image}}
            cond["last_frame"] = ["21", 0]
        wf["5"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": cond}

    return wf


SAVE_VIDEO_VARIANTS = [
    {"format": "auto", "format.codec": "auto"},
    {"format": "mp4", "format.codec": "h264"},
    {"format": "auto", "codec": "auto"},
    {},
]


# --------------------------------------------------------------------------- #
# GPU worker shared implementation
# --------------------------------------------------------------------------- #
class H3WorkerBase:
    MODE = "fl2va"
    GPU_NAME = GPU
    cold_start_seconds: float | None = None

    def _boot(self) -> None:
        Path("/tmp/extra_model_paths.yaml").write_text(EXTRA_PATHS_YAML)
        os.makedirs(OUT_DIR, exist_ok=True)
        started = time.time()
        self.proc = subprocess.Popen(
            [
                "python", "main.py",
                "--listen", "127.0.0.1",
                "--port", str(COMFY_PORT),
                "--disable-auto-launch",
                "--extra-model-paths-config", "/tmp/extra_model_paths.yaml",
                "--output-directory", OUT_DIR,
            ],
            cwd=COMFY_DIR,
        )
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("ComfyUI exited during startup")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{COMFY_PORT}/system_stats", timeout=2)
                self.cold_start_seconds = round(time.time() - started, 1)
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError("ComfyUI did not become ready in 300s")

    def _shutdown(self) -> None:
        proc = getattr(self, "proc", None)
        if proc is not None:
            proc.terminate()

    def _http(self, path: str, payload: dict | None = None) -> dict:
        url = f"http://127.0.0.1:{COMFY_PORT}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())

    def _save_input_media(self, b64: str, fallback_ext: str = ".png") -> str:
        raw = base64.b64decode(b64)
        ext = _detect_ext(raw, fallback_ext)
        name = f"{uuid.uuid4().hex}{ext}"
        os.makedirs(f"{COMFY_DIR}/input", exist_ok=True)
        Path(f"{COMFY_DIR}/input/{name}").write_bytes(raw)
        return name

    def _queue(self, wf: dict) -> str:
        last_err = None
        for variant in SAVE_VIDEO_VARIANTS:
            wf["14"]["inputs"] = {"video": ["13", 0], "filename_prefix": "video/h3", **variant}
            try:
                return self._http("/prompt", {"prompt": wf, "client_id": uuid.uuid4().hex})["prompt_id"]
            except urllib.error.HTTPError as exc:
                last_err = exc.read().decode()
                print(f"[queue] variant {variant} rejected: {last_err[:500]}")
        raise RuntimeError(f"ComfyUI rejected workflow: {last_err}")

    def _generate(self, payload: dict) -> dict:
        t0 = time.time()
        mode = payload.get("mode") or self.MODE
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        if mode == "ref2va" and not payload.get("ref_images_b64"):
            raise ValueError("Ref2VA requires at least one reference image")

        duration = _clamp(float(payload.get("duration") or 5), 4.0, 15.0)
        width = _round32(int(payload.get("width") or 1344))
        height = _round32(int(payload.get("height") or 768))
        if width * height > MAX_CANVAS_AREA * 1.05:
            raise ValueError(f"canvas {width}x{height} exceeds the ~1344x768 limit")
        seed = payload.get("seed")
        seed = random.randint(0, 2**63 - 1) if seed is None else int(seed)

        first_image = (
            self._save_input_media(payload["first_frame_b64"], ".png")
            if payload.get("first_frame_b64") else None
        )
        last_image = (
            self._save_input_media(payload["last_frame_b64"], ".png")
            if payload.get("last_frame_b64") else None
        )
        ref_images = [
            self._save_input_media(value, ".png")
            for value in (payload.get("ref_images_b64") or [])[:MAX_REF_IMAGES]
            if value
        ]

        wf = build_prompt(
            mode=mode,
            prompt=prompt,
            width=width,
            height=height,
            seconds=duration,
            steps=int(payload.get("steps") or 20),
            seed=seed,
            sampler_name=payload.get("sampler_name") or "res_multistep",
            scheduler=payload.get("scheduler") or "simple",
            shift_video=float(payload.get("shift_video") or 12.0),
            shift_audio=float(payload.get("shift_audio") or 3.0),
            first_image=first_image,
            last_image=last_image,
            ref_images=ref_images,
            ref_image_size=payload.get("ref_image_size") or "match",
        )
        prompt_id = self._queue(wf)

        data = None
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError("ComfyUI crashed (likely OOM) — check logs")
            history = self._http(f"/history/{prompt_id}")
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                    raise RuntimeError(f"ComfyUI execution error: {json.dumps(msgs)[:2000]}")
                outputs = entry.get("outputs", {}).get("14", {})
                files = []
                for key in ("images", "videos", "gifs", "video"):
                    value = outputs.get(key)
                    if isinstance(value, list) and value:
                        files = value
                        break
                    if isinstance(value, dict) and value.get("filename"):
                        files = [value]
                        break
                if files and isinstance(files[0], dict) and files[0].get("filename"):
                    item = files[0]
                    data = (Path(OUT_DIR) / item.get("subfolder", "") / item["filename"]).read_bytes()
                    break
            time.sleep(2)

        elapsed = round(time.time() - t0, 1)
        cold = self.cold_start_seconds or 0
        length = _snap_length(duration)
        return {
            "video_b64": base64.b64encode(data).decode("ascii"),
            "seed": seed,
            "frames": length,
            "width": width,
            "height": height,
            "mode": mode,
            "duration_seconds": round(length / 24, 2),
            "resolution": f"{width}x{height}",
            "generation_seconds": elapsed,
            "cold_start_seconds": cold,
            "gpu_seconds": round(elapsed + cold, 1),
            "gpu": self.GPU_NAME,
        }


def _cls_kwargs() -> dict:
    return dict(
        image=gpu_image,
        gpu=GPU,
        volumes={MODELS_DIR: models_volume},
        timeout=GEN_TIMEOUT,
        scaledown_window=SCALEDOWN_SECONDS,
        min_containers=0,
        max_containers=MAX_GPU_CONTAINERS,
    )


@app.cls(**_cls_kwargs())
class H3FL2VA(H3WorkerBase):
    MODE = "fl2va"
    GPU_NAME = GPU

    @modal.enter()
    def start_comfy(self):
        self._boot()

    @modal.exit()
    def stop_comfy(self):
        self._shutdown()

    @modal.method()
    def generate(self, payload: dict) -> dict:
        return self._generate(payload)


@app.cls(**_cls_kwargs())
class H3Ref2VA(H3WorkerBase):
    MODE = "ref2va"
    GPU_NAME = GPU

    @modal.enter()
    def start_comfy(self):
        self._boot()

    @modal.exit()
    def stop_comfy(self):
        self._shutdown()

    @modal.method()
    def generate(self, payload: dict) -> dict:
        return self._generate(payload)


# --------------------------------------------------------------------------- #
# Public gateway (CPU only): submit -> poll -> fetch
# --------------------------------------------------------------------------- #
@app.function(image=gateway_image, volumes={MODELS_DIR: models_volume}, secrets=[secret], timeout=300)
@modal.asgi_app()
def api():
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.responses import Response

    web = FastAPI(title="KeyRouter MiniMax H3 Gateway")

    def auth(authorization: str = Header(default="")):
        key = os.environ.get("MODAL_VIDEO_GATEWAY_TOKEN", "")
        if not key:
            return  # unauthenticated if no token was injected
        if authorization != f"Bearer {key}":
            raise HTTPException(401, "invalid gateway token")

    def _get_call(call_id: str):
        try:
            return modal.FunctionCall.from_id(call_id)
        except Exception:
            raise HTTPException(404, "unknown call_id")

    @web.post("/generate", dependencies=[Depends(auth)])
    def generate(payload: dict):
        try:
            job_payload = _build_worker_payload(payload)
        except Exception as exc:
            raise HTTPException(400, str(exc)[:500])
        worker = H3Ref2VA if job_payload["mode"] == "ref2va" else H3FL2VA
        call = worker().generate.spawn(job_payload)
        return {"call_id": call.object_id}

    @web.get("/status/{call_id}", dependencies=[Depends(auth)])
    def status(call_id: str):
        call = _get_call(call_id)
        try:
            out = call.get(timeout=2.0)
        except TimeoutError:
            return {"status": "running", "progress": 0.5}
        except Exception as exc:  # job failed
            return {
                "status": "failed",
                "progress": 0.0,
                "error": {"code": "generation_failed", "message": str(exc)[:500]},
            }
        return {
            "status": "completed",
            "progress": 1.0,
            "output": {
                "duration_seconds": out.get("duration_seconds"),
                "resolution": out.get("resolution"),
                "seed": out.get("seed"),
                "family": out.get("mode"),
            },
            "usage": {
                "gpu": out.get("gpu"),
                "cold_start_seconds": out.get("cold_start_seconds"),
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
        return Response(content=base64.b64decode(out["video_b64"]), media_type="video/mp4")

    @web.post("/cancel/{call_id}", dependencies=[Depends(auth)])
    def cancel(call_id: str):
        modal.FunctionCall.from_id(call_id).cancel()
        return {"cancelled": True}

    @web.get("/health")
    def health():
        return {
            "ok": True,
            "app": APP_NAME,
            "fl2va": (Path(MODELS_DIR) / "diffusion_models" / UNET_FL2VA).exists(),
            "ref2va": (Path(MODELS_DIR) / "diffusion_models" / UNET_REF2VA).exists(),
        }

    return web


# --------------------------------------------------------------------------- #
# Local test:  modal run minimaxh3.py --mode fl2va --prompt "..."
# --------------------------------------------------------------------------- #
@app.local_entrypoint()
def main(
    prompt: str = DEFAULT_PROMPT,
    mode: str = "fl2va",
    duration: float = 5.0,
    out: str = "out.mp4",
):
    payload = _build_worker_payload({
        "prompt": prompt,
        "mode": mode,
        "duration_seconds": duration,
        "resolution": "720p",
        "aspect_ratio": "16:9",
    })
    worker = H3Ref2VA if payload["mode"] == "ref2va" else H3FL2VA
    res = worker().generate.remote(payload)
    Path(out).write_bytes(base64.b64decode(res["video_b64"]))
    print(
        f"saved {out} mode={res['mode']} seed={res['seed']} "
        f"{res['width']}x{res['height']} frames={res['frames']} in {res['generation_seconds']}s "
        f"(cold_start={res['cold_start_seconds']}s)"
    )
