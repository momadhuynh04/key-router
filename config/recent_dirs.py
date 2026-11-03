import json
import os
from typing import List

MAX_RECENT_DIRS = 8


def _get_config_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "config.json")


def _read_config() -> dict:
    path = _get_config_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _write_config(data: dict) -> None:
    path = _get_config_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


def load_recent_dirs() -> List[str]:
    dirs = _read_config().get("recent_dirs", [])
    if not isinstance(dirs, list):
        return []
    return [d for d in dirs if isinstance(d, str) and os.path.isdir(d)][:MAX_RECENT_DIRS]


def add_recent_dir(path: str) -> List[str]:
    normalized = os.path.normpath(os.path.abspath(os.path.expanduser(path)))
    dirs = [d for d in load_recent_dirs() if d != normalized]
    dirs.insert(0, normalized)
    dirs = dirs[:MAX_RECENT_DIRS]
    data = _read_config()
    data["recent_dirs"] = dirs
    _write_config(data)
    return dirs
