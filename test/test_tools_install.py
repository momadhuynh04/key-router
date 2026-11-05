import asyncio
import io
import json
import subprocess
import tarfile

import pytest

from config import tools as tools_store
from proxy import tools_hub


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    registry = tmp_path / "tools.json"
    logs = tmp_path / "logs"
    runtime = tmp_path / "runtime.json"
    monkeypatch.setattr("config.tools._get_tools_root", lambda: str(root))
    monkeypatch.setattr("config.tools._get_registry_path", lambda: str(registry))
    monkeypatch.setattr("config.tools._get_logs_dir", lambda: str(logs))
    monkeypatch.setattr("config.tools._get_runtime_path", lambda: str(runtime))
    tools_store.invalidate_tools_cache()
    yield {"root": root, "registry": registry, "logs": logs, "runtime": runtime}
    tools_store.invalidate_tools_cache()


def _write_tool(directory, **overrides):
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": "demo-tool",
        "name": "Demo",
        "version": "0.1.0",
        "description": "Demo tool",
        "start": ["{python}", "-m", "demo", "--port", "{port}"],
        "health": "/health",
        "default_port": 9200,
    }
    manifest.update(overrides)
    (directory / "tool.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "marker.txt").write_text("hello", encoding="utf-8")
    return directory


def _make_tar(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for name, payload in members.items():
            data = payload.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_install_local_registers_without_copying(env, tmp_path):
    source = _write_tool(tmp_path / "src" / "demo-tool")
    result = asyncio.run(tools_hub.install_tool(local_path=str(source)))

    assert result["id"] == "demo-tool"
    entry = tools_store.load_registry()["tools"]["demo-tool"]
    assert entry["install_dir"] == str(source.resolve())
    assert entry["source"]["type"] == "local"
    assert (source / "marker.txt").exists()
    assert not (env["root"] / "demo-tool").exists()


def test_install_local_rejects_missing_folder(env, tmp_path):
    with pytest.raises(FileNotFoundError):
        asyncio.run(tools_hub.install_tool(local_path=str(tmp_path / "nope")))


def test_install_github_moves_package_into_tools_root(env, tmp_path, monkeypatch):
    package_root = _write_tool(tmp_path / "extract" / "demo-tool-abc123")

    async def fake_download(repo, branch):
        assert repo == "owner/demo-tool"
        assert branch == "main"
        return str(tmp_path / "archive.tgz"), str(package_root)

    monkeypatch.setattr("proxy.tools_hub._download_and_extract", fake_download)

    result = asyncio.run(tools_hub.install_tool(repo="owner/demo-tool", branch="main"))

    destination = env["root"] / "demo-tool"
    assert destination.is_dir()
    assert (destination / "marker.txt").read_text() == "hello"
    assert result["install_dir"] == str(destination)
    entry = tools_store.load_registry()["tools"]["demo-tool"]
    assert entry["source"] == {"type": "github", "repo": "owner/demo-tool", "branch": "main"}


def test_install_rejects_bad_repo_id(env):
    with pytest.raises(ValueError):
        asyncio.run(tools_hub.install_tool(repo="not-a-repo"))


def test_safe_extract_blocks_traversal(env, tmp_path):
    archive = _make_tar(tmp_path / "evil.tar.gz", {"../evil.txt": "x"})
    with tarfile.open(archive, "r:gz") as tar:
        with pytest.raises(ValueError):
            tools_hub._safe_extract(tar, str(tmp_path / "dest"))


def test_safe_extract_blocks_links(env, tmp_path):
    path = tmp_path / "link.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("demo-tool/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with tarfile.open(path, "r:gz") as tar:
        with pytest.raises(ValueError):
            tools_hub._safe_extract(tar, str(tmp_path / "dest"))


def test_extract_package_returns_single_root(env, tmp_path):
    import os
    import shutil

    archive = _make_tar(
        tmp_path / "pkg.tar.gz",
        {
            "demo-tool-abc/tool.json": "{}",
            "demo-tool-abc/sub/file.txt": "x",
        },
    )
    root = tools_hub._extract_package(str(archive))
    try:
        assert root.endswith("demo-tool-abc")
        assert os.path.isfile(os.path.join(root, "tool.json"))
        assert os.path.isfile(os.path.join(root, "sub", "file.txt"))
    finally:
        shutil.rmtree(os.path.dirname(root), ignore_errors=True)


def test_extract_package_rejects_multiple_roots(env, tmp_path):
    archive = _make_tar(tmp_path / "pkg.tar.gz", {"a/tool.json": "{}", "b/x.txt": "x"})
    with pytest.raises(ValueError):
        tools_hub._extract_package(str(archive))


def _init_git(directory, remote="https://github.com/owner/demo-tool.git"):
    subprocess.run(["git", "-C", str(directory), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(directory), "remote", "add", "origin", remote], check=True, capture_output=True)


def test_local_install_records_git_remote(env, tmp_path):
    source = _write_tool(tmp_path / "src" / "demo-tool")
    _init_git(source)

    asyncio.run(tools_hub.install_tool(local_path=str(source)))

    entry = tools_store.load_registry()["tools"]["demo-tool"]
    assert entry["source"]["repo"] == "owner/demo-tool"
    assert entry["source"]["branch"] == "main"


def test_backfill_records_repo_for_local_entry(env, tmp_path):
    source = _write_tool(tmp_path / "src" / "demo-tool")
    _init_git(source)
    asyncio.run(tools_hub.install_tool(local_path=str(source)))

    registry = tools_store.registry_copy()
    registry["tools"]["demo-tool"]["source"].pop("repo", None)
    registry["tools"]["demo-tool"]["source"].pop("branch", None)
    tools_store.save_registry(registry)

    tools_hub.installed_tools()

    entry = tools_store.load_registry()["tools"]["demo-tool"]
    assert entry["source"]["repo"] == "owner/demo-tool"
    assert entry["source"]["branch"] == "main"


def test_reinstall_uses_recorded_repo(env, tmp_path, monkeypatch):
    registry = tools_store.registry_copy()
    registry["tools"]["demo-tool"] = {
        "id": "demo-tool",
        "install_dir": str(tmp_path / "gone"),
        "source": {"type": "github", "repo": "owner/demo-tool", "branch": "main"},
    }
    tools_store.save_registry(registry)

    calls = {}

    async def fake_install(**kwargs):
        calls.update(kwargs)
        return {"id": "demo-tool", "install_dir": str(tmp_path / "installed")}

    monkeypatch.setattr("proxy.tools_hub.install_tool", fake_install)
    monkeypatch.setattr("proxy.tools_hub.pid_alive", lambda pid: False)

    result = asyncio.run(tools_hub.reinstall_tool("demo-tool"))

    assert calls == {"repo": "owner/demo-tool", "branch": "main"}
    assert result["id"] == "demo-tool"


def test_reinstall_without_source_repo_raises(env, tmp_path):
    registry = tools_store.registry_copy()
    registry["tools"]["demo-tool"] = {"id": "demo-tool", "install_dir": str(tmp_path / "gone"), "source": {}}
    tools_store.save_registry(registry)

    with pytest.raises(ValueError):
        asyncio.run(tools_hub.reinstall_tool("demo-tool"))


def test_uninstall_local_keeps_source_folder(env, tmp_path):
    source = _write_tool(tmp_path / "src" / "demo-tool")
    asyncio.run(tools_hub.install_tool(local_path=str(source)))

    tools_hub.uninstall_tool("demo-tool")

    assert source.is_dir()
    assert "demo-tool" not in tools_store.load_registry()["tools"]
