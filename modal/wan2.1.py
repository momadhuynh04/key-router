"""
KeyRouter video app — Modal + ComfyUI, scale-to-zero.

Model: Wan2.2 (Apache-2.0), ComfyUI **core** Wan nodes (no extra custom nodes
needed for the graph itself). Weights are the ComfyUI-ready single-file
repackaged safetensors from Comfy-Org.

Cost design:
  * GPU classes have min_containers=0 -> no idle GPU.
  * Weights live in a Modal Volume, downloaded ONCE by a CPU-only function.
  * The public API is a tiny CPU-only gateway; it only *spawns* GPU jobs.

GPU tiers (edit below after your Phase 0 benchmark):
  draft/auto  -> WanDraftWorker    (Wan2.2 TI2V-5B, L40S)
  standard    -> WanStandardWorker (Wan2.2 I2V-A14B 4-step, A100-80GB)
  hero        -> (not wired; config.json maps it to the paid_api stub)

Deploy:
    modal run modal/video_app.py::download_models
    modal deploy modal/video_app.py

Gateway contract (consumed by video/drivers/modal_gateway.py):
    POST /generate          -> {"call_id": "..."}
    GET  /status/{call_id}  -> {"status","progress","error","usage","output"}
    GET  /content/{call_id} -> video/mp4 bytes
    POST /cancel/{call_id}
    GET  /health
The gateway is UNAUTHENTICATED (like the other apps in this workspace). Its URL
is public, so anyone with the link can spend GPU credits — add a Modal secret
later if you need bearer auth.
"""

import base64
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import modal

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
APP_NAME = "keyrouter-wan2.1"
VOLUME_NAME = "keyrouter-wan2.1-models"

DRAFT_GPU = "L40S"          # Wan2.2 TI2V-5B
STANDARD_GPU = "A100-80GB"  # Wan2.2 I2V-A14B fp8 (2-stage)
SCALEDOWN_SECONDS = 60      # idle seconds before the GPU container is released
MAX_GPU_CONTAINERS = 2      # hard cap on parallel GPU spend
GEN_TIMEOUT = 20 * 60

# Pinned (resolved with `git ls-remote` / Hugging Face API on 2026-10-19).
COMFYUI_REPO = "https://github.com/Comfy-Org/ComfyUI"
COMFYUI_SHA = "e3bee7ffcb7d6547ed0bcd15221792f3b8646b17"          # ComfyUI 0.42.0

MODELS_DIR = "/models"
COMFY_DIR = "/opt/ComfyUI"
WORKFLOWS_DIR = "/workflows"
OUT_DIR = "/tmp/comfy_out"
COMFY_PORT = 8188

WAN_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
WAN_REV = "ee6f4a40737a995bf5818954cfce6d59443b0f04"

