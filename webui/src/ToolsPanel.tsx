import { useEffect, useMemo, useState } from 'react'

type InstalledTool = {
  id: string
  name: string
  version: string
  description: string
  install_dir: string
  installed: boolean
  source: { type?: string; repo?: string; branch?: string; path?: string }
  port: number
  health: string
  has_setup: boolean
  running: boolean
  healthy: boolean
  pid: number | null
  url: string
  frame_url: string
  installed_at: string
  last_started_at: string
  error: string
}

type ToolsData = {
  installed: InstalledTool[]
  tools_root: string
}

type StatusFilter = 'all' | 'running' | 'stopped'

const btnCls = "h-8 rounded-full border border-border bg-surface-850 px-3 text-xs font-medium text-text-secondary transition-colors hover:bg-surface-800 disabled:cursor-not-allowed disabled:opacity-40"
const btnPrimaryCls = "h-8 rounded-full bg-brand px-4 text-xs font-medium text-white transition-colors hover:bg-brand-muted disabled:cursor-not-allowed disabled:opacity-40"

const GRADIENTS = [
  'from-indigo-500 to-violet-600',
  'from-emerald-500 to-teal-600',
  'from-sky-500 to-blue-600',
  'from-amber-500 to-orange-600',
  'from-rose-500 to-pink-600',
  'from-fuchsia-500 to-purple-600',
]

function gradientFor(id: string): string {
  let hash = 0
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) % 9973
  return GRADIENTS[hash % GRADIENTS.length]
}

