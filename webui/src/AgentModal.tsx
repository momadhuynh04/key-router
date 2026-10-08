import { useState } from 'react'

export type Agent = {
  id: string
  name: string
  cli: string
  model: string
  workdir: string
  permission: string
  effort: string
  tools: string
  restricted: boolean
  add_dirs: string[]
  append_system_prompt: string
  fallback_model: string
  max_budget_usd: number | null
  allowed_tools: string[]
  disallowed_tools: string[]
}

const PERMISSIONS = [
  { value: 'plan', label: 'plan — read only, edits blocked' },
  { value: 'edit', label: 'edit — write files inside the workdir' },
  { value: 'auto', label: 'auto — hands-off, classifier-reviewed' },
  { value: 'ci', label: 'ci — only pre-approved tools (dontAsk)' },
  { value: 'bypass', label: 'bypass — skip every check (dangerous)' },
]

const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']

const TOOLS = [
  { value: 'readonly', label: 'readonly — Read, Grep, Glob' },
  { value: 'edit', label: 'edit — Read, Grep, Glob, Edit, Write' },
  { value: 'full', label: 'full — CLI default (all built-in tools)' },
]

// The `codex` mapping key belongs to the Codex CLI ingress (family fallback in
// config/model_map.py). A Claude Code agent never sends it, so it is hidden here.
const CODEX_MAPPING_KEY = 'codex'

const inputCls = "w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
const areaCls = "w-full rounded-lg bg-surface-900 border border-border px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
const hintCls = "text-xs text-text-muted mt-1"
const labelCls = "block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5"

const emptyAgent: Agent = {
  id: '', name: '', cli: 'claude', model: 'opus', workdir: '',
  permission: 'edit', effort: 'medium', tools: 'edit', restricted: false,
  add_dirs: [], append_system_prompt: '', fallback_model: '',
  max_budget_usd: null, allowed_tools: [], disallowed_tools: [],
}

