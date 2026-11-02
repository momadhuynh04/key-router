import json
import os
from pathlib import Path

import pytest

from config import skills


def write_skill(root: Path, slug: str, name: str = "", description: str = "desc") -> Path:
    path = root / slug
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name or slug}\ndescription: {description}\n---\n\n# {name or slug}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    agents = tmp_path / "agents.json"
    agents.write_text(json.dumps({"token": "t", "allowed_roots": [str(tmp_path)]}), encoding="utf-8")
    monkeypatch.setattr("bridge.config.AGENTS_PATH", agents)
    return tmp_path


# --------------------------------------------------------------- manifests

def test_parse_frontmatter_name_and_description(tmp_path):
    path = write_skill(tmp_path, "alpha", "Alpha Skill", "Does alpha things")
    manifest = skills.read_manifest(path)
    assert manifest is not None
    assert manifest["name"] == "Alpha Skill"
    assert manifest["description"] == "Does alpha things"


def test_parse_multiline_description(tmp_path):
    path = tmp_path / "beta"
    path.mkdir()
    (path / "SKILL.md").write_text(
        "---\nname: beta\ndescription: >\n  line one\n  line two\n---\n",
        encoding="utf-8",
    )
    manifest = skills.read_manifest(path)
    assert manifest is not None
    assert "line one" in manifest["description"]
    assert "line two" in manifest["description"]


