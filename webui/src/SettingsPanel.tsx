import { useEffect, useState } from 'react'

type KeyRow = {
  scope: 'builtin' | 'custom'
  id: string
  label: string
  has_key: boolean
  preview: string
  source: 'json' | 'missing'
}

type BaseUrlRow = {
  id: string
  label: string
  value: string
  default: string
  source: 'json' | 'default'
}

type SettingsData = {
  keys: KeyRow[]
  base_urls: BaseUrlRow[]
  secrets_path: string
}

const inputCls = "w-full h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
const labelCls = "block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5"

const SOURCE_TEXT: Record<KeyRow['source'], string> = {
  json: 'Stored in secrets.json',
  missing: 'Missing',
}

function SourcePill({ source }: { source: KeyRow['source'] }) {
  const cls = source === 'json'
    ? 'border-success/30 bg-success/10 text-success'
    : 'border-danger/30 bg-danger/10 text-danger'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${source === 'json' ? 'bg-success' : 'bg-danger'}`} />
      {SOURCE_TEXT[source]}
    </span>
  )
}

function endpointHint(id: string): string {
  switch (id) {
    case 'openrouter': return 'OpenAI: <base>/v1/chat/completions · Anthropic: <base>/v1/messages'
    case 'deepseek_anthropic': return '<base>/v1/messages'
    case 'deepseek_openai': return '<base>/chat/completions'
    case 'google': return '<base>/models/{model}:generateContent'
    default: return ''
  }
}

export default function SettingsPanel({ onOpenProviders, onKeysChanged }: {
  onOpenProviders: () => void
  onKeysChanged: () => void
}) {
  const [data, setData] = useState<SettingsData | null>(null)
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({})
  const [baseDrafts, setBaseDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")

  useEffect(() => {
    fetch('/api/settings').then(r => r.json()).then(setData).catch(() => setError('Could not load settings.'))
  }, [])

  const mutate = async (path: string, method: string, body: unknown, success: string) => {
    setBusy(`${method}:${path}`)
    setError("")
    setNotice("")
    try {
      const res = await fetch(path, {
        method,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        const msg = typeof payload.detail === 'string' ? payload.detail : payload.detail ? JSON.stringify(payload.detail) : `Request failed (${res.status})`
        throw new Error(msg)
      }
      setData(payload as SettingsData)
      setNotice(success)
      onKeysChanged()
      return true
    } catch (e: any) {
      setError(e.message || String(e))
      return false
    } finally {
      setBusy(null)
    }
  }

  const saveKey = async (key: KeyRow) => {
    const draftKey = `${key.scope}:${key.id}`
    const value = (keyDrafts[draftKey] || '').trim()
    if (!value) return
    const ok = await mutate(`/api/settings/keys/${key.scope}/${encodeURIComponent(key.id)}`, 'PUT', { value }, 'Key saved. Applied immediately — no restart needed.')
    if (ok) setKeyDrafts(drafts => ({ ...drafts, [draftKey]: '' }))
  }

  const removeKey = async (key: KeyRow) => {
    if (!confirm(`Remove the ${key.scope === 'builtin' ? 'built-in' : 'custom'} key for "${key.label}"?`)) return
    const draftKey = `${key.scope}:${key.id}`
    const ok = await mutate(`/api/settings/keys/${key.scope}/${encodeURIComponent(key.id)}`, 'DELETE', undefined, 'Key removed.')
    if (ok) setKeyDrafts(drafts => ({ ...drafts, [draftKey]: '' }))
  }

  const saveBaseUrl = async (row: BaseUrlRow) => {
    const value = (baseDrafts[row.id] ?? row.value).trim()
    if (!value) return
    const ok = await mutate(`/api/settings/base-urls/${encodeURIComponent(row.id)}`, 'PUT', { value }, 'Base URL saved. Applied immediately — no restart needed.')
    if (ok) setBaseDrafts(drafts => ({ ...drafts, [row.id]: '' }))
  }

  const resetBaseUrl = async (row: BaseUrlRow) => {
    if (!confirm(`Reset the ${row.label} base URL to the default?`)) return
    await mutate(`/api/settings/base-urls/${encodeURIComponent(row.id)}`, 'DELETE', undefined, 'Base URL reset to default.')
  }

  if (!data) {
    return (
      <div className="rounded-2xl border border-border bg-surface-900 p-6 text-sm text-text-muted">
        {error || 'Loading settings…'}
      </div>
    )
  }

  const builtinKeys = data.keys.filter(k => k.scope === 'builtin')
  const customKeys = data.keys.filter(k => k.scope === 'custom')

  const renderKeyRow = (key: KeyRow) => {
    const draftKey = `${key.scope}:${key.id}`
    const draft = keyDrafts[draftKey] || ''
    const rowBusy = busy !== null
    return (
      <div key={draftKey} className="grid gap-3 rounded-xl border border-border bg-surface-850 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto] lg:items-end">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-text-primary">{key.label}</span>
            <code className="text-[11px] text-text-muted">{key.id}</code>
            <SourcePill source={key.source} />
          </div>
          <p className="mt-1 font-mono text-xs text-text-muted">{key.has_key ? key.preview : 'no key configured'}</p>
        </div>
        <input
          type="password"
          value={draft}
          onChange={e => setKeyDrafts(drafts => ({ ...drafts, [draftKey]: e.target.value }))}
          placeholder={key.has_key ? 'Replace key…' : 'Paste API key…'}
          autoComplete="off"
          className={inputCls}
        />
        <div className="flex items-center gap-2">
          <button onClick={() => saveKey(key)} disabled={!draft.trim() || rowBusy} className="h-9 rounded-full bg-brand px-4 text-xs font-medium text-white transition-colors hover:bg-brand-muted disabled:cursor-not-allowed disabled:opacity-40">Save</button>
          <button
            onClick={() => removeKey(key)}
            disabled={key.source !== 'json' || rowBusy}
            className="h-9 rounded-full border border-border bg-surface-900 px-4 text-xs font-medium text-text-muted transition-colors hover:text-danger disabled:cursor-not-allowed disabled:opacity-40"
          >Remove</button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <div className="mb-4">
          <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Provider API keys</h2>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-text-secondary">
            Keys are stored in <code className="rounded border border-border bg-surface-850 px-1.5 py-0.5 font-mono text-xs">{data.secrets_path}</code> and
            applied on the next request — no restart. The dashboard only ever receives a masked preview.
          </p>
        </div>
        <div className="space-y-2">
          {builtinKeys.map(renderKeyRow)}
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Custom provider keys</h2>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-text-secondary">
              One key per custom provider, stored the same way. Edit a provider to change its endpoint or models.
            </p>
          </div>
          <button onClick={onOpenProviders} className="text-xs text-text-muted transition-colors hover:text-brand">Manage providers →</button>
        </div>
        {customKeys.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface-850 py-8 text-center text-sm text-text-muted">
            No custom providers yet. Add one on the Providers tab.
          </div>
        ) : (
          <div className="space-y-2">
            {customKeys.map(renderKeyRow)}
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Base URLs</h2>
        <p className="mt-1.5 mb-4 max-w-2xl text-sm leading-relaxed text-text-secondary">
          Upstream API roots for the built-in providers. Leave a row untouched to keep the default/environment value.
        </p>
        <div className="space-y-3">
          {data.base_urls.map(row => {
            const draft = baseDrafts[row.id] ?? row.value
            const rowBusy = busy !== null
            const changed = draft.trim() !== row.value
            return (
              <div key={row.id} className="rounded-xl border border-border bg-surface-850 px-4 py-3">
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)_auto] lg:items-end">
                  <div className="min-w-0">
                    <label className={labelCls}>{row.label}</label>
                    <p className="font-mono text-[11px] text-text-muted break-all">{endpointHint(row.id)}</p>
                  </div>
                  <input
                    value={draft}
                    onChange={e => setBaseDrafts(drafts => ({ ...drafts, [row.id]: e.target.value }))}
                    className={inputCls}
                  />
                  <div className="flex items-center gap-2">
                    <button onClick={() => saveBaseUrl(row)} disabled={!draft.trim() || !changed || rowBusy} className="h-9 rounded-full bg-brand px-4 text-xs font-medium text-white transition-colors hover:bg-brand-muted disabled:cursor-not-allowed disabled:opacity-40">Save</button>
                    <button onClick={() => resetBaseUrl(row)} disabled={row.source === 'default' || rowBusy} className="h-9 rounded-full border border-border bg-surface-900 px-4 text-xs font-medium text-text-muted transition-colors hover:text-danger disabled:cursor-not-allowed disabled:opacity-40">Reset</button>
                  </div>
                </div>
                <p className="mt-2 text-[11px] text-text-muted">
                  {row.source === 'json' ? 'Overridden in secrets.json' : `Default: ${row.default}`}
                </p>
              </div>
            )
          })}
        </div>
      </div>

      {error && <div className="rounded-lg border border-danger/20 bg-danger/10 p-3 text-sm text-danger">{error}</div>}
      {notice && <div className="rounded-lg border border-success/30 bg-success/10 p-3 text-sm text-success">{notice}</div>}

      <p className="text-xs leading-relaxed text-text-muted">
        <code className="rounded border border-border bg-surface-850 px-1.5 py-0.5 font-mono">{data.secrets_path}</code> is gitignored and written with owner-only permissions.
        It is the only source for API keys.
      </p>
    </div>
  )
}
