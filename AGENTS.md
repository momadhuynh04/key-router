# AGENTS.md — key-router

Authoritative guide for coding agents. Read this **before** exploring the repo; it is
kept in sync with the code so a new session does not have to rediscover the architecture.
`CLAUDE.md` imports this file.

If something here contradicts the code, trust the code and update this file in the same change.

---

## 1. What this project is

**key-router** is a local FastAPI proxy that lets these clients talk to non-Anthropic
providers (OpenRouter, DeepSeek, Google AI Studio, or any OpenAI/Anthropic-compatible API):

- **Claude Code** (CLI + VS Code/Cursor/VSCodium extension) — Anthropic Messages ingress.
- **Codex CLI** — OpenAI Responses ingress (and Chat Completions as a fallback).
- **Open WebUI / any OpenAI client** — through the separate **agent-bridge** service,
  which exposes exactly one model, `key-router-agents`, and runs Claude Code as a real agent.

A React + Tailwind dashboard (`webui/`) manages routes, providers, IDE launching, the bridge
and skills.

| Service | Default URL | Entry point | Started by |
| ------- | ----------- | ----------- | ---------- |
| Proxy + dashboard | `http://127.0.0.1:8082` | `proxy/server.py` (`app = create_app()`) | `start.sh` / `start.bat` / `python -m cli.main` |
| Agent bridge | `http://127.0.0.1:8083/v1` | `bridge/__main__.py` (`python -m bridge`) | Dashboard → Agents → Start bridge |
| Vite dev server | `http://localhost:5173` | `webui/` | `dev.sh` / `dev.bat` |

The proxy data path performs **no authentication and spawns nothing**. Only the bridge
spawns CLI processes, and only for configured agents inside `allowed_roots`.

---

## 2. Commands

```bash
# Setup (Python 3.11.9 — see .python-version)
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # add at least one provider key

# Run
./start.sh                          # production, opens :8082   (Windows: start.bat)
./dev.sh                            # backend :8082 --reload + Vite :5173 (dev.bat)
python -m cli.main                  # same as start.sh
python -m cli serve                 # typer subcommand
uvicorn proxy.server:app --host 127.0.0.1 --port 8082

# Bridge (usually started from the dashboard)
python -m bridge                    # listens on agents.json port (8083)

# Tests — always run the full suite
python -m pytest test/ -v

# Frontend
cd webui && npm install
npm run dev                         # Vite
npm run build                       # tsc -b && vite build  -> webui/dist (served by the proxy)
npm run lint                        # oxlint

# Custom provider CLI
python -m cli add-provider --id my --display-name "MyAI" --api openai_compatible \
  --base-url https://api.my.ai/v1 --api-key "sk-..." --model my-model:"My Model"
python -m cli list-providers
python -m cli remove-provider my
python -m cli list-models my
```

WebUI build is required for production: the proxy mounts `webui/dist` at `/`. Without it,
`/` returns a JSON hint. `webui/dist/` is gitignored.

---

## 3. Architecture

```
Claude Code   POST /v1/messages            ─┐
Codex         POST /v1/responses            ├─→ proxy/routers/* → ingress translation
OpenAI client POST /v1/chat/completions    ─┘        ↓
                                             AnthropicRequest (internal format)
                                                       ↓
                                             ModelMapper.resolve(requested_model)
                                                       ↓  ("provider/target-model")
                                             ProviderRouter.get_provider*()
                                                       ↓
                                    provider adapter (OpenAIBaseProvider / Anthropic-native)
                                                       ↓
                                    upstream API → AnthropicResponse / Anthropic SSE
                                                       ↓
                                 ingress back-translation → client-native response
```

**The internal wire format is always Anthropic Messages.** Every ingress converts to
`models/anthropic.py` types, and every provider adapter emits Anthropic-shaped responses
and `SSEEvent`s; the ingress then converts back to OpenAI Chat or Responses.

### Two request paths per protocol

`ProviderRouter` picks an adapter per ingress:

- `get_provider_for_anthropic()` — Anthropic-native adapter when available
  (OpenRouter → `/api/v1/messages`, DeepSeek → `/anthropic`, custom `anthropic`).
- `get_provider_for_openai()` / `get_provider()` — OpenAI-compatible adapter
  (`OpenAIBaseProvider`: Chat Completions + local Anthropic↔OpenAI translation).

