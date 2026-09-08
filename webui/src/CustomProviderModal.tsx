import { useState } from 'react'

type ModelRow = { id: string; name: string; reasoning: boolean; image: boolean }
type HeaderRow = { key: string; value: string }

export default function CustomProviderModal({ customProviders, onClose, onSaved }: {
  customProviders: Record<string, any>
  onClose: () => void
  onSaved: () => void
}) {
  const [providerId, setProviderId] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [providerApi, setProviderApi] = useState("openai_compatible")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [models, setModels] = useState<ModelRow[]>([{ id: "", name: "", reasoning: false, image: false }])
  const [headers, setHeaders] = useState<HeaderRow[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const idValid = /^[a-z0-9_-]{2,32}$/.test(providerId)
  const hasDuplicate = !!customProviders[providerId]
  const baseValid = /^https?:\/\/.+/.test(baseUrl)
  const hasModel = models.some(m => m.id.trim())
  const hasHeaders = headers.some(h => h.key.trim() && h.value.trim())
  const apiKeyEnvValid = apiKey.trim().length > 0 || hasHeaders
  const canSubmit = idValid && !hasDuplicate && displayName.trim() && baseValid && apiKeyEnvValid && hasModel && !submitting
  const allReasoning = models.length > 0 && models.every(m => m.reasoning)
  const allImage = models.length > 0 && models.every(m => m.image)

  const submit = async () => {
    setError("")
    setSubmitting(true)
    try {
      const body = {
        id: providerId.trim().toLowerCase(),
        display_name: displayName.trim(),
        provider_api: providerApi,
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
        headers: Object.fromEntries(headers.filter(h => h.key.trim()).map(h => [h.key.trim(), h.value])),
        models: models.filter(m => m.id.trim()).map(m => ({ id: m.id.trim(), name: m.name.trim() || m.id.trim(), reasoning: m.reasoning, image: m.image })),
      }
      const res = await fetch('/api/custom-providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const msg = typeof data.detail === 'string' ? data.detail : data.detail ? JSON.stringify(data.detail) : data.error || `Request failed (${res.status})`
        throw new Error(msg)
      }
      onSaved()
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl border border-border bg-surface-900 shadow-xl p-6" onClick={e=>e.stopPropagation()}>
        <div className="flex items-center justify-between gap-4 pb-4 border-b border-border mb-5">
          <h3 className="text-sm font-semibold text-text-primary">Add custom provider</h3>
          <button onClick={onClose} className="w-8 h-8 rounded-full hover:bg-white/5 flex items-center justify-center text-text-muted">×</button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5">Provider ID</label>
            <input value={providerId} onChange={e=>setProviderId(e.target.value.toLowerCase())} placeholder="myprovider" className="w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand" />
            <p className="text-xs text-text-muted mt-1">{hasDuplicate ? 'ID already exists' : !providerId ? '' : !idValid ? 'Must be 2-32 chars: lowercase, numbers, hyphens, underscores' : 'Lowercase letters, numbers, hyphens, or underscores'}</p>
          </div>
          <div>
            <label className="block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5">Display name</label>
            <input value={displayName} onChange={e=>setDisplayName(e.target.value)} placeholder="My AI Provider" className="w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand" />
          </div>
          <div>
            <label className="block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5">Provider API</label>
            <select value={providerApi} onChange={e=>setProviderApi(e.target.value)} className="w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand">
              <option value="openai_compatible">OpenAI Compatible</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5">Base URL</label>
            <input value={baseUrl} onChange={e=>setBaseUrl(e.target.value)} placeholder="https://api.myprovider.com/v1" className="w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand" />
          </div>
          <div>
            <label className="block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5">API key <span className="normal-case tracking-normal text-text-muted font-normal">— ENV var name, not the raw key</span></label>
            <input value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder="e.g. BEEKNOEE_API_KEY" className="w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand" />
            <p className="text-xs text-text-muted mt-1">ENV var <b className="text-text-secondary">name</b> (e.g. BEEKNOEE_API_KEY). Real key goes in <code className="font-mono bg-surface-850 border border-border px-1 py-0.5 rounded text-brand">.env</code>.</p>
          </div>

          <div>
            <div className="flex items-center justify-between gap-4 mb-2">
              <label className="text-[11px] font-medium tracking-widest uppercase text-text-muted">Models</label>
              <div className="flex gap-3">
                <label className="flex items-center gap-1.5 text-xs text-text-muted cursor-pointer"><input type="checkbox" checked={allReasoning} onChange={e=>setModels(ms=>ms.map(m=>({...m, reasoning: e.target.checked})))} className="rounded" /> Toggle reasoning</label>
                <label className="flex items-center gap-1.5 text-xs text-text-muted cursor-pointer"><input type="checkbox" checked={allImage} onChange={e=>setModels(ms=>ms.map(m=>({...m, image: e.target.checked})))} className="rounded" /> Toggle image</label>
              </div>
            </div>
            <div className="rounded-xl border border-border p-3 space-y-2 bg-surface-850">
              <div className="grid grid-cols-[1fr_1fr_auto] gap-2 text-xs text-text-muted">
                <span>ID</span><span>Name</span><span />
              </div>
              {models.map((row, i) => (
                <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2 items-center">
                  <input value={row.id} onChange={e=>setModels(ms=>ms.map((m,j)=>j===i?{...m,id:e.target.value}:m))} placeholder="model-id" className="h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30" />
                  <input value={row.name} onChange={e=>setModels(ms=>ms.map((m,j)=>j===i?{...m,name:e.target.value}:m))} placeholder="Display Name" className="h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30" />
                  <button onClick={()=>setModels(ms=>ms.filter((_,j)=>j!==i))} className="text-text-muted hover:text-danger p-1">×</button>
                  <label className="flex items-center gap-1.5 text-xs text-text-muted"><input type="checkbox" checked={row.reasoning} onChange={e=>setModels(ms=>ms.map((m,j)=>j===i?{...m,reasoning:e.target.checked}:m))} className="rounded" /> Reasoning</label>
                  <label className="flex items-center gap-1.5 text-xs text-text-muted"><input type="checkbox" checked={row.image} onChange={e=>setModels(ms=>ms.map((m,j)=>j===i?{...m,image:e.target.checked}:m))} className="rounded" /> Image</label>
                  <span />
                </div>
              ))}
              <button onClick={()=>setModels(ms=>[...ms,{id:"",name:"",reasoning:false,image:false}])} className="text-sm text-brand hover:text-brand-muted">+ Add model</button>
            </div>
          </div>

          <div>
            <label className="block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5">Headers (optional)</label>
            <div className="space-y-2">
              {headers.map((h,i)=>(
                <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                  <input value={h.key} onChange={e=>setHeaders(hs=>hs.map((x,j)=>j===i?{...x,key:e.target.value}:x))} placeholder="Header-Name" className="h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30" />
                  <input value={h.value} onChange={e=>setHeaders(hs=>hs.map((x,j)=>j===i?{...x,value:e.target.value}:x))} placeholder="value" className="h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30" />
                  <button onClick={()=>setHeaders(hs=>hs.filter((_,j)=>j!==i))} className="text-text-muted hover:text-danger p-1">×</button>
                </div>
              ))}
              <button onClick={()=>setHeaders(hs=>[...hs,{key:"",value:""}])} className="text-sm text-brand hover:text-brand-muted">+ Add header</button>
            </div>
          </div>

          {error && <div className="text-sm text-danger rounded-lg bg-danger/10 border border-danger/20 p-3">{error}</div>}

          <button onClick={submit} disabled={!canSubmit} className="w-full h-10 rounded-full bg-brand text-white hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-sm font-medium transition-colors">Submit</button>
        </div>
      </div>
    </div>
  )
}
