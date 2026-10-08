import { useEffect, useState } from 'react'

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
  const bridgeUrl = status ? `http://127.0.0.1:${status.port}/v1` : '—'

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
        <p className="text-sm text-text-secondary leading-relaxed max-w-2xl mb-5">
          In Open WebUI: <b>Settings → Connections → OpenAI API</b>. The dropdown shows exactly one model,
          <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded mx-1">key-router-agents</code>.
          Pick the agent inside the chat with
          <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded mx-1">/start &lt;id&gt;</code>.
        </p>

        <div className="space-y-3">
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

        <div className="mt-5 rounded-xl border border-accent-border bg-accent-bg p-4">
          <p className="text-xs text-text-secondary leading-relaxed">
            <b className="text-accent">Required:</b> set <code className="font-mono bg-surface-850 border border-border px-1 py-0.5 rounded">ENABLE_FORWARD_USER_INFO_HEADERS=True</code> for
            Open WebUI. Without it, <code className="font-mono">X-OpenWebUI-Chat-Id</code> is never sent and every chat shares a single session.
          </p>
        </div>

        <div className="mt-5">
          <label className={labelCls}>Starter prompts (if Open WebUI swallows leading “/”)</label>
          <p className="text-xs text-text-muted mb-2">Create these under Workspace → Prompts, using the body as the prompt content:</p>
          <div className="rounded-xl border border-border bg-surface-850 divide-y divide-border">
            {[
              { cmd: '/agent-start', body: '/start {{id | text:placeholder="agent id":required}}' },
              { cmd: '/agent-stop', body: '/stop' },
              { cmd: '/agent-list', body: '/agents' },
            ].map(row => (
              <div key={row.cmd} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <code className="text-xs font-mono text-brand">{row.cmd}</code>
                <code className="text-xs font-mono text-text-secondary flex-1 truncate">{row.body}</code>
                <CopyButton value={row.body} label="Copy" />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* C — Bridge settings */}
      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary mb-2">Bridge settings</h2>
        <p className="text-sm text-text-secondary leading-relaxed max-w-2xl mb-5">
          <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">allowed_roots</code> is the safety fence:
          every agent workdir must sit inside one of these folders.
        </p>

        <div className="space-y-4">
          <div>
            <label className={labelCls}>Allowed roots</label>
            <div className="space-y-2">
              {roots.map((r, i) => (
                <div key={i} className="flex gap-2">
                  <input value={r} onChange={e => setRoots(rs => rs.map((x, j) => j === i ? e.target.value : x))} placeholder="/mnt/Data/ProjectStorage" className={inputCls} />
                  <button onClick={() => setRoots(rs => rs.filter((_, j) => j !== i))} className="text-text-muted hover:text-danger px-1">×</button>
                </div>
              ))}
              <button onClick={() => setRoots(rs => [...rs, ''])} className="text-sm text-brand hover:text-brand-muted">+ Add root</button>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className={labelCls}>Default agent</label>
              <select value={draft.default_agent} onChange={e => setDraft(d => ({ ...d, default_agent: e.target.value }))} className={inputCls}>
                <option value="">(none — /start required)</option>
                {agentIds.map(id => <option key={id} value={id}>{id}</option>)}
              </select>
            </div>
            <div>
              <label className={labelCls}>Concurrent turns</label>
              <input type="number" min={1} value={draft.max_concurrent_runs} onChange={e => setDraft(d => ({ ...d, max_concurrent_runs: Number(e.target.value) }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Turn timeout (seconds)</label>
              <input type="number" min={1} value={draft.turn_timeout_sec} onChange={e => setDraft(d => ({ ...d, turn_timeout_sec: Number(e.target.value) }))} className={inputCls} />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className={labelCls}>Port</label>
              <input type="number" min={1} max={65535} value={draft.port} onChange={e => setDraft(d => ({ ...d, port: Number(e.target.value) }))} className={inputCls} />
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input type="checkbox" checked={draft.allow_bypass} onChange={e => setDraft(d => ({ ...d, allow_bypass: e.target.checked }))} className="rounded" />
            Allow the <code className="font-mono text-xs">bypass</code> permission (skips every permission check)
          </label>

          <div className="flex items-center gap-3">
            <button onClick={saveSettings} disabled={busy} className="h-9 rounded-full bg-brand hover:bg-brand-muted text-white text-sm font-medium px-6 transition-colors disabled:opacity-40">Save settings</button>
            {saved && <span className="text-xs text-success">Saved</span>}
            {running && <span className="text-xs text-text-muted">Changing the port requires Stop, then Start again.</span>}
          </div>
        </div>
      </div>
    </div>
  )
}
