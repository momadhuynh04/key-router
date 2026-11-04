import { useEffect, useRef, useState } from 'react'
import SearchableDropdown from './SearchableDropdown'

type Lane = 'claude' | 'codex'
type CheckStatus = 'pass' | 'fail' | 'warn' | 'skip'

type Check = { id: string; label: string; status: CheckStatus; detail: string }
type Step = {
  id?: string
  kind: string
  label: string
  detail?: string
  status?: string
  exit_code?: number | null
}
type Artifact = { name: string; path: string; size: number; content?: string }
type ExtraFile = { name: string; path: string; size: number }

type Run = {
  id: string
  cli: Lane
  model: string
  workdir: string
  run_dir: string
  status: 'running' | 'complete' | 'error' | 'stopped'
  error: string
  started: number
  finished: number | null
  elapsed_sec: number
  timeout_sec: number
  has_image: boolean
  steps: Step[]
  final_text: string
  transcript: string
  shell_count: number
  checks: Check[]
  artifacts: Artifact[]
  extra_files: ExtraFile[]
  languages: string[]
  active: boolean
  passed?: number
  failed?: number
}

type Meta = {
  scenario: { id: string; label: string; shell_limit: number; checks: string[] }
  clis: { id: Lane; label: string; available: boolean }[]
  allowed_roots: string[]
  default_timeout_sec: number
  languages: string[]
  html_name: string
  md_name: string
}

type ImagePayload = { name: string; mediaType: string; dataBase64: string; preview: string }

const inputCls = "w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
const labelCls = "block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5"

const STATUS_STYLES: Record<string, string> = {
  pass: 'border-success/30 bg-success/10 text-success',
  fail: 'border-danger/30 bg-danger/10 text-danger',
  warn: 'border-accent-border bg-accent-bg text-accent',
  skip: 'border-border bg-surface-900 text-text-muted',
}

const RUN_STATUS: Record<Run['status'], string> = {
  running: 'border-accent-border bg-accent-bg text-accent',
  complete: 'border-success/30 bg-success/10 text-success',
  error: 'border-danger/30 bg-danger/10 text-danger',
  stopped: 'border-border bg-surface-900 text-text-muted',
}

