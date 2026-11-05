import asyncio
import base64
import platform
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from bridge.config import AgentSpec, BridgeConfig
from bridge.registry import codex_argv
from proxy import testlab
from proxy.app import create_app
from proxy.router import provider_router

# 1x1 transparent PNG
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

CAPTURED = {"argv": [], "prompt": ""}
MODE = {"sleep": 0.0}


async def fake_run_cli(cfg, argv, cwd, prompt, chat_key, agent_id, turns):
    CAPTURED["argv"] = list(argv)
    CAPTURED["prompt"] = prompt
    os_text = f"{platform.system()} {platform.machine()} {platform.release()}"
    if MODE["sleep"]:
        await asyncio.sleep(MODE["sleep"])

    base = Path(cwd) / testlab.OUTPUT_DIR
    run_dir = max(base.iterdir(), key=lambda path: path.stat().st_mtime)
    (run_dir / testlab.HTML_NAME).write_text(
        "<!doctype html><html><body><h1>Test</h1><p>hello</p></body></html>\n" * 3,
        encoding="utf-8",
    )
    (run_dir / testlab.MD_NAME).write_text(
        "# Review\n\nReviewed key-router-test.html on Linux x86_64 with Python.\n" * 3,
        encoding="utf-8",
    )

    if argv[0] == "claude":
        yield {"type": "system", "session_id": "sess-test"}
        yield {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": f"The image shows a cat. OS: {os_text}. Languages: Python."}]},
        }
        yield {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "uname -a"}}]},
        }
        yield {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": os_text}]},
        }
        yield {
            "type": "result",
            "result": f"Done. {os_text}. Python.",
            "is_error": False,
            "duration_ms": 1234,
            "num_turns": 2,
            "total_cost_usd": 0.0012,
        }
    else:
        yield {"type": "thread.started", "thread_id": "t-1"}
        yield {"type": "turn.started"}
        yield {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "uname -a"}}
        yield {
            "type": "item.completed",
            "item": {
                "id": "i1",
                "type": "command_execution",
                "command": "uname -a",
                "aggregated_output": os_text,
                "exit_code": 0,
                "status": "completed",
            },
        }
        yield {
            "type": "item.completed",
            "item": {
                "id": "i2",
                "type": "command_execution",
                "command": "which python3",
                "aggregated_output": "/usr/bin/python3",
                "exit_code": 0,
                "status": "completed",
            },
        }
        yield {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": f"{os_text}. Python is installed."}}
        yield {
            "type": "item.completed",
            "item": {"id": "i4", "type": "file_change", "changes": [{"path": "key-router-test.html", "kind": "add"}]},
        }
        yield {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    workdir = tmp_path / "project"
    workdir.mkdir()
    cfg = BridgeConfig(
        allowed_roots=[str(tmp_path)],
        key_router_url="http://127.0.0.1:8082",
        turn_timeout_sec=60,
    )
    monkeypatch.setattr("proxy.testlab.bridge_config.ensure_config", lambda: cfg)
    monkeypatch.setattr("proxy.testlab.cli_available", lambda cli: cli in ("claude", "codex"))
    monkeypatch.setattr("proxy.testlab.detect_languages", lambda: ["Python"])
    monkeypatch.setattr("config.codex_config.setup_codex_config", lambda **kwargs: False)
    monkeypatch.setattr("proxy.testlab.bridge_runner.run_cli", fake_run_cli)

    testlab.RUNS.clear()
    testlab.TASKS.clear()
    testlab.ACTIVE_RUN_ID = None
    CAPTURED["argv"] = []
    CAPTURED["prompt"] = ""
    MODE["sleep"] = 0.0
    yield workdir
    testlab.RUNS.clear()
    testlab.TASKS.clear()
    testlab.ACTIVE_RUN_ID = None


async def _wait(client, run_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = await client.get(f"/api/testlab/runs/{run_id}")
        data = response.json()
        if not data["active"]:
            return data
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish")


def _checks(run):
    return {check["id"]: check for check in run["checks"]}


@pytest.mark.asyncio
async def test_claude_run_passes_scenario(env):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/testlab/runs", json={"cli": "claude", "workdir": str(env)})
        assert response.status_code == 202

        run = await _wait(client, response.json()["run_id"])

    checks = _checks(run)
    assert run["status"] == "complete"
    assert checks["html"]["status"] == "pass"
    assert checks["markdown"]["status"] == "pass"
    assert checks["os"]["status"] == "pass"
    assert checks["languages"]["status"] == "pass"
    assert checks["image"]["status"] == "skip"
    assert checks["shell_budget"]["status"] == "pass"
    assert checks["permissions"]["status"] == "pass"
    assert run["shell_count"] == 1
    assert "Done." in run["final_text"] and "Python" in run["final_text"]
    assert {artifact["name"] for artifact in run["artifacts"]} == {
        testlab.HTML_NAME,
        testlab.MD_NAME,
    }
    assert all(artifact["content"] for artifact in run["artifacts"])
    assert Path(run["run_dir"]).is_dir()


@pytest.mark.asyncio
async def test_codex_run_parses_events(env):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/testlab/runs",
            json={"cli": "codex", "model": "deepseekplatform/deepseek-flash", "workdir": str(env)},
        )
        assert response.status_code == 202

        run = await _wait(client, response.json()["run_id"])

    assert run["status"] == "complete"
    assert run["shell_count"] == 2
    assert "Python is installed." in run["final_text"]
    assert any(step["kind"] == "file" for step in run["steps"])
    assert CAPTURED["argv"][0] == "codex"
    assert "--skip-git-repo-check" in CAPTURED["argv"]
    assert "-m" in CAPTURED["argv"]


@pytest.mark.asyncio
async def test_image_attached_reaches_cli_and_passes_check(env):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/testlab/runs",
            json={
                "cli": "claude",
                "workdir": str(env),
                "image": {"media_type": "image/png", "data_base64": PNG_BASE64},
            },
        )
        assert response.status_code == 202

        run = await _wait(client, response.json()["run_id"])

    assert run["has_image"] is True
    assert _checks(run)["image"]["status"] == "pass"
    image_file = Path(run["run_dir"]) / "image.png"
    assert image_file.is_file()
    assert '"type": "image"' in CAPTURED["prompt"]
    assert base64.b64encode(image_file.read_bytes()).decode() in CAPTURED["prompt"]


@pytest.mark.asyncio
async def test_codex_image_uses_image_flag(env):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/testlab/runs",
            json={
                "cli": "codex",
                "workdir": str(env),
                "image": {"media_type": "image/png", "data_base64": PNG_BASE64},
            },
        )
        assert response.status_code == 202
        await _wait(client, response.json()["run_id"])

    argv = CAPTURED["argv"]
    assert "-i" in argv
    image_arg = argv[argv.index("-i") + 1]
    assert image_arg.endswith("image.png")


