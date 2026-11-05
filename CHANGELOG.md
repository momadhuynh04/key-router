# Changelog

All notable changes to **key-router**. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The version shown in the dashboard is `APP_VERSION` in `webui/src/App.tsx`; bump it together with the
matching section here. Releases before 0.1.32 are tracked in git history (`git log --oneline`).

## [Unreleased]

### Added

- **Personal tools (Tools tab)** — an optional hub for your own small apps. Tools are separate
  GitHub repos with a `tool.json` manifest (`start`, optional `setup`, `health`, `default_port`,
  `env`); install by `owner/repo` (or a local folder) into the repo's `tools/<id>/` root
  (safe tarball extract, 100 MB cap), start it on a free loopback port, and embed its UI in an
  iframe. Stop kills the whole process group and reaps the child; Uninstall removes the folder.
  Registry (`.key-router/tools.json`) is tracked; runtime/logs/state are gitignored. The old
  `~/.key-router/` tree is migrated on first use.
- **Tool credentials** — `secrets.json` gains a `tool_keys` section. A manifest declares them
  (`"GITHUB_TOKEN": "tool:github"`) and each tool configures them **in its own Settings UI**; reads and
  writes go through `/api/tools/<id>/credentials/<name>` and require that tool's per-start
  `X-Tool-Token`. The dashboard only ever sees masked previews.
- **Git workspace tool** (`tools/git-tool/`, standalone FastAPI + Vite) — GitHub token status,
  branch page (local above remote; every remote branch compared against the selected local branch or
  the default branch with ahead/behind, commit and file diffs both ways; a lane-drawn commit/merge
  graph; switching local branches), and pull requests/issues (list, detail, create, comment,
  open/close). Can be registered from a local folder while developing or installed from GitHub.
- **Git tool workflows** — a **Workflow** tab runs the collaborator loop with a step-by-step log:
  `checkout base → pull --ff-only → checkout -b <task>`; merge the base into a task branch
  (`checkout base → pull → checkout task → merge base`); stage-all + commit; push with upstream.
  Network commands authenticate with the tool's GitHub token through `GIT_CONFIG_*` env vars, so
  the token never appears in argv or in the repo config. Pull requests can be merged from the PR
  detail (merge commit / squash / rebase).
- **PR pages split by role** — pull request creation now lives at the end of the Workflow tab
  (no generic form). Everyone gets **My pull requests** (own PRs with state + GitHub link);
  owners also get the full **Pull requests** page with merge controls. Lists fetch `state=all`
  and filter locally so a merged PR stays visible under the "open" chip.
- **Issues & discussions** — the issues page is rebuilt around a segmented control: issues (state
  chips, labels, detail, comments, create, open/close) and repository discussions read through the
  GitHub GraphQL API (title, category, author, comment count, link). A missing `discussion` scope
  degrades to an inline message instead of a broken page.
- **One Sync button** — the repo header's refresh/fetch pair is merged into a single Sync that
  runs `git fetch --prune` and bumps a version every panel watches, so branches, PRs, issues and
  counts reload in place instead of after switching tabs. Tab counts are also fetched eagerly on
  repo selection.
- **Repo discovery** in the Git tool is token-first — every repo the token can see is listed with
  its role (`owner` / `collaborator` / `organization_member`) and `allowed_roots` are scanned
  (depth 3, pruned, capped) only to mark which of them have a local clone. Clones the token cannot
  reach are hidden; with no token the list falls back to the local scan. Remote-only repos show
  “not found on this machine (or outside allowed roots)”. Sidebar search, All/Local/Remote/Pinned
  filters and pinning; scan roots, depth and refresh live in the tool's Settings tab.
- **Persistent tool state** — `tool.json` can declare `"TOOL_DATA": "tool:data"`; the hub creates
  `.key-router/data/<id>/` and passes it. The Git tool stores its cache, pins and recent repos
  there (`state.json`).
- **Tool reinstall** — the registry records each tool's source repo (local installs read it from the
  git remote). If the install folder is deleted, the card shows **Reinstall** and downloads the
  package again from that repo; without a recorded repo it asks for `owner/repo` once.

### Changed

- **Shared process helpers** — `kill_tree` / `pid_alive` / `reap` moved to `proxy/process_utils.py`;
  `pid_alive` now treats zombies as dead so stopped bridge and tool processes do not linger.
