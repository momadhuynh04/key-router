"""Load/save the `video_models` section of config.json.

Video aliases live in their own namespace (NOT in `model_mappings`), so the text
and image routing code — and `ModelMapper` — are never touched and no alias can
accidentally hit the gpt/codex family fallback.
"""

import json
import os
from typing import Any, Dict, List, Optional

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

SUPPORTED_DRIVERS = ("modal_gateway", "paid_api")

DEFAULT_DURATION_RANGE = [1, 15]
DEFAULT_RESOLUTIONS = ["480p", "720p", "1080p"]
DEFAULT_ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"]


def _read_config(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_video_models(config_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    data = _read_config(config_path or _CONFIG_PATH)
    models = data.get("video_models") or {}
    return models if isinstance(models, dict) else {}


def _normalize(alias: str, spec: Any) -> Dict[str, Any]:
    spec = spec if isinstance(spec, dict) else {}
    return {
        "alias": alias,
        "driver": spec.get("driver") or "modal_gateway",
        "app": spec.get("app"),
        "family": spec.get("family"),
        "quality_tier": spec.get("quality_tier") or "auto",
        "provider": spec.get("provider"),
        "upstream_model": spec.get("upstream_model"),
        "max_references": int(spec.get("max_references", 9)),
        "duration_range": list(spec.get("duration_range") or DEFAULT_DURATION_RANGE),
        "resolutions": list(spec.get("resolutions") or DEFAULT_RESOLUTIONS),
        "aspect_ratios": list(spec.get("aspect_ratios") or DEFAULT_ASPECT_RATIOS),
        "supports_native_audio": bool(spec.get("supports_native_audio", False)),
        "extra": spec.get("extra") or {},
    }


def get_video_model_config(alias: str, config_path: Optional[str] = None) -> Dict[str, Any]:
    models = load_video_models(config_path)
    if alias not in models:
        raise ValueError(
            f"No video alias '{alias}' configured. Add it under config.json -> video_models."
        )
    return _normalize(alias, models[alias])


def list_video_aliases(config_path: Optional[str] = None) -> List[str]:
    return sorted(load_video_models(config_path).keys())


def save_video_alias(alias: str, spec: Dict[str, Any], config_path: Optional[str] = None) -> None:
    path = config_path or _CONFIG_PATH
    data = _read_config(path)
    models = data.get("video_models") or {}
    models[alias] = spec
    data["video_models"] = models
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