Provider classes declare `supports_anthropic` / `supports_openai`; `OpenAIBaseProvider`
implements the full translation (tools, images, thinking, SSE).

### Agent bridge (separate service)

```
Open WebUI → POST :8083/v1/chat/completions
  → bridge/directives.py parses /start /stop /call /agents
  → bridge/app.py resolves the bound agent + session
  → bridge/registry.py builds argv        (all CLI flag knowledge lives here)
  → bridge/runner.py spawns `claude -p --output-format stream-json` (prompt via stdin)
  → bridge/sse.py turns JSONL events into markdown + OpenAI SSE chunks
  → bridge/sessions.py persists binding/session id in bridge/state.json
```

The bridge has **no code-level dependency on `proxy/` or `config/`** (it can be split into
its own repo later). `runner.proxy_env()` sets `ANTHROPIC_BASE_URL` etc. locally on purpose.

---

## 4. Module map

### `proxy/` — HTTP surface and protocol translation

| File | Responsibility |
| ---- | -------------- |
| `app.py` | `create_app()`: CORS, registers routers, catch-all `/v1/*` 404 with request preview, mounts `webui/dist`. |
| `server.py` | Thin `app = create_app()`. **Back-compat shims**: `provider_router`, `_detect_ides`, `_launch_ide` are re-exported/delegated so tests can patch `proxy.server.*`. Keep them working. |
| `router.py` | `provider_router` singleton. Resolves custom-provider direct form (`<custom-id>/<model>`) before `ModelMapper`; instantiates the right adapter. Add new built-in providers here. |
| `routers/anthropic.py` | `/v1/messages` (stream + non-stream) and `/v1/messages/count_tokens`. Agentic retry lives inline here. |
| `routers/openai.py` | `/v1/chat/completions` (Codex chat fallback, generic OpenAI clients). |
| `routers/responses.py` | `/v1/responses` — Codex native. Unwraps `tools` / `additional_tools` / `namespace` / `custom:exec` and runs agentic retry. |
| `routers/management.py` | Dashboard API: `/api/models`, `available-models`, IDE detect/setup/launch, Codex setup, custom-provider CRUD, recent dirs, folder picker. |
| `routers/agents.py` | Dashboard API for `agents.json` plus bridge process lifecycle (`/api/bridge/start|stop|status|logs`, PID file). |
| `routers/skills.py` | `/api/skills*` — list, marketplace search, install, toggle, delete, preview. |
| `routers/settings.py` | `/api/settings*` — key list/save/delete (built-in + custom), built-in base URLs. Responses only carry masked previews. |
| `routers/testlab.py` | `/api/testlab*` — headless Claude Code/Codex runs: start, poll, stop, meta. |
| `testlab.py` | Test-run domain: scenario prompt, run registry, Claude/Codex JSONL parsing, artifact + check building. Reuses `bridge.runner`/`registry`/`sse`; needs no bridge process. |
| `routers/tools.py` | `/api/tools*` — personal tools: list, install/reinstall, setup, start/stop/restart, logs, uninstall, tool credentials (masked). |
| `tools_hub.py` | Tool domain: GitHub tarball install (safe extract), loopback port allocation, process spawn/stop with session token, `tool.json` env mapping. |
| `process_utils.py` | Shared `kill_tree`, zombie-aware `pid_alive`, `reap`, detached `spawn_kwargs`, `tail_file`. |
| `openai_ingress.py` | OpenAI Chat ↔ Anthropic translation, including image parts and OpenAI SSE chunks. |
| `responses_ingress.py` | Responses ↔ Anthropic translation, typed Responses SSE, thinking ↔ `reasoning` items, tool unwrapping, text-imitating-tool-call rescue, narration/garbled detection. **Large, central file — touch carefully.** |
| `server_helpers.py` | IDE definitions/detection, safe JSON merge of `~/.claude/settings.json` and IDE settings, terminal/IDE launching. |
| `errors.py` | Anthropic/OpenAI/Responses error shapes. |
| `retry.py` | `AGENTIC_RETRY_ATTEMPTS = 3` and generic retry helpers (routers currently implement retry inline). |
| `image_utils.py` | Internal image block conversions: data URL / `image_url` / `inlineData`. |

### `provider/` — upstream adapters

