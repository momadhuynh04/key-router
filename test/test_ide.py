from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import json
import os
import shutil
import platform
from proxy.server import app, _safe_merge_json, _get_ide_settings_path, IDE_DEFINITIONS

client = TestClient(app)


# ----------------------------------------
# 1. IDE Detection
# ----------------------------------------

def test_ide_detect_endpoint():
    """IDE detect endpoint returns detected editors."""
    response = client.get("/api/ide-detect")
    assert response.status_code == 200
    data = response.json()
    assert "detected" in data
    assert isinstance(data["detected"], dict)


def test_ide_detect_refresh_endpoint():
    """IDE detect refresh forces rescan and returns results."""
    response = client.get("/api/ide-detect-refresh")
    assert response.status_code == 200
    data = response.json()
    assert "detected" in data


def test_ide_detection_includes_supported_ides():
    """Detection returns IDEs that are actually installed."""
    response = client.get("/api/ide-detect-refresh")
    data = response.json()
    detected = data["detected"]

    for ide_def in IDE_DEFINITIONS:
        if shutil.which(ide_def["binary"]):
            assert ide_def["id"] in detected, f"{ide_def['id']} should be detected"
            info = detected[ide_def["id"]]
            assert "binary" in info
            assert "name" in info
            assert "version" in info
            assert "supports_claude_extension" in info


def test_ide_detection_fields():
    """Each detected IDE has all required fields."""
    response = client.get("/api/ide-detect-refresh")
    data = response.json()
    for ide_id, info in data["detected"].items():
        assert "binary" in info
        assert "name" in info
        assert "version" in info
        assert "config_dir" in info
        assert "supports_claude_extension" in info
        assert "last_detected" in info
        assert isinstance(info["supports_claude_extension"], bool)


# ----------------------------------------
# 2. IDE Setup
# ----------------------------------------

def test_ide_setup_no_editors():
    """IDE setup with empty editors list still configures Claude settings."""
    response = client.post("/api/ide-setup", json={"editors": []})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    results = {r["target"] for r in data["results"]}
    assert "claude_settings" in results


def test_ide_setup_unknown_editor():
    """IDE setup with non-existent editor is silently ignored."""
    response = client.post("/api/ide-setup", json={"editors": ["nonexistent_ide"]})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"


def test_ide_setup_vscode_has_claude_extension():
    """VS Code definition should support Claude Code extension."""
    vscode_def = next(d for d in IDE_DEFINITIONS if d["id"] == "vscode")
    assert vscode_def["supports_claude_extension"] is True


def test_ide_setup_codium_has_claude_extension():
    """VSCodium definition should support Claude Code extension."""
    codium_def = next(d for d in IDE_DEFINITIONS if d["id"] == "vscodium")
    assert codium_def["supports_claude_extension"] is True


def test_ide_setup_cursor_has_claude_extension():
    """Cursor definition should support Claude Code extension."""
    cursor_def = next(d for d in IDE_DEFINITIONS if d["id"] == "cursor")
    assert cursor_def["supports_claude_extension"] is True


# ----------------------------------------
# 3. IDE Launch
# ----------------------------------------

def test_ide_launch_missing_editor():
    """Launch with unknown editor returns 404."""
    response = client.post("/api/ide-launch", json={
        "editor": "nonexistent_ide",
        "path": None
    })
    assert response.status_code == 404


