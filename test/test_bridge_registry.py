import pytest

from bridge.config import AgentSpec, BridgeConfig
from bridge import registry


def _cfg(tmp_path, **overrides):
    workdir = tmp_path / "proj"
    workdir.mkdir(exist_ok=True)
    defaults = dict(allowed_roots=[str(tmp_path)], agents=[])
    defaults.update(overrides)
    return BridgeConfig(**defaults), workdir


def _agent(workdir, **overrides):
    data = dict(
        id="a1",
        cli="claude",
        model="opus",
        workdir=str(workdir),
        permission="edit",
        effort="high",
        tools="edit",
    )
    data.update(overrides)
    return AgentSpec(**data)


def _value_after(argv, flag):
    return argv[argv.index(flag) + 1]


def test_claude_never_waits_for_prompt(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    argv, cwd = registry.build_invocation(_agent(workdir), cfg)
    assert "--permission-prompts" in argv
    assert _value_after(argv, "--permission-prompts") == "none"
    assert cwd == workdir.resolve()


def test_claude_uses_print_mode(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    argv, _ = registry.build_invocation(_agent(workdir), cfg)
    assert argv[0] == "claude"
    assert "-p" in argv


def test_build_invocation_takes_no_prompt(tmp_path):
    """`--tools`, `--add-dir`, `--allowedTools` are variadic and would eat a trailing prompt.

    Regression test for a real failure: claude reported
    "Input must be provided either through stdin or as a prompt argument".
    The prompt therefore travels over stdin, never through argv.
    """
    import inspect

    assert "prompt" not in inspect.signature(registry.build_invocation).parameters


def test_variadic_options_are_grouped_last(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    agent = _agent(
        workdir,
        tools="readonly",
        add_dirs=[str(extra)],
        allowed_tools=["Bash(git *)"],
        disallowed_tools=["Bash(rm *)"],
    )
    argv, _ = registry.build_invocation(agent, cfg)

    assert argv[-4:] == [
        "--allowedTools", "Bash(git *)",
        "--disallowedTools", "Bash(rm *)",
    ]
    assert argv[-6:-4] == ["--add-dir", str(extra)]


def test_claude_permission_modes(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    expected = {
        "plan": "plan",
        "edit": "acceptEdits",
        "auto": "auto",
        "ci": "dontAsk",
        "bypass": "bypassPermissions",
    }
    for permission, mode in expected.items():
        argv, _ = registry.build_invocation(_agent(workdir, permission=permission), cfg)
        assert _value_after(argv, "--permission-mode") == mode, permission


def test_claude_effort_and_model(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    argv, _ = registry.build_invocation(_agent(workdir, effort="xhigh"), cfg)
    assert _value_after(argv, "--effort") == "xhigh"
    assert _value_after(argv, "--model") == "opus"


def test_claude_tool_profiles(tmp_path):
    cfg, workdir = _cfg(tmp_path)

    readonly, _ = registry.build_invocation(_agent(workdir, tools="readonly"), cfg)
    assert _value_after(readonly, "--tools") == "Read,Grep,Glob"

    edit, _ = registry.build_invocation(_agent(workdir, tools="edit"), cfg)
    assert _value_after(edit, "--tools") == "Read,Grep,Glob,Edit,Write"

    full, _ = registry.build_invocation(_agent(workdir, tools="full"), cfg)
    assert "--tools" not in full


def test_claude_advanced_flags(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    agent = _agent(
        workdir,
        fallback_model="sonnet",
        max_budget_usd=1.5,
        append_system_prompt="Be terse.",
    )
    argv, _ = registry.build_invocation(agent, cfg)
    assert _value_after(argv, "--fallback-model") == "sonnet"
    assert _value_after(argv, "--max-budget-usd") == "1.5"
    assert _value_after(argv, "--append-system-prompt") == "Be terse."


def test_advanced_flags_omitted_when_unset(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    argv, _ = registry.build_invocation(_agent(workdir), cfg)
    for flag in (
        "--fallback-model",
        "--max-budget-usd",
        "--append-system-prompt",
        "--allowedTools",
        "--disallowedTools",
    ):
        assert flag not in argv


def test_claude_restricted_and_add_dirs(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    argv, _ = registry.build_invocation(
        _agent(workdir, restricted=True, add_dirs=[str(extra)]), cfg
    )
    assert "--restricted" in argv
    assert _value_after(argv, "--add-dir") == str(extra)


def test_claude_new_session_uses_session_id(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    argv, _ = registry.build_invocation(_agent(workdir), cfg, session_id="abc", resume=False)
    assert _value_after(argv, "--session-id") == "abc"
    assert "--resume" not in argv


def test_claude_resume_uses_resume_flag(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    argv, _ = registry.build_invocation(_agent(workdir), cfg, session_id="abc", resume=True)
    assert _value_after(argv, "--resume") == "abc"
    assert "--session-id" not in argv


def test_codex_sandbox_and_workdir(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    agent = _agent(workdir, cli="codex", model="gpt-5.6", permission="edit")
    argv, cwd = registry.build_invocation(agent, cfg)
    assert argv[:2] == ["codex", "exec"]
    assert "--json" in argv
    assert _value_after(argv, "-s") == "workspace-write"
    assert _value_after(argv, "-C") == str(workdir.resolve())
    assert _value_after(argv, "-m") == "gpt-5.6"
    assert cwd == workdir.resolve()


def test_codex_readonly_plan_and_effort_clamped(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    agent = _agent(workdir, cli="codex", effort="max", permission="plan")
    argv, _ = registry.build_invocation(agent, cfg)
    assert _value_after(argv, "-s") == "read-only"
    assert 'model_reasoning_effort="high"' in argv


def test_codex_resume_subcommand(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    agent = _agent(workdir, cli="codex")
    argv, _ = registry.build_invocation(agent, cfg, session_id="sess-9", resume=True)
    assert argv[2:4] == ["resume", "sess-9"]


def test_workdir_outside_allowed_roots_rejected(tmp_path):
    outside = tmp_path.parent / "definitely-outside"
    outside.mkdir(exist_ok=True)
    cfg = BridgeConfig(allowed_roots=[str(tmp_path / "proj")])
    agent = AgentSpec(id="a1", cli="claude", workdir=str(outside))
    with pytest.raises(ValueError, match="outside allowed_roots"):
        registry.build_invocation(agent, cfg)


def test_empty_allowed_roots_is_rejected(tmp_path):
    cfg, workdir = _cfg(tmp_path, allowed_roots=[])
    with pytest.raises(ValueError, match="allowed_roots"):
        registry.build_invocation(_agent(workdir), cfg)


def test_relative_workdir_is_rejected(tmp_path):
    cfg, workdir = _cfg(tmp_path)
    agent = AgentSpec(id="a1", cli="claude", workdir="relative/path")
    with pytest.raises(ValueError, match="absolute path"):
        registry.build_invocation(agent, cfg)


def test_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "sneaky"
    link.symlink_to(outside)

    cfg = BridgeConfig(allowed_roots=[str(root)])
    agent = AgentSpec(id="a1", cli="claude", workdir=str(link))
    with pytest.raises(ValueError, match="outside allowed_roots"):
        registry.build_invocation(agent, cfg)
