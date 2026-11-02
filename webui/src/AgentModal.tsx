import { useState, type KeyboardEvent, type ReactNode } from 'react'

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

// Short recap shown under the Permission select. Headless runs pass
// --permission-prompts none, so "asks" really means "is denied".
const PERMISSION_DETAILS: Record<string, { allows: string; denies: string }> = {
  plan: {
    allows: 'Reading files and searching.',
    denies: 'Every edit and every shell command.',
  },
  edit: {
    allows: 'Reading, searching, and editing files inside the workdir.',
    denies: 'Shell commands such as rg, find, tests, or git — they are auto-denied.',
  },
  auto: {
    allows: 'A classifier approves ordinary actions, including non-destructive shell commands.',
    denies: 'Destructive actions still need approval, so they are auto-denied.',
  },
  ci: {
    allows: 'Only the tools listed under Advanced options → Allowed tools.',
    denies: 'Everything else, including shell unless you add a pattern like Bash(rg:*).',
  },
  bypass: {
    allows: 'Everything, with no checks.',
    denies: 'Nothing — use it only inside a container or VM.',
  },
}

const TOOL_HINTS: Record<string, string> = {
  readonly: 'Only Read, Grep, and Glob exist for this agent.',
  edit: 'Read, Grep, Glob, Edit, and Write exist. No shell.',
  full: 'All built-in tools exist, including Bash. Permission still decides whether they run.',
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="border-t border-border pt-6 first:border-0 first:pt-0">
      <div className="mb-4">
        <h4 className="text-[11px] font-semibold tracking-widest uppercase text-text-secondary">{title}</h4>
        {hint && <p className="mt-1 text-xs leading-relaxed text-text-muted">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

const COMMON_TOOLS = ['Bash', 'Read', 'Edit', 'Write', 'Grep', 'Glob', 'WebFetch', 'WebSearch', 'Task', 'NotebookEdit']
const CUSTOM_TOOL = '__custom'

function ToolListEditor({ label, hint, values, onChange, tone = 'brand' }: {
  label: string
  hint: string
  values: string[]
  onChange: (next: string[]) => void
  tone?: 'brand' | 'danger'
}) {
  const [tool, setTool] = useState('Bash')
  const [arg, setArg] = useState('')
  const [custom, setCustom] = useState('')

  const add = () => {
    let pattern = ''
    if (tool === CUSTOM_TOOL) pattern = custom.trim()
    else if (tool === 'Bash') pattern = arg.trim() ? `Bash(${arg.trim()})` : 'Bash'
    else pattern = tool
    if (!pattern) return
    if (!values.includes(pattern)) onChange([...values, pattern])
    setArg('')
    setCustom('')
  }

  const onEnter = (e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      add()
    }
  }

  const chipCls = tone === 'danger'
    ? 'border-danger/30 bg-danger/10 text-danger'
    : 'border-brand-border bg-brand-bg text-brand'
  const fieldCls = "h-9 rounded-lg border border-border bg-surface-850 px-2 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30"

  return (
    <div>
      <label className={labelCls}>{label}</label>
      <div className="min-h-[42px] rounded-lg border border-border bg-surface-850 p-2">
        {values.length === 0 ? (
          <p className="px-1 py-1 text-xs text-text-muted">No rules yet.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {values.map(value => (
              <span key={value} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-mono ${chipCls}`}>
                <span className="break-all">{value}</span>
                <button
                  type="button"
                  onClick={() => onChange(values.filter(v => v !== value))}
                  aria-label={`Remove ${value}`}
                  className="opacity-70 transition-opacity hover:opacity-100"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        <select
          value={tool}
          onChange={e => { setTool(e.target.value); setArg(''); setCustom('') }}
          className={fieldCls}
        >
          {COMMON_TOOLS.map(t => <option key={t} value={t}>{t}</option>)}
          <option value={CUSTOM_TOOL}>Custom pattern…</option>
        </select>
        {tool === 'Bash' && (
          <input
            value={arg}
            onChange={e => setArg(e.target.value)}
            onKeyDown={onEnter}
            placeholder="rg:* or git *"
            spellCheck={false}
            className={`${fieldCls} min-w-[140px] flex-1 font-mono`}
          />
        )}
        {tool === CUSTOM_TOOL && (
          <input
            value={custom}
            onChange={e => setCustom(e.target.value)}
            onKeyDown={onEnter}
            placeholder="mcp__server__tool"
            spellCheck={false}
            className={`${fieldCls} min-w-[140px] flex-1 font-mono`}
          />
        )}
        <button
          type="button"
          onClick={add}
          className="h-9 shrink-0 rounded-full border border-border bg-surface-850 px-3 text-xs font-medium text-text-secondary transition-colors hover:bg-surface-800"
        >
          + Add
        </button>
      </div>
      <p className={hintCls}>{hint}</p>
    </div>
  )
}

const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']

const TOOLS = [
  { value: 'readonly', label: 'readonly — Read, Grep, Glob' },
  { value: 'edit', label: 'edit — Read, Grep, Glob, Edit, Write' },
  { value: 'full', label: 'full — CLI default (all built-in tools)' },
]

// Family keys route every model of an ingress through one route. They are not
// valid `--model` values, so they are hidden from the agent model picker —
// instead a Claude agent picks a real alias (opus/sonnet/haiku) that the
// `claude` family route resolves in config/model_map.py.
const FAMILY_MAPPING_KEYS = new Set(['claude', 'codex'])
const CLAUDE_ALIASES = ['opus', 'sonnet', 'haiku']

const inputCls = "w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
const areaCls = "w-full rounded-lg bg-surface-850 border border-border px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
const hintCls = "text-xs text-text-muted mt-1 leading-relaxed"
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
  const restrictedConflict = form.restricted && form.permission === 'bypass'
  const canSubmit = idValid && !idTaken && form.workdir.trim().length > 0 && !restrictedConflict && !submitting

  const tierKeys = Object.keys(modelMappings).filter(k => !FAMILY_MAPPING_KEYS.has(k))
  const claudeAliases = modelMappings.claude
    ? CLAUDE_ALIASES.filter(alias => !tierKeys.includes(alias))
    : []
  const modelOptions = [...tierKeys, ...claudeAliases]
  const modelIsOption = modelOptions.includes(form.model)

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm sm:p-6" onClick={onClose}>
      <div
        className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-surface-900 shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">{agent ? `Edit agent · ${agent.id}` : 'Add agent'}</h3>
            <p className="mt-0.5 text-xs text-text-muted">Runs the Claude Code CLI headless, driven from the chat.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-full text-text-muted transition-colors hover:bg-white/5"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          <Section title="Identity" hint="The id is what you type after /start. The display name is only for the dashboard.">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className={labelCls}>Agent ID</label>
                <input
                  value={form.id}
                  onChange={e => set('id', e.target.value.toLowerCase())}
                  placeholder="opus-probrowser"
                  autoComplete="off"
                  spellCheck={false}
                  className={inputCls}
                />
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
          </Section>

          <Section title="Model & runtime" hint="Which model runs, where it runs, and how much reasoning effort it spends.">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className={labelCls}>Model</label>
                {modelOptions.length > 0 ? (
                  <select value={modelIsOption ? form.model : ''} onChange={e => set('model', e.target.value)} className={inputCls}>
                    {!modelIsOption && (
                      <option value="">{form.model ? `${form.model} (not in routing)` : 'Select a model…'}</option>
                    )}
                    {tierKeys.map(key => (
                      <option key={key} value={key}>{titleCase(key)} ({modelMappings[key]})</option>
                    ))}
                    {claudeAliases.map(alias => (
                      <option key={alias} value={alias}>{titleCase(alias)} (Claude route)</option>
                    ))}
                  </select>
                ) : (
                  <input value={form.model} onChange={e => set('model', e.target.value)} placeholder="opus" className={inputCls} />
                )}
                <p className={hintCls}>
                  {modelOptions.length > 0
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

            <div className="mt-4 space-y-4">
              <div>
                <label className={labelCls}>Working directory</label>
                <div className="flex gap-2">
                  <input value={form.workdir} onChange={e => set('workdir', e.target.value)} onBlur={e => checkPath(e.target.value)} placeholder="/mnt/Data/ProjectStorage/myapp" className={inputCls} />
                  <button type="button" onClick={browse} className="h-9 shrink-0 rounded-full border border-border bg-surface-850 px-4 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-800">Browse…</button>
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

              <div>
                <label className={labelCls}>Extra directories</label>
                <p className={hintCls}>Extra writable folders passed as --add-dir.</p>
                <div className="mt-2 space-y-2">
                  {form.add_dirs.map((d, i) => (
                    <div key={i} className="flex gap-2">
                      <input value={d} onChange={e => set('add_dirs', form.add_dirs.map((x, j) => j === i ? e.target.value : x))} placeholder="/path/to/extra" className={inputCls} />
                      <button type="button" onClick={() => set('add_dirs', form.add_dirs.filter((_, j) => j !== i))} aria-label="Remove directory" className="px-2 text-lg text-text-muted transition-colors hover:text-danger">×</button>
                    </div>
                  ))}
                  <button type="button" onClick={() => set('add_dirs', [...form.add_dirs, ''])} className="text-sm text-brand transition-colors hover:text-brand-muted">+ Add directory</button>
                </div>
              </div>
            </div>
          </Section>

          <Section title="Permissions" hint="Headless runs never prompt: anything that would ask is denied and shows as a red “Blocked by permissions” line in chat.">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className={labelCls}>Permission</label>
                <select value={form.permission} onChange={e => set('permission', e.target.value)} className={inputCls}>
                  {PERMISSIONS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </div>
              <div>
                <label className={labelCls}>Tools</label>
                <select value={form.tools} onChange={e => set('tools', e.target.value)} className={inputCls}>
                  {TOOLS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
                <p className={hintCls}>{TOOL_HINTS[form.tools] || 'Which built-in tools the agent may use.'}</p>
              </div>
            </div>

            <div className="mt-4 space-y-4">
              {PERMISSION_DETAILS[form.permission] && (
                <div className="rounded-lg border border-border bg-surface-850 px-3 py-2.5">
                  <p className="text-xs leading-relaxed text-text-secondary">
                    <span className="font-medium text-success">Allowed</span> · {PERMISSION_DETAILS[form.permission].allows}
                  </p>
                  <p className="mt-1 text-xs leading-relaxed text-text-secondary">
                    <span className="font-medium text-danger">Denied</span> · {PERMISSION_DETAILS[form.permission].denies}
                  </p>
                </div>
              )}

              {form.tools === 'full' && !['auto', 'bypass'].includes(form.permission) && (
                <div className="rounded-lg border border-accent-border bg-accent-bg p-3 text-xs leading-relaxed text-text-secondary">
                  <b className="text-accent">Shell will be blocked.</b> Tools is <code className="font-mono">full</code>, but the
                  <code className="mx-1 font-mono">{form.permission}</code> permission does not pre-approve Bash, so every
                  <code className="mx-1 font-mono">rg</code>/<code className="font-mono">find</code>/<code className="font-mono">git</code> call is auto-denied.
                  Switch Permission to <b>auto</b>, or add patterns such as <code className="font-mono">Bash(rg:*)</code> under Advanced options → Allowed tools.
                </div>
              )}

              {form.permission === 'bypass' && (
                <div className="rounded-lg border border-danger/20 bg-danger/10 p-3 text-xs leading-relaxed text-danger">
                  bypass disables every permission check. Use it only inside a container or VM. It also requires
                  <code className="mx-1 font-mono">allow_bypass</code> in Bridge settings.
                </div>
              )}

              {restrictedConflict && (
                <div className="rounded-lg border border-danger/20 bg-danger/10 p-3 text-xs leading-relaxed text-danger">
                  Restricted mode refuses <code className="font-mono">bypassPermissions</code>. Pick one: turn off
                  Restricted, or choose another Permission.
                </div>
              )}

              <label className="flex items-start gap-2 text-sm text-text-secondary">
                <input type="checkbox" checked={form.restricted} onChange={e => set('restricted', e.target.checked)} className="mt-0.5 rounded" />
                <span>
                  Restricted mode — no command execution
                  <span className="mt-0.5 block text-xs leading-relaxed text-text-muted">
                    Passes <code className="font-mono">--restricted</code> to Claude Code: removes Bash, PowerShell and other
                    code-running tools, confines file tools to the workdir, and ignores user/project settings. Use it for
                    untrusted repos. Tools named under Allowed tools can still be re-enabled.
                  </span>
                </span>
              </label>
            </div>
          </Section>

          <section className="border-t border-border pt-6">
            <button
              type="button"
              onClick={() => setShowAdvanced(v => !v)}
              aria-expanded={showAdvanced}
              className="flex w-full items-center justify-between gap-4 rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
            >
              <span>
                <span className="block text-[11px] font-semibold tracking-widest uppercase text-text-secondary">Advanced options</span>
                <span className="mt-1 block text-xs leading-relaxed text-text-muted">System prompt · fallback model · budget cap · tool allow/deny rules</span>
              </span>
              <svg
                className={`h-4 w-4 shrink-0 text-text-muted transition-transform ${showAdvanced ? 'rotate-180' : ''}`}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {showAdvanced && (
              <div className="mt-5 space-y-4">
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

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <ToolListEditor
                    label="Allowed tools (extra)"
                    hint="Extra pre-approved rules. Pick a tool; for Bash add a command pattern like rg:* or git *. Other tools are allowed by name."
                    values={form.allowed_tools}
                    onChange={next => set('allowed_tools', next)}
                  />
                  <ToolListEditor
                    label="Disallowed tools"
                    hint="Denied rules apply in every permission mode. Same syntax as Allowed tools."
                    values={form.disallowed_tools}
                    onChange={next => set('disallowed_tools', next)}
                    tone="danger"
                  />
                </div>
              </div>
            )}
          </section>
        </div>

        <div className="border-t border-border bg-surface-900 px-6 py-4">
          {error && <div className="mb-3 whitespace-pre-wrap break-words rounded-lg border border-danger/20 bg-danger/10 p-3 text-sm text-danger">{error}</div>}
          <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-text-muted">
              {canSubmit ? 'Changes apply on the next turn.' : 'Fill in the required fields to save.'}
            </p>
            <div className="flex gap-3">
              <button type="button" onClick={onClose} className="h-10 rounded-full border border-border bg-surface-850 px-6 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-800">Cancel</button>
              <button
                type="button"
                onClick={submit}
                disabled={!canSubmit}
                className="h-10 min-w-[150px] rounded-full bg-brand text-sm font-medium text-white transition-colors hover:bg-brand-muted disabled:cursor-not-allowed disabled:opacity-40"
              >
                {agent ? 'Save changes' : 'Add agent'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