@patch("proxy.server._detect_ides")
@patch("proxy.server._launch_ide")
def test_ide_launch_vscode(mock_launch, mock_detect):
    """Launch VS Code calls _launch_ide with correct binary and cwd."""
    mock_detect.return_value = {"vscode": {"binary": "/fake/code", "name": "VS Code"}}
    response = client.post("/api/ide-launch", json={
        "editor": "vscode",
        "path": None
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    mock_launch.assert_called_once()
    args = mock_launch.call_args[0]
    assert "code" in args[0]


@patch("proxy.server._detect_ides")
@patch("proxy.server._launch_ide")
def test_ide_launch_with_path(mock_launch, mock_detect):
    """Launch with explicit path passes it to launcher."""
    mock_detect.return_value = {"vscode": {"binary": "/fake/code", "name": "VS Code"}}
    test_path = "/tmp/test_project"
    os.makedirs(test_path, exist_ok=True)

    response = client.post("/api/ide-launch", json={
        "editor": "vscode",
        "path": test_path
    })
    assert response.status_code == 200
    mock_launch.assert_called_once()
    args = mock_launch.call_args[0]
    assert test_path in args[1] or test_path == args[1]

    os.rmdir(test_path)


# ----------------------------------------
# 4. Count Tokens Endpoint
# ----------------------------------------

def test_count_tokens_basic():
    """Count tokens returns input_tokens for a simple message."""
    response = client.post("/v1/messages/count_tokens", json={
        "messages": [{"role": "user", "content": "hello world"}]
    })
    assert response.status_code == 200
    data = response.json()
    assert "input_tokens" in data
    assert data["input_tokens"] > 0


def test_count_tokens_with_system():
    """Count tokens includes system prompt in count."""
    response = client.post("/v1/messages/count_tokens", json={
        "messages": [{"role": "user", "content": "hello"}],
        "system": "You are a helpful assistant"
    })
    assert response.status_code == 200
    data = response.json()
    assert "input_tokens" in data
    assert data["input_tokens"] > 0


def test_count_tokens_multiple_messages():
    """Count tokens handles multiple messages."""
    response = client.post("/v1/messages/count_tokens", json={
        "messages": [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "second message"}
        ]
    })
    assert response.status_code == 200
    data = response.json()
    assert data["input_tokens"] > 0


def test_count_tokens_content_blocks():
    """Count tokens handles content as list of blocks."""
    response = client.post("/v1/messages/count_tokens", json={
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "hello world"},
                {"type": "text", "text": "more text here"}
            ]
        }]
    })
    assert response.status_code == 200
    data = response.json()
    assert data["input_tokens"] > 0


