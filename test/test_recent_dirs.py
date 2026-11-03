import json

import pytest

from config import recent_dirs


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(recent_dirs, "_get_config_path", lambda: str(config_file))
    return config_file


def test_add_and_load_recent_dirs(temp_config, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()

    assert recent_dirs.load_recent_dirs() == []
    assert recent_dirs.add_recent_dir(str(a)) == [str(a)]
    assert recent_dirs.add_recent_dir(str(b)) == [str(b), str(a)]
    assert recent_dirs.load_recent_dirs() == [str(b), str(a)]


def test_add_existing_dir_moves_to_front(temp_config, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()

    recent_dirs.add_recent_dir(str(a))
    recent_dirs.add_recent_dir(str(b))
    assert recent_dirs.add_recent_dir(str(a)) == [str(a), str(b)]


def test_missing_dirs_skipped_and_limit_enforced(temp_config, tmp_path):
    existing = tmp_path / "here"
    existing.mkdir()
    temp_config.write_text(json.dumps({"recent_dirs": [str(existing), str(tmp_path / "gone")]}))

    assert recent_dirs.load_recent_dirs() == [str(existing)]

    for i in range(recent_dirs.MAX_RECENT_DIRS + 3):
        d = tmp_path / f"d{i}"
        d.mkdir()
        recent_dirs.add_recent_dir(str(d))

    dirs = recent_dirs.load_recent_dirs()
    assert len(dirs) == recent_dirs.MAX_RECENT_DIRS
    assert dirs[0] == str(tmp_path / f"d{recent_dirs.MAX_RECENT_DIRS + 2}")


def test_other_config_keys_preserved(temp_config, tmp_path):
    temp_config.write_text(json.dumps({"model_mappings": {"claude": "openrouter/anthropic/claude-sonnet-4-5"}}))
    d = tmp_path / "d"
    d.mkdir()

    recent_dirs.add_recent_dir(str(d))

    data = json.loads(temp_config.read_text())
    assert data["model_mappings"] == {"claude": "openrouter/anthropic/claude-sonnet-4-5"}
    assert data["recent_dirs"] == [str(d)]
