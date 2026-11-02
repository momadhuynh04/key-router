import { useEffect, useState, type ReactNode } from 'react'

export type BridgeSettings = {
  host: string
  port: number
  token: string
  key_router_url: string
  allowed_roots: string[]
  default_agent: string | null
  max_concurrent_runs: number
  turn_timeout_sec: number
  allow_bypass: boolean
}

type Status = {
  running: boolean
  healthy: boolean
  pid: number | null
  port: number
  uptime_sec: number | null
  url: string
}

const inputCls = "w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
const labelCls = "block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5"
const labelInlineCls = "text-[11px] font-medium tracking-widest uppercase text-text-muted"
const hintCls = "text-xs text-text-muted mt-1.5 leading-relaxed"
const groupCls = "text-[11px] font-semibold tracking-widest uppercase text-text-secondary mb-2"

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      {children}
      {hint && <p className={hintCls}>{hint}</p>}
    </div>
  )
}

function StepBadge({ n }: { n: number }) {
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border bg-surface-850 text-[11px] font-semibold text-text-secondary">
      {n}
    </span>
  )
}

function Snippet({ title, value }: { title: string; value: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface-850">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <span className="text-[10px] font-medium tracking-widest uppercase text-text-muted">{title}</span>
        <CopyButton value={value} label="Copy" />
      </div>
      <pre className="whitespace-pre-wrap break-words px-3 py-2.5 text-[11px] font-mono leading-relaxed text-text-secondary">{value}</pre>
    </div>
  )
}

const CHAT_COMMANDS = [
  {
    cmd: '/start <id> [prompt]',
    aliases: '',
    what: 'Bind an agent to this chat and open its session. Add a prompt to run it immediately.',
  },
  {
    cmd: '/call <id> <prompt>',
    aliases: '/sub · /run',
    what: 'Run a one-off sub-agent on this chat’s history without changing the bound agent. Its steps and result are handed to the main agent next turn.',
  },
  {
    cmd: '/stop',
    aliases: '',
    what: 'Kill the running turn and unbind the agent from this chat.',
  },
  {
    cmd: '/agents',
    aliases: '/list · /help',
    what: 'List every configured agent with its model, permission, and workdir.',
  },
]

const CHAT_COMMANDS_TEXT = CHAT_COMMANDS.map(c => c.cmd).join('\n')

const FORWARD_HEADER_ENV = 'ENABLE_FORWARD_USER_INFO_HEADERS=True'
const FORWARD_HEADER_SH = `export ${FORWARD_HEADER_ENV}\nopen-webui serve`
const FORWARD_HEADER_BAT = `set ${FORWARD_HEADER_ENV}\nopen-webui serve`
const FORWARD_HEADER_PS = `$env:ENABLE_FORWARD_USER_INFO_HEADERS="True"\nopen-webui serve`
const FORWARD_HEADER_COMPOSE = `environment:\n  - ${FORWARD_HEADER_ENV}`

const PRESET_BODIES = [
  '/start {{id | text:placeholder="agent id":required}}',
  '/call {{id | text:placeholder="agent id":required}} {{task | text:placeholder="task":required}}',
  '/stop',
  '/agents',
].join('\n')

function fmtUptime(sec: number | null) {
  if (sec == null) return '—'
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value)
          setCopied(true)
          setTimeout(() => setCopied(false), 1200)
        } catch { /* clipboard blocked */ }
      }}
      className="h-8 shrink-0 rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-xs font-medium px-3 transition-colors"
    >
      {copied ? 'Copied' : label}
    </button>
  )
}

