import asyncio
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.parse
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.logging import log_warn
from config.model_map import model_mapper
from config.codex_config import setup_codex_config, persist_codex_env_var, get_codex_config_path, DEFAULT_BASE_URL as CODEX_BASE_URL
import proxy.server_helpers as _helpers
from proxy.server_helpers import (
    IDE_DEFINITIONS,
    PROXY_URL,
    _save_ide_detection,
    _get_ide_settings_path,
    _safe_merge_json,
    _proxy_env,
    _setup_claude_env,
    _setup_ide_settings,
    _find_linux_terminal,
    _launch_terminal,
    _resolve_launch,
)

router = APIRouter()
cached_available_models: Dict = {}


class ModelMappingRequest(BaseModel):
    source_model: str
    target: str

class LaunchRequest(BaseModel):
    path: Optional[str] = None
    repo_url: Optional[str] = None

class IDESetupRequest(BaseModel):
    editors: list[str] = []

class IDELaunchRequest(BaseModel):
    editor: str
    path: Optional[str] = None

class CodexSetupRequest(BaseModel):
    base_url: Optional[str] = None

class CustomModelSpec(BaseModel):
    id: str
    name: str
    reasoning: bool = False
    image: bool = False

class CustomProviderSpec(BaseModel):
    id: str
    display_name: str
    provider_api: str
    base_url: str
    api_key: str
    headers: Optional[Dict[str, str]] = None
    models: list[CustomModelSpec]


@router.get("/api/health")
async def health_check():
    return {"status": "ok"}


@router.get("/api/ide-detect")
async def ide_detect():
    import proxy.server as _srv
    fn = getattr(_srv, "_detect_ides", _helpers._detect_ides)
    detected = await asyncio.to_thread(fn)
    if detected:
        _save_ide_detection(detected)
    return {"detected": detected}


@router.get("/api/ide-detect-refresh")
async def ide_detect_refresh():
    import proxy.server as _srv
    fn = getattr(_srv, "_detect_ides", _helpers._detect_ides)
    detected = await asyncio.to_thread(fn)
    _save_ide_detection(detected)
    return {"detected": detected}


@router.post("/api/ide-setup")
async def ide_setup(request: IDESetupRequest):
    results = []
    claude_changed = _setup_claude_env()
    results.append({"target": "claude_settings", "configured": claude_changed})
    codex_changed = setup_codex_config()
    persist_codex_env_var()
    results.append({"target": "codex_config", "configured": codex_changed})
    import proxy.server as _srv2
    _det = getattr(_srv2, "_detect_ides", _helpers._detect_ides)
    detected = await asyncio.to_thread(_det)
    for ide_def in IDE_DEFINITIONS:
        if ide_def["id"] in request.editors and ide_def["id"] in detected:
            if ide_def["supports_claude_extension"]:
                changed = _setup_ide_settings(ide_def["config_dir"])
                results.append({"target": ide_def["id"], "configured": changed})
            else:
                results.append({"target": ide_def["id"], "configured": False, "note": "IDE does not support Claude Code extension — use terminal inside this IDE with `claude` CLI instead"})
    return {"status": "success", "results": results}


@router.post("/api/ide-launch")
async def ide_launch(request: IDELaunchRequest):
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_cwd = base_dir
    if request.path and os.path.isdir(request.path):
        target_cwd = request.path
    import proxy.server as _srv3
    _det3 = getattr(_srv3, "_detect_ides", _helpers._detect_ides)
    _launch_fn = getattr(_srv3, "_launch_ide", _helpers._launch_ide)
    detected = await asyncio.to_thread(_det3)
    info = detected.get(request.editor)
    if not info:
        raise HTTPException(status_code=404, detail=f"IDE '{request.editor}' not found. Run /api/ide-detect-refresh first.")
    try:
        _launch_fn(info["binary"], target_cwd)
        return {"status": "success", "message": f"Launched {info['name']}!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/models")
async def get_models():
    return {"mappings": model_mapper.get_all()}