def test_parse_missing_frontmatter(tmp_path):
    path = tmp_path / "plain"
    path.mkdir()
    (path / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
    manifest = skills.read_manifest(path)
    assert manifest == {"name": "", "description": "", "raw": ""}


def test_parse_malformed_yaml_falls_back(tmp_path):
    path = tmp_path / "broken"
    path.mkdir()
    (path / "SKILL.md").write_text(
        "---\nname: broken\ndescription: [unclosed\n---\n",
        encoding="utf-8",
    )
    manifest = skills.read_manifest(path)
    assert manifest is not None
    assert manifest["name"] == "broken"


# ------------------------------------------------------------------- scan

def test_scan_groups_and_disabled_skills(tmp_path):
    home = tmp_path / "home"
    claude = home / ".claude" / "skills"
    write_skill(claude, "alpha", description="A")
    write_skill(claude / skills.DISABLED_DIR, "gamma", description="G")

    codex = home / ".agents" / "skills"
    write_skill(codex, "beta", description="B")
    system = home / ".codex" / "skills" / ".system"
    write_skill(system, "system-skill", description="S")

    data = skills.scan_all(None, home=home)
    by_id = {s["id"]: s for s in data["skills"]["claude"]["global"]}
    assert by_id["alpha"]["enabled"] is True
    assert by_id["gamma"]["enabled"] is False

    codex_by_id = {s["id"]: s for s in data["skills"]["codex"]["global"]}
    assert codex_by_id["beta"]["origin"] == "agents"
    assert codex_by_id["system-skill"]["writable"] is False
    assert codex_by_id["system-skill"]["origin"] == "system"


def test_scan_marks_symlink_read_only(tmp_path):
    home = tmp_path / "home"
    root = home / ".claude" / "skills"
    target = write_skill(tmp_path / "source", "linked")
    root.mkdir(parents=True)
    try:
        os.symlink(target, root / "linked")
    except OSError:
        pytest.skip("symlinks are not available")

    data = skills.scan_all(None, home=home)
    item = data["skills"]["claude"]["global"][0]
    assert item["install_mode"] == "symlink"
    assert item["writable"] is False


def test_scan_dedupes_duplicate_active_roots(tmp_path):
    home = tmp_path / "home"
    write_skill(home / ".agents" / "skills", "dup", description="A")
    write_skill(home / ".codex" / "skills", "dup", description="B")

    data = skills.scan_all(None, home=home)
    items = [s for s in data["skills"]["codex"]["global"] if s["id"] == "dup"]
    assert len(items) == 1
    assert items[0]["duplicate_paths"]
    assert items[0]["writable"] is False


# ----------------------------------------------------------------- toggle

def test_toggle_disables_and_enables(tmp_path):
    home = tmp_path / "home"
    write_skill(home / ".claude" / "skills", "alpha")

    disabled = skills.toggle_skill("claude", "global", None, "alpha", False, home=home)
    assert disabled.enabled is False
    assert skills.DISABLED_DIR in disabled.path

    enabled = skills.toggle_skill("claude", "global", None, "alpha", True, home=home)
    assert enabled.enabled is True
    assert skills.DISABLED_DIR not in enabled.path


def test_toggle_read_only_and_missing(tmp_path):
    home = tmp_path / "home"
    write_skill(home / ".codex" / "skills" / ".system", "system-skill")

    with pytest.raises(ValueError, match="read-only"):
        skills.toggle_skill("codex", "global", None, "system-skill", False, home=home)
    with pytest.raises(FileNotFoundError):
        skills.toggle_skill("claude", "global", None, "missing", False, home=home)


def test_toggle_conflict(tmp_path):
    home = tmp_path / "home"
    root = home / ".claude" / "skills"
    write_skill(root, "alpha")
    write_skill(root / skills.DISABLED_DIR, "alpha")

    with pytest.raises(FileExistsError):
        skills.toggle_skill("claude", "global", None, "alpha", False, home=home)


# --------------------------------------------------------------- validation

def test_project_path_validation(allowed):
    project = allowed / "proj"
    project.mkdir()
    assert skills.resolve_project_path(str(project)) == project.resolve()

    outside = allowed.parent / f"{allowed.name}-outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="outside allowed_roots"):
        skills.resolve_project_path(str(outside))
    outside.rmdir()


def test_project_path_symlink_escape_rejected(allowed):
    project = allowed / "real"
    project.mkdir()
    outside = allowed.parent / f"{allowed.name}-escape"
    outside.mkdir()
    link = allowed / "link"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlinks are not available")
    try:
        with pytest.raises(ValueError, match="outside allowed_roots"):
            skills.resolve_project_path(str(link))
    finally:
        link.unlink()
        outside.rmdir()


def test_project_path_requires_roots(allowed, monkeypatch):
    agents = allowed / "empty-agents.json"
    agents.write_text(json.dumps({"token": "t", "allowed_roots": []}), encoding="utf-8")
    monkeypatch.setattr("bridge.config.AGENTS_PATH", agents)
    project = allowed / "proj"
    project.mkdir()
    with pytest.raises(ValueError, match="allowed_roots is empty"):
        skills.resolve_project_path(str(project))


def test_parse_source_and_skill():
    assert skills.parse_source_and_skill("owner/repo@my-skill") == ("owner/repo", "my-skill")
    assert skills.parse_source_and_skill("git@github.com:owner/repo.git") == ("git@github.com:owner/repo.git", "")
    assert skills.parse_source_and_skill("https://github.com/o/r@v1") == ("https://github.com/o/r@v1", "")
    assert skills.parse_source_and_skill("/tmp/local-skills@v1") == ("/tmp/local-skills@v1", "")


def test_validate_source_and_skill(allowed):
    skills.validate_source("owner/repo", "my-skill")
    skills.validate_source("https://github.com/owner/repo", "")
    skills.validate_source("git@github.com:owner/repo.git", "")

    local = allowed / "local skills"
    local.mkdir()
    skills.validate_source(str(local), "x")

    with pytest.raises(ValueError):
        skills.validate_source("-bad")
    with pytest.raises(ValueError):
        skills.validate_source("owner/../repo")
    with pytest.raises(ValueError):
        skills.validate_source("owner/repo", "bad\x00name")


def test_parse_node_version():
    assert skills.parse_node_version("v22.20.0")[:2] == (22, 20)
    assert skills.parse_node_version("v22.19.9")[:2] < skills.NODE_MIN_VERSION
    assert skills.parse_node_version("nonsense") is None


def test_build_install_and_remove_argv(monkeypatch):
    monkeypatch.setattr("config.skills._npx", lambda: "npx")
    project_argv = skills.build_install_argv("owner/repo", "my skill", ["claude", "codex"], "project")
    assert project_argv[:6] == ["npx", "-y", skills.SKILLS_CLI_SPEC, "add", "owner/repo", "--skill"]
    assert "my skill" in project_argv
    assert "--copy" in project_argv
    assert project_argv.count("-a") == 2
    assert "claude-code" in project_argv and "codex" in project_argv
    assert "-g" not in project_argv

    global_argv = skills.build_install_argv("owner/repo", "skill", ["claude"], "global")
    assert "-g" in global_argv
    assert global_argv.count("-a") == 1

    remove_argv = skills.build_remove_argv("my skill", "codex", "global")
    assert remove_argv[:5] == ["npx", "-y", skills.SKILLS_CLI_SPEC, "remove", "my skill"]
    assert "codex" in remove_argv and "-g" in remove_argv


def test_remove_skill_dir_refuses_symlink(tmp_path):
    home = tmp_path / "home"
    target = write_skill(tmp_path / "source", "linked")
    root = home / ".claude" / "skills"
    root.mkdir(parents=True)
    try:
        os.symlink(target, root / "linked")
    except OSError:
        pytest.skip("symlinks are not available")
    info = skills.find_skill("claude", "global", None, "linked", home=home)
    with pytest.raises(ValueError, match="read-only"):
        skills.remove_skill_dir(info)