function Pill({ status, children }: { status: CheckStatus; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${STATUS_STYLES[status]}`}>
      {children}
    </span>
  )
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export default function TestLabPanel() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [lane, setLane] = useState<Lane>('claude')
  const [workdir, setWorkdir] = useState('')
  const [model, setModel] = useState('')
  const [timeoutSec, setTimeoutSec] = useState(300)
  const [image, setImage] = useState<ImagePayload | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadHistory = () => {
    fetch('/api/testlab/runs').then(r => r.json()).then(d => setRuns(d.runs || [])).catch(() => {})
  }

  useEffect(() => {
    fetch('/api/testlab/meta').then(r => r.json()).then((data: Meta) => {
      setMeta(data)
      const claude = data.clis.find(c => c.id === 'claude' && c.available)
      setLane(claude ? 'claude' : 'codex')
      if (!workdir && data.allowed_roots.length) setWorkdir(data.allowed_roots[0])
      if (data.default_timeout_sec) setTimeoutSec(data.default_timeout_sec)
    }).catch(() => setError('Could not load Test lab settings.'))
    fetch('/api/available-models').then(r => r.json()).then((models: Record<string, string[]>) => {
      const options = Object.entries(models).flatMap(([provider, ids]) =>
        (ids || []).map(id => `${provider}/${id}`)
      )
      setModelOptions(options)
    }).catch(() => {})
    loadHistory()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!activeId) return
    let cancelled = false
    const tick = async () => {
      try {
        const res = await fetch(`/api/testlab/runs/${encodeURIComponent(activeId)}`)
        if (!res.ok) return
        const data: Run = await res.json()
        if (cancelled) return
        setRun(data)
        if (!data.active) {
          setActiveId(null)
          loadHistory()
        }
      } catch {
        /* keep polling */
      }
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId])

  const pickImage = (file?: File) => {
    setError('')
    if (fileInputRef.current) fileInputRef.current.value = ''
    if (!file) {
      setImage(null)
      return
    }
    if (file.size > 8 * 1024 * 1024) {
      setError('Image must be 8 MiB or smaller.')
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result || '')
      const comma = dataUrl.indexOf(',')
      if (comma < 0) return
      const mediaType = dataUrl.slice(5, dataUrl.indexOf(';'))
      setImage({ name: file.name, mediaType, dataBase64: dataUrl.slice(comma + 1), preview: dataUrl })
    }
    reader.readAsDataURL(file)
  }

  const browse = async () => {
    try {
      const res = await fetch('/api/browse-folder')
      const data = await res.json()
      if (data.path) setWorkdir(data.path)
    } catch (e) {
      setError(String(e))
    }
  }

  const start = async () => {
    setError('')
    setBusy(true)
    try {
      const payload: Record<string, unknown> = {
        cli: lane,
        model: model.trim(),
        workdir: workdir.trim(),
        timeout_sec: Number(timeoutSec) || undefined,
      }
      if (image) payload.image = { media_type: image.mediaType, data_base64: image.dataBase64 }
      const res = await fetch('/api/testlab/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const msg = typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`
        throw new Error(msg)
      }
      setRun(data.run)
      setActiveId(data.run_id)
      loadHistory()
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    if (!activeId) return
    try {
      await fetch(`/api/testlab/runs/${encodeURIComponent(activeId)}/stop`, { method: 'POST' })
    } catch (e) {
      setError(String(e))
    }
  }

  const openRun = async (id: string) => {
    try {
      const res = await fetch(`/api/testlab/runs/${encodeURIComponent(id)}`)
      if (!res.ok) return
      const data: Run = await res.json()
      setRun(data)
      setActiveId(data.active ? data.id : null)
    } catch (e) {
      setError(String(e))
    }
  }

  const available = (id: Lane) => Boolean(meta?.clis.find(c => c.id === id)?.available)
  const running = Boolean(activeId)

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <div className="mb-5">
          <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Test lab</h2>
          <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-text-secondary">
            Run one headless agent turn and verify image understanding, file writing, OS/language discovery and a
            Markdown review. Model overrides use <code className="rounded border border-border bg-surface-850 px-1.5 py-0.5 font-mono text-xs">provider/model</code> and
            bypass Model routing; plain names use the current route.
          </p>
        </div>

        <div className="mb-5 inline-flex rounded-full border border-border bg-surface-850 p-1 gap-1">
          {(['claude', 'codex'] as Lane[]).map(id => {
            const laneLabel = meta?.clis.find(c => c.id === id)?.label || id
            const disabled = !available(id) || running
            return (
              <button
                key={id}
                onClick={() => setLane(id)}
                disabled={disabled}
                title={!available(id) ? `${laneLabel} was not found in PATH` : undefined}
                className={`px-4 py-1.5 rounded-full text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${lane === id ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}
              >{laneLabel}</button>
            )
          })}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div>
            <label className={labelCls}>Test folder</label>
            <div className="flex gap-2">
              <input value={workdir} onChange={e => setWorkdir(e.target.value)} placeholder="/path/to/test-folder" className={inputCls} />
              <button onClick={browse} disabled={running} className="h-9 shrink-0 rounded-full border border-border bg-surface-850 px-4 text-xs font-medium text-text-secondary transition-colors hover:bg-surface-800 disabled:opacity-40">Browse…</button>
            </div>
          </div>
          <div>
            <label className={labelCls}>Model override</label>
            <SearchableDropdown
              options={modelOptions}
              value={model}
              onChange={setModel}
              placeholder="provider/model or leave blank"
            />
            <p className="mt-1.5 text-xs text-text-muted">
              Example: <code className="font-mono">deepseekplatform/deepseek-flash</code> or <code className="font-mono">openrouter/anthropic/claude-sonnet-4.5</code>
            </p>
          </div>
          <div>
            <label className={labelCls}>Image (optional)</label>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              disabled={running}
              onChange={e => pickImage(e.target.files?.[0])}
              className="hidden"
            />
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={running}
                className="h-9 shrink-0 rounded-full border border-border bg-surface-850 px-4 text-xs font-medium text-text-secondary transition-colors hover:bg-surface-800 disabled:cursor-not-allowed disabled:opacity-40"
              >Choose image…</button>
              <span className="min-w-0 flex-1 truncate text-xs text-text-muted">{image ? image.name : 'No file chosen'}</span>
            </div>
            {image && (
              <div className="mt-2 flex items-center gap-3">
                <img src={image.preview} alt="" className="h-12 w-12 rounded-lg border border-border object-cover" />
                <button onClick={() => pickImage(undefined)} disabled={running} className="text-xs text-text-muted hover:text-danger">Remove</button>
              </div>
            )}
          </div>
          <div className="flex items-end gap-3">
            <div className="w-32">
              <label className={labelCls}>Timeout (s)</label>
              <input type="number" min={30} max={1800} value={timeoutSec} onChange={e => setTimeoutSec(Number(e.target.value))} className={inputCls} />
            </div>
            <button
              onClick={start}
              disabled={busy || running || !workdir.trim() || !available(lane)}
              className="h-9 rounded-full bg-brand px-6 text-sm font-medium text-white transition-colors hover:bg-brand-muted disabled:cursor-not-allowed disabled:opacity-40"
            >{busy ? 'Starting…' : 'Run test'}</button>
            {running && (
              <button onClick={stop} className="h-9 rounded-full border border-danger/30 bg-danger/10 px-4 text-sm font-medium text-danger transition-colors hover:bg-danger/20">Stop</button>
            )}
          </div>
        </div>

        {meta && (
          <div className="mt-5 rounded-xl border border-border bg-surface-850 p-4">
            <p className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-2">Scenario · {meta.scenario.label}</p>
            <div className="flex flex-wrap gap-2">
              {meta.scenario.checks.map(item => (
                <span key={item} className="rounded-full border border-border bg-surface-900 px-3 py-1 text-xs text-text-secondary">{item}</span>
              ))}
            </div>
            <p className="mt-2 text-xs text-text-muted">
              Detected on this machine: {meta.languages.length ? meta.languages.join(', ') : 'no known toolchains'}
            </p>
          </div>
        )}

        {error && <div className="mt-4 rounded-lg border border-danger/20 bg-danger/10 p-3 text-sm text-danger">{error}</div>}
      </div>

      {run && (
        <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Run {run.id}</h3>
            <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${RUN_STATUS[run.status]}`}>{run.status}</span>
            <span className="text-xs text-text-muted">{run.cli}</span>
            {run.model && <span className="rounded-full border border-border bg-surface-850 px-2 py-0.5 font-mono text-[11px] text-text-secondary">{run.model}</span>}
            <span className="text-xs text-text-muted">{run.elapsed_sec}s</span>
            <span className="text-xs text-text-muted">{run.shell_count} shell</span>
            {run.has_image && <span className="rounded-full border border-brand-border bg-brand-bg px-2 py-0.5 text-[10px] text-brand">image</span>}
          </div>

          {run.error && <div className="mt-3 rounded-lg border border-danger/20 bg-danger/10 p-3 text-sm text-danger">{run.error}</div>}

          <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {run.checks.map(check => (
              <div key={check.id} className="rounded-xl border border-border bg-surface-850 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-text-secondary">{check.label}</span>
                  <Pill status={check.status}>{check.status}</Pill>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-text-muted">{check.detail}</p>
              </div>
            ))}
          </div>

          {run.final_text && (
            <div className="mt-5">
              <p className={labelCls}>Agent answer</p>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-xl border border-border bg-surface-850 p-4 text-xs leading-relaxed text-text-secondary">{run.final_text}</pre>
            </div>
          )}

          <div className="mt-5">
            <p className={labelCls}>Timeline ({run.steps.length})</p>
            <div className="max-h-72 space-y-2 overflow-auto rounded-xl border border-border bg-surface-850 p-3">
              {run.steps.length === 0 ? (
                <p className="text-xs text-text-muted">Waiting for the first step…</p>
              ) : run.steps.map((step, index) => (
                <div key={`${step.id || index}`} className="rounded-lg border border-border bg-surface-900 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wider text-text-muted">{step.kind}</span>
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-secondary">{step.label}</span>
                    {step.exit_code !== undefined && step.exit_code !== null && (
                      <span className={`text-[10px] ${step.exit_code === 0 ? 'text-success' : 'text-danger'}`}>exit {step.exit_code}</span>
                    )}
                    {step.status === 'running' && <span className="text-[10px] text-accent">running…</span>}
                  </div>
                  {step.detail && <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-text-muted">{step.detail}</pre>}
                </div>
              ))}
            </div>
          </div>

          {run.artifacts.length > 0 && (
            <div className="mt-5 space-y-2">
              <p className={labelCls}>Artifacts</p>
              {run.artifacts.map(artifact => (
                <details key={artifact.name} className="rounded-xl border border-border bg-surface-850">
                  <summary className="cursor-pointer px-3 py-2 text-xs text-text-secondary">
                    <span className="font-mono text-brand">{artifact.name}</span>
                    <span className="ml-2 text-text-muted">{formatBytes(artifact.size)}</span>
                  </summary>
                  <div className="border-t border-border px-3 py-2">
                    <p className="mb-2 break-all font-mono text-[10px] text-text-muted">{artifact.path}</p>
                    <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-text-secondary">{artifact.content}</pre>
                  </div>
                </details>
              ))}
              {run.extra_files.length > 0 && (
                <p className="text-[11px] text-text-muted">Other files: {run.extra_files.map(f => f.name).join(', ')}</p>
              )}
            </div>
          )}

          <details className="mt-5 rounded-xl border border-border bg-surface-850">
            <summary className="cursor-pointer px-3 py-2 text-xs text-text-secondary">Full transcript</summary>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap border-t border-border px-3 py-2 text-[11px] leading-relaxed text-text-muted">{run.transcript || '(empty)'}</pre>
          </details>
        </div>
      )}

      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <h3 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary mb-3">Recent runs</h3>
        {runs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface-850 py-8 text-center text-sm text-text-muted">
            No test runs yet.
          </div>
        ) : (
          <div className="space-y-2">
            {runs.map(item => (
              <button
                key={item.id}
                onClick={() => openRun(item.id)}
                className="flex w-full flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-850 px-3 py-2 text-left transition-colors hover:bg-surface-800"
              >
                <span key="status" className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${RUN_STATUS[item.status]}`}>{item.status}</span>
                <span className="text-xs text-text-secondary">{item.cli}</span>
                {item.model && <span className="font-mono text-[11px] text-text-muted">{item.model}</span>}
                <span className="text-[11px] text-text-muted">{item.elapsed_sec}s</span>
                <span className="text-[11px] text-text-muted">{item.shell_count} shell</span>
                <span className="ml-auto text-[11px] text-text-muted">
                  {item.passed ?? 0} pass · {item.failed ?? 0} fail
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