- **Tool restart keeps the session token** — `restart` no longer invalidates an embedded iframe, and
  the dashboard picks up a changed `frame_url` without a manual reload.
- **Tool↔UI API version handshake** — `/health` and `/api/settings` report `api_version`; the Git tool
  UI shows an explicit “backend is outdated, restart it” banner instead of silently breaking when a
  new frontend runs against an old process.
- **Tool state moved into the repository** — the hub's registry, tool logs and per-tool data now
  live in `<repo>/.key-router/` (`tools.json`, `logs/<id>.log`, `data/<id>/`) instead of
  `~/.key-router/`. A one-time migration moves the legacy tree on first access; installed tool code
  stays in `<repo>/tools/<id>/`. `.key-router/tools.json` is **tracked** so a fresh clone knows each
  tool's source repo and can Install it; `.key-router/runtime.json` (pid/session token/port), logs
  and data are gitignored.
- **Tools page install states** — a tool whose folder is missing renders greyed out as
  **not installed** with an **Install** action (a corrupt install keeps **Reinstall**). The
  in-dashboard add-tool form was removed; tools are registered personally.

## [0.1.33] — 2026-11-04

### Added

- **Test lab (headless Claude Code / Codex)** — a new tab runs one agent turn in a chosen folder
  (inside `allowed_roots`) with optional image input and notes the result per check: image
  understanding, HTML file, OS/machine info, installed languages, Markdown review, and a ≤10 shell
  command budget. The model can be overridden with `provider/model` (e.g.
  `deepseekplatform/deepseek-flash`) which **bypasses Model routing** and calls that provider directly.
  Runs stream steps, show artifacts, can be stopped, and are kept in a 20-run history. Each run writes
  artifacts to `<workdir>/.key-router-testlab/<run-id>/`.
- **Settings page** — a dedicated **Settings** tab manages provider API keys for OpenRouter, DeepSeek,
  Google AI Studio and every custom provider, plus the built-in base URLs. Keys are written to
  `secrets.json` (gitignored, `0600`) and read on every provider instantiation, so saving applies on
  the next request **without restarting the proxy**. The dashboard only ever receives masked previews,
  and `secrets.json` is the only source for keys.
- **Providers page** — custom providers now have a dedicated **Providers** tab under **Model routing**
  with full CRUD: list cards, inline expand for base URL, API type, key status, models, headers and
  the routes that reference the provider, add/edit through the existing modal, and **Fetch models**
  from the provider's `/models` endpoint. Editing keeps the provider ID locked because routes
  reference it.
- **Browse for allowed roots** — the Safety fence in **Agents → Bridge settings** now has a
  **Browse folder** button that appends the picked folder, and a **Browse…** button on each row
  to replace it. It uses the same native folder picker as the launcher.
- **Provider API hint** — the Add custom provider modal now explains that `Anthropic` uses
  `/v1/messages` (only models that endpoint supports, e.g. use OpenAI Compatible for
  DeepSeek/OSS models), while `OpenAI Compatible` uses `/chat/completions`.

### Changed

- **Direct `provider/model` addressing** — every ingress now resolves a requested model written as
  `<builtin|custom-provider>/<model>` directly (e.g. `openrouter/anthropic/claude-sonnet-4.5`),
  bypassing `config.json` routes. The Test lab uses this for its model override.
- **API keys moved from `.env` to `secrets.json`** — `secrets.json` is now the only key source;
  `.env` only holds `HOST`/`PORT`. Custom providers store one key per provider ID instead of
  referencing an ENV name, the form takes the raw key, and an empty key field on edit keeps the
  existing key. Obsolete `api_key_env` values in `config.json` are ignored.
- **Custom-provider CLI takes the raw key** — `add-provider --api-key "sk-..."` now stores the key in
  `secrets.json`; `--api-key-env` was removed. Existing `api_key_env` fields in `config.json` are ignored.
- **Provider deletion is guarded** — `DELETE /api/custom-providers/{id}` now returns `409` with the
  offending routes while any `model_mappings` target still starts with `<id>/`. The dashboard
  disables **Delete** and names the routes to remove or repoint first. The old
  `+ Add custom provider` button on Model routing is now a **Manage providers →** link to the new tab.