@router.get("/api/available-models")
async def get_available_models():
    global cached_available_models
    if cached_available_models:
        return cached_available_models
    models_data = {
        "openrouter": [],
        "deepseekplatform": ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://openrouter.ai/api/v1/models", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                models_data["openrouter"] = [m["id"] for m in data.get("data", [])]
    except Exception as e:
        log_warn(f"Error fetching OpenRouter models: {e}")
        models_data["openrouter"] = ["qwen/qwen-turbo", "meta-llama/llama-3-8b-instruct"]
    try:
        from config.settings import settings
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.deepseek.com/models", headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                models_data["deepseekplatform"] = [m["id"] for m in data.get("data", [])]
            else:
                models_data["deepseekplatform"] = ["deepseek-chat", "deepseek-reasoner"]
    except Exception as e:
        log_warn(f"Error fetching DeepSeek models: {e}")
        models_data["deepseekplatform"] = ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"]
    try:
        from config.settings import settings as _gs
        if _gs.google_api_key:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{_gs.google_base_url.rstrip('/')}/models?key={_gs.google_api_key}", timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    ids = []
                    for m in data.get("models", []):
                        methods = m.get("supportedGenerationMethods") or []
                        if "generateContent" in methods:
                            mid = m.get("name", "")
                            if mid.startswith("models/"):
                                mid = mid[len("models/"):]
                            if mid:
                                ids.append(mid)
                    if ids:
                        models_data["googleaistudio"] = ids
        if "googleaistudio" not in models_data:
            models_data["googleaistudio"] = ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.5-flash"]
    except Exception as e:
        log_warn(f"Error fetching Google models: {e}")
        models_data.setdefault("googleaistudio", ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.5-flash"])
    try:
        from config.custom_providers import load_custom_providers as _load_cp
        for _pid, _spec in _load_cp().items():
            models_data[_pid] = [m.get("id") for m in (_spec.get("models") or []) if isinstance(m, dict) and m.get("id")]
    except Exception:
        pass
    cached_available_models = models_data
    return models_data


@router.get("/api/custom-providers")
async def list_custom_providers():
    from config.custom_providers import get_masked_providers
    return {"providers": get_masked_providers()}


@router.post("/api/custom-providers")
async def create_custom_provider(request: CustomProviderSpec):
    from config.custom_providers import load_custom_providers, save_custom_providers, normalize_api_key_input
    raw_env = normalize_api_key_input(request.api_key)
    if not raw_env.strip():
        headers = request.headers or {}
        has_headers = any(k.strip() and v.strip() for k, v in headers.items()) if isinstance(headers, dict) else False
        if not has_headers:
            raise HTTPException(status_code=400, detail="API key ENV var is required (or provide auth via headers)")
    else:
        try:
            from config.custom_providers import validate_spec as _vs
            _vs({"id": request.id, "display_name": request.display_name, "provider_api": request.provider_api, "base_url": request.base_url, "api_key_env": raw_env, "headers": request.headers, "models": [m.model_dump() for m in request.models]})
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
    spec = {
        "id": request.id.strip().lower(),
        "display_name": request.display_name.strip(),
        "provider_api": request.provider_api,
        "base_url": request.base_url.strip().rstrip("/"),
        "api_key_env": raw_env,
        "headers": request.headers or {},
        "models": [m.model_dump() for m in request.models],
    }
    providers = dict(load_custom_providers())
    providers[spec["id"]] = spec
    save_custom_providers(providers)
    global cached_available_models
    cached_available_models = {}
    from config.custom_providers import get_masked_providers as _masked
    masked = _masked()
    return {"status": "success", "providers": masked}


@router.delete("/api/custom-providers/{provider_id}")
async def delete_custom_provider(provider_id: str):
    from config.custom_providers import delete_provider, load_custom_providers, get_masked_providers
    providers = load_custom_providers()
    if provider_id not in providers:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    orphaned = [k for k, v in model_mapper.get_all().items() if v.startswith(f"{provider_id}/")]
    delete_provider(provider_id)
    global cached_available_models
    cached_available_models = {}
    return {"status": "success", "providers": get_masked_providers(), "orphaned_mappings": orphaned}


@router.get("/api/custom-providers/{provider_id}/models")
async def fetch_custom_provider_models(provider_id: str):
    import os as _os
    from config.custom_providers import load_custom_providers
    spec = load_custom_providers().get(provider_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    api_key = _os.environ.get(spec.get("api_key_env", ""), "") if spec.get("api_key_env") else ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if spec.get("headers"):
        headers.update(spec["headers"])
    base = spec.get("base_url", "").rstrip("/")
    candidates = [f"{base}/models", f"{base}/v1/models"] if not base.endswith("/models") else [base]
    for url in candidates:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data.get("data"), list):
                        ids = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]
                        return {"ids": ids}
                    if isinstance(data, list):
                        return {"ids": [str(x) for x in data]}
        except Exception:
            continue
    return {"ids": []}


@router.post("/api/models")
async def update_model(request: ModelMappingRequest):
    model_mapper.set_mapping(request.source_model, request.target)
    return {"status": "success", "mappings": model_mapper.get_all()}


@router.post("/api/launch")
async def launch_claude(request: LaunchRequest):
    cmd, target_cwd = _resolve_launch(request, "claude")
    try:
        _launch_terminal(cmd, target_cwd)
        return {"status": "success", "message": "Launched Claude Code!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/codex-detect")
async def codex_detect():
    detected = {}
    binary_path = shutil.which("codex")
    if binary_path:
        version = ""
        try:
            result = subprocess.run([binary_path, "--version"], capture_output=True, text=True, timeout=10)
            version = result.stdout.strip().split("\n")[0] if result.returncode == 0 else ""
        except Exception:
            pass
        detected = {
            "binary": binary_path,
            "version": version,
            "name": "Codex CLI",
            "last_detected": datetime.datetime.now().isoformat()
        }
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json")
        data = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    pass
        data["codex_cli_detected"] = detected
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return {"detected": detected}


@router.post("/api/codex-setup")
async def codex_setup(request: CodexSetupRequest | None = None):
    base_url = (request.base_url if request and request.base_url else CODEX_BASE_URL)
    changed = setup_codex_config(base_url=base_url)
    persisted = persist_codex_env_var()
    return {
        "status": "success",
        "configured": changed,
        "config_path": get_codex_config_path(),
        "base_url": base_url,
        "env_persisted": persisted
    }


@router.post("/api/codex-launch")
async def codex_launch(request: LaunchRequest):
    cmd, target_cwd = _resolve_launch(request, "codex")
    try:
        _launch_terminal(cmd, target_cwd)
        return {"status": "success", "message": "Launched Codex!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/browse-folder")
async def browse_folder():
    try:
        system = platform.system()
        def _run_linux_picker():
            for picker in ["zenity", "kdialog", "yad"]:
                if shutil.which(picker):
                    try:
                        if picker == "zenity":
                            cmd = [picker, "--file-selection", "--directory", "--title=Select Project Folder"]
                        elif picker == "yad":
                            cmd = [picker, "--file-selection", "--directory", "--title=Select Project Folder"]
                        else:
                            cmd = [picker, "--getexistingdirectory", "--title=Select Project Folder"]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                        path = result.stdout.strip()
                        if path:
                            return path
                    except Exception:
                        pass
            return None
        def _run_win_picker():
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                path = filedialog.askdirectory(parent=root, title="Select Project Folder")
                root.destroy()
                return path if path else None
            except Exception:
                return None
        def _run_tkinter_picker():
            script = '''
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
print(filedialog.askdirectory(parent=root, title="Select Project Folder"))
'''
            try:
                result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
                return result.stdout.strip()
            except Exception:
                return None
        if system == "Linux":
            path = await asyncio.to_thread(_run_linux_picker)
            if path:
                return {"path": path}
        if system == "Windows":
            path = await asyncio.to_thread(_run_win_picker)
            if path:
                return {"path": path}
        path = await asyncio.to_thread(_run_tkinter_picker)
        if path:
            return {"path": path}
    except Exception:
        pass
    return {"path": ""}


@router.api_route("/api/hello", methods=["GET", "HEAD"])
async def api_hello():
    return {"message": "key-router proxy is running"}
