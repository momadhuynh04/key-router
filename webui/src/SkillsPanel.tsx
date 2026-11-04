import { useCallback, useEffect, useState } from 'react'

type Provider = 'claude' | 'codex'
type Scope = 'global' | 'project'

type Skill = {
  id: string
  name: string
  description: string
  provider: Provider
  scope: Scope
  path: string
  origin: string
  enabled: boolean
  writable: boolean
  install_mode: string
  files: number
  bytes: number
  source: string | null
  duplicate_paths: string[]
}

type ProjectInfo = {
  path: string
  valid: boolean
  allowed: boolean
  reason: string | null
  error: string | null
  allowed_roots: string[]
}
type ProviderEnvironment = { detected: boolean; roots: string[] }
type Environment = {
  node: { version: string | null; ok: boolean; required: string }
  npx: boolean
  install_supported: boolean
  cli_spec: string
  providers: Record<Provider, ProviderEnvironment>
}
type SkillsResponse = {
  project: ProjectInfo
  environment: Environment
  skills: Record<Provider, Record<Scope, Skill[]>>
  warnings: string[]
}
type MarketplaceSkill = { id: string; source: string; skillId?: string; name: string; installs: number }
type MarketplaceResponse = {
  skills: MarketplaceSkill[]
  suggestions: string[]
  cached: boolean
  warnings: string[]
}

const inputCls = "w-full h-9 rounded-lg bg-surface-850 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand"
const labelCls = "block text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1.5"
const pillGroup = "inline-flex p-1 bg-surface-850 rounded-full border border-border gap-1"
const errorCls = "text-sm text-danger rounded-lg bg-danger/10 border border-danger/20 p-3 whitespace-pre-wrap break-words"

function pillCls(active: boolean) {
  return `px-4 py-1.5 rounded-full text-xs font-medium transition-colors ${active ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:text-text-secondary'}`
}