@pytest.mark.asyncio
async def test_workdir_outside_allowed_roots_is_rejected(env):
    outside = env.parent.parent
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/testlab/runs",
            json={"cli": "claude", "workdir": str(outside)},
        )

    assert response.status_code == 400
    assert "allowed_roots" in response.json()["detail"]


@pytest.mark.asyncio
async def test_second_run_is_rejected_and_stop_works(env):
    MODE["sleep"] = 1.0
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/testlab/runs", json={"cli": "claude", "workdir": str(env)})
        assert first.status_code == 202
        run_id = first.json()["run_id"]

        second = await client.post("/api/testlab/runs", json={"cli": "claude", "workdir": str(env)})
        assert second.status_code == 409

        stopped = await client.post(f"/api/testlab/runs/{run_id}/stop")
        assert stopped.status_code == 200
        await asyncio.sleep(0.1)
        run = (await client.get(f"/api/testlab/runs/{run_id}")).json()

    assert run["status"] == "stopped"


def test_direct_provider_model_resolution():
    assert provider_router._resolve("openrouter/anthropic/x") == ("openrouter", "anthropic/x")
    assert provider_router._resolve("deepseekplatform/deepseek-flash") == (
        "deepseekplatform", "deepseek-flash",
    )
    assert provider_router._resolve("googleaistudio/gemini-2.0-flash") == (
        "googleaistudio", "gemini-2.0-flash",
    )


def test_codex_argv_attaches_images(tmp_path):
    agent = AgentSpec(
        id="testlab",
        cli="codex",
        workdir=str(tmp_path),
        permission="ci",
        effort="high",
        tools="full",
    )

    argv = codex_argv(agent, tmp_path, None, False, ["/tmp/shot.png"])

    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("-i") + 1] == "/tmp/shot.png"


@pytest.mark.asyncio
async def test_web_search_scenario_uses_search_prompt(env):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/testlab/runs",
            json={"cli": "claude", "workdir": str(env), "scenario": "web-search"},
        )
        assert response.status_code == 202
        run = await _wait(client, response.json()["run_id"])

    assert run["scenario"] == "web-search"
    assert "web_search" in CAPTURED["prompt"]
    checks = _checks(run)
    assert "web_search" in checks and "sources" in checks
    assert "html" not in checks


@pytest.mark.asyncio
async def test_unknown_scenario_rejected(env):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/testlab/runs",
            json={"cli": "claude", "workdir": str(env), "scenario": "nope"},
        )

    assert response.status_code == 400


def _web_search_run(tmp_path, answer, report):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / testlab.MD_NAME).write_text(report, encoding="utf-8")
    run = testlab.TestRun(
        id="run-1", cli="claude", model="", workdir=str(tmp_path),
        run_dir=str(run_dir), scenario="web-search",
    )
    run.final_text = answer
    return run


def test_web_search_checks_pass_with_cited_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(testlab.websearch, "configured", lambda: True)
    run = _web_search_run(
        tmp_path,
        "1. Python — https://www.python.org/downloads/\n2. Docs — https://docs.python.org/3/",
        "# Report\n\nhttps://www.python.org/downloads/\n",
    )

    checks = {check["id"]: check for check in testlab._web_search_checks(run)}

    assert checks["web_search"]["status"] == "pass"
    assert checks["sources"]["status"] == "pass"


def test_web_search_checks_fail_without_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(testlab.websearch, "configured", lambda: True)
    run = _web_search_run(tmp_path, "I could not search.", "# Report\n\nNo sources.\n")

    checks = {check["id"]: check for check in testlab._web_search_checks(run)}

    assert checks["web_search"]["status"] == "fail"
    assert checks["sources"]["status"] == "fail"


def test_web_search_checks_skip_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(testlab.websearch, "configured", lambda: False)
    run = _web_search_run(tmp_path, "anything", "# Report\n\nhttps://example.com/\n")

    checks = {check["id"]: check for check in testlab._web_search_checks(run)}

    assert checks["web_search"]["status"] == "skip"
