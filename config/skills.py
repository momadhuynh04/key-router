"""Skills management for Claude Code and Codex.

Filesystem layout (discovery paths used by the CLIs):

  Claude Code  global   ~/.claude/skills/<id>/SKILL.md
               project  <project>/.claude/skills/<id>/SKILL.md
  Codex        global   ~/.agents/skills/<id>/SKILL.md
                        ~/.codex/skills/<id>/SKILL.md
                        ~/.codex/skills/.system/<id>/SKILL.md   (read-only)
               project  <project>/.agents/skills/<id>/SKILL.md
                        <project>/.codex/skills/<id>/SKILL.md

Disabled skills are moved to `<root>/.disabled/<id>/`; the agents scan direct
children only, so they stop discovering the skill. Key-router installs use
`npx skills ... --copy`, which gives each provider an independent directory and
makes per-provider toggles safe.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # PyYAML is a declared dependency; keep a fallback for bare installs.
    import yaml
except ImportError:  # pragma: no cover - exercised only without the dependency
    yaml = None

from bridge.config import ensure_config

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ("claude", "codex")
SCOPES = ("global", "project")
DISABLED_DIR = ".disabled"

SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SKILLS_CLI_SPEC = os.getenv("KEY_ROUTER_SKILLS_CLI", "skills@1.7.0")
NODE_MIN_VERSION = (22, 20)
MAX_SKILL_MD_BYTES = 64 * 1024
MAX_SCAN_FILES = 1000
MAX_SCAN_BYTES = 25 * 1024 * 1024
MAX_CLI_OUTPUT = 64 * 1024

_PROVIDER_AGENT = {"claude": "claude-code", "codex": "codex"}
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass(frozen=True)
class SkillRoot:
    path: Path
    provider: str
    scope: str
    origin: str
    writable: bool = True


@dataclass
class SkillInfo:
    id: str
    name: str
    description: str
    provider: str
    scope: str
    path: str
    origin: str
    enabled: bool
    writable: bool
    install_mode: str
    files: int = 0
    bytes: int = 0
    source: Optional[str] = None
    duplicate_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "provider": self.provider,
            "scope": self.scope,
            "path": self.path,
            "origin": self.origin,
            "enabled": self.enabled,
            "writable": self.writable,
            "install_mode": self.install_mode,
            "files": self.files,
            "bytes": self.bytes,
            "source": self.source,
            "duplicate_paths": list(self.duplicate_paths),
        }


# ---------------------------------------------------------------- path roots

def get_home() -> Path:
    return Path.home()


def _home(home: Optional[Path]) -> Path:
    return Path(home).expanduser() if home is not None else get_home()


def _codex_home(home: Path) -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else home / ".codex"


def claude_global_roots(home: Optional[Path] = None) -> List[SkillRoot]:
    h = _home(home)
    return [SkillRoot(h / ".claude" / "skills", "claude", "global", "user", True)]


def claude_project_roots(project: Path) -> List[SkillRoot]:
    return [SkillRoot(Path(project) / ".claude" / "skills", "claude", "project", "user", True)]


def codex_global_roots(home: Optional[Path] = None) -> List[SkillRoot]:
    h = _home(home)
    codex_home = _codex_home(h)
    return [
        SkillRoot(h / ".agents" / "skills", "codex", "global", "agents", True),
        SkillRoot(codex_home / "skills", "codex", "global", "codex", True),
        SkillRoot(codex_home / "skills" / ".system", "codex", "global", "system", False),
    ]


def codex_project_roots(project: Path) -> List[SkillRoot]:
    p = Path(project)
    return [
        SkillRoot(p / ".agents" / "skills", "codex", "project", "agents", True),
        SkillRoot(p / ".codex" / "skills", "codex", "project", "codex", True),
    ]


def roots_for(
    provider: str,
    scope: str,
    project: Optional[Path] = None,
    home: Optional[Path] = None,
) -> List[SkillRoot]:
    if provider not in PROVIDERS:
        raise ValueError(f"invalid provider {provider!r}")
    if scope not in SCOPES:
        raise ValueError(f"invalid scope {scope!r}")
    if scope == "project":
        if project is None:
            raise ValueError("project path is required for project scope")
        return claude_project_roots(project) if provider == "claude" else codex_project_roots(project)
    return claude_global_roots(home) if provider == "claude" else codex_global_roots(home)


# --------------------------------------------------------------- manifests

_FRONTMATTER_RE = re.compile(r"^\ufeff?---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", re.DOTALL)


def _fallback_frontmatter(raw: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for line in raw.splitlines():
        match = re.match(r"^(name|description)\s*:\s*(.*)$", line)
        if match:
            value = match.group(2).strip().strip("'\"")
            if value:
                data[match.group(1)] = value
    return data


def parse_skill_markdown(text: str) -> Dict[str, str]:
    """Extract name + description from a SKILL.md frontmatter block."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {"name": "", "description": "", "raw": ""}

    raw = match.group(1)
    data: Dict[str, Any] = {}
    if yaml is not None:
        try:
            loaded = yaml.safe_load(raw)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    if not data:
        data = _fallback_frontmatter(raw)

    name = data.get("name")
    description = data.get("description")
    if isinstance(description, (dict, list)):
        description = json.dumps(description, ensure_ascii=False)
    return {
        "name": str(name).strip() if name else "",
        "description": str(description).strip() if description else "",
        "raw": raw,
    }