function fmtInstalls(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`
  return String(value)
}

function providerLabel(provider: Provider) {
  return provider === 'claude' ? 'Claude Code' : 'Codex'
}

function isBundled(skill: Skill) {
  return skill.origin === 'system' || skill.origin === 'plugin'
}

function skillKey(provider: Provider, scope: Scope, skill: Skill) {
  return `${provider}:${scope}:${skill.id}:${skill.path}`
}

export default function SkillsPanel({ projectPath = '', onOpenSettings }: { projectPath?: string; onOpenSettings?: () => void }) {
  const project = projectPath.trim()
  const [collapsed, setCollapsed] = useState(false)
  const [provider, setProvider] = useState<Provider>('claude')
  const [scope, setScope] = useState<Scope>(project ? 'project' : 'global')
  const [tab, setTab] = useState<'installed' | 'browse'>('installed')
  const [data, setData] = useState<SkillsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [contents, setContents] = useState<Record<string, string>>({})
  const [busyId, setBusyId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [market, setMarket] = useState<MarketplaceSkill[]>([])
  const [suggestions, setSuggestions] = useState<string[]>(['react', 'python', 'typescript', 'testing'])
  const [marketBusy, setMarketBusy] = useState(false)
  const [marketError, setMarketError] = useState('')
  const [installTarget, setInstallTarget] = useState<MarketplaceSkill | null>(null)
  const [customOpen, setCustomOpen] = useState(false)
  const [customSource, setCustomSource] = useState('')
  const [customSkill, setCustomSkill] = useState('')

  const fetchSkills = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const qs = new URLSearchParams()
      if (project) qs.set('project', project)
      const suffix = qs.toString() ? `?${qs.toString()}` : ''
      const res = await fetch(`/api/skills${suffix}`)
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${res.status}`)
      setData(payload as SkillsResponse)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [project])

  useEffect(() => {
    const timer = setTimeout(fetchSkills, project ? 450 : 0)
    return () => clearTimeout(timer)
  }, [fetchSkills, project])

  useEffect(() => {
    if (!project) setScope('global')
  }, [project])

  useEffect(() => {
    if (data && !data.project.valid && scope === 'project') setScope('global')
  }, [data, scope])

  useEffect(() => {
    if (tab !== 'browse') return
    const q = query.trim()
    if (q.length < 2) {
      setMarket([])
      setMarketError('')
      return
    }
    const timer = setTimeout(async () => {
      setMarketBusy(true)
      setMarketError('')
      try {
        const res = await fetch(`/api/skills/marketplace?q=${encodeURIComponent(q)}&limit=30`)
        const payload = await res.json().catch(() => ({}))
        if (!res.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${res.status}`)
        const parsed = payload as MarketplaceResponse
        setMarket(parsed.skills || [])
        if (parsed.suggestions?.length) setSuggestions(parsed.suggestions)
        if (parsed.warnings?.length) setMarketError(parsed.warnings.join('\n'))
      } catch (e) {
        setMarket([])
        setMarketError(e instanceof Error ? e.message : String(e))
      } finally {
        setMarketBusy(false)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [query, tab])

  const skillsForScope = data?.skills[provider]?.[scope] || []
  const providerCount = provider
    ? (data?.skills[provider]?.global.length || 0) + (data?.skills[provider]?.project.length || 0)
    : 0
  const projectValid = Boolean(data?.project.valid)
  const nodeOk = data?.environment.node.ok ?? true
  const installSupported = data?.environment.install_supported ?? true

  const visible = skillsForScope.filter(skill => {
    const needle = filter.trim().toLowerCase()
    return !needle || skill.name.toLowerCase().includes(needle) || skill.description.toLowerCase().includes(needle)
  })
  const installedGroup = visible.filter(skill => !isBundled(skill))
  const bundledGroup = visible.filter(isBundled)

  const flash = (message: string) => {
    setNotice(message)
    setTimeout(() => setNotice(''), 3000)
  }

  const toggle = async (skill: Skill) => {
    setBusyId(skill.path)
    setError('')
    try {
      const res = await fetch('/api/skills/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider,
          scope,
          project: scope === 'project' ? project : null,
          id: skill.id,
          enabled: !skill.enabled,
        }),
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${res.status}`)
      await fetchSkills()
      flash(skill.enabled ? `Disabled ${skill.name}` : `Enabled ${skill.name}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (skill: Skill) => {
    if (!confirm(`Remove "${skill.name}" from ${providerLabel(provider)} (${scope})?`)) return
    setBusyId(skill.path)
    setError('')
    try {
      const qs = new URLSearchParams()
      if (scope === 'project' && project) qs.set('project', project)
      const suffix = qs.toString() ? `?${qs.toString()}` : ''
      const res = await fetch(`/api/skills/${provider}/${scope}/${encodeURIComponent(skill.id)}${suffix}`, { method: 'DELETE' })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${res.status}`)
      setExpanded(null)
      await fetchSkills()
      flash(`Removed ${skill.name}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  const preview = async (skill: Skill) => {
    const key = skillKey(provider, scope, skill)
    if (expanded === key) {
      setExpanded(null)
      return
    }
    setExpanded(key)
    if (contents[key]) return
    try {
      const qs = new URLSearchParams({ provider, scope, id: skill.id })
      if (scope === 'project' && project) qs.set('project', project)
      const res = await fetch(`/api/skills/content?${qs.toString()}`)
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${res.status}`)
      setContents(prev => ({ ...prev, [key]: payload.content || '' }))
    } catch (e) {
      setContents(prev => ({ ...prev, [key]: `Could not load SKILL.md: ${e instanceof Error ? e.message : String(e)}` }))
    }
  }

  const customParsed = (() => {
    const raw = customSource.trim()
    let skill = customSkill.trim()
    let source = raw
    if (!skill && raw.includes("@") && !raw.startsWith("http") && !raw.startsWith("git@") && !raw.startsWith("ssh://") && raw.includes("/")) {
      const at = raw.lastIndexOf("@")
      source = raw.slice(0, at)
      skill = raw.slice(at + 1)
    }
    return { source, skill }
  })()

  const openCustomInstall = () => {
    if (!customParsed.source || !customParsed.skill) return
    setInstallTarget({
      id: `custom:${customParsed.skill}`,
      source: customParsed.source,
      skillId: customParsed.skill,
      name: customParsed.skill,
      installs: 0,
    })
  }

  const renderInstalledRow = (skill: Skill) => {
    const key = skillKey(provider, scope, skill)
    return (
      <div key={key} className="px-4 py-3 hover:bg-surface-800/50 transition-colors">
        <div className="flex flex-wrap items-start gap-3">
          <button onClick={() => preview(skill)} className="min-w-0 flex-1 text-left">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-text-primary">{skill.name}</span>
              <span className="inline-flex items-center rounded-full bg-accent-bg border border-accent-border text-accent text-[10px] font-medium px-2 py-0.5">{skill.scope}</span>
              <span className="inline-flex items-center rounded-full bg-surface-900 border border-border text-text-muted text-[10px] font-medium px-2 py-0.5">{skill.origin}</span>
              {skill.install_mode === 'symlink' && <span className="inline-flex items-center rounded-full bg-surface-900 border border-border text-text-muted text-[10px] px-2 py-0.5">linked</span>}
              {skill.duplicate_paths.length > 0 && <span className="text-[10px] text-accent" title={skill.duplicate_paths.join('\n')}>duplicate</span>}
            </div>
            <p className="text-xs text-text-muted leading-relaxed mt-1 line-clamp-2">{skill.description || 'No description in SKILL.md'}</p>
            <p className="text-[11px] text-text-muted font-mono truncate mt-1" title={skill.path}>{skill.path}</p>
          </button>
          <div className="flex items-center gap-3 pt-0.5">
            <button
              role="switch"
              aria-checked={skill.enabled}
              aria-label={`${skill.enabled ? 'Disable' : 'Enable'} ${skill.name}`}
              title={skill.writable ? undefined : 'Read-only: symlink, system, or plugin skill'}
              disabled={!skill.writable || busyId === skill.path}
              onClick={() => toggle(skill)}
              className={`relative w-9 h-5 rounded-full transition-colors ${skill.enabled ? 'bg-brand' : 'bg-surface-700'} ${!skill.writable || busyId === skill.path ? 'opacity-40 cursor-not-allowed' : ''}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${skill.enabled ? 'translate-x-4' : ''}`} />
            </button>
            <button
              onClick={() => remove(skill)}
              disabled={!skill.writable || busyId === skill.path}
              title={skill.writable ? 'Remove skill' : 'Read-only skill'}
              className="text-text-muted hover:text-danger disabled:opacity-30 disabled:cursor-not-allowed text-sm leading-none px-1"
            >
              ×
            </button>
          </div>
        </div>
        {expanded === key && (
          <pre className="mt-3 max-h-64 overflow-auto rounded-lg border border-border bg-surface-950 px-3 py-2 text-[11px] font-mono text-text-secondary whitespace-pre-wrap break-words">
            {contents[key] ?? 'Loading…'}
          </pre>
        )}
      </div>
    )
  }

  return (
    <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button onClick={() => setCollapsed(v => !v)} className="flex items-center gap-2 text-[13px] font-semibold tracking-widest uppercase text-text-secondary">
          Skills
          <span className="text-text-muted text-sm">{collapsed ? '+' : '−'}</span>
        </button>
        <div className="text-xs text-text-muted">
          {data ? `${providerCount} ${providerLabel(provider).toLowerCase()} skill${providerCount === 1 ? '' : 's'}` : 'Loading…'}
        </div>
      </div>

      {!collapsed && (
        <>
          <p className="text-sm text-text-secondary leading-relaxed max-w-3xl mt-2">
            Install and manage skills for Claude Code and Codex. Project skills live in the selected project
            and stay inside the <b>Allowed roots</b> safety fence; global skills apply everywhere.
            key-router copies files and never executes a skill.
          </p>

          <div className="flex flex-wrap items-center gap-2 mt-4">
            <div className={pillGroup}>
              {(['claude', 'codex'] as Provider[]).map(p => {
                const detected = data?.environment.providers[p]?.detected ?? true
                return (
                  <button
                    key={p}
                    onClick={() => { setProvider(p); setExpanded(null) }}
                    className={`${pillCls(provider === p)} ${detected ? '' : 'opacity-60'}`}
                    title={detected ? undefined : `${providerLabel(p)} CLI not detected`}
                  >
                    {providerLabel(p)}
                  </button>
                )
              })}
            </div>
            <div className={pillGroup}>
              <button onClick={() => setScope('global')} className={pillCls(scope === 'global')}>Global</button>
              <button
                onClick={() => setScope('project')}
                disabled={!project}
                title={
                  !project
                    ? 'Set a project folder above first'
                    : data && !projectValid
                      ? 'This folder is outside allowed_roots — see the notice below'
                      : undefined
                }
                className={`${pillCls(scope === 'project')} ${!project ? 'opacity-40 cursor-not-allowed' : ''}`}
              >
                Project
              </button>
            </div>
            <button onClick={fetchSkills} disabled={loading} className="h-8 rounded-full border border-border bg-surface-850 hover:bg-surface-800 disabled:opacity-40 text-text-secondary text-xs font-medium px-4 transition-colors">
              {loading ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>

          {error && <div className={`${errorCls} mt-4`}>{error}</div>}
          {!nodeOk && (
            <div className="text-xs text-accent rounded-lg bg-accent-bg border border-accent-border p-3 mt-4">
              Marketplace installs need Node &gt;= {data?.environment.node.required || '22.20'} (current: {data?.environment.node.version || 'not found'}). Browse still works; install is disabled.
            </div>
          )}
          {project && data && !projectValid && (
            <div className="rounded-xl border border-accent-border bg-accent-bg p-4 mt-4 text-xs leading-relaxed text-text-secondary">
              <p className="text-sm font-semibold text-accent">Project scope is unavailable for this folder</p>
              <p className="mt-2">
                Project skills are read from and installed into the selected folder
                (<code className="font-mono">.claude/skills</code> for Claude Code,
                <code className="font-mono"> .agents/skills</code> for Codex). key-router only touches folders
                inside <b>Allowed roots</b> — the same safety fence that limits agent workdirs, so a typo or
                symlink cannot point the proxy at an unrelated directory.
              </p>

              {data.project.reason === 'outside_roots' && (
                <>
                  <p className="mt-2 break-all">
                    <span className="text-text-muted">Selected folder:</span>{' '}
                    <code className="font-mono">{project}</code>
                  </p>
                  <p className="mt-2">Allowed roots:</p>
                  {data.project.allowed_roots.length > 0 ? (
                    <ul className="mt-1 list-disc pl-5 font-mono break-all">
                      {data.project.allowed_roots.map(root => <li key={root}>{root}</li>)}
                    </ul>
                  ) : (
                    <p className="mt-1 italic">None configured.</p>
                  )}
                </>
              )}

              {data.project.reason === 'no_roots' && (
                <p className="mt-2">
                  <b>No allowed roots are configured</b>, so project scope is disabled everywhere.
                </p>
              )}

              {data.project.reason === 'not_a_directory' && (
                <p className="mt-2">That path does not exist or is not a folder. Pick an existing project folder above.</p>
              )}

              {data.project.reason === 'relative_path' && (
                <p className="mt-2">Project folders must be absolute paths, e.g. <code className="font-mono">/mnt/Data/projects/app</code>.</p>
              )}

              {!['outside_roots', 'no_roots', 'not_a_directory', 'relative_path'].includes(data.project.reason || '') && (
                <p className="mt-2 break-all">{data.project.error}</p>
              )}

              {(data.project.reason === 'outside_roots' || data.project.reason === 'no_roots') && (
                <>
                  <p className="mt-3">
                    <b>To enable project skills:</b> open <b>Agents → Bridge settings → Safety fence → Allowed roots</b>,
                    add this folder (or a parent folder), then <b>Save settings</b> and press <b>Refresh</b> here.
                    Global skills keep working either way.
                  </p>
                  {onOpenSettings && (
                    <button
                      onClick={onOpenSettings}
                      className="mt-3 h-8 rounded-full border border-accent-border bg-surface-900 hover:bg-surface-800 text-accent text-xs font-medium px-4 transition-colors"
                    >
                      Open Bridge settings
                    </button>
                  )}
                </>
              )}
            </div>
          )}
          {notice && <div className="text-xs text-success rounded-lg bg-success/10 border border-success/20 p-3 mt-4">{notice}</div>}

          <div className="flex flex-wrap items-center gap-2 mt-4">
            <div className={pillGroup}>
              <button onClick={() => setTab('installed')} className={pillCls(tab === 'installed')}>
                Installed ({skillsForScope.length})
              </button>
              <button onClick={() => setTab('browse')} className={pillCls(tab === 'browse')}>Browse</button>
            </div>
            {scope === 'project' && project && <span className="text-[11px] text-text-muted font-mono truncate max-w-full">{project}</span>}
          </div>

          {tab === 'installed' ? (
            <div className="mt-4">
              <input
                value={filter}
                onChange={e => setFilter(e.target.value)}
                placeholder={`Filter ${providerLabel(provider)} skills…`}
                className={inputCls}
              />
              {loading && !data ? (
                <div className="rounded-xl border border-border bg-surface-850 py-10 text-center text-sm text-text-muted mt-3">Loading skills…</div>
              ) : visible.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border bg-surface-850 py-10 text-center text-sm text-text-muted mt-3">
                  No skills for {providerLabel(provider)} in this {scope}. Use Browse to install one.
                </div>
              ) : (
                <div className="mt-3 space-y-4">
                  {installedGroup.length > 0 && (
                    <div>
                      <div className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-2">Installed</div>
                      <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850">
                        {installedGroup.map(renderInstalledRow)}
                      </div>
                    </div>
                  )}
                  {bundledGroup.length > 0 && (
                    <div>
                      <div className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-2">Bundled / system</div>
                      <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850">
                        {bundledGroup.map(renderInstalledRow)}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="mt-4 space-y-4">
              <div>
                <label className={labelCls}>Search skills.sh</label>
                <input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="e.g. react, python, testing…"
                  className={inputCls}
                />
                {!query.trim() && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {suggestions.map(s => (
                      <button key={s} onClick={() => setQuery(s)} className="rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-muted text-xs px-3 py-1 transition-colors">{s}</button>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <button onClick={() => setCustomOpen(v => !v)} className="text-xs text-brand hover:text-brand-muted">
                  {customOpen ? '− Hide install from repo/URL' : '+ Install from repo/URL'}
                </button>
                {customOpen && (
                  <div className="rounded-xl border border-border bg-surface-850 p-4 mt-2">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div>
                        <label className={labelCls}>Source</label>
                        <input value={customSource} onChange={e => setCustomSource(e.target.value)} placeholder="owner/repo or https://github.com/…" className={inputCls} />
                      </div>
                      <div>
                        <label className={labelCls}>Skill name</label>
                        <input value={customSkill} onChange={e => setCustomSkill(e.target.value)} placeholder="owner/repo@skill also works" className={inputCls} />
                      </div>
                    </div>
                    <button
                      onClick={openCustomInstall}
                      disabled={!customParsed.source || !customParsed.skill}
                      className="mt-3 h-9 rounded-full bg-brand hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium px-5 transition-colors"
                    >
                      Choose install target
                    </button>
                    <p className="text-[11px] text-text-muted mt-2">
                      The skill name must match the <code className="font-mono">name:</code> field in SKILL.md.
                    </p>
                  </div>
                )}
              </div>

              {marketBusy && <div className="text-xs text-text-muted animate-pulse">Searching skills.sh…</div>}
              {marketError && <div className={errorCls}>{marketError}</div>}
              {!marketBusy && query.trim().length >= 2 && market.length === 0 && !marketError && (
                <div className="rounded-xl border border-dashed border-border bg-surface-850 py-8 text-center text-sm text-text-muted">No skills found.</div>
              )}
              {market.length > 0 && (
                <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850">
                  {market.map(item => (
                    <div key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-surface-800/50 transition-colors">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium text-text-primary">{item.name}</span>
                          <span className="text-[11px] text-text-muted font-mono truncate">{item.source}</span>
                        </div>
                        <p className="text-[11px] text-text-muted mt-0.5">{fmtInstalls(item.installs)} installs</p>
                      </div>
                      <div className="flex items-center gap-3">
                        <a href={`https://skills.sh/${item.id}`} target="_blank" rel="noopener noreferrer" className="text-xs text-text-muted hover:text-text-secondary">View</a>
                        <button onClick={() => setInstallTarget(item)} className="h-8 rounded-full bg-brand hover:bg-brand-muted text-white text-xs font-medium px-4 transition-colors">Install</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {installTarget && (
        <InstallDialog
          target={installTarget}
          project={project}
          projectValid={projectValid}
          environment={data?.environment || null}
          installSupported={installSupported}
          onClose={() => setInstallTarget(null)}
          onInstalled={() => {
            setInstallTarget(null)
            setTab('installed')
            flash('Skill installed')
            fetchSkills()
          }}
        />
      )}
    </div>
  )
}

function InstallDialog({
  target,
  project,
  projectValid,
  environment,
  installSupported,
  onClose,
  onInstalled,
}: {
  target: MarketplaceSkill
  project: string
  projectValid: boolean
  environment: Environment | null
  installSupported: boolean
  onClose: () => void
  onInstalled: () => void
}) {
  const claudeDetected = environment?.providers.claude.detected ?? true
  const codexDetected = environment?.providers.codex.detected ?? true
  const initialProviders: Provider[] = []
  if (claudeDetected) initialProviders.push('claude')
  if (codexDetected) initialProviders.push('codex')
  const [providers, setProviders] = useState<Provider[]>(initialProviders.length ? initialProviders : ['claude'])
  const [scope, setScope] = useState<Scope>(projectValid && project ? 'project' : 'global')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const skill = target.name || target.skillId || ''
  const toggleProvider = (p: Provider) => {
    setProviders(list => list.includes(p) ? list.filter(x => x !== p) : [...list, p])
  }
  const targetRoot = (p: Provider) => {
    if (scope === 'project') return `${project}/${p === 'claude' ? '.claude' : '.agents'}/skills`
    return p === 'claude' ? '~/.claude/skills' : '~/.agents/skills'
  }

  const submit = async () => {
    if (!providers.length) {
      setError('Select at least one provider.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await fetch('/api/skills/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: target.source,
          skill,
          scope,
          project: scope === 'project' ? project : null,
          providers,
        }),
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${res.status}`)
      onInstalled()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl border border-border bg-surface-900 shadow-xl p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 pb-4 border-b border-border mb-4">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-text-primary">Install skill</h3>
            <p className="text-xs text-text-muted mt-0.5 truncate">{skill}</p>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-full hover:bg-white/5 flex items-center justify-center text-text-muted">×</button>
        </div>

        <div className="space-y-4">
          <div>
            <label className={labelCls}>Source</label>
            <div className="text-xs font-mono text-text-secondary break-all rounded-lg bg-surface-850 border border-border px-3 py-2">{target.source}</div>
          </div>

          <div>
            <label className={labelCls}>Install for</label>
            <div className="space-y-2">
              {(['claude', 'codex'] as Provider[]).map(p => {
                const detected = environment?.providers[p]?.detected ?? true
                return (
                  <label key={p} className={`flex items-center gap-2 text-sm text-text-secondary ${detected ? 'cursor-pointer' : 'opacity-50 cursor-not-allowed'}`}>
                    <input type="checkbox" checked={providers.includes(p)} disabled={!detected} onChange={() => toggleProvider(p)} className="rounded" />
                    {providerLabel(p)}
                    {!detected && <span className="text-xs text-text-muted">CLI not detected</span>}
                  </label>
                )
              })}
            </div>
          </div>

          <div>
            <label className={labelCls}>Scope</label>
            <div className={pillGroup}>
              <button onClick={() => setScope('global')} className={pillCls(scope === 'global')}>Global</button>
              <button
                onClick={() => setScope('project')}
                disabled={!projectValid || !project}
                className={`${pillCls(scope === 'project')} ${!projectValid || !project ? 'opacity-40 cursor-not-allowed' : ''}`}
              >
                Project
              </button>
            </div>
          </div>

          {providers.length > 0 && (
            <div>
              <label className={labelCls}>Target paths</label>
              <div className="rounded-lg bg-surface-850 border border-border px-3 py-2 space-y-1">
                {providers.map(p => (
                  <div key={p} className="text-[11px] font-mono text-text-muted break-all">{targetRoot(p)}/{skill || '<skill>'}</div>
                ))}
              </div>
            </div>
          )}

          <div className="text-xs text-accent rounded-lg bg-accent-bg border border-accent-border p-3">
            Skills are third-party instructions and scripts that run with the agent’s permissions.
            Only install sources you trust.
          </div>

          {!installSupported && (
            <div className="text-xs text-danger rounded-lg bg-danger/10 border border-danger/20 p-3">
              Install is unavailable: Node &gt;= 22.20 and npx are required.
            </div>
          )}
          {error && <div className={errorCls}>{error}</div>}

          <div className="flex gap-3">
            <button
              onClick={submit}
              disabled={busy || !skill || !providers.length || !installSupported}
              className="flex-1 h-10 rounded-full bg-brand hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
            >
              {busy ? 'Installing…' : 'Install'}
            </button>
            <button onClick={onClose} className="h-10 rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-sm font-medium px-5 transition-colors">Cancel</button>
          </div>
        </div>
      </div>
    </div>
  )
}