function titleCase(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export default function AgentModal({ agent, existingIds, allowedRoots, modelMappings, onClose, onSaved }: {
  agent: Agent | null
  existingIds: string[]
  allowedRoots: string[]
  modelMappings: Record<string, string>
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Agent>(
    agent
      ? {
          ...emptyAgent,
          ...agent,
          add_dirs: agent.add_dirs || [],
          allowed_tools: agent.allowed_tools || [],
          disallowed_tools: agent.disallowed_tools || [],
        }
      : { ...emptyAgent }
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [budgetText, setBudgetText] = useState(agent?.max_budget_usd != null ? String(agent.max_budget_usd) : "")
  const [showAdvanced, setShowAdvanced] = useState(
    !!agent && (
      !!agent.append_system_prompt ||
      !!agent.fallback_model ||
      agent.max_budget_usd != null ||
      (agent.allowed_tools || []).length > 0 ||
      (agent.disallowed_tools || []).length > 0
    )
  )
  const [pathCheck, setPathCheck] = useState<{ allowed: boolean; exists: boolean } | null>(null)

  const set = <K extends keyof Agent>(key: K, value: Agent[K]) => setForm(f => ({ ...f, [key]: value }))

  const idValid = /^[a-z0-9_-]{2,32}$/.test(form.id)
  const idTaken = existingIds.includes(form.id) && form.id !== agent?.id
  const canSubmit = idValid && !idTaken && form.workdir.trim().length > 0 && !submitting

  const mappingKeys = Object.keys(modelMappings).filter(k => k !== CODEX_MAPPING_KEY)
  const modelIsMapping = mappingKeys.includes(form.model)

  const checkPath = async (path: string) => {
    if (!path.trim()) { setPathCheck(null); return }
    try {
      const res = await fetch('/api/agents/validate-path', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      })
      if (res.ok) setPathCheck(await res.json())
    } catch { /* advisory only */ }
  }

  const browse = async () => {
    try {
      const res = await fetch('/api/browse-folder')
      const data = await res.json()
      if (data.path) { set('workdir', data.path); checkPath(data.path) }
    } catch (e) { console.error(e) }
  }

  const submit = async () => {
    setError(""); setSubmitting(true)
    const budget = budgetText.trim() === "" ? null : Number(budgetText)
    try {
      const res = await fetch('/api/agents', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          cli: 'claude',
          id: form.id.trim().toLowerCase(),
          name: form.name.trim() || form.id.trim(),
          workdir: form.workdir.trim(),
          max_budget_usd: budget,
          add_dirs: form.add_dirs.map(d => d.trim()).filter(Boolean),
          allowed_tools: form.allowed_tools.map(t => t.trim()).filter(Boolean),
          disallowed_tools: form.disallowed_tools.map(t => t.trim()).filter(Boolean),
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const msg = typeof data.detail === 'string' ? data.detail : data.detail ? JSON.stringify(data.detail) : `Request failed (${res.status})`
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
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl border border-border bg-surface-900 shadow-xl p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 pb-4 border-b border-border mb-5">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">{agent ? `Edit agent · ${agent.id}` : 'Add agent'}</h3>
            <p className="text-xs text-text-muted mt-0.5">Runs the Claude Code CLI headless, driven from the chat.</p>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-full hover:bg-white/5 flex items-center justify-center text-text-muted">×</button>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className={labelCls}>Agent ID</label>
              <input value={form.id} onChange={e => set('id', e.target.value.toLowerCase())} placeholder="opus-probrowser" className={inputCls} />
              <p className={hintCls}>
                {idTaken
                  ? 'ID already exists'
                  : !form.id
                    ? 'Used as /start <id> in chat'
                    : !idValid
                      ? 'Only a-z, 0-9, "-", "_" · 2–32 characters'
                      : 'Used as /start <id> in chat'}
              </p>
            </div>
            <div>
              <label className={labelCls}>Display name</label>
              <input value={form.name} onChange={e => set('name', e.target.value)} placeholder="Opus @probrowser" className={inputCls} />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className={labelCls}>Model</label>
              {mappingKeys.length > 0 ? (
                <select value={modelIsMapping ? form.model : ''} onChange={e => set('model', e.target.value)} className={inputCls}>
                  {!modelIsMapping && (
                    <option value="">{form.model ? `${form.model} (not in routing)` : 'Select a model…'}</option>
                  )}
                  {mappingKeys.map(key => (
                    <option key={key} value={key}>{titleCase(key)} ({modelMappings[key]})</option>
                  ))}
                </select>
              ) : (
                <input value={form.model} onChange={e => set('model', e.target.value)} placeholder="opus" className={inputCls} />
              )}
              <p className={hintCls}>
                {mappingKeys.length > 0
                  ? 'From Model routing — the alias resolves through key-router.'
                  : 'No routes yet. Add one in Model routing first.'}
              </p>
            </div>
            <div>
              <label className={labelCls}>Effort</label>
              <select value={form.effort} onChange={e => set('effort', e.target.value)} className={inputCls}>
                {EFFORTS.map(e => <option key={e} value={e}>{e}</option>)}
              </select>
              <p className={hintCls}>Reasoning effort passed to the CLI.</p>
            </div>
          </div>

          <div>
            <label className={labelCls}>Working directory</label>
            <div className="flex gap-2">
              <input value={form.workdir} onChange={e => set('workdir', e.target.value)} onBlur={e => checkPath(e.target.value)} placeholder="/mnt/Data/ProjectStorage/myapp" className={inputCls} />
              <button onClick={browse} className="h-9 shrink-0 rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-sm font-medium px-4 transition-colors">Browse…</button>
            </div>
            {pathCheck ? (
              <p className={`text-xs mt-1 ${pathCheck.allowed ? 'text-success' : 'text-danger'}`}>
                {pathCheck.allowed ? '✓ Inside allowed_roots' : '✗ Outside allowed_roots — add the root in Bridge settings'}
                {!pathCheck.exists && ' · folder does not exist yet'}
              </p>
            ) : (
              <p className={hintCls}>
                {allowedRoots.length > 0 ? `Must be inside: ${allowedRoots.join(', ')}` : 'No allowed roots configured yet.'}
              </p>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className={labelCls}>Permission</label>
              <select value={form.permission} onChange={e => set('permission', e.target.value)} className={inputCls}>
                {PERMISSIONS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
              <p className={hintCls}>Headless runs never prompt: anything that would ask is denied instead.</p>
            </div>
            <div>
              <label className={labelCls}>Tools</label>
              <select value={form.tools} onChange={e => set('tools', e.target.value)} className={inputCls}>
                {TOOLS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
              <p className={hintCls}>Which built-in tools the agent may use.</p>
            </div>
          </div>

          {form.permission === 'bypass' && (
            <div className="text-xs text-danger rounded-lg bg-danger/10 border border-danger/20 p-3">
              bypass disables every permission check. Use it only inside a container or VM. It also requires
              <code className="font-mono mx-1">allow_bypass</code> in Bridge settings.
            </div>
          )}

          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input type="checkbox" checked={form.restricted} onChange={e => set('restricted', e.target.checked)} className="rounded" />
            restricted — hard sandbox: drops the command-running tools
          </label>

          <div className="rounded-xl border border-border bg-surface-850 p-4">
            <button onClick={() => setShowAdvanced(v => !v)} className="flex w-full items-center justify-between text-[11px] font-medium tracking-widest uppercase text-text-secondary">
              Advanced
              <span className="text-text-muted text-sm">{showAdvanced ? '−' : '+'}</span>
            </button>

            {showAdvanced && (
              <div className="mt-4 space-y-4">
                <div>
                  <label className={labelCls}>Append system prompt</label>
                  <textarea
                    value={form.append_system_prompt}
                    onChange={e => set('append_system_prompt', e.target.value)}
                    rows={3}
                    placeholder="Project-specific instructions for this agent…"
                    className={areaCls}
                  />
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className={labelCls}>Fallback model</label>
                    <input value={form.fallback_model} onChange={e => set('fallback_model', e.target.value)} placeholder="sonnet" className={inputCls} />
                    <p className={hintCls}>Retry on another model when the primary is overloaded.</p>
                  </div>
                  <div>
                    <label className={labelCls}>Max budget (USD per turn)</label>
                    <input value={budgetText} onChange={e => setBudgetText(e.target.value)} inputMode="decimal" placeholder="empty = no cap" className={inputCls} />
                    <p className={hintCls}>Stops the turn once API spend reaches this amount.</p>
                  </div>
                </div>

                <div>
                  <label className={labelCls}>Allowed tools (extra)</label>
                  <textarea
                    value={form.allowed_tools.join('\n')}
                    onChange={e => set('allowed_tools', e.target.value.split('\n'))}
                    rows={2}
                    placeholder={'Bash(git *)\nRead'}
                    className={`${areaCls} font-mono`}
                  />
                  <p className={hintCls}>One pattern per line. Mostly useful with the ci permission.</p>
                </div>

                <div>
                  <label className={labelCls}>Disallowed tools</label>
                  <textarea
                    value={form.disallowed_tools.join('\n')}
                    onChange={e => set('disallowed_tools', e.target.value.split('\n'))}
                    rows={2}
                    placeholder={'Bash(git push *)'}
                    className={`${areaCls} font-mono`}
                  />
                  <p className={hintCls}>One pattern per line. Denied rules apply in every permission mode.</p>
                </div>
              </div>
            )}
          </div>

          <div>
            <label className={labelCls}>Extra directories (--add-dir)</label>
            <div className="space-y-2">
              {form.add_dirs.map((d, i) => (
                <div key={i} className="flex gap-2">
                  <input value={d} onChange={e => set('add_dirs', form.add_dirs.map((x, j) => j === i ? e.target.value : x))} placeholder="/path/to/extra" className={inputCls} />
                  <button onClick={() => set('add_dirs', form.add_dirs.filter((_, j) => j !== i))} className="text-text-muted hover:text-danger px-1">×</button>
                </div>
              ))}
              <button onClick={() => set('add_dirs', [...form.add_dirs, ''])} className="text-sm text-brand hover:text-brand-muted">+ Add directory</button>
            </div>
          </div>

          {error && <div className="text-sm text-danger rounded-lg bg-danger/10 border border-danger/20 p-3 whitespace-pre-wrap break-words">{error}</div>}

          <div className="flex gap-3">
            <button onClick={submit} disabled={!canSubmit} className="flex-1 h-10 rounded-full bg-brand text-white hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-sm font-medium transition-colors">
              {agent ? 'Save changes' : 'Add agent'}
            </button>
            <button onClick={onClose} className="h-10 rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-sm font-medium px-6 transition-colors">Cancel</button>
          </div>
        </div>
      </div>
    </div>
  )
}