| File | Responsibility |
| ---- | -------------- |
| `base.py` | `BaseProvider` ABC: `translate_request`, `translate_response`, `stream`, `generate`. |
| `openai_base.py` | Shared Anthropic↔OpenAI Chat translation, tool schema sanitizing, DeepSeek/thinking handling, streaming SSE. Most OpenAI-compatible providers inherit this. |
| `openrouter/adapter.py` | `OpenRouterProvider` (OpenAI base) + `OpenRouterAnthropicProvider` (native `/v1/messages`). |
| `deepseekplatform/adapter.py` | `DeepSeekAnthropicProvider` (native) + `DeepSeekOpenAIProvider`. |
| `googleaistudio/adapter.py` | Gemini `generateContent`/`streamGenerateContent`, tool schema sanitizing, `thoughtSignature` cache. |
| `custom/adapter.py` | `GenericOpenAIProvider` and `GenericAnthropicProvider` for user-defined providers. |
| `CUSTOM_PROVIDER_GUIDE.md` | Guide for adding a provider in code. |

### `config/` — persistence and settings

| File | Responsibility |
| ---- | -------------- |
| `settings.py` | Pydantic settings from `.env` (`host`, `port` only). |
| `secrets.py` | `secrets.json` store: built-in/custom API keys, built-in base URL overrides (defaults hardcoded) and `tool_keys` for personal tools; masked previews, atomic `0600` writes. Sole key source; read per provider instantiation for hot reload. |
| `tools.py` | Personal-tool registry: `.key-router/tools.json` (tracked catalog/source) + `.key-router/runtime.json` (gitignored pid/token/port), install/manifest validation (`tool.json`), tool/log paths. Installed code lives in the repo's `tools/<id>/` root (gitignored). |
| `model_map.py` | `ModelMapper` singleton. Exact match → tier keyword → family fallback (`claude`, `codex`) → error. Consolidates legacy `opus`/`sonnet`/`haiku` into `claude` and rewrites `config.json` on load. |
| `custom_providers.py` | Custom provider CRUD in `config.json`, one key per provider ID via `secrets.py`, key masking, `lru_cache` (invalidate after writes). |
| `codex_config.py` | Merges `~/.codex/config.toml` (`wire_api = "responses"`, dummy `env_key`) and persists the env var per OS. |
| `skills.py` | Filesystem skills manager for Claude Code/Codex, global/project roots, `.disabled/` toggle, `npx skills@1.7.0 --copy`, `allowed_roots` fence. |
| `recent_dirs.py` | Last 8 launch folders in `config.json`. |
| `logging.py` | Shared `key-router` logger. Use `log_warn` / `log_error` / `logger.info`. |

### `models/`

| File | Responsibility |
| ---- | -------------- |
| `anthropic.py` | Internal request/response models (`extra="allow"` on purpose). |
| `openai_compat.py` | Ingress schemas: `OpenAIRequest`, `ResponsesRequest`. |
| `events.py` | `SSEEvent(format())` used by every provider stream. |

### `bridge/` — agent-bridge service