- **Add custom provider validation** — when **Submit** is disabled, the modal now lists
  every blocking field (invalid ID, Base URL without `http(s)://`, missing model, …) instead
  of leaving the button greyed out silently. The Base URL hint shows which endpoint is
  called (`<base>/v1/messages` or `<base>/chat/completions`).
- **Agent guide rewritten** — `AGENTS.md` now documents the architecture, module map,
  invariants, testing rules, recipes and release checklist; `CLAUDE.md` imports it.

### Fixed

- **Codex parallel tool calls no longer 400 on Anthropic-native providers** — a Responses history can
  contain several consecutive `function_call` items. Translation used to emit one assistant message per
  call, so strict gateways (DeepSeek `/anthropic`) rejected the first `tool_use` with
  `tool_use ids were found without tool_result blocks immediately after`. Adjacent same-role messages
  are now merged, thinking blocks lead the assistant message, and every `tool_use` is guaranteed a
  matching `tool_result` in the next user message (a placeholder is inserted when an output is missing).
- **Test lab reports the real provider error** — when Claude Code or Codex exits non-zero, the run now
  surfaces the provider/stream error (for example an upstream 400) instead of the CLI's last stderr line.
- **Fetch models uses the stored key** — `GET /api/custom-providers/{id}/models` now resolves the same
  key source as live requests and sends `x-api-key` for Anthropic-native providers.
- **Claude Code `role: "system"` messages no longer break Anthropic-native providers** —
  Claude Code ≥ 2.1.3xx sends its Environment/context block as `messages[1].role = "system"`.
  Anthropic upstreams that only accept `user | assistant` rejected the whole request with
  `Invalid discriminator value. Expected 'user' | 'assistant' at messages.N.role`. The proxy
  now folds every system-role message into the top-level `system` prompt before translation,
  so native Anthropic providers, DeepSeek `/anthropic`, and the OpenAI-compatible path all
  keep the context.
- **`Invalid input at thinking` on follow-up requests** — Claude Code ≥ 2.1.3xx sends
  `thinking: {"type": "adaptive", "display": "updates"}`. The `display` beta hint makes
  strict Anthropic gateways reject the request with `Invalid input at thinking`, so the
  proxy now drops it (`proxy/routers/anthropic.py::_strip_thinking_display`). The thinking
  mode itself is preserved; only the rendering hint is removed.
- **Codex + Anthropic-native providers keep reasoning across tool turns** — Responses
  translation now emits a typed `reasoning` item for Anthropic thinking blocks instead of
  folding them into assistant text. Codex echoes the item back on the next turn, and the proxy
  re-attaches it as a `thinking` block before `tool_use`, so DeepSeek `/anthropic` no longer
  returns `The content[].thinking in the thinking mode must be passed back to the API`.
  Orphan reasoning is merged into the previous assistant message before the next user turn,
  and thinking-only assistant messages are dropped on the OpenAI-compatible path so no empty
  turns are sent upstream.
- **Project scope explains the `allowed_roots` fence** — `GET /api/skills` now returns
  `project.reason` (`outside_roots`, `no_roots`, `not_a_directory`, …) and
  `project.allowed_roots`. The Skills panel states why project scope is disabled, lists the
  configured roots, and points to **Agents → Bridge settings → Safety fence → Allowed roots**
  with a shortcut button. Backend error messages include the allowed roots and the fix.

## [0.1.32] — 2026-11-02

### Added

- **Skills manager (Launcher → Skills)** — Installed/Browse tabs, Claude Code/Codex split,
  Global/Project scope, real enable/disable via `<root>/.disabled/`, SKILL.md preview, and install
  from the skills.sh marketplace or a repo through `npx skills@1.7.0 --copy`. Project writes are
  fenced by `allowed_roots`; system, plugin and symlinked skills are read-only.
- **Unified Claude model route** — a single `claude` key now covers every Claude Code model
  (`claude-*`, opus/sonnet/haiku/fable). Existing `opus`/`sonnet`/`haiku` keys are consolidated on
  load; exact per-model keys still override the family route.
- **Proxy vision passthrough** — pasted images from Codex (`input_image`/`image_url`) and Claude Code
  (`image` blocks) survive request translation: OpenAI-compatible providers receive data-URL
  `image_url` parts, Gemini receives `inlineData`, and remote image URLs are preserved.
