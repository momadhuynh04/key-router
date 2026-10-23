"""Load/save the `audio_models` section of config.json.

Audio aliases live in their own namespace (like `video_models`), so the text,
image and video routing code is untouched.
"""

import json
import os
from typing import Any, Dict, List, Optional

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

SUPPORTED_DRIVERS = ("modal_gateway",)

DEFAULT_PROMPT = (
    "diegetic sound effects and ambience synchronized to the action; "
    "no speech, no dialogue, no music"
)


def _read_config(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_audio_models(config_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    data = _read_config(config_path or _CONFIG_PATH)
    models = data.get("audio_models") or {}
    return models if isinstance(models, dict) else {}


def _normalize(alias: str, spec: Any) -> Dict[str, Any]:
    spec = spec if isinstance(spec, dict) else {}
    return {
        "alias": alias,
        "driver": spec.get("driver") or "modal_gateway",
        "app": spec.get("app"),
        "family": spec.get("family"),
        "quality_tier": spec.get("quality_tier") or "default",
        "max_video_seconds": float(spec.get("max_video_seconds", 15)),
        "response_format": spec.get("response_format") or "wav",
        "extra": spec.get("extra") or {},
    }


def get_audio_model_config(alias: str, config_path: Optional[str] = None) -> Dict[str, Any]:
    models = load_audio_models(config_path)
    if alias not in models:
        raise ValueError(
            f"No audio alias '{alias}' configured. Add it under config.json -> audio_models."
        )
    return _normalize(alias, models[alias])


def list_audio_aliases(config_path: Optional[str] = None) -> List[str]:
    return sorted(load_audio_models(config_path).keys())