export default function BridgePanel({ settings, agentIds, onSettingsChanged, onStatusChange }: {
  settings: BridgeSettings | null
  agentIds: string[]
  onSettingsChanged: () => void
  onStatusChange: (running: boolean) => void
}) {
  const [status, setStatus] = useState<Status | null>(null)
  const [logs, setLogs] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [showToken, setShowToken] = useState(false)
  const [roots, setRoots] = useState<string[]>([])
  const [draft, setDraft] = useState({ default_agent: '', max_concurrent_runs: 2, turn_timeout_sec: 1800, allow_bypass: false, port: 8083 })
  const [saved, setSaved] = useState(false)

  const fetchStatus = async () => {
    try {
      const res = await fetch('/api/bridge/status')
      const data: Status = await res.json()
      setStatus(data)
      onStatusChange(data.running)
    } catch { /* bridge not reachable yet */ }
  }

  const fetchLogs = async () => {
    try {
      const res = await fetch('/api/bridge/logs?lines=80')
      const data = await res.json()
      setLogs(data.lines || '')
    } catch { /* ignore */ }
  }

  useEffect(() => {
    fetchStatus()
    fetchLogs()
    const t = setInterval(fetchStatus, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!settings) return
    setRoots(settings.allowed_roots || [])
    setDraft({
      default_agent: settings.default_agent || '',
      max_concurrent_runs: settings.max_concurrent_runs,
      turn_timeout_sec: settings.turn_timeout_sec,
      allow_bypass: settings.allow_bypass,
      port: settings.port,
    })
  }, [settings])

  const act = async (path: string) => {
    setBusy(true); setError("")
    try {
      const res = await fetch(path, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`)
      await fetchStatus()
      await fetchLogs()
    } catch (e: any) {
      setError(e.message || String(e))
      await fetchLogs()
    } finally {
      setBusy(false)
    }
  }

  const saveSettings = async () => {
    setBusy(true); setError(""); setSaved(false)
    try {
      const res = await fetch('/api/agents/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...draft, default_agent: draft.default_agent || null, allowed_roots: roots.map(r => r.trim()).filter(Boolean) }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`)
      setSaved(true)
      setTimeout(() => setSaved(false), 1500)
      onSettingsChanged()
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const regenerateToken = async () => {
    if (!confirm('Regenerating the token disconnects Open WebUI until you paste the new one. Continue?')) return
    setBusy(true); setError("")
    try {
      const res = await fetch('/api/agents/token', { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      onSettingsChanged()
      setShowToken(true)
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const running = status?.running ?? false
  const bridgePort = status?.port ?? settings?.port
  const bridgeUrl = bridgePort ? `http://127.0.0.1:${bridgePort}/v1` : '—'

  return (
    <div className="space-y-6">
      {/* A — Bridge control */}
      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-3">
          <div className="flex items-center gap-3">
            <span className={`w-2.5 h-2.5 rounded-full ${running ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
            <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Bridge</h2>
            <span className="text-xs text-text-muted">
              {running ? `running · PID ${status?.pid} · ${fmtUptime(status?.uptime_sec ?? null)}` : 'stopped'}
            </span>
            {running && !status?.healthy && <span className="text-xs text-danger">· /health not responding</span>}
          </div>
          <div className="flex gap-2">
            <button onClick={() => { fetchStatus(); fetchLogs() }} className="h-9 rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-sm font-medium px-4 transition-colors">Refresh</button>
            {running ? (
              <button onClick={() => act('/api/bridge/stop')} disabled={busy} className="h-9 rounded-full bg-danger/90 hover:bg-danger text-white text-sm font-medium px-5 transition-colors disabled:opacity-40">Stop bridge</button>
            ) : (
              <button onClick={() => act('/api/bridge/start')} disabled={busy} className="h-9 rounded-full bg-brand hover:bg-brand-muted text-white text-sm font-medium px-5 transition-colors disabled:opacity-40">{busy ? 'Starting…' : 'Start bridge'}</button>
            )}
          </div>
        </div>

        <p className="text-xs text-text-muted mb-4 leading-relaxed">
          The dashboard starts the bridge as a background process and tracks it by PID. If you launch
          <code className="font-mono bg-surface-850 border border-border px-1 py-0.5 rounded mx-1">python -m bridge</code>
          yourself, this panel cannot see it — use the Start button here, or run it in a terminal and ignore this panel.
        </p>

        {error && <div className="text-sm text-danger rounded-lg bg-danger/10 border border-danger/20 p-3 mb-4 whitespace-pre-wrap break-words">{error}</div>}

        <div className="rounded-xl border border-border bg-surface-850 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 border-b border-border">
            <span className="text-[11px] font-medium tracking-widest uppercase text-text-muted">bridge.log</span>
          </div>
          <pre className="px-4 py-3 text-xs font-mono text-text-secondary overflow-auto max-h-64 whitespace-pre-wrap break-words">{logs || '(empty)'}</pre>
        </div>
      </div>

      {/* B — Connect Open WebUI */}
      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary mb-2">Connect Open WebUI</h2>
        <p className="text-sm text-text-secondary leading-relaxed max-w-2xl">
          Point Open WebUI at this bridge once, then pick an agent inside any chat.
        </p>

        <ol className="mt-5 space-y-5">
          <li className="flex gap-3">
            <StepBadge n={1} />
            <div className="min-w-0 flex-1 space-y-3">
              <div>
                <p className="text-sm text-text-secondary"><b>Add an OpenAI connection.</b></p>
                <p className={hintCls}>Open WebUI → Settings → Connections → OpenAI API → Add connection.</p>
              </div>
              <div>
                <label className={labelCls}>Base URL</label>
                <div className="flex gap-2">
                  <input readOnly value={bridgeUrl} className={inputCls} />
                  <CopyButton value={bridgeUrl} label="Copy" />
                </div>
              </div>
              <div>
                <label className={labelCls}>API key (bridge token)</label>
                <div className="flex gap-2">
                  <input readOnly value={settings ? (showToken ? settings.token : '•'.repeat(Math.min(48, settings.token.length))) : ''} className={inputCls} />
                  <button onClick={() => setShowToken(v => !v)} className="h-8 shrink-0 rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-xs font-medium px-3 transition-colors">{showToken ? 'Hide' : 'Show'}</button>
                  {settings && <CopyButton value={settings.token} label="Copy" />}
                </div>
                <button onClick={regenerateToken} disabled={busy} className="text-xs text-text-muted hover:text-danger mt-2 transition-colors">Regenerate token…</button>
              </div>
            </div>
          </li>

          <li className="flex gap-3">
            <StepBadge n={2} />
            <div className="min-w-0 flex-1">
              <p className="text-sm text-text-secondary">
                <b>Forward the chat id.</b> Set this before Open WebUI starts, then restart it.
                If your launcher already exports it, you are done.
              </p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <Snippet title="Linux / macOS · start.sh" value={FORWARD_HEADER_SH} />
                <Snippet title="Windows · start.bat (CMD)" value={FORWARD_HEADER_BAT} />
                <Snippet title="Windows · PowerShell" value={FORWARD_HEADER_PS} />
                <Snippet title="Docker · compose" value={FORWARD_HEADER_COMPOSE} />
              </div>
              <p className={hintCls}>
                <b className="text-accent">Required.</b> Without it <code className="font-mono">X-OpenWebUI-Chat-Id</code> is never sent and every chat shares one session.
              </p>
            </div>
          </li>

          <li className="flex gap-3">
            <StepBadge n={3} />
            <div className="min-w-0 flex-1">
              <p className="text-sm text-text-secondary"><b>Select the model and bind an agent.</b></p>
              <p className={hintCls}>
                The model dropdown shows exactly one entry,
                <code className="mx-1 font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">key-router-agents</code>.
                Then send a command as the first line of a chat message:
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <code className="rounded-lg border border-border bg-surface-850 px-3 py-1.5 text-xs font-mono text-brand">/start &lt;id&gt;</code>
                <CopyButton value="/start <id>" label="Copy" />
                <span className="text-xs text-text-muted">or <code className="font-mono">/agents</code> to list ids.</span>
              </div>
            </div>
          </li>
        </ol>

        <div className="mt-7">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className={labelInlineCls}>Commands in chat</span>
            <CopyButton value={CHAT_COMMANDS_TEXT} label="Copy all" />
          </div>
          <p className={hintCls + ' mb-2'}>
            A command must be the <b>first line</b> of the message. A heading, quote, list marker, or leading space before it turns the whole message into a normal prompt.
          </p>
          <div className="rounded-xl border border-border bg-surface-850 divide-y divide-border">
            {CHAT_COMMANDS.map(row => (
              <div key={row.cmd} className="flex items-start justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="text-xs font-mono text-brand">{row.cmd}</code>
                    {row.aliases && (
                      <span className="rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wider text-text-muted">
                        alias {row.aliases}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-text-muted">{row.what}</p>
                </div>
                <CopyButton value={row.cmd} label="Copy" />
              </div>
            ))}
          </div>
          <p className={hintCls}>
            Images pasted into the message are forwarded to the CLI as image blocks, so the agent can actually see them.
          </p>
          <p className={hintCls}>
            Agents can also ask you a question with numbered options using the built-in
            <code className="font-mono mx-1">ask_user</code> tool. The turn stops there — your next message is the answer.
          </p>
        </div>

        <div className="mt-5 rounded-xl border border-border bg-surface-850 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-medium text-text-secondary">Open WebUI strips a leading “/”?</p>
              <p className={hintCls}>Create these under Workspace → Prompts. Each body is the prompt content.</p>
            </div>
            <CopyButton value={PRESET_BODIES} label="Copy bodies" />
          </div>
          <pre className="mt-3 whitespace-pre-wrap break-words text-[11px] font-mono leading-relaxed text-text-muted">{PRESET_BODIES}</pre>
        </div>
      </div>

      {/* C — Bridge settings */}
      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary mb-2">Bridge settings</h2>
        <p className="text-sm text-text-secondary leading-relaxed max-w-2xl mb-6">
          Stored in <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">agents.json</code> and
          re-read on every request, so agents pick up changes without a restart. <b>Port is the exception</b> — stop the bridge,
          change it, then start again.
        </p>

        <div className="space-y-6">
          <div>
            <h3 className={groupCls}>Safety fence</h3>
            <p className={hintCls + ' mb-3'}>
              Every agent workdir must resolve inside one of these folders. Symlinks are resolved before the check,
              so a link cannot be used to escape a root. An empty list disables every agent.
            </p>
            <div className="space-y-2">
              {roots.map((r, i) => (
                <div key={i} className="flex gap-2">
                  <input value={r} onChange={e => setRoots(rs => rs.map((x, j) => j === i ? e.target.value : x))} placeholder="/mnt/Data/ProjectStorage" className={inputCls} />
                  <button onClick={() => setRoots(rs => rs.filter((_, j) => j !== i))} aria-label="Remove root" className="text-text-muted hover:text-danger px-2 text-lg">×</button>
                </div>
              ))}
              <button onClick={() => setRoots(rs => [...rs, ''])} className="text-sm text-brand hover:text-brand-muted">+ Add root</button>
            </div>
            <p className={hintCls}>Example: <code className="font-mono">/mnt/Data/ProjectStorage</code> allows every project below it.</p>
          </div>

          <div>
            <h3 className={groupCls}>Defaults &amp; limits</h3>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <Field
                label="Default agent"
                hint="Used when a chat sends a prompt without /start. Empty means every chat must run /start first."
              >
                <select value={draft.default_agent} onChange={e => setDraft(d => ({ ...d, default_agent: e.target.value }))} className={inputCls}>
                  <option value="">(none — /start required)</option>
                  {agentIds.map(id => <option key={id} value={id}>{id}</option>)}
                </select>
              </Field>
              <Field
                label="Concurrent turns"
                hint="How many CLI processes may run at once across all chats. Extra turns are refused with a notice, not queued."
              >
                <input type="number" min={1} value={draft.max_concurrent_runs} onChange={e => setDraft(d => ({ ...d, max_concurrent_runs: Number(e.target.value) }))} className={inputCls} />
              </Field>
              <Field
                label="Turn timeout (seconds)"
                hint="A turn still running after this long is killed, including its child processes. 1800 = 30 minutes."
              >
                <input type="number" min={1} value={draft.turn_timeout_sec} onChange={e => setDraft(d => ({ ...d, turn_timeout_sec: Number(e.target.value) }))} className={inputCls} />
              </Field>
            </div>
          </div>

          <div>
            <h3 className={groupCls}>Connection</h3>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <Field
                label="Port"
                hint="Where the bridge listens for Open WebUI. Changing it requires Stop, then Start, and updating the Base URL in Open WebUI."
              >
                <input type="number" min={1} max={65535} value={draft.port} onChange={e => setDraft(d => ({ ...d, port: Number(e.target.value) }))} className={inputCls} />
              </Field>
            </div>
          </div>

          <div className="rounded-xl border border-danger/20 bg-danger/5 p-4">
            <h3 className={groupCls + ' text-danger'}>Danger zone</h3>
            <label className="flex items-start gap-2 text-sm text-text-secondary cursor-pointer">
              <input type="checkbox" checked={draft.allow_bypass} onChange={e => setDraft(d => ({ ...d, allow_bypass: e.target.checked }))} className="rounded mt-0.5" />
              <span>
                Allow the <code className="font-mono text-xs">bypass</code> permission
                <span className={hintCls + ' block'}>
                  An agent set to bypass skips every permission check, including shell commands and writes outside the workdir.
                  Only enable it inside a container or VM.
                </span>
              </span>
            </label>
          </div>

          <div className="flex items-center gap-3">
            <button onClick={saveSettings} disabled={busy} className="h-9 rounded-full bg-brand hover:bg-brand-muted text-white text-sm font-medium px-6 transition-colors disabled:opacity-40">Save settings</button>
            {saved && <span className="text-xs text-success">Saved</span>}
            {running && <span className="text-xs text-text-muted">Port changes require Stop, then Start.</span>}
          </div>
        </div>
      </div>
    </div>
  )
}