def read_manifest(skill_dir: Path) -> Optional[Dict[str, str]]:
    skill_md = Path(skill_dir) / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_MD_BYTES]
    except OSError:
        return None
    return parse_skill_markdown(text)


def _dir_stats(path: Path) -> Tuple[int, int]:
    files = 0
    total = 0
    for root, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".git")]
        for name in filenames:
            files += 1
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
            if files >= MAX_SCAN_FILES or total >= MAX_SCAN_BYTES:
                return files, total
    return files, total


def _load_lock_sources(path: Path) -> Dict[str, str]:
    """Best-effort name → source map from a skills-lock.json file."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    sources: Dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("name") or node.get("skill") or node.get("id")
            source = node.get("source") or node.get("repo") or node.get("repository")
            if isinstance(name, str) and isinstance(source, str) and name and source:
                sources.setdefault(name, source)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return sources


def _lock_sources(scope: str, project: Optional[Path], home: Optional[Path]) -> Dict[str, str]:
    h = _home(home)
    sources: Dict[str, str] = {}
    if scope == "project" and project is not None:
        sources.update(_load_lock_sources(Path(project) / "skills-lock.json"))
    sources.update(_load_lock_sources(h / ".agents" / ".skill-lock.json"))
    return sources


def _iter_skill_dirs(root: SkillRoot) -> Iterable[Tuple[Path, bool]]:
    for base, enabled in ((root.path, True), (root.path / DISABLED_DIR, False)):
        if not base.is_dir():
            continue
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if not entry.is_dir() and not entry.is_symlink():
                continue
            if not (entry / "SKILL.md").is_file():
                continue
            yield entry, enabled


def _skill_info(
    entry: Path,
    root: SkillRoot,
    enabled: bool,
    lock_sources: Dict[str, str],
) -> Optional[SkillInfo]:
    manifest = read_manifest(entry)
    if manifest is None:
        return None
    if not SKILL_ID_RE.match(entry.name):
        return None
    is_link = entry.is_symlink()
    files, size = _dir_stats(entry) if not is_link else (0, 0)
    name = manifest["name"] or entry.name
    return SkillInfo(
        id=entry.name,
        name=name,
        description=manifest["description"],
        provider=root.provider,
        scope=root.scope,
        path=str(entry),
        origin=root.origin,
        enabled=enabled,
        writable=root.writable and not is_link,
        install_mode="symlink" if is_link else "dir",
        files=files,
        bytes=size,
        source=lock_sources.get(name) or lock_sources.get(entry.name),
    )


def scan_skills(
    provider: str,
    scope: str,
    project: Optional[Path] = None,
    home: Optional[Path] = None,
) -> List[SkillInfo]:
    roots = roots_for(provider, scope, project, home)
    lock_sources = _lock_sources(scope, project, home)
    raw: List[SkillInfo] = []
    for root in roots:
        for entry, enabled in _iter_skill_dirs(root):
            info = _skill_info(entry, root, enabled, lock_sources)
            if info is not None:
                raw.append(info)

    grouped: Dict[str, List[SkillInfo]] = {}
    for info in raw:
        grouped.setdefault(info.id, []).append(info)

    result: List[SkillInfo] = []
    for items in grouped.values():
        enabled_items = [item for item in items if item.enabled]
        pool = enabled_items or items
        # Prefer writable entries, then stable path order.
        chosen = sorted(pool, key=lambda item: (not item.writable, item.path))[0]
        chosen.duplicate_paths = [item.path for item in items if item.path != chosen.path]
        if len(enabled_items) > 1:
            # Two active copies mean toggling one would not actually disable the skill.
            chosen.writable = False
        result.append(chosen)
    return sorted(result, key=lambda item: (not item.enabled, item.name.lower()))


def scan_all(project: Optional[Path] = None, home: Optional[Path] = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    warnings: List[str] = []
    for provider in PROVIDERS:
        data[provider] = {"global": [], "project": []}
        for scope in SCOPES:
            if scope == "project" and project is None:
                continue
            try:
                data[provider][scope] = [s.to_dict() for s in scan_skills(provider, scope, project, home)]
            except Exception as exc:  # keep one broken root from blanking the panel
                warnings.append(f"{provider}/{scope}: {exc}")
    return {"skills": data, "warnings": warnings}


# --------------------------------------------------------------- validation

def parse_source_and_skill(source: str, skill: str = "") -> Tuple[str, str]:
    raw_source = (source or "").strip()
    raw_skill = (skill or "").strip()
    if (
        not raw_skill
        and "@" in raw_source
        and not raw_source.startswith(("http://", "https://", "git@", "ssh://", "git://"))
        and not Path(raw_source).expanduser().is_absolute()
        and "/" in raw_source
    ):
        base, _, tail = raw_source.rpartition("@")
        if base and tail:
            raw_source, raw_skill = base, tail
    return raw_source, raw_skill


def validate_source(source: str, skill: str = "") -> None:
    if not source:
        raise ValueError("source is required")
    if len(source) > 2048:
        raise ValueError("source is too long")
    if source.startswith("-"):
        raise ValueError("source must not start with '-'")
    if re.search(r"[\x00-\x1f\x7f]", source):
        raise ValueError("source must not contain control characters")

    is_local = Path(source).expanduser().is_absolute()
    if not is_local and re.search(r"\s", source):
        raise ValueError("source must not contain whitespace")

    if source.startswith(("http://", "https://", "git@", "ssh://", "git://")):
        pass
    elif is_local:
        resolved = Path(source).expanduser().resolve()
        cfg = ensure_config()
        roots = [Path(root).expanduser().resolve() for root in cfg.allowed_roots]
        if not roots:
            raise ValueError("allowed_roots is empty — add a root in the Agents tab")
        if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
            raise ValueError(f"local source {resolved} is outside allowed_roots")
        if not resolved.is_dir():
            raise ValueError(f"local source must be a directory: {resolved}")
    else:
        parts = source.split("/")
        if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
            raise ValueError("source must be owner/repo, a git URL, or a local path")
        if any(not re.match(r"^[A-Za-z0-9_.-]+$", part) for part in parts):
            raise ValueError("source must be owner/repo, a git URL, or a local path")

    if skill:
        if len(skill) > 128:
            raise ValueError("skill name is too long")
        if skill.startswith("-"):
            raise ValueError("skill name must not start with '-'")
        if re.search(r"[\x00-\x1f\x7f]", skill):
            raise ValueError("skill name contains control characters")


def resolve_project_path(raw: str) -> Path:
    if not raw or not raw.strip():
        raise ValueError("project path is required for project scope")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"project path must be absolute: {raw!r}")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ValueError(f"invalid project path: {exc}") from exc
    cfg = ensure_config()
    roots = [Path(root).expanduser().resolve() for root in cfg.allowed_roots]
    if not roots:
        raise ValueError("allowed_roots is empty — add a root in the Agents tab")
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise ValueError(f"project path {resolved} is outside allowed_roots")
    if not resolved.is_dir():
        raise ValueError(f"not a directory: {resolved}")
    return resolved


def _validate_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"invalid scope {scope!r}")


def _validate_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"invalid provider {provider!r}")


def find_skill(
    provider: str,
    scope: str,
    project: Optional[Path],
    skill_id: str,
    home: Optional[Path] = None,
) -> SkillInfo:
    _validate_provider(provider)
    _validate_scope(scope)
    if not SKILL_ID_RE.match(skill_id or ""):
        raise ValueError(f"invalid skill id {skill_id!r}")
    for info in scan_skills(provider, scope, project, home):
        if info.id == skill_id:
            return info
    raise FileNotFoundError(f"skill '{skill_id}' not found for {provider}/{scope}")


def toggle_skill(
    provider: str,
    scope: str,
    project: Optional[Path],
    skill_id: str,
    enabled: bool,
    home: Optional[Path] = None,
) -> SkillInfo:
    info = find_skill(provider, scope, project, skill_id, home)
    if not info.writable:
        raise ValueError("skill is read-only (symlink/system/plugin); manage it with npx skills")
    if info.enabled == enabled:
        return info

    path = Path(info.path)
    if info.enabled:
        root = path.parent
        target = root / DISABLED_DIR / skill_id
    else:
        root = path.parent.parent
        target = root / skill_id
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"target already exists: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)
    if enabled:
        _prune_disabled(root)
    return find_skill(provider, scope, project, skill_id, home)


def _prune_disabled(root: Path) -> None:
    try:
        (Path(root) / DISABLED_DIR).rmdir()
    except OSError:
        pass


def read_skill_content(
    provider: str,
    scope: str,
    project: Optional[Path],
    skill_id: str,
    home: Optional[Path] = None,
) -> Dict[str, str]:
    info = find_skill(provider, scope, project, skill_id, home)
    skill_md = Path(info.path) / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md not found for '{skill_id}'")
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_MD_BYTES]
    except OSError as exc:
        raise FileNotFoundError(f"could not read {skill_md}: {exc}") from exc
    return {"path": str(skill_md), "content": content}


# ----------------------------------------------------------- environment/CLI

def parse_node_version(raw: str) -> Optional[Tuple[int, int, int]]:
    match = re.search(r"v?(\d+)\.(\d+)(?:\.(\d+))?", raw or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _run_version(binary: Optional[str]) -> Optional[str]:
    if not binary:
        return None
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def environment(home: Optional[Path] = None) -> Dict[str, Any]:
    h = _home(home)
    node_bin = shutil.which("node")
    npx_bin = shutil.which("npx")
    node_version = _run_version(node_bin)
    parsed = parse_node_version(node_version or "")
    node_ok = bool(parsed and parsed[:2] >= NODE_MIN_VERSION)
    claude_detected = bool(shutil.which("claude")) or (h / ".claude").is_dir()
    codex_detected = bool(shutil.which("codex")) or (h / ".codex").is_dir()
    return {
        "node": {
            "version": node_version,
            "ok": node_ok,
            "required": f"{NODE_MIN_VERSION[0]}.{NODE_MIN_VERSION[1]}",
        },
        "npx": bool(npx_bin),
        "install_supported": bool(node_ok and npx_bin),
        "cli_spec": SKILLS_CLI_SPEC,
        "providers": {
            "claude": {
                "detected": claude_detected,
                "roots": [str(root.path) for root in claude_global_roots(h)],
            },
            "codex": {
                "detected": codex_detected,
                "roots": [str(root.path) for root in codex_global_roots(h)],
            },
        },
    }


def _npx() -> str:
    return shutil.which("npx") or "npx"


def build_install_argv(source: str, skill: str, providers: List[str], scope: str) -> List[str]:
    argv = [_npx(), "-y", SKILLS_CLI_SPEC, "add", source, "--skill", skill, "--copy", "-y"]
    for provider in providers:
        _validate_provider(provider)
        argv += ["-a", _PROVIDER_AGENT[provider]]
    if scope == "global":
        argv.append("-g")
    return argv


def build_remove_argv(name: str, provider: str, scope: str) -> List[str]:
    _validate_provider(provider)
    argv = [_npx(), "-y", SKILLS_CLI_SPEC, "remove", name, "-a", _PROVIDER_AGENT[provider], "-y"]
    if scope == "global":
        argv.append("-g")
    return argv


def _cli_env() -> Dict[str, str]:
    env = os.environ.copy()
    env.update({
        "DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "NO_COLOR": "1",
        "npm_config_ignore_scripts": "true",
        "npm_config_yes": "true",
    })
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        except Exception:
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def run_skills_cli(argv: List[str], cwd: Path, timeout: int = 300) -> Tuple[int, str, str]:
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=_cli_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(sys.platform != "win32"),
        )
    except FileNotFoundError as exc:
        raise RuntimeError("npx was not found in PATH") from exc

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except Exception:
            stdout, stderr = "", ""
        raise TimeoutError(f"npx skills timed out after {timeout}s") from None
    return proc.returncode or 0, _clean(stdout), _clean(stderr)


def _clean(text: str) -> str:
    return _ANSI_RE.sub("", text or "").replace("\r", "")


def _tail(text: str, limit: int = MAX_CLI_OUTPUT) -> str:
    return text[-limit:] if len(text) > limit else text


def _installed_after_install(
    providers: List[str],
    scope: str,
    project: Optional[Path],
    skill: str,
    home: Optional[Path],
) -> List[Dict[str, str]]:
    installed: List[Dict[str, str]] = []
    wanted = skill.strip().lower()
    for provider in providers:
        for info in scan_skills(provider, scope, project, home):
            if info.name.lower() == wanted or info.id.lower() == wanted:
                installed.append({
                    "provider": provider,
                    "scope": scope,
                    "id": info.id,
                    "name": info.name,
                    "path": info.path,
                })
    return installed


def install_skill(
    source: str,
    skill: str,
    providers: List[str],
    scope: str,
    project: str = "",
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    source, skill = parse_source_and_skill(source, skill)
    validate_source(source, skill)
    if not skill:
        raise ValueError("skill name is required (use owner/repo@skill for a repo)")
    _validate_scope(scope)
    cleaned: List[str] = []
    for provider in providers or []:
        _validate_provider(provider)
        if provider not in cleaned:
            cleaned.append(provider)
    if not cleaned:
        raise ValueError("select at least one provider")

    env = environment(home)
    if not env["install_supported"]:
        node = env["node"]["version"] or "not found"
        raise ValueError(
            f"npx skills needs Node >= {NODE_MIN_VERSION[0]}.{NODE_MIN_VERSION[1]} "
            f"(current: {node}) and npx in PATH"
        )

    project_path = resolve_project_path(project) if scope == "project" else None
    argv = build_install_argv(source, skill, cleaned, scope)
    cwd = project_path or REPO_ROOT
    code, stdout, stderr = run_skills_cli(argv, cwd)
    if code != 0:
        detail = _tail((stderr or stdout).strip()) or f"exit code {code}"
        raise RuntimeError(detail)

    installed = _installed_after_install(cleaned, scope, project_path, skill, home)
    warnings = [] if installed else ["install finished but the new skill was not found on disk; refresh the list"]
    return {
        "status": "success",
        "installed": installed,
        "stdout_tail": _tail(stdout.strip()),
        "warnings": warnings,
    }


def remove_skill_dir(info: SkillInfo) -> None:
    if not info.writable:
        raise ValueError("skill is read-only (symlink/system/plugin); manage it with npx skills")
    path = Path(info.path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"refusing to remove non-directory skill path: {path}")
    shutil.rmtree(path)
    _prune_disabled(path.parent.parent if path.parent.name == DISABLED_DIR else path.parent)


def _lock_path(scope: str, project: Optional[Path], home: Optional[Path]) -> Path:
    if scope == "project" and project is not None:
        return Path(project) / "skills-lock.json"
    return _home(home) / ".agents" / ".skill-lock.json"


def _skill_still_installed(
    scope: str,
    project: Optional[Path],
    names: Iterable[str],
    home: Optional[Path],
) -> bool:
    wanted = {name.strip().lower() for name in names if name}
    for provider in PROVIDERS:
        try:
            items = scan_skills(provider, scope, project, home)
        except Exception:
            continue
        for item in items:
            if item.name.lower() in wanted or item.id.lower() in wanted:
                return True
    return False


def _prune_lock_entry(
    scope: str,
    project: Optional[Path],
    info: SkillInfo,
    home: Optional[Path],
) -> None:
    """Drop the lock entry once no provider still has the skill."""
    names = [info.name, info.id]
    if _skill_still_installed(scope, project, names, home):
        return
    path = _lock_path(scope, project, home)
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    entries = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return

    wanted = {name.strip().lower() for name in names if name}
    changed = False
    for key in list(entries.keys()):
        if key.strip().lower() in wanted:
            entries.pop(key, None)
            changed = True
    if not changed:
        return
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def remove_skill(
    provider: str,
    scope: str,
    project: Optional[Path],
    skill_id: str,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    info = find_skill(provider, scope, project, skill_id, home)
    if not info.writable:
        raise ValueError("skill is read-only (symlink/system/plugin); manage it with npx skills")

    env = environment(home)
    if env["install_supported"]:
        argv = build_remove_argv(info.name or info.id, provider, scope)
        cwd = project if scope == "project" and project is not None else REPO_ROOT
        try:
            code, stdout, stderr = run_skills_cli(argv, cwd)
        except TimeoutError:
            code, stdout, stderr = 1, "", "timeout"
        if code == 0:
            _prune_disabled(Path(info.path).parent)
            if not Path(info.path).exists() and not Path(info.path).is_symlink():
                _prune_lock_entry(scope, project, info, home)
                return {"removed": [info.path], "via": "cli", "stdout_tail": _tail(stdout.strip())}
            # The CLI may leave the canonical .agents/skills copy in place when
            # only the Codex view is selected; finish the job directly.
            remove_skill_dir(info)
            _prune_lock_entry(scope, project, info, home)
            return {
                "removed": [info.path],
                "via": "cli+direct",
                "stdout_tail": _tail(stdout.strip()),
                "warnings": ["the skills CLI left the directory on disk; removed it directly"],
            }

    remove_skill_dir(info)
    _prune_lock_entry(scope, project, info, home)
    return {
        "removed": [info.path],
        "via": "direct",
        "warnings": ["removed from disk directly; the skills lockfile may still list it"],
    }