# (repo_id, remote_path_in_repo, local_subdir, revision)
MODEL_FILES = [
    (WAN_REPO, "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", "diffusion_models", WAN_REV),
    (WAN_REPO, "split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "diffusion_models", WAN_REV),
    (WAN_REPO, "split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "diffusion_models", WAN_REV),
    (WAN_REPO, "split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "loras", WAN_REV),
    (WAN_REPO, "split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "loras", WAN_REV),
    (WAN_REPO, "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders", WAN_REV),
    (WAN_REPO, "split_files/vae/wan2.2_vae.safetensors", "vae", WAN_REV),
    # Wan2.2 14B (I2V/T2V) uses the Wan2.1 VAE; only the 5B TI2V uses wan2.2_vae.
    (WAN_REPO, "split_files/vae/wan_2.1_vae.safetensors", "vae", WAN_REV),
]

RESOLUTION_BASE = {"480p": 480, "720p": 720, "1080p": 1080}


# IMPORTANT: every Modal object below is created unconditionally. Creating Modal
# objects inside conditionals (e.g. based on a local .env file) makes the local and
# remote dependency lists diverge and fails with:
#   "Function has N dependencies but container got M object ids".
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
# Always exactly ONE secret object (even when HF_TOKEN is absent -> empty secret),
# so local and container dependency lists match. Used by download_models only.
HF_SECRET = modal.Secret.from_dict({k: v for k, v in _ENV.items() if k == "HF_TOKEN" and v})

app = modal.App(APP_NAME)
models_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

downloader_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface_hub[hf_xet]>=0.30")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

gpu_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0")
    .pip_install("torch", "torchvision", "torchaudio", "requests")
    .run_commands(
        f"git clone {COMFYUI_REPO} {COMFY_DIR}",
        f"cd {COMFY_DIR} && git checkout {COMFYUI_SHA}",
        f"pip install -r {COMFY_DIR}/requirements.txt",
    )
    .add_local_dir(str(Path(__file__).resolve().parent / "workflows"), WORKFLOWS_DIR)
)

gateway_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]")
    # Also ship the workflows with the gateway so /debug/workflow can report
    # exactly which workflow version the deployed image contains.
    .add_local_dir(str(Path(__file__).resolve().parent / "workflows"), WORKFLOWS_DIR)
)

EXTRA_MODEL_PATHS_YAML = f"""keyrouter:
  base_path: {MODELS_DIR}
  diffusion_models: diffusion_models
  loras: loras
  vae: vae
  text_encoders: text_encoders
"""


# --------------------------------------------------------------------------- #
# Phase 0 helpers
# --------------------------------------------------------------------------- #
def dims_for(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    """Map (resolution tier, aspect ratio) -> (width, height) on a /32 grid."""
    base = RESOLUTION_BASE.get((resolution or "720p").lower(), 720)
    try:
        a, b = (aspect_ratio or "16:9").split(":", 1)
        ratio = float(a) / float(b)
    except (ValueError, ZeroDivisionError):
        ratio = 16 / 9
    if ratio >= 1:
        height, width = base, base * ratio
    else:
        width, height = base, base / ratio
    width = max(32, (int(round(width)) // 32) * 32)
    height = max(32, (int(round(height)) // 32) * 32)
    return width, height


def frames_for(duration_seconds: float, fps: int) -> int:
    """Wan latent length must be 4n+1 (step=4 on the latent node)."""
    n = max(5, round(float(duration_seconds) * fps))
    return n + (1 - n % 4) % 4


def _read_param_map() -> dict:
    with open(os.path.join(WORKFLOWS_DIR, "_param_map.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def apply_params(workflow: dict, family_map: dict, values: dict) -> dict:
    """Patch a ComfyUI API-format workflow using the registered param map."""
    wf = json.loads(json.dumps(workflow))  # deep copy
    for logical, spec in (family_map.get("nodes") or {}).items():
        node_id = str(spec.get("node"))
        if node_id not in wf:
            raise RuntimeError(f"Workflow is missing node '{node_id}' for param '{logical}'.")
        inputs = wf[node_id].setdefault("inputs", {})
        if "input" in spec:
            value = values.get(logical)
            if value is not None:  # never write None into a workflow input
                inputs[spec["input"]] = value
        for target_key, value_key in (spec.get("inputs") or {}).items():
            value = values.get(value_key)
            if value is not None:
                inputs[target_key] = value
    return wf


# --------------------------------------------------------------------------- #
# One-off: download weights into the Volume (CPU only, NO GPU billing)
# --------------------------------------------------------------------------- #
@app.function(image=downloader_image, volumes={MODELS_DIR: models_volume}, secrets=[HF_SECRET], timeout=3 * 3600)
def download_models():
    from huggingface_hub import hf_hub_download

    if not MODEL_FILES:
        raise RuntimeError("MODEL_FILES is empty.")
    token = os.environ.get("HF_TOKEN") or None
    for repo_id, remote_path, subdir, revision in MODEL_FILES:
        local_dir = Path(MODELS_DIR) / subdir
        local_dir.mkdir(parents=True, exist_ok=True)
        target = local_dir / Path(remote_path).name
        if target.exists():
            print(f"[skip] {target}")
            continue
        print(f"[download] {repo_id}/{remote_path} @ {revision}")
        with tempfile.TemporaryDirectory() as tmp:
            got = hf_hub_download(repo_id, remote_path, revision=revision, token=token, local_dir=tmp)
            shutil.move(got, target)
        models_volume.commit()
    models_volume.commit()
    print("done")


# --------------------------------------------------------------------------- #
# GPU worker shared implementation (no Modal decorators here)
# --------------------------------------------------------------------------- #
class WanWorkerBase:
    cold_start_seconds: float | None = None

    def _boot(self) -> None:
        Path("/tmp/extra_model_paths.yaml").write_text(EXTRA_MODEL_PATHS_YAML)
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

    # -- helpers ----------------------------------------------------------- #
    def _http(self, path, payload=None):
        url = f"http://127.0.0.1:{COMFY_PORT}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())

    def _save_input_image(self, url_or_data: str) -> str:
        if url_or_data.startswith("data:"):
            header, _, b64 = url_or_data.partition(",")
            raw = base64.b64decode(b64)
            ext = ".png" if "png" in header else (".jpg" if "jpe" in header else ".webp")
        else:
            with urllib.request.urlopen(url_or_data, timeout=60) as resp:
                raw = resp.read()
            ext = ".jpg"
        name = f"{uuid.uuid4().hex}{ext}"
        os.makedirs(f"{COMFY_DIR}/input", exist_ok=True)
        Path(f"{COMFY_DIR}/input/{name}").write_bytes(raw)
        return name

    @staticmethod
    def _pick_family(param_map: dict, requested: str, has_image: bool) -> str:
        family = requested if requested in param_map else "wan22_ti2v_5b"
        spec = param_map.get(family, {})
        if has_image and not spec.get("requires_image") and spec.get("fallback_with_image"):
            return spec["fallback_with_image"]
        if not has_image and spec.get("requires_image") and spec.get("fallback_without_image"):
            return spec["fallback_without_image"]
        return family

    def _run(self, payload: dict) -> dict:
        t0 = time.time()
        param_map = _read_param_map()

        images = []
        if payload.get("image_url"):
            images.append(payload["image_url"])
        images.extend(payload.get("image_urls") or [])
        image_files = [self._save_input_image(u) for u in images]

        family = self._pick_family(param_map, payload.get("family") or "", bool(image_files))
        family_map = param_map.get(family)
        if not family_map:
            raise RuntimeError(f"Unknown family '{family}'. Known: {sorted(param_map)}")

        # Wan2.2 I2V conditions on a single start frame; extra images are ignored.
        max_images = int(family_map.get("max_images") or 1)
        if len(image_files) > max_images:
            print(f"[warn] family={family} uses at most {max_images} image(s); "
                  f"ignoring {len(image_files) - max_images} extra reference(s)")

        wf_path = Path(WORKFLOWS_DIR) / family_map["workflow"]
        if not wf_path.exists():
            raise RuntimeError(f"Workflow '{family_map['workflow']}' not found in {WORKFLOWS_DIR}.")
        workflow = json.loads(wf_path.read_text())

        fps = int(family_map.get("fps") or 16)
        width, height = dims_for(payload.get("resolution", "720p"), payload.get("aspect_ratio", "16:9"))
        seed = payload.get("seed")
        seed = random.randint(0, 2**31 - 1) if seed is None else int(seed)

        values = {
            "prompt": payload.get("prompt", ""),
            "negative_prompt": payload.get("extra", {}).get("negative_prompt", ""),
            "width": width,
            "height": height,
            "length": frames_for(payload.get("duration_seconds") or 5, fps),
            "fps": fps,
            "steps": int((payload.get("workflow_overrides") or {}).get("steps", family_map.get("steps") or 20)),
            "seed": seed,
            "first_image": image_files[0] if image_files else None,
            "last_image": image_files[1] if len(image_files) > 1 else None,
        }
        values.update(payload.get("workflow_overrides") or {})

        workflow = apply_params(workflow, family_map, values)
        prompt_id = self._http("/prompt", {"prompt": workflow, "client_id": uuid.uuid4().hex})["prompt_id"]
        output_node = str(family_map.get("output_node") or "")

        while True:
            if self.proc.poll() is not None:
                raise RuntimeError("ComfyUI crashed (likely OOM) — check logs")
            history = self._http(f"/history/{prompt_id}")
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI execution error: {json.dumps(status)[:1500]}")
                outputs = entry.get("outputs", {}).get(output_node, {})
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
                    f = files[0]
                    data = (Path(OUT_DIR) / f.get("subfolder", "") / f["filename"]).read_bytes()
                    break
            time.sleep(2)

        elapsed = round(time.time() - t0, 1)
        cold = self.cold_start_seconds or 0
        return {
            "video_b64": base64.b64encode(data).decode(),
            "seed": seed,
            "width": width,
            "height": height,
            "frames": values["length"],
            "fps": fps,
            "family": family,
            "duration_seconds": round(values["length"] / fps, 2),
            "resolution": f"{width}x{height}",
            "generation_seconds": elapsed,
            "cold_start_seconds": self.cold_start_seconds,
            "gpu_seconds": round(elapsed + cold, 1),
            "gpu": getattr(self, "GPU_NAME", None),
        }


def _cls_kwargs(gpu: str) -> dict:
    return dict(
        image=gpu_image,
        gpu=gpu,
        volumes={MODELS_DIR: models_volume},
        timeout=GEN_TIMEOUT,
        scaledown_window=SCALEDOWN_SECONDS,
        min_containers=0,
        max_containers=MAX_GPU_CONTAINERS,
    )


@app.cls(**_cls_kwargs(DRAFT_GPU))
class WanDraftWorker(WanWorkerBase):
    GPU_NAME = DRAFT_GPU

    @modal.enter()
    def start_comfy(self):
        self._boot()

    @modal.exit()
    def stop_comfy(self):
        self._shutdown()

    @modal.method()
    def generate(self, payload: dict) -> dict:
        return self._run(payload)


@app.cls(**_cls_kwargs(STANDARD_GPU))
class WanStandardWorker(WanWorkerBase):
    GPU_NAME = STANDARD_GPU

    @modal.enter()
    def start_comfy(self):
        self._boot()

    @modal.exit()
    def stop_comfy(self):
        self._shutdown()

    @modal.method()
    def generate(self, payload: dict) -> dict:
        return self._run(payload)


# --------------------------------------------------------------------------- #
# Public gateway (CPU only, scale-to-zero): submit -> poll -> fetch.
# --------------------------------------------------------------------------- #
@app.function(image=gateway_image, timeout=300)
@modal.asgi_app()
def api():
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.responses import Response

    web = FastAPI(title="KeyRouter Video Gateway")

    WORKERS = {
        "draft": WanDraftWorker,
        "auto": WanDraftWorker,
        "standard": WanStandardWorker,
        "hero": WanStandardWorker,
    }

    def auth(authorization: str = Header(default="")):
        # No token is injected -> open access, matching the other Modal apps here.
        # To enable auth: create a Modal secret exposing MODAL_VIDEO_GATEWAY_TOKEN
        # and add `secrets=[modal.Secret.from_name("keyrouter-video-auth")]` to `api`.
        key = os.environ.get("MODAL_VIDEO_GATEWAY_TOKEN", "")
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
        worker = WORKERS.get(payload.get("quality_tier") or "auto", WanDraftWorker)
        call = worker().generate.spawn(payload)
        return {"call_id": call.object_id}

    @web.get("/status/{call_id}", dependencies=[Depends(auth)])
    def status(call_id: str):
        call = _get_call(call_id)
        try:
            # NOTE: timeout=0 uses a zero RPC deadline and times out even for
            # finished calls. A small positive timeout returns immediately when
            # the result is ready, otherwise it is a bounded poll.
            out = call.get(timeout=2.0)
        except TimeoutError:
            return {"status": "running", "progress": 0.5}
        except Exception as exc:  # job failed
            return {"status": "failed", "progress": 0.0,
                    "error": {"code": "generation_failed", "message": str(exc)[:500]}}
        return {
            "status": "completed",
            "progress": 1.0,
            "output": {
                "resolution": out.get("resolution"),
                "duration_seconds": out.get("duration_seconds"),
                "seed": out.get("seed"),
                "family": out.get("family"),
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
        return {"ok": True}

    # Diagnostics: shows the workflow files baked into THIS deployment.
    @web.get("/debug/workflows")
    def debug_workflows():
        import hashlib

        result = {}
        for path in sorted(Path(WORKFLOWS_DIR).glob("*.json")):
            data = path.read_bytes()
            result[path.name] = {
                "sha256": hashlib.sha256(data).hexdigest()[:16],
                "bytes": len(data),
            }
        return result

    @web.get("/debug/workflow/{name}")
    def debug_workflow(name: str):
        path = Path(WORKFLOWS_DIR) / name
        if path.suffix != ".json" or not path.exists():
            raise HTTPException(404, "workflow not found")
        return Response(content=path.read_bytes(), media_type="application/json")

    return web
