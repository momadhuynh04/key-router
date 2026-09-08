"""
Codex configuration — writes ~/.codex/config.toml so both Codex CLI and the
Codex VS Code extension route their traffic through the key-router proxy.

Codex (>= 0.149) only supports wire_api = "responses" and REQUIRES env_key on
custom providers — without it the CLI silently exits before sending anything.
The proxy performs no auth, so we pair env_key with a constant dummy value and
persist it into the user session (environment.d / launchctl / setx) so the
Codex extension works even in IDEs that were not launched by key-router.
"""

import json
import os
import platform
import subprocess
import tomllib
from typing import Any, Dict, List, Optional

KEY_ROUTER_PROVIDER_ID = "key-router"
API_KEY_ENV = "KEY_ROUTER_API_KEY"
API_KEY_VALUE = "key-router"  # proxy performs no auth — value is a placeholder
DEFAULT_BASE_URL = "http://127.0.0.1:8082/v1"

PROVIDER_DEFAULTS = {
    "name": "key-router",
    "base_url": DEFAULT_BASE_URL,
    "env_key": API_KEY_ENV,
    # Codex >= 0.149 removed wire_api = "chat"; the proxy implements /v1/responses.
    "wire_api": "responses",
}

# Backwards-compat aliases
FREECLAUDE_PROVIDER_ID = KEY_ROUTER_PROVIDER_ID
FREECLAUDE_API_KEY_ENV = API_KEY_ENV
FREECLAUDE_API_KEY = API_KEY_ENV


def get_codex_config_path(home: Optional[str] = None) -> str:
    home = home or os.path.expanduser("~")
    return os.path.join(home, ".codex", "config.toml")


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # TOML basic strings share JSON escaping for our use cases
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_value(v) for v in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value)!r}")


def _dump_toml(data: Dict[str, Any], prefix: str = "") -> str:
    """Minimal TOML serializer: scalar keys first, then nested tables."""
    lines = []
    for key, value in data.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_format_toml_value(value)}")

    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        full_name = f"{prefix}.{key}" if prefix else key
        if lines:
            lines.append("")
        lines.append(f"[{full_name}]")
        table_body = _dump_toml(value, full_name)
        if table_body:
            lines.append(table_body)

    return "\n".join(lines)


def _migrate_legacy_provider(data: Dict[str, Any]) -> bool:
    """Migrate old freeclaude entries to key-router (one-time, backwards-compat)."""
    migrated = False
    if data.get("model_provider") == "freeclaude":
        data["model_provider"] = KEY_ROUTER_PROVIDER_ID
        migrated = True
    providers = data.get("model_providers")
    if isinstance(providers, dict) and "freeclaude" in providers and KEY_ROUTER_PROVIDER_ID not in providers:
        providers[KEY_ROUTER_PROVIDER_ID] = providers.pop("freeclaude")
        migrated = True
    elif isinstance(providers, dict) and "freeclaude" in providers:
        providers.pop("freeclaude", None)
        migrated = True
    return migrated


def setup_codex_config(
    config_path: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
) -> bool:
    """
    Merge key-router's provider entry into ~/.codex/config.toml.

    Existing user settings are preserved — only `model_provider` and our
    [model_providers.key-router] table are touched.

    Returns True if the file was written/updated, False when already up to date.
    """
    path = config_path or get_codex_config_path()

    data: Dict[str, Any] = {}
    file_exists = os.path.exists(path)
    if file_exists:
        with open(path, "rb") as f:
            try:
                data = tomllib.load(f)
            except tomllib.TOMLDecodeError:
                data = {}

    changed = _migrate_legacy_provider(data)

    if data.get("model_provider") != KEY_ROUTER_PROVIDER_ID:
        data["model_provider"] = KEY_ROUTER_PROVIDER_ID
        changed = True

    providers = data.setdefault("model_providers", {})
    managed = dict(providers.get(KEY_ROUTER_PROVIDER_ID) or {})

    defaults = dict(PROVIDER_DEFAULTS)
    defaults["base_url"] = base_url

    for key, value in defaults.items():
        if managed.get(key) != value:
            managed[key] = value
            changed = True

    providers[KEY_ROUTER_PROVIDER_ID] = managed

    if changed:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        content = _dump_toml(data)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content + "\n")

    return changed


def persist_codex_env_var(home: Optional[str] = None) -> List[str]:
    """
    Make API_KEY_ENV visible to GUI apps so the Codex VS Code extension works
    even in IDEs that were NOT launched by key-router. Best-effort per platform:

      - Linux:   ~/.config/environment.d/key-router.conf (systemd user session)
                 + `systemctl --user set-environment` for immediate effect
      - Windows: `setx` (applies to newly launched processes)
      - macOS:   `launchctl setenv` (applies to newly launched GUI apps)

    Returns a list describing what was applied (empty when nothing worked).
    """
    applied: List[str] = []
    home = home or os.path.expanduser("~")
    system = platform.system()

    if system == "Linux":
        try:
            env_dir = os.path.join(home, ".config", "environment.d")
            os.makedirs(env_dir, exist_ok=True)
            conf_path = os.path.join(env_dir, "key-router.conf")
            content = f"{API_KEY_ENV}={API_KEY_VALUE}\n"
            existing = ""
            if os.path.exists(conf_path):
                with open(conf_path, "r", encoding="utf-8") as f:
                    existing = f.read()
            if existing != content:
                with open(conf_path, "w", encoding="utf-8") as f:
                    f.write(content)
            applied.append(conf_path)
        except OSError:
            pass
        try:
            subprocess.run(
                ["systemctl", "--user", "set-environment", f"{API_KEY_ENV}={API_KEY_VALUE}"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
    elif system == "Windows":
        try:
            subprocess.run(["setx", API_KEY_ENV, API_KEY_VALUE],
                           capture_output=True, timeout=15)
            applied.append("setx")
        except Exception:
            pass
    else:  # macOS
        try:
            subprocess.run(["launchctl", "setenv", API_KEY_ENV, API_KEY_VALUE],
                           capture_output=True, timeout=10)
            applied.append("launchctl")
        except Exception:
            pass

    return applied
