# Changelog

All notable changes to **key-router**. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The version shown in the dashboard is `APP_VERSION` in `webui/src/App.tsx`; bump it together with the
matching section here. Releases before 0.1.32 are tracked in git history (`git log --oneline`).

## [Unreleased]

_Nothing yet._

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
