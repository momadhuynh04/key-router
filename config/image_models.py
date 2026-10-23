"""Per-model configuration for the AdCraft image route.

Image backends differ a lot: reference-image field names, whether they accept
an aspect ratio or explicit width/height, output-format parameter names, and how
many references they support. `config.json -> image_models` maps a target id
(`"<backend>/<upstream_model>"`) to those settings; missing entries fall back to
sensible per-backend defaults.
"""

import json
import os
from typing import Any, Dict, Optional

SUPPORTED_BACKENDS = ("openrouter", "replicate")

DEFAULT_ASPECT_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4"]

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def _defaults(backend: str, upstream_model: str) -> Dict[str, Any]:
    return {
        "backend": backend,
        "upstream_model": upstream_model,
        "max_references": 4 if backend == "openrouter" else 0,
        "reference_field": None,
        "aspect_ratio_mode": "ratio" if backend == "openrouter" else "none",
        "aspect_ratio_field": "aspect_ratio",
        "width_field": "width",
        "height_field": "height",
        "supported_aspect_ratios": list(DEFAULT_ASPECT_RATIOS),
        "output_format_field": "output_format" if backend == "openrouter" else None,
        "extra_input": {},
    }


def load_image_models(config_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    path = config_path or _CONFIG_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    models = data.get("image_models") or {}
    return models if isinstance(models, dict) else {}


def get_image_model_config(target_id: str, config_path: Optional[str] = None) -> Dict[str, Any]:
    """Merge config.json overrides for `<backend>/<upstream_model>` with defaults."""
    if not isinstance(target_id, str) or "/" not in target_id:
        raise ValueError(f"Invalid image model id '{target_id}'. Expected 'backend/upstream_model'.")
    backend, upstream_model = target_id.split("/", 1)
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(
            f"Unsupported image backend '{backend}' for '{target_id}'. "
            f"Expected one of: {', '.join(SUPPORTED_BACKENDS)}."
        )
    if not upstream_model:
        raise ValueError(f"Image model id '{target_id}' is missing the upstream model name.")

    cfg = _defaults(backend, upstream_model)
    overrides = load_image_models(config_path).get(target_id) or {}
    if isinstance(overrides, dict):
        cfg.update(overrides)

    # The id is authoritative for backend/model.
    cfg["backend"] = backend
    cfg["upstream_model"] = upstream_model
    return cfg