function initialsFor(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return words[0].slice(1, 3).toUpperCase() || words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

function statusOf(tool: InstalledTool): { text: string; dot: string; pill: string } {
  if (tool.installed === false) return { text: 'not installed', dot: 'bg-text-muted', pill: 'border-border bg-surface-850 text-text-muted' }
  if (tool.error) return { text: 'broken', dot: 'bg-danger', pill: 'border-danger/30 bg-danger/10 text-danger' }
  if (tool.healthy) return { text: 'running', dot: 'bg-success animate-pulse', pill: 'border-success/30 bg-success/10 text-success' }
  if (tool.running) return { text: 'starting', dot: 'bg-accent animate-pulse', pill: 'border-accent-border bg-accent-bg text-accent' }
  return { text: 'stopped', dot: 'bg-text-muted', pill: 'border-border bg-surface-850 text-text-muted' }
}

function IconButton({ label, onClick, disabled, danger, children }: {
  label: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-surface-850 text-text-muted transition-colors hover:bg-surface-800 hover:text-text-secondary disabled:cursor-not-allowed disabled:opacity-40 ${danger ? 'hover:text-danger' : ''}`}
    >
      {children}
    </button>
  )
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-20" onMouseDown={onClose}>
      <div
        className="w-full max-w-xl rounded-2xl border border-border bg-surface-900 p-5 shadow-2xl"
        onMouseDown={event => event.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">{title}</h3>
          <button onClick={onClose} aria-label="Close" className="flex h-8 w-8 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-surface-850 hover:text-text-secondary">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function ToolRow({ tool, busy, onOpen, onStart, onStop, onRestart, onSetup, onLogs, onUninstall, onReinstall }: {
  tool: InstalledTool
  busy: string | null
  onOpen: () => void
  onStart: () => void
  onStop: () => void
  onRestart: () => void
  onSetup: () => void
  onLogs: () => void
  onUninstall: () => void
  onReinstall: () => void
}) {
  const status = statusOf(tool)
  const sourceLabel = tool.source?.repo || tool.source?.path || 'local folder'
  const missing = tool.installed === false
  const broken = !missing && Boolean(tool.error)

  return (
    <article className={`group flex flex-col gap-4 rounded-2xl border bg-surface-900 p-4 transition-all hover:bg-surface-850 lg:grid lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center lg:gap-6 lg:px-5 lg:py-4 ${missing ? 'opacity-70 hover:opacity-100' : ''} ${tool.running ? 'border-brand-border' : 'border-border'}`}>
      <div className="flex min-w-0 items-start gap-3.5">
        <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${gradientFor(tool.id)} text-sm font-bold tracking-wide text-white shadow-inner ${missing ? 'opacity-50 grayscale' : ''}`}>
          {initialsFor(tool.name)}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 className="truncate text-[15px] font-semibold text-text-primary">{tool.name}</h3>
            {tool.version && <span className="shrink-0 rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wider text-text-muted">{tool.version}</span>}
          </div>

          <p className="mt-1 line-clamp-1 text-[13px] leading-relaxed text-text-secondary">
            {tool.description || 'No description.'}
          </p>

          <div className="mt-2 flex min-w-0 items-center gap-2 text-[11px] text-text-muted">
            <span className="shrink-0 font-mono">{tool.id}</span>
            <span className="text-border">·</span>
            <span className="min-w-0 truncate font-mono" title={tool.install_dir}>{sourceLabel}</span>
            {missing && <span className="min-w-0 truncate">not installed on this machine</span>}
            {broken && <span className="min-w-0 truncate text-danger" title={tool.error}>{tool.error}</span>}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4 sm:gap-6 lg:justify-end">
        <div className="hidden items-center gap-6 sm:flex">
          <div className="min-w-[72px]">
            <p className="text-[10px] font-medium uppercase tracking-widest text-text-muted">Status</p>
            <span className={`mt-1 inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${status.pill}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
              {status.text}
            </span>
          </div>
          <div className="min-w-[64px]">
            <p className="text-[10px] font-medium uppercase tracking-widest text-text-muted">Port</p>
            <p className="mt-1 font-mono text-xs text-text-secondary">{tool.port > 0 ? `:${tool.port}` : '—'}</p>
          </div>
          <div className="min-w-[56px]">
            <p className="text-[10px] font-medium uppercase tracking-widest text-text-muted">Source</p>
            <p className="mt-1 text-xs capitalize text-text-secondary">{tool.source?.type || 'local'}</p>
          </div>
        </div>

        <div className="flex items-center gap-2 sm:border-l sm:border-border sm:pl-5">
        {missing || broken ? (
          <button onClick={onReinstall} disabled={busy !== null} className={btnPrimaryCls}>
            {busy === `reinstall:${tool.id}`
              ? (missing ? 'Installing…' : 'Reinstalling…')
              : (missing ? 'Install' : 'Reinstall')}
          </button>
        ) : tool.running ? (
          <>
            <button onClick={onOpen} disabled={!tool.frame_url} className={btnPrimaryCls}>Open</button>
            <button onClick={onStop} disabled={busy !== null} className={btnCls}>Stop</button>
          </>
        ) : (
          <button onClick={onStart} disabled={busy !== null} className={btnPrimaryCls}>
            {busy === `start:${tool.id}` ? 'Starting…' : 'Start'}
          </button>
        )}

        <div className="flex items-center gap-1">
          <IconButton label="Restart" onClick={onRestart} disabled={busy !== null || missing || broken}>
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
          </IconButton>
          {tool.has_setup && (
            <IconButton label="Run setup" onClick={onSetup} disabled={busy !== null || tool.running || missing || broken}>
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17L17.25 21A2.65 2.65 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" /></svg>
            </IconButton>
          )}
          <IconButton label="Logs" onClick={onLogs}>
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z" /></svg>
          </IconButton>
          <IconButton label="Uninstall" onClick={onUninstall} disabled={busy !== null} danger>
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673A2.25 2.25 0 0115.916 21.75H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" /></svg>
          </IconButton>
        </div>
        </div>
      </div>
    </article>
  )
}

export default function ToolsPanel() {
  const [data, setData] = useState<ToolsData | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [logs, setLogs] = useState<{ id: string; text: string } | null>(null)
  const [openTool, setOpenTool] = useState<InstalledTool | null>(null)
  const [reinstallTarget, setReinstallTarget] = useState<InstalledTool | null>(null)
  const [reinstallRepo, setReinstallRepo] = useState('')
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  const load = async (path = '/api/tools') => {
    try {
      const res = await fetch(path)
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(payload.detail || `Request failed (${res.status})`)
      setData(payload as ToolsData)
    } catch (e: any) {
      setError(e.message || String(e))
    }
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    const timer = setInterval(() => { load().catch(() => {}) }, 10000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    setOpenTool(current => {
      if (!current) return current
      const latest = data?.installed.find(t => t.id === current.id)
      return latest && (latest.running !== current.running || latest.frame_url !== current.frame_url)
        ? latest
        : current
    })
  }, [data])

  const act = async (label: string, path: string, method: string, body: unknown, success: string) => {
    setBusy(label)
    setError('')
    setNotice('')
    try {
      const res = await fetch(path, {
        method,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        const detail = payload.detail
        throw new Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `Request failed (${res.status})`)
      }
      setNotice(success)
      await load()
      return payload
    } catch (e: any) {
      setError(e.message || String(e))
      return null
    } finally {
      setBusy(null)
    }
  }

  const start = async (tool: InstalledTool) => {
    const payload = await act(`start:${tool.id}`, `/api/tools/${tool.id}/start`, 'POST', {}, `${tool.name} started.`)
    if (payload && payload.frame_url) setOpenTool(payload)
  }
  const stop = (tool: InstalledTool) => act(`stop:${tool.id}`, `/api/tools/${tool.id}/stop`, 'POST', {}, `${tool.name} stopped.`)
  const restart = async (tool: InstalledTool) => {
    const payload = await act(`restart:${tool.id}`, `/api/tools/${tool.id}/restart`, 'POST', {}, `${tool.name} restarted.`)
    if (payload?.frame_url) {
      setOpenTool(current => (current && current.id === tool.id ? payload : current))
    }
  }
  const setup = (tool: InstalledTool) => act(`setup:${tool.id}`, `/api/tools/${tool.id}/setup`, 'POST', {}, `${tool.name} setup finished.`)
  const uninstall = async (tool: InstalledTool) => {
    if (!confirm(`Uninstall "${tool.name}"? The tool process stops and its folder is removed.`)) return
    await act(`uninstall:${tool.id}`, `/api/tools/${tool.id}`, 'DELETE', undefined, `${tool.name} uninstalled.`)
  }

  const runReinstall = async (tool: InstalledTool, repo = '') => {
    const action = tool.installed === false ? 'installed' : 'reinstalled'
    const payload = await act(
      `reinstall:${tool.id}`,
      `/api/tools/${tool.id}/reinstall`,
      'POST',
      { repo, branch: '' },
      `${tool.name} ${action}.`,
    )
    if (payload) {
      setReinstallTarget(null)
      setReinstallRepo('')
      if (openTool?.id === tool.id) setOpenTool(null)
    }
  }

  const requestReinstall = (tool: InstalledTool) => {
    if (tool.source?.repo) {
      runReinstall(tool)
    } else {
      setReinstallTarget(tool)
      setReinstallRepo('')
    }
  }

  const showLogs = async (id: string) => {
    try {
      const res = await fetch(`/api/tools/${id}/logs?lines=150`)
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(payload.detail || `Request failed (${res.status})`)
      setLogs({ id, text: payload.lines || '(empty)' })
    } catch (e: any) {
      setError(e.message || String(e))
    }
  }

  const runningCount = data?.installed.filter(tool => tool.running).length || 0
  const stoppedCount = (data?.installed.length || 0) - runningCount

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return [...(data?.installed || [])]
      .sort((a, b) => {
        if (a.running !== b.running) return a.running ? -1 : 1
        return a.name.localeCompare(b.name)
      })
      .filter(tool => {
        if (statusFilter === 'running' && !tool.running) return false
        if (statusFilter === 'stopped' && tool.running) return false
        if (!needle) return true
        return [tool.name, tool.id, tool.description, tool.source?.repo, tool.source?.path]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(needle)
      })
  }, [data, query, statusFilter])

  if (!data) {
    return (
      <div className="rounded-2xl border border-border bg-surface-900 p-6 text-sm text-text-muted">
        {error || 'Loading tools…'}
      </div>
    )
  }

  if (openTool) {
    const status = statusOf(openTool)
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-surface-900 px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <button onClick={() => setOpenTool(null)} className={btnCls}>← All tools</button>
            <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${gradientFor(openTool.id)} text-xs font-bold text-white`}>
              {initialsFor(openTool.name)}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-text-primary">{openTool.name}</p>
              <p className="text-[11px] text-text-muted">
                <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${status.dot}`} />
                {status.text} · 127.0.0.1:{openTool.port}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => restart(openTool)} disabled={busy !== null} className={btnCls}>Restart</button>
            <a href={openTool.frame_url || openTool.url} target="_blank" rel="noopener noreferrer" className={btnCls}>Open in new tab ↗</a>
          </div>
        </div>
        {!openTool.running && (
          <div className="rounded-lg border border-danger/20 bg-danger/10 p-3 text-sm text-danger">
            This tool is not running. Go back and press Start.
          </div>
        )}
        <iframe
          src={openTool.frame_url || openTool.url}
          title={openTool.name}
          className="h-[calc(100vh-190px)] w-full rounded-2xl border border-border bg-surface-900"
        />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Personal tools</h2>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-text-secondary">
          Small apps that run locally and open inside the dashboard. Each tool has its own settings and credentials,
          stored by key-router in <code className="rounded border border-border bg-surface-850 px-1.5 py-0.5 font-mono text-xs">secrets.json</code>.
        </p>
        <p className="mt-2 truncate font-mono text-[11px] text-text-muted" title={data.tools_root}>{data.tools_root}</p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
          <svg className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path strokeLinecap="round" d="M20 20l-3.5-3.5" />
          </svg>
          <input
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="Search tools…"
            aria-label="Search tools"
            className="h-9 w-full rounded-full border border-border bg-surface-900 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
          />
        </div>

        <div className="inline-flex gap-1 rounded-full border border-border bg-surface-850 p-1">
          {([
            ['all', 'All', data.installed.length],
            ['running', 'Running', runningCount],
            ['stopped', 'Stopped', stoppedCount],
          ] as [StatusFilter, string, number][]).map(([id, label, count]) => (
            <button
              key={id}
              onClick={() => setStatusFilter(id)}
              className={`h-7 rounded-full px-3 text-xs font-medium transition-colors ${statusFilter === id ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}
            >
              {label} <span className="opacity-60">{count}</span>
            </button>
          ))}
        </div>
      </div>

      {data.installed.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-surface-900 px-6 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-surface-850 text-text-muted">
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085" /></svg>
          </div>
          <div>
            <p className="text-sm font-medium text-text-primary">No tools installed yet</p>
            <p className="mt-1 text-xs text-text-muted">Registered tools show up here.</p>
          </div>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-surface-900 px-6 py-14 text-center text-sm text-text-muted">
          No tools match this filter.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {visible.map(tool => (
            <ToolRow
              key={tool.id}
              tool={tool}
              busy={busy}
              onOpen={() => setOpenTool(tool)}
              onStart={() => start(tool)}
              onStop={() => stop(tool)}
              onRestart={() => restart(tool)}
              onSetup={() => setup(tool)}
              onLogs={() => showLogs(tool.id)}
              onUninstall={() => uninstall(tool)}
              onReinstall={() => requestReinstall(tool)}
            />
          ))}
        </div>
      )}

      {reinstallTarget && (
        <Modal title={`Reinstall ${reinstallTarget.name}`} onClose={() => setReinstallTarget(null)}>
          <p className="text-sm leading-relaxed text-text-secondary">
            The tool folder is missing and no source repo was recorded. Enter the GitHub repo to download it again.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <input
              value={reinstallRepo}
              onChange={event => setReinstallRepo(event.target.value)}
              placeholder="owner/repo"
              className="h-9 min-w-[180px] flex-1 rounded-lg border border-border bg-surface-850 px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
              onKeyDown={event => {
                if (event.key === 'Enter' && reinstallRepo.trim()) runReinstall(reinstallTarget, reinstallRepo.trim())
              }}
            />
            <button
              onClick={() => runReinstall(reinstallTarget, reinstallRepo.trim())}
              disabled={busy !== null || !reinstallRepo.trim()}
              className={btnPrimaryCls}
            >
              {busy === `reinstall:${reinstallTarget.id}` ? 'Reinstalling…' : 'Reinstall'}
            </button>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-text-muted">
            The repo needs a <code className="font-mono">tool.json</code> at its root. It installs to <code className="font-mono">{data.tools_root}/{reinstallTarget.id}</code>.
          </p>
        </Modal>
      )}

      {logs && (
        <Modal title={`${logs.id} log`} onClose={() => setLogs(null)}>
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-all rounded-xl border border-border bg-surface-950 p-3 font-mono text-[11px] leading-relaxed text-text-secondary">{logs.text}</pre>
        </Modal>
      )}

      {error && <div className="rounded-lg border border-danger/20 bg-danger/10 p-3 text-sm text-danger whitespace-pre-wrap">{error}</div>}
      {notice && <div className="rounded-lg border border-success/30 bg-success/10 p-3 text-sm text-success">{notice}</div>}
    </div>
  )
}