- **One-off sub-agents (`/call <id> <prompt>`, aliases `/sub` and `/run`)** — runs any configured agent
  in its own workdir without rebinding the chat. The sub-agent receives a transcript of the chat
  (last 80 messages, 24k chars), and the main agent receives a `[Sub-agent handoff]` block on its next
  turn containing the task, **all tool steps**, and the final result.
- **`ask_user` MCP tool** (`bridge/mcp_ask.py`) — agents can present a question plus numbered options.
  The turn stops there; the next user message is treated as the answer. Registered automatically through
  `--mcp-config` and pre-approved with `--allowedTools`, so no per-agent setup is needed.
- **Image attachments in the bridge** — OpenAI `image_url` / Responses `input_image` parts (base64 data
  URLs or remote URLs) are forwarded to Claude Code as image blocks. Image turns use
  `--input-format stream-json`; text-only turns keep the plain stdin path.
- **Model routing branding** — custom SVG key-router mark (key head + three routed branches), a
  full-bleed hero banner with the GitHub link and `v0.1.32`.
- **Dashboard setup guide** — Connect Open WebUI is now a 3-step guide with copyable Base URL, token, and
  `ENABLE_FORWARD_USER_INFO_HEADERS=True` snippets for Linux/macOS (`start.sh`), Windows (`start.bat`),
  PowerShell, and Docker Compose.
- **Unified command reference** — `/start`, `/call`, `/stop`, `/agents` with aliases and per-command copy
  buttons, plus a copy-all button and a compact Open WebUI preset block.
- **Redesigned Add/Edit agent modal** — Identity, Model & runtime, Permissions, and Advanced sections;
  sticky footer; wider layout; tool allow/deny rules are now a chip editor (pick a tool, add, remove)
  instead of free-form textareas.
- **Tested-with matrix** in the README (Arch Linux, Claude Code 2.1.304, Codex 0.149.0, Open WebUI 0.10.2).

### Fixed

- **Large image events no longer break a turn** — `asyncio.StreamReader.readline()` defaults to 64 KiB per
  line, but Claude Code emits a base64 image in one JSONL event. That surfaced as
  `ValueError: Separator is not found, and chunk exceed the limit` and was mislabelled "Invalid agent
  configuration". The bridge now uses a 64 MiB line limit and reports a clear `RunError` if a line exceeds it.
- **Config errors are labelled correctly** — added `AgentConfigError`; unrelated `ValueError`s no longer
  show up as "Invalid agent configuration".
- **`/call` works before `/start`** — pending questions and sub-agent handoffs now live in a chat-scoped
  meta section (`__meta__` in `state.json`), independent of the bound agent. Existing state files are migrated.
- **`restricted` + `bypass` is rejected** — the UI blocks it and the backend raises
  `restricted mode refuses permission 'bypass' — pick one`.
- **Bridge Base URL** falls back to the configured port when the bridge is stopped instead of showing `—`.
- **Stale docs** — test badge and project-structure count updated to 298 tests.
- **Windows CI** — `parse_source_and_skill` no longer assumes a POSIX `/tmp` path; the absolute-path
  guard is now cross-platform.

### Changed

- Sub-agent handoff now includes **every tool step** (tool + input preview), not just the first 15, and its
  header states that the runs are finished one-off processes, not live peers to message.
- Sub-agent prompts ask for a short final summary, since that is what the main agent receives.
- Agent permission hints clarify that headless runs auto-deny anything that would prompt; the
  "Restricted sandbox" toggle is now "Restricted mode — no command execution" with an explanation of
  `--restricted`.
- README reorganised with a Tested-with section and a changelog link.
- **Model routing UI** — the source dropdown now shows the `CLAUDE`/`CODEX` family routes plus only
  override keys that actually exist in `config.json`; stale OPUS/SONNET/HAIKU options are gone.
- **Launcher** — the Skills manager lives below the launch card; no new top-level tab.

### Tested with

- **Arch Linux**
- Claude Code **2.1.304**
- Codex **0.149.0**
- Open WebUI **0.10.2**
- `skills` CLI **1.7.0** (Node **26.5.1**) — marketplace install/toggle/remove
- `python -m pytest` — **298 passed**