def test_count_tokens_empty_body():
    """Count tokens handles empty body with a fallback value."""
    response = client.post("/v1/messages/count_tokens", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["input_tokens"] == 100


def test_count_tokens_invalid_json():
    """Count tokens handles invalid JSON body."""
    response = client.post("/v1/messages/count_tokens",
                           content=b"not json",
                           headers={"Content-Type": "application/json"})
    assert response.status_code in [200, 400, 422]


def test_count_tokens_system_as_list():
    """Count tokens handles system prompt as list of content blocks."""
    response = client.post("/v1/messages/count_tokens", json={
        "messages": [{"role": "user", "content": "test"}],
        "system": [{"type": "text", "text": "System instruction"}]
    })
    assert response.status_code == 200
    data = response.json()
    assert data["input_tokens"] > 0


# ----------------------------------------
# 5. API Hello Endpoint
# ----------------------------------------

def test_api_hello():
    """Hello endpoint returns a greeting."""
    response = client.get("/api/hello")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "key-router" in data["message"].lower()


# ----------------------------------------
# 6. Browse Folder Endpoint
# ----------------------------------------

@patch("proxy.server.platform")
def test_browse_folder_returns_path(mock_platform):
    """Browse folder endpoint returns a path field (may be empty if no GUI)."""
    mock_platform.system.return_value = "Headless"
    response = client.get("/api/browse-folder")
    assert response.status_code == 200
    data = response.json()
    assert "path" in data


# ----------------------------------------
# 7. Safe Merge JSON
# ----------------------------------------

def test_safe_merge_creates_file(tmp_path):
    """Safe merge creates a new file if it doesn't exist."""
    test_file = tmp_path / "test_settings.json"
    result = _safe_merge_json(str(test_file), {"key": "value"})
    assert result is True
    assert os.path.exists(test_file)
    with open(test_file) as f:
        data = json.load(f)
    assert data["key"] == "value"


def test_safe_merge_preserves_existing(tmp_path):
    """Safe merge keeps existing keys and adds new ones."""
    test_file = tmp_path / "existing.json"
    original = {"theme": "dark", "model": "opus"}
    with open(test_file, "w") as f:
        json.dump(original, f)

    result = _safe_merge_json(str(test_file), {"env": {"FOO": "bar"}})
    assert result is True
    with open(test_file) as f:
        data = json.load(f)
    assert data["theme"] == "dark"
    assert data["model"] == "opus"
    assert data["env"]["FOO"] == "bar"


def test_safe_merge_deep_merge_preserves_subkeys(tmp_path):
    """Safe merge deep-merges objects without overwriting subkeys."""
    test_file = tmp_path / "deep.json"
    original = {"env": {"EXISTING": "keep_me", "OTHER": "also_keep"}}
    with open(test_file, "w") as f:
        json.dump(original, f)

    result = _safe_merge_json(str(test_file), {"env": {"NEW": "add_me"}})
    assert result is True
    with open(test_file) as f:
        data = json.load(f)
    assert data["env"]["EXISTING"] == "keep_me"
    assert data["env"]["OTHER"] == "also_keep"
    assert data["env"]["NEW"] == "add_me"


def test_safe_merge_no_change_if_same(tmp_path):
    """Safe merge returns False if nothing changed."""
    test_file = tmp_path / "same.json"
    original = {"key": "value"}
    with open(test_file, "w") as f:
        json.dump(original, f)

    result = _safe_merge_json(str(test_file), {"key": "value"})
    assert result is False


def test_safe_merge_overwrites_scalar(tmp_path):
    """Safe merge overwrites scalar values that differ."""
    test_file = tmp_path / "overwrite.json"
    original = {"key": "old_value"}
    with open(test_file, "w") as f:
        json.dump(original, f)

    result = _safe_merge_json(str(test_file), {"key": "new_value"})
    assert result is True
    with open(test_file) as f:
        data = json.load(f)
    assert data["key"] == "new_value"


def test_safe_merge_handles_malformed_json(tmp_path):
    """Safe merge handles malformed JSON by treating as empty and writing new."""
    test_file = tmp_path / "broken.json"
    with open(test_file, "w") as f:
        f.write("not valid json{{{")

    result = _safe_merge_json(str(test_file), {"key": "value"})
    assert result is True
    with open(test_file) as f:
        data = json.load(f)
    assert data["key"] == "value"


def test_safe_merge_creates_parent_dirs(tmp_path):
    """Safe merge creates parent directories if they don't exist."""
    test_file = tmp_path / "nested" / "deep" / "settings.json"
    result = _safe_merge_json(str(test_file), {"key": "value"})
    assert result is True
    assert os.path.exists(test_file)


# ----------------------------------------
# 8. IDE Settings Paths
# ----------------------------------------

def test_get_ide_settings_path_vscode_linux():
    """VSCode settings path on Linux follows XDG convention."""
    path = _get_ide_settings_path("Code")
    assert "Code" in path
    assert "settings.json" in path


def test_get_ide_settings_path_vscodium():
    """VSCodium settings path includes correct config dir."""
    path = _get_ide_settings_path("VSCodium")
    assert "VSCodium" in path
    assert "settings.json" in path


def test_get_ide_settings_path_cursor():
    """Cursor settings path includes correct config dir."""
    path = _get_ide_settings_path("Cursor")
    assert "Cursor" in path
    assert "settings.json" in path


def test_get_ide_settings_path_is_absolute():
    """Settings paths should be absolute."""
    for config_dir in ["Code", "VSCodium", "Cursor", "Windsurf"]:
        path = _get_ide_settings_path(config_dir)
        assert os.path.isabs(path), f"Path for {config_dir} is not absolute: {path}"


# ----------------------------------------
# 9. IDE Definitions Integrity
# ----------------------------------------

def test_ide_definitions_have_all_fields():
    """Every IDE definition has all required fields."""
    required = {"id", "name", "binary", "config_dir", "supports_claude_extension"}
    for ide in IDE_DEFINITIONS:
        assert required.issubset(ide.keys()), f"Missing fields in {ide.get('name', 'unknown')}"


def test_ide_definitions_unique_ids():
    """All IDE definitions have unique IDs."""
    ids = [ide["id"] for ide in IDE_DEFINITIONS]
    assert len(ids) == len(set(ids))


def test_ide_definitions_unique_binaries():
    """All IDE definitions have unique binary names."""
    binaries = [ide["binary"] for ide in IDE_DEFINITIONS]
    assert len(binaries) == len(set(binaries))


def test_ide_definitions_count():
    """Only 3 IDEs are currently supported (no Windsurf)."""
    assert len(IDE_DEFINITIONS) == 3
    supported = {ide["id"] for ide in IDE_DEFINITIONS}
    assert supported == {"vscode", "vscodium", "cursor"}


# ----------------------------------------
# 10. IDE Setup - save to config.json adds ide_detected section
# ----------------------------------------

def test_ide_detect_saves_to_config():
    """After detection, config.json should have ide_detected section."""
    response = client.get("/api/ide-detect-refresh")
    assert response.status_code == 200

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "config.json")
    assert os.path.exists(config_path)

    with open(config_path) as f:
        data = json.load(f)

    assert "ide_detected" in data
    assert isinstance(data["ide_detected"], dict)
    assert "model_mappings" in data
