import { useState } from 'react'
import CustomProviderModal from './CustomProviderModal'

function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={`h-4 w-4 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75} aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
    </svg>
  )
}

export default function CustomProvidersPanel({ providers, mappings, onRefresh, onMappingsChanged }: {
  providers: Record<string, any>
  mappings: Record<string, string>
  onRefresh: () => void
  onMappingsChanged: () => void
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [modalMode, setModalMode] = useState<'add' | 'edit' | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [error, setError] = useState("")

  const ids = Object.keys(providers)
  const routesFor = (id: string) => Object.entries(mappings).filter(([, target]) => target.startsWith(`${id}/`))
  const routeLabel = ([source, target]: [string, string]) => `${source} → ${target}`

  const refreshAll = () => {
    onRefresh()
    onMappingsChanged()
  }

  const openAdd = () => {
    setError("")
    setEditingId(null)
    setModalMode('add')
  }

  const openEdit = (id: string) => {
    setError("")
    setEditingId(id)
    setModalMode('edit')
  }

  const closeModal = () => {
    setModalMode(null)
    setEditingId(null)
  }

  const deleteProvider = async (id: string) => {
    if (routesFor(id).length > 0) return
    if (!confirm(`Delete provider "${id}"? Its stored API key in secrets.json will also be removed. This cannot be undone.`)) return
    setError("")
    try {
      const res = await fetch(`/api/custom-providers/${encodeURIComponent(id)}`, { method: 'DELETE' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const msg = typeof data.detail === 'string' ? data.detail : data.detail ? JSON.stringify(data.detail) : `Request failed (${res.status})`
        throw new Error(msg)
      }
      setExpandedId(null)
      refreshAll()
    } catch (e: any) {
      setError(e.message || String(e))
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
          <div>
            <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Custom providers</h2>
            <p className="text-sm text-text-secondary mt-1.5 max-w-2xl leading-relaxed">
              Bring your own OpenAI-compatible or Anthropic-native endpoint. Routes on the Model routing tab can target these providers.
            </p>
          </div>
          <button onClick={openAdd} className="rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">+ Add provider</button>
        </div>

        {error && <div className="text-sm text-danger rounded-lg bg-danger/10 border border-danger/20 p-3 mb-4">{error}</div>}

        {ids.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface-850 py-10 text-center text-sm text-text-muted">
            No custom providers yet. Add one to route Claude Code or Codex to your own API.
          </div>
        ) : (
          <div className="space-y-3">
            {ids.map(id => {
              const p = providers[id] || {}
              const routes = routesFor(id)
              const expanded = expandedId === id
              const models: any[] = Array.isArray(p.models) ? p.models : []
              const headers: Record<string, string> = p.headers || {}
              const headerNames = Object.keys(headers)
              return (
                <div key={id} className="rounded-xl border border-border bg-surface-850 overflow-hidden">
                  <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <button onClick={() => setExpandedId(expanded ? null : id)} aria-expanded={expanded} aria-label={expanded ? 'Collapse provider' : 'Expand provider'} className="shrink-0 rounded-md p-1 text-text-muted hover:bg-white/[0.06] hover:text-text-secondary transition-colors">
                      <Chevron open={expanded} />
                    </button>
                    <span className="text-sm font-medium text-text-primary">{p.display_name || id}</span>
                    <span className="inline-flex items-center rounded-full bg-brand-bg border border-brand-border text-brand text-xs font-mono px-3 py-1">{id}</span>
                    <span className="text-xs text-text-muted px-2 py-0.5 rounded-full border border-border">{p.provider_api}</span>
                    <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full border ${p.has_key ? 'border-success/30 bg-success/10 text-success' : 'border-danger/30 bg-danger/10 text-danger'}`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${p.has_key ? 'bg-success' : 'bg-danger'}`} />
                      {p.has_key ? 'key set' : 'key missing'}
                    </span>
                    <span className="text-xs text-text-muted font-mono truncate max-w-[280px]" title={p.base_url}>{p.base_url}</span>
                    <span className="text-xs text-text-muted">{models.length} model{models.length === 1 ? '' : 's'}</span>
                    <div className="ml-auto flex items-center gap-3">
                      <button onClick={() => openEdit(id)} className="text-xs text-text-secondary hover:text-brand transition-colors">Edit</button>
                      <button
                        onClick={() => deleteProvider(id)}
                        disabled={routes.length > 0}
                        title={routes.length > 0 ? `Remove these routes first: ${routes.map(routeLabel).join(', ')}` : undefined}
                        className="text-xs text-text-muted hover:text-danger disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-text-muted transition-colors"
                      >Delete</button>
                    </div>
                  </div>

                  {expanded && (
                    <div className="border-t border-border bg-surface-900/40 px-4 py-4 space-y-4">
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <div>
                          <p className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1">Base URL</p>
                          <p className="text-xs font-mono text-text-secondary break-all">{p.base_url}</p>
                        </div>
                        <div>
                          <p className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1">API key</p>
                          <p className="text-xs font-mono text-text-secondary break-all">
                            {p.has_key ? `${p.api_key_preview} · stored in secrets.json` : 'no key stored'}
                          </p>
                        </div>
                      </div>

                      <div>
                        <p className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-2">Models ({models.length})</p>
                        {models.length === 0 ? (
                          <p className="text-xs text-text-muted">No models configured.</p>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {models.map(m => (
                              <span key={m.id} className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-900 px-3 py-1 text-xs">
                                <span className="font-mono text-text-secondary">{m.id}</span>
                                {m.name && m.name !== m.id && <span className="text-text-muted">{m.name}</span>}
                                {m.reasoning && <span className="rounded-full bg-brand-bg border border-brand-border px-1.5 text-[10px] text-brand">reasoning</span>}
                                {m.image && <span className="rounded-full bg-accent-bg border border-accent-border px-1.5 text-[10px] text-accent">image</span>}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>

                      <div>
                        <p className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-2">Headers</p>
                        {headerNames.length === 0 ? (
                          <p className="text-xs text-text-muted">None.</p>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {headerNames.map(name => (
                              <span key={name} className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-900 px-3 py-1 text-xs font-mono text-text-secondary">
                                {name}
                                <span className="text-text-muted">{headers[name] ? '••••' : ''}</span>
                              </span>
                            ))}
                          </div>
                        )}
                      </div>

                      <div>
                        <p className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-2">Routes using this provider</p>
                        {routes.length === 0 ? (
                          <p className="text-xs text-text-muted">No routes. This provider can be deleted.</p>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {routes.map(([source, target]) => (
                              <span key={source} className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-900 px-3 py-1 text-xs font-mono">
                                <span className="text-accent">{source}</span>
                                <span className="text-text-muted">→</span>
                                <span className="text-brand">{target}</span>
                              </span>
                            ))}
                          </div>
                        )}
                        {routes.length > 0 && (
                          <p className="text-xs text-text-muted mt-2">Delete is blocked while routes reference this provider. Repoint or remove them on the Model routing tab.</p>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {modalMode && (
        <CustomProviderModal
          customProviders={providers}
          editingProvider={modalMode === 'edit' && editingId ? providers[editingId] : null}
          onClose={closeModal}
          onSaved={() => { closeModal(); refreshAll() }}
        />
      )}
    </div>
  )
}