| File | Responsibility |
| ---- | -------------- |
| `app.py` | FastAPI app, bearer-token auth, concurrency limiter, turn orchestration, sub-agent handoff, ask_user continuation. |
| `config.py` | `agents.json` loading/validation, `allowed_roots` fence, state/PID/log paths, `AgentConfigError`. |
| `registry.py` | **All CLI argv knowledge.** Claude/Codex flag maps, tool profiles, `ask_user` MCP config, stdin payloads. |
| `runner.py` | Spawn headless CLI in a new process group, read JSONL, per-turn timeout, kill tree, stderr capture, 64 MiB line limit. |
| `sessions.py` | Chat-keyed state (`X-OpenWebUI-Chat-Id`), `__meta__` for pending questions and sub-agent handoffs. |
| `sse.py` | JSONL event → reply text / markdown tool rendering / OpenAI SSE chunks. |
| `directives.py` | Parses `/start`, `/stop`, `/call|/sub|/run`, `/agents|/list|/help`. |
| `mcp_ask.py` | Minimal stdio MCP server exposing `ask_user` (Claude's own tool is unavailable under `-p`). |

### `webui/src/`

No router/libraries beyond React. Tabs live in `App.tsx` (`routing`, `providers`, `testlab`,
`tools`, `launcher`, `agents`, `settings`); panel components are `AgentModal.tsx`, `BridgePanel.tsx`,
`SkillsPanel.tsx`, `CustomProvidersPanel.tsx`, `CustomProviderModal.tsx`, `SettingsPanel.tsx`,
`TestLabPanel.tsx`, `ToolsPanel.tsx`. Data comes from `fetch('/api/...')`; Vite proxies `/api` and `/v1`
to `:8082` in dev. Styling is Tailwind utility classes with custom tokens in `index.css`.

### `tools/` — personal tool sub-apps

`tools/git-tool/` is a standalone FastAPI + Vite app (own `requirements.txt`, own `webui/`) published
from its own GitHub repo. It reads `tool.json` from its root, gets `GITHUB_TOKEN` /
`KEY_ROUTER_URL` / `KEY_ROUTER_TOOL_TOKEN` / `KEY_ROUTER_TOOL_DATA` from the environment, and
validates the session token on `/api/*`. It must not import anything from `proxy/` or `config/`.

Its repo discovery (`git_tool/discovery.py`) treats the token as the source of truth: it lists
GitHub repos by affiliation (`owner` / `collaborator` / `organization_member`, 100/page, 5-page cap)
and only then scans the `allowed_roots` from `GET /api/agents` (depth 3, prunes
`node_modules`/venv/dist, 20k dirs / 20s) to mark which of those have a local clone. Clones the token
cannot reach stay hidden; with no token (or an empty result) the list falls back to the local scan.
`meta.source` says which mode produced the list. Results, pins and recent repos are cached in
`KEY_ROUTER_TOOL_DATA/state.json`.

Write operations run the user's collaborator loop through `/api/workflow/start`, `/api/workflow/sync`,
`/api/commit` and `/api/push`. Both step endpoints return `{ok, steps[]}` with one entry per git
command so the UI can show where a run stopped. Network commands (`pull`, `push`) authenticate with
the tool's GitHub token via `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` env vars plus
`GIT_TERMINAL_PROMPT=0` — never on the command line and never written to config.

Discussions use the GitHub GraphQL endpoint (`POST /graphql`), exposed as `/api/discussions`;
GraphQL errors are surfaced as `GitHubError` text so the UI can show "discussions unavailable"
instead of an empty list. Panel data is keyed by a `dataVersion` counter in the app: the header
**Sync** button runs `git fetch` and bumps it, and every panel includes it in its load effect.

### `cli/` and scripts

`cli/main.py` is a typer app (`serve`, `add-provider`, `list-providers`,
`remove-provider`, `list-models`). No args → serves. `start.*`/`dev.*`/`detect-ide.*` are
thin platform wrappers.

### `test/`

Flat pytest suite (~383 tests), no conftest. Typical pattern: `TestClient(proxy.server.app)`
plus `patch('proxy.server.provider_router.get_provider')` / `AsyncMock` providers.
Bridge tests use `pytest-asyncio`, `tmp_path`, and `monkeypatch`. The git tool keeps its own
suite in `tools/git-tool/tests/`; its `conftest.py` puts the package on `sys.path`, so the root
`python -m pytest` collects it too and `cd tools/git-tool && python -m pytest` still works.

---

## 5. Key invariants and gotchas

1. **Internal format is Anthropic.** Do not leak OpenAI shapes into provider adapters or
   Anthropic shapes into client responses; convert at the ingress boundary.
2. **Provider-specific hacks stay in that provider.** E.g. DeepSeek thinking, Gemini
   `thoughtSignature`, Cohere schema quirks. Do not push them into `BaseProvider`.
3. **`webui/dist` is generated.** Never edit it; edit `webui/src` and rebuild.
4. **`config.json` is shared state.** Writers (model map, custom providers, recent dirs,
   IDE detection) must merge, not replace, and custom-provider writes must call
   `invalidate_custom_providers_cache()`. Keep the file pretty-printed, no secrets.
5. **`secrets.json` is the only key source.** Built-in keys are keyed by provider (`openrouter`,
   `deepseek`, `google`), custom keys by provider ID. `.env` only holds `HOST`/`PORT`; `api_key_env`
   in `config.json` is obsolete and ignored. Dashboard responses only carry `has_key`/masked previews.
   `secrets.json` is gitignored and written atomically with `0600`.
6. **`agents.json` is re-read on every bridge request.** Dashboard edits apply without a
   restart. It is gitignored; `agents.example.json` is the template.
7. **`allowed_roots` is a security fence.** `resolve_workdir()` and project-scoped skills
   resolve symlinks and reject paths outside the roots.
8. **Headless runs auto-deny prompts.** `--permission-prompts none`; `permission: edit`
   still denies Bash. `ask_user` is pre-approved through `--allowedTools`. Keep that rule
   in `registry.py`; removing it silently breaks the tool.
9. **Prompt goes through stdin, not argv.** `--tools`, `--add-dir`, `--allowedTools` are
   variadic and would swallow a trailing prompt. Do not move it back to argv.
10. **Every turn is a new process** (spawn + `--resume`). Kill the process group on
    timeout/stop; never leave orphaned agents editing files.
11. **Agentic retry is deliberate.** On `/v1/messages` and `/v1/responses`, with tools
    offered, a premature/truncated/empty/narrating/garbled reply is retried (max 3) with
    `AGENTIC_NUDGE` and a bumped `max_tokens`. `/v1/chat/completions` does **not** retry.
    Changes here affect every client — test stream and non-stream.
12. **Bridge must not import `proxy/` or `config/`.** It is intentionally separable.
13. **Only `cli: "claude"` agents run today.** `bridge/app.py` rejects `cli: "codex"`
    turns with a "Phase 2" message; `bridge/registry.py::codex_argv` exists but is unused.
14. **Tests patch `proxy.server.*`.** Keep the re-export/delegation shims in
    `proxy/server.py` and `proxy/server_helpers.py` usage intact.
15. **Cross-platform is mandatory.** CI runs Ubuntu **and Windows**; avoid POSIX-only
    paths, signals, and shell assumptions. Use `pathlib`, `os.replace`, guarded `killpg`.
16. **Do not edit `venv/`, `webui/node_modules/`, `projects/`, `backup/`, `bridge.log`,
    `bridge/state.json`, `.env`, `agents.json`, or `__pycache__/`.**
17. **Test lab runs headless inside the proxy.** It reuses `bridge.runner`/`registry`/`sse` but does
    not need the bridge process. Workdirs are fenced by `agents.json::allowed_roots`; artifacts go to
    `<workdir>/.key-router-testlab/<run-id>/`; only one run is active at a time. A model written as
    `<builtin|custom-provider>/<model>` resolves **directly**, bypassing `config.json` routes, on every
    ingress. Codex gets `--skip-git-repo-check` and `-i <path>` for images; Claude gets image blocks
    through stdin (`--input-format stream-json`).
18. **Reasoning must survive Responses round-trips.** Anthropic `thinking` blocks become
    Responses `reasoning` items (never assistant text); Codex echoes them back and
    `responses_to_anthropic` re-attaches them to the `tool_use` message. DeepSeek `/anthropic`
    rejects tool turns whose thinking was dropped (`content[].thinking ... must be passed back`).
19. **Personal tools run as separate loopback processes.** Install goes to the repo's `tools/<id>/`
    root (gitignored). `.key-router/tools.json` is **tracked** (catalog + source repo); runtime
    (`runtime.json`), logs and data are gitignored. A tool's
    credentials live in `secrets.json::tool_keys`, declared by `tool.json` (`"ENV": "tool:<name>"`).
    They are configured **in the tool's own UI** and read/written through
    `/api/tools/<id>/credentials/<name>`, which only accepts that tool's per-start `X-Tool-Token`.
    The registry records the source repo (local installs infer it from the git remote), so a deleted
    install folder can be restored through `POST /api/tools/<id>/reinstall`.
    Tools bind `127.0.0.1` and validate the same token on `/api/*`. `"ENV": "tool:data"` resolves to
    `.key-router/data/<id>/` (created on start) for persistent tool state. Stop must kill the process
    group **and** reap it — a stopped child is a zombie, and `kill(pid, 0)` still succeeds for zombies
    (handled in `proxy/process_utils.py`).

---

## 6. Configuration and state

### `.env` (never commit; template in `.env.example`)

| Variable | Purpose |
| -------- | ------- |
| `HOST` / `PORT` | default `127.0.0.1` / `8082` |

No keys or base URLs live here. Manage them in the dashboard **Settings** tab (stored in `secrets.json`).

### `secrets.json` (gitignored, dashboard-managed, `0600`)

```json
{
  "keys": {"openrouter": "...", "deepseek": "...", "google": "..."},
  "custom_keys": {"<provider-id>": "..."},
  "base_urls": {"openrouter": "...", "deepseek_anthropic": "...", "deepseek_openai": "...", "google": "..."},
  "tool_keys": {"github": "..."}
}
```

Never return raw values from an API. There is no environment/`.env` fallback for keys: `secrets.json`
is authoritative. Base URLs use the hardcoded default unless overridden here. Providers read this file
on every instantiation, so Settings changes apply without a restart.

### `config.json` (git-tracked, edited by dashboard)

```json
{
  "model_mappings": {"claude": "<provider>/<model>", "codex": "<provider>/<model>"},
  "custom_providers": {"<id>": {"provider_api": "openai_compatible|anthropic", "...": "..."}},
  "ide_detected": {},
  "codex_cli_detected": {},
  "recent_dirs": []
}
```

`claude` / `codex` are **family routes**; exact model keys override a family route.
Legacy `opus`/`sonnet`/`haiku` are consolidated on load.

### `agents.json` (gitignored, dashboard-managed, template `agents.example.json`)

Top level: `host`, `port`, `token`, `key_router_url`, `allowed_roots`, `default_agent`,
`max_concurrent_runs`, `turn_timeout_sec`, `allow_bypass`, `agents[]`.
Per agent: `id`, `name`, `cli`, `model`, `workdir`, `permission`, `effort`, `tools`,
`restricted`, `add_dirs`, `append_system_prompt`, `fallback_model`, `max_budget_usd`,
`allowed_tools`, `disallowed_tools`. Valid values live in `bridge/config.py`.

### Files written outside the repo

`~/.claude/settings.json` (`ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY`),
`~/.codex/config.toml` (`model_provider = "key-router"`),
IDE `User/settings.json` (`claudeCode.disableLoginPrompt`),
`~/.config/environment.d/key-router.conf` / `setx` / `launchctl` for `KEY_ROUTER_API_KEY`,
and for personal tools: `.key-router/tools.json` (tracked registry, `0600`),
`.key-router/runtime.json` (gitignored pid/token/port), `.key-router/logs/<id>.log` (start/setup
output), `.key-router/data/<id>/` (per-tool state, e.g. the Git tool's `state.json`). Installed
tool code lives in the repo's `tools/<id>/`.

---

## 7. Coding conventions

- **Simple, readable, modular.** Prefer the smallest change that is correct; no speculative
  abstractions, flags, or compatibility shims.
- **DRY, composition over copy-paste.** Shared logic goes in a shared base/utility
  (`provider/openai_base.py`, `proxy/image_utils.py`), never "provider A imports provider B".
- **Encapsulation.** Use accessors (`set_mapping`, `set_pending`, `set_session_id`) instead
  of mutating internal `_state` from outside.
- **No new `# type: ignore` / `# ty: ignore`.** Fix the type. Existing ones are legacy.
- **No dead code or hardcoded provider names.** Use settings/config where a value exists.
- **Backward compatibility on moves.** Re-export from the old location so existing imports
  and tests keep working.
- **Comments/docstrings explain *why*.** Existing modules document non-obvious protocol or
  security decisions; preserve that style. Do not narrate obvious code.
- **Frontend:** React 19 function components + hooks, Tailwind classes, no new runtime
  dependencies unless required. Keep `App.tsx` as the shell and put panels in their own file.
- **Git:** conventional commits with a scope, e.g. `feat(bridge): ...`, `fix(proxy): ...`.

---

## 8. Verification / definition of done

Before calling a change done:

1. `python -m pytest test/ -v` — all pass (no skipped-by-platform surprises).
2. If `webui/src` changed: `cd webui && npm run build` (runs `tsc -b`) and `npm run lint`.
3. If a request path changed: verify both **stream** and **non-stream**, and both
   Anthropic (`/v1/messages`) and OpenAI/Responses ingress when relevant.
4. If a provider changed: test against its real endpoint or a mocked adapter; check tools,
   images, thinking and truncation.
5. If the bridge changed: `python -m bridge` starts, `/health` is OK, and a turn still
   streams; ensure no orphan process remains after `/stop` or timeout.
6. If files moved: re-exports added; imports and tests still resolve.
7. Update `README.md` / `CHANGELOG.md` / this file when behavior or structure changes.

---

## 9. Recipes

**Add a provider (code):** read `provider/CUSTOM_PROVIDER_GUIDE.md`. Inherit
`OpenAIBaseProvider` (OpenAI-compatible) or `BaseProvider` (native protocol), implement
`translate_request`/`translate_response`/`stream`/`generate`, then register it in
`proxy/router.py` and add a test in `test/`.

**Add a management endpoint:** put it in `proxy/routers/management.py` (or a new router),
register the router in `proxy/app.py`, and expose it from `webui/src/App.tsx`.

**Add an agent field:** add it to `AgentSpec` (`bridge/config.py`), validate it in
`validate_agent`, map it to a CLI flag in `bridge/registry.py`, mirror it in
`proxy/routers/agents.py::AgentPayload`, and add it to `AgentModal.tsx` + `agents.example.json`.

**Change CLI flag handling:** only in `bridge/registry.py`; then update
`test/test_bridge_registry.py` and `README.md`.

**Add a personal tool:** create a repo with `tool.json` (`id`, `name`, `start`, optional `setup`,
`health`, `default_port`, `env`), a `/health` endpoint, and bind `127.0.0.1`. Publish it and install
by `owner/repo` (or register a local folder). If the tool needs a credential, declare
`"ENV_VAR": "tool:<name>"` and add the name/label in `proxy/tools_hub.py::TOOL_KEY_LABELS`. The
tool's own Settings UI saves it via `PUT /api/tools/<id>/credentials/<name>` with its `X-Tool-Token`
(see `tools/git-tool/git_tool/keyrouter.py`); key-router keeps it in `secrets.json::tool_keys` and
returns only masked previews to the dashboard. For persistent state, declare
`"TOOL_DATA": "tool:data"`; the hub creates `.key-router/data/<id>/` and passes it.
Do not import `proxy/` or `config/` from a tool.

**Bump the version (single source of truth is `APP_VERSION`):**
`webui/src/App.tsx` → `webui/package.json` + `webui/package-lock.json` (both top-level
`version` fields) → `README.md` badge and any `v0.1.x` references → new section in
`CHANGELOG.md` (Keep a Changelog, dated `YYYY-MM-DD`, Added/Fixed/Changed/Tested with).

---

## 10. Symptom → where to look

| Symptom | Look at |
| ------- | ------- |
| `No mapping found for model ...` | `config/model_map.py::resolve`, `config.json::model_mappings` |
| 400 on multi-turn tool calls / thinking | `provider/openai_base.py`, `provider/deepseekplatform/adapter.py` |
| Gemini empty response / 400 on tools | `provider/googleaistudio/adapter.py` (schema sanitizing, `thoughtSignature`) |
| Test lab run fails immediately | `proxy/testlab.py` (CLI in `PATH`, `allowed_roots` fence, image base64/size), `bridge/registry.py` flags |
| Tool will not start / stays "starting" | `proxy/tools_hub.py` (manifest `start`/`health`, port, env), `.key-router/logs/<id>.log` |
| Tool iframe shows 401 | session token mismatch — restart the tool; check `KEY_ROUTER_TOOL_TOKEN` handling in the tool |
| Codex "narrates" instead of running tools | `proxy/responses_ingress.py` (`looks_like_action_narration`, `_extract_text_tool_calls`), `proxy/routers/responses.py` |
| `Invalid discriminator value ... at messages.N.role` | `proxy/routers/anthropic.py::_fold_system_messages` (Claude Code sends `role: "system"` messages) |
| `Invalid input at thinking` | `proxy/routers/anthropic.py::_strip_thinking_display` (Claude Code sends `thinking.display`) |
| `content[].thinking ... must be passed back` | `proxy/responses_ingress.py` (thinking ↔ Responses `reasoning` items; attach to `tool_use`) |
| Truncated stream / max_tokens retry loops | `proxy/routers/anthropic.py`, `proxy/retry.py`, `_map_finish_reason` in adapters |
| Images dropped | `proxy/image_utils.py`, `proxy/*_ingress.py`, `bridge/app.py::_image_block` |
| IDE not detected/launched | `proxy/server_helpers.py`, `detect-ide.sh|.bat` |
| Bridge "Invalid agent configuration" | `bridge/config.py` validation, `bridge.log`, `proxy/routers/agents.py::_tail_log` |
| Bridge turn hangs / orphan process | `bridge/runner.py`, `agents.json::turn_timeout_sec` |
| Skills install fails | `config/skills.py` (`npx skills`, Node ≥ 22.20), `bridge/config.py::allowed_roots` |
| Dashboard blank | build `webui/dist`; check `proxy/app.py` static mount |
