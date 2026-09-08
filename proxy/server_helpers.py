import datetime
import json
import os
import platform
import shutil
import subprocess

PROXY_URL = "http://127.0.0.1:8082"

IDE_DEFINITIONS = [
    {"id": "vscode", "name": "VS Code", "binary": "code", "config_dir": "Code", "supports_claude_extension": True},
    {"id": "vscodium", "name": "VSCodium", "binary": "codium", "config_dir": "VSCodium", "supports_claude_extension": True},
    {"id": "cursor", "name": "Cursor", "binary": "cursor", "config_dir": "Cursor", "supports_claude_extension": True},
]

def _detect_ides():
    detected = {}
    for ide in IDE_DEFINITIONS:
        binary_path = shutil.which(ide["binary"])
        if binary_path:
            version = ""
            try:
                result = subprocess.run([binary_path, "--version"], capture_output=True, text=True, timeout=5)
                version = result.stdout.strip().split("\n")[0] if result.returncode == 0 else ""
            except Exception:
                pass
            detected[ide["id"]] = {
                "binary": binary_path,
                "version": version,
                "name": ide["name"],
                "config_dir": ide["config_dir"],
                "supports_claude_extension": ide["supports_claude_extension"],
                "last_detected": datetime.datetime.now().isoformat()
            }
    return detected

def _save_ide_detection(detected):
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    data = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                pass
    data["ide_detected"] = detected
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _get_ide_settings_path(config_dir):
    system = platform.system()
    home = os.path.expanduser("~")
    if system == "Linux":
        return os.path.join(home, ".config", config_dir, "User", "settings.json")
    elif system == "Windows":
        return os.path.join(os.environ.get("APPDATA", ""), config_dir, "User", "settings.json")
    else:
        return os.path.join(home, "Library", "Application Support", config_dir, "User", "settings.json")

def _safe_merge_json(filepath, updates):
    data = {}
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                pass
    changed = False
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            for sub_key, sub_value in value.items():
                if data[key].get(sub_key) != sub_value:
                    data[key][sub_key] = sub_value
                    changed = True
        else:
            if data.get(key) != value:
                data[key] = value
                changed = True
    if changed:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return changed

def _proxy_env() -> dict:
    return {
        "ANTHROPIC_BASE_URL": PROXY_URL,
        "ANTHROPIC_API_KEY": "key-router",
        "OPENAI_BASE_URL": f"{PROXY_URL}/v1",
        "KEY_ROUTER_API_KEY": "key-router",
        "FREECLAUDE_API_KEY": "key-router",
    }

def _setup_claude_env():
    claude_settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    return _safe_merge_json(claude_settings_path, {
        "env": {
            "ANTHROPIC_BASE_URL": PROXY_URL,
            "ANTHROPIC_API_KEY": "key-router"
        }
    })

def _setup_ide_settings(config_dir):
    settings_path = _get_ide_settings_path(config_dir)
    return _safe_merge_json(settings_path, {
        "claudeCode.disableLoginPrompt": True
    })

def _launch_ide(binary, cwd):
    env = os.environ.copy()
    env.update(_proxy_env())
    subprocess.Popen([binary, "--new-window", cwd], start_new_session=True, env=env)

def _find_linux_terminal():
    terminals = [
        "gnome-terminal", "konsole", "xfce4-terminal", "lxterminal",
        "alacritty", "kitty", "foot", "terminology", "xterm"
    ]
    for term in terminals:
        if shutil.which(term):
            return term
    return None

def _launch_terminal(cmd, cwd):
    system = platform.system()
    env = os.environ.copy()
    env.update(_proxy_env())
    if system == "Windows":
        import base64
        ps_cmd = cmd.replace('&&', ';')
        encoded = base64.b64encode(ps_cmd.encode('utf-16-le')).decode('ascii')
        return subprocess.Popen(
            f'start "" powershell -NoExit -EncodedCommand {encoded}',
            shell=True, cwd=cwd, env=env
        )
    elif system == "Linux":
        terminal = _find_linux_terminal()
        if not terminal:
            raise RuntimeError("No terminal emulator found. Install gnome-terminal, konsole, xterm, etc.")
        if terminal == "gnome-terminal":
            term_cmd = [terminal, "--working-directory", cwd, "--", "bash", "-c", f"{cmd}; exec bash"]
        elif terminal == "konsole":
            term_cmd = [terminal, "--workdir", cwd, "-e", "bash", "-c", f"{cmd}; exec bash"]
        elif terminal == "kitty":
            term_cmd = [terminal, "--directory", cwd, "bash", "-c", f"{cmd}; exec bash"]
        elif terminal == "alacritty":
            term_cmd = [terminal, "--working-directory", cwd, "-e", "bash", "-c", f"{cmd}; exec bash"]
        elif terminal == "foot":
            term_cmd = [terminal, "--working-directory", cwd, "bash", "-c", f"{cmd}; exec bash"]
        else:
            term_cmd = [terminal, "-e", f"bash -c 'cd {cwd} && {cmd}; exec bash'"]
        return subprocess.Popen(term_cmd, env=env, start_new_session=True)
    else:
        escaped_cwd = cwd.replace('"', '\\"')
        escaped_cmd = cmd.replace('"', '\\"')
        apple_script = f'tell app "Terminal" to do script "cd \\"{escaped_cwd}\\"; {escaped_cmd}"'
        return subprocess.Popen(
            ["osascript", "-e", apple_script],
            env=env
        )

def _resolve_launch(request, cli_cmd: str):
    import urllib.parse
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_cwd = base_dir
    if request.repo_url:
        projects_dir = os.path.join(base_dir, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        parsed_url = urllib.parse.urlparse(request.repo_url)
        path_parts = parsed_url.path.strip("/").split("/")
        repo_name = path_parts[-1] if path_parts else "repo"
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        repo_path = os.path.join(projects_dir, repo_name)
        if os.path.exists(repo_path):
            cmd = f'cd "{repo_path}" && {cli_cmd}'
        else:
            cmd = f'cd "{projects_dir}" && git clone {request.repo_url} && cd "{repo_name}" && {cli_cmd}'
    else:
        if request.path and os.path.isdir(request.path):
            target_cwd = request.path
        cmd = cli_cmd
    return cmd, target_cwd


def init_routers_file():
    pass
