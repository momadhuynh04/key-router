import { useState, useEffect, useRef } from 'react'
import CustomProviderModal from './CustomProviderModal'

function SearchableDropdown({ options, value, onChange, placeholder }: { options: string[], value: string, onChange: (val: string) => void, placeholder: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const filteredOptions = options.filter(opt => opt.toLowerCase().includes(search.toLowerCase()));

  useEffect(() => {
    if (!isOpen) setSearch(value);
  }, [isOpen, value]);

  return (
    <div ref={wrapperRef} className="relative w-full">
      <input
        type="text"
        value={isOpen ? search : value}
        onChange={e => {
          setSearch(e.target.value);
          if (!isOpen) setIsOpen(true);
          onChange(e.target.value);
        }}
        onFocus={() => setIsOpen(true)}
        placeholder={placeholder}
        className="w-full h-9 rounded-lg bg-surface-900 border border-border px-3 pr-8 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand transition"
      />
      <div className={`absolute right-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}>
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </div>
      {isOpen && (
        <div className="absolute z-50 w-full mt-2 bg-surface-800 rounded-xl border border-border shadow-lg max-h-60 overflow-y-auto">
          {filteredOptions.length === 0 ? (
            <div className="px-3 py-3 text-text-muted text-sm italic text-center">No models found</div>
          ) : (
            filteredOptions.map((opt) => (
              <div
                key={opt}
                onClick={() => {
                  onChange(opt);
                  setSearch(opt);
                  setIsOpen(false);
                }}
                className={`px-3 py-2 cursor-pointer text-sm transition-colors hover:bg-white/[0.06] ${value === opt ? 'bg-brand/15 text-brand' : 'text-text-secondary'}`}
              >
                {opt}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function NavButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${active ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:bg-white/[0.06] hover:text-text-secondary'}`}
    >
      {icon}
      {label}
    </button>
  )
}

function App() {
  const [activeTab, setActiveTab] = useState<'routing' | 'launcher'>('routing');
  const [mappings, setMappings] = useState<Record<string, string>>({});
  const [sourceModel, setSourceModel] = useState("opus");
  const [provider, setProvider] = useState("openrouter");
  const [targetModel, setTargetModel] = useState("");
  const [launchTarget, setLaunchTarget] = useState<string>('terminal');
  const [launchMode, setLaunchMode] = useState<'local' | 'git'>('local');
  const [localPath, setLocalPath] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [isLaunching, setIsLaunching] = useState(false);
  const [ideList, setIdeList] = useState<Record<string, { name: string; version: string; binary: string; supports_claude_extension: boolean }>>({});
  const [codexInfo, setCodexInfo] = useState<{ name: string; version: string } | null>(null);
  const [availableModels, setAvailableModels] = useState<Record<string, string[]>>({ openrouter: [], deepseekplatform: [], googleaistudio: [] });
  const [isLoadingModels, setIsLoadingModels] = useState(true);
  const [customProviders, setCustomProviders] = useState<Record<string, any>>({});
  const [showCustomModal, setShowCustomModal] = useState(false);

  const fetchMappings = () => {
    fetch('/api/models').then(r => r.json()).then(d => setMappings(d.mappings || {}));
  }
  useEffect(() => { fetchMappings(); }, []);
  useEffect(() => {
    fetch('/api/ide-detect').then(r => r.json()).then(d => setIdeList(d.detected || {})).catch(() => {});
    fetch('/api/codex-detect').then(r => r.json()).then(d => setCodexInfo(d.detected?.binary ? d.detected : null)).catch(() => {});
  }, []);
  const fetchCustomProviders = () => {
    fetch('/api/custom-providers').then(r => r.json()).then(d => setCustomProviders(d.providers || {})).catch(() => {});
  };
  useEffect(() => { fetchCustomProviders(); }, []);
  useEffect(() => {
    setIsLoadingModels(true);
    fetch('/api/available-models').then(r => r.json()).then(d => { setAvailableModels(d); setIsLoadingModels(false); }).catch(() => setIsLoadingModels(false));
  }, []);

  const addMapping = async () => {
    if (!sourceModel || !targetModel) return;
    const cleanTarget = targetModel.startsWith(provider + "/") ? targetModel.replace(provider + "/", "") : targetModel;
    const fullTarget = `${provider}/${cleanTarget}`;
    const res = await fetch('/api/models', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_model: sourceModel, target: fullTarget }) });
    const d = await res.json();
    setMappings(d.mappings);
    setTargetModel("");
  };

  const launchClaude = async () => {
    setIsLaunching(true);
    if (launchTarget === 'terminal' || launchTarget === 'codex') {
      const endpoint = launchTarget === 'codex' ? '/api/codex-launch' : '/api/launch';
      const payload = { path: launchMode === 'local' ? localPath : null, repo_url: launchMode === 'git' ? repoUrl : null };
      try {
        if (launchTarget === 'codex') {
          await fetch('/api/codex-setup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        }
        await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      } catch (e) { console.error(e); }
    } else {
      try {
        await fetch('/api/ide-setup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ editors: [launchTarget] }) });
        await fetch('/api/ide-launch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ editor: launchTarget, path: localPath || null }) });
      } catch (e) { console.error(e); }
    }
    setTimeout(() => setIsLaunching(false), 1000);
  };

  const currentProviderModels = availableModels[provider] || [];

  const routingIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" /></svg>
  const launcherIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>

  return (
    <div className="min-h-screen bg-surface-950 flex">
      <aside className="w-[260px] shrink-0 flex flex-col border-r border-border bg-surface-900 sticky top-0 h-screen">
        <div className="px-6 py-6">
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">key-router</h1>
          <p className="text-[11px] tracking-widest uppercase text-text-muted mt-1">Universal proxy</p>
          <div className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-surface-850 border border-border px-3 py-1 text-xs text-text-muted">
            <span className="w-2 h-2 rounded-full bg-success animate-pulse" />
            proxy · :8082
          </div>
        </div>
        <nav className="px-3 py-2 space-y-1 flex-1">
          <NavButton active={activeTab === 'routing'} onClick={() => setActiveTab('routing')} icon={routingIcon} label="Model routing" />
          <NavButton active={activeTab === 'launcher'} onClick={() => setActiveTab('launcher')} icon={launcherIcon} label="Agent launcher" />
        </nav>
        <div className="px-4 py-4 border-t border-border">
          <a href="https://github.com/momadhuynh04/key-router.git" target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 text-xs text-text-muted hover:text-text-secondary transition-colors">
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/></svg>
            momadhuynh04/key-router
          </a>
          <p className="text-[11px] text-text-muted mt-2">© {new Date().getFullYear()} HUYNHHOANG04</p>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <main className="flex-1 px-6 py-8">
          {activeTab === 'routing' && (
            <div className="border border-border bg-surface-900 p-6 rounded-2xl">
              <div className="flex items-center justify-between gap-4 mb-5">
                <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Active model routes</h2>
                <button onClick={() => setShowCustomModal(true)} className="rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">+ Add custom provider</button>
              </div>

              {Object.keys(mappings).length === 0 ? (
                <div className="rounded-xl border border-dashed border-border bg-surface-850 py-10 text-center text-sm text-text-muted">No active model routes configured.</div>
              ) : (
                <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850 mb-6">
                  {Object.entries(mappings).map(([src, tgt]) => (
                    <div key={src} className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-surface-800/50 transition-colors">
                      <span className="inline-flex items-center rounded-full bg-accent-bg border border-accent-border text-accent text-xs font-medium px-3 py-1 font-mono">{src.toUpperCase()}</span>
                      <span className="text-text-muted">→</span>
                      <span className="inline-flex items-center rounded-full bg-brand-bg border border-brand-border text-brand text-xs font-mono px-3 py-1 break-all">{tgt}</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="rounded-xl bg-surface-850 border border-border p-5">
                <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary mb-4">Add or update route</h3>
                <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr_1.7fr_auto] gap-4 items-end">
                  <div>
                    <label className="block text-[11px] font-medium tracking-widest uppercase text-text-secondary mb-1.5">Source model</label>
                    <select value={sourceModel} onChange={e => setSourceModel(e.target.value)} className="w-full h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand">
                      <option value="opus">OPUS</option>
                      <option value="sonnet">SONNET</option>
                      <option value="haiku">HAIKU</option>
                      <option value="codex">CODEX</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[11px] font-medium tracking-widest uppercase text-text-secondary mb-1.5">Target provider</label>
                    <select value={provider} onChange={e => { setProvider(e.target.value); setTargetModel(""); }} className="w-full h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand">
                      <option value="openrouter">OPENROUTER</option>
                      <option value="deepseekplatform">DEEPSEEK</option>
                      <option value="googleaistudio">GOOGLE</option>
                      {Object.keys(customProviders).map(id => (
                        <option key={id} value={id}>{customProviders[id].display_name || id}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-[11px] font-medium tracking-widest uppercase text-text-secondary">Target model</label>
                      {isLoadingModels && <span className="text-xs text-text-muted animate-blink">Loading…</span>}
                    </div>
                    <SearchableDropdown options={currentProviderModels} value={targetModel} onChange={setTargetModel} placeholder="Search and select a model…" />
                  </div>
                  <button onClick={addMapping} disabled={!targetModel} className="h-9 rounded-full bg-brand text-white hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-sm font-medium px-6 transition-colors">Save route</button>
                </div>
              </div>
            </div>
          )}

          {/* LAUNCHER */}
          {activeTab === 'launcher' && (
            <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
              <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary mb-2">Agent launcher</h2>
              <p className="text-sm text-text-secondary leading-relaxed max-w-2xl mb-5">
                Launch Claude Code or Codex with proxy env pre-configured (<code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">ANTHROPIC_BASE_URL</code> for Claude Code, <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">~/.codex/config.toml</code> for Codex). Open a local folder or clone a Git repo.
              </p>

              <div className="flex flex-wrap gap-2 mb-4">
                <div className="inline-flex p-1 bg-surface-850 rounded-full border border-border gap-1">
                  {[
                    { id: 'terminal', label: 'Claude Code' },
                    { id: 'codex', label: codexInfo ? 'Codex' : 'Codex *', dim: !codexInfo, title: codexInfo ? `${codexInfo.name} ${codexInfo.version}` : 'Codex CLI not found in PATH' },
                  ].map(b => (
                    <button key={b.id} onClick={() => setLaunchTarget(b.id)} title={(b as any).title} className={`px-4 py-1.5 rounded-full text-xs font-medium transition-colors ${launchTarget === b.id ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:text-text-secondary'} ${(b as any).dim ? 'opacity-60' : ''}`}>{b.label}</button>
                  ))}
                  {Object.entries(ideList).map(([id, info]) => (
                    <button key={id} onClick={() => setLaunchTarget(id)} title={info.supports_claude_extension ? 'Auto-configures Claude Code extension' : 'Opens IDE — use terminal inside with `claude`'} className={`px-4 py-1.5 rounded-full text-xs font-medium transition-colors ${launchTarget === id ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:text-text-secondary'} ${!info.supports_claude_extension ? 'opacity-60' : ''}`}>{info.name}</button>
                  ))}
                </div>
                {(launchTarget === 'terminal' || launchTarget === 'codex') && (
                  <div className="inline-flex p-1 bg-surface-850 rounded-full border border-border gap-1">
                    <button onClick={() => setLaunchMode('local')} className={`px-4 py-1.5 rounded-full text-xs font-medium transition-colors ${launchMode === 'local' ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}>Local directory</button>
                    <button onClick={() => setLaunchMode('git')} className={`px-4 py-1.5 rounded-full text-xs font-medium transition-colors ${launchMode === 'git' ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}>Git repository</button>
                  </div>
                )}
              </div>

              <div className="rounded-xl bg-surface-850 border border-border p-5">
                {(launchTarget === 'terminal' || launchTarget === 'codex') && launchMode === 'git' ? (
                  <div>
                    <label className="block text-[11px] font-medium tracking-widest uppercase text-text-secondary mb-1.5">Git repository URL</label>
                    <input type="text" value={repoUrl} onChange={e => setRepoUrl(e.target.value)} placeholder="e.g. https://github.com/facebook/react.git" className="w-full h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand" />
                    <p className="text-xs text-text-muted mt-2 flex items-center gap-1.5"><svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg> Repo will be cloned to key-router/projects/ and the agent opens automatically.</p>
                  </div>
                ) : (
                  <div>
                    <label className="block text-[11px] font-medium tracking-widest uppercase text-text-secondary mb-1.5">Project folder path</label>
                    <div className="flex gap-2">
                      <input type="text" value={localPath} onChange={e => setLocalPath(e.target.value)} placeholder="e.g. /home/user/projects/myapp or leave blank" className="flex-1 h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand" />
                      <button onClick={async () => { try { const res = await fetch('/api/browse-folder'); const data = await res.json(); if (data.path) setLocalPath(data.path); } catch (e) { console.error(e); } }} className="h-9 rounded-full border border-border bg-surface-900 hover:bg-surface-800 text-text-secondary text-sm font-medium px-4 transition-colors">Browse…</button>
                    </div>
                    <p className="text-xs text-text-muted mt-2 leading-relaxed">{launchTarget === 'terminal' ? 'A new terminal window will open at this location with Claude loaded.' : launchTarget === 'codex' ? 'Writes ~/.codex/config.toml, then opens a terminal with Codex routed through the proxy.' : (ideList[launchTarget]?.supports_claude_extension ? `${ideList[launchTarget]?.name || 'IDE'} will open with Claude Code + Codex pre-configured.` : `${ideList[launchTarget]?.name || 'IDE'} will open — use its terminal with \`claude\`.`)}</p>
                  </div>
                )}
                <button onClick={launchClaude} disabled={isLaunching || ((launchTarget === 'terminal' || launchTarget === 'codex') && launchMode === 'git' && !repoUrl)} className="mt-5 inline-flex items-center gap-2 rounded-full bg-brand hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium px-6 py-2.5 transition-colors">
                  {isLaunching ? <><span className="w-2 h-2 rounded-full bg-white animate-pulse" /> Launching…</> : <>{launchTarget === 'codex' ? 'Launch Codex' : launchTarget === 'terminal' ? 'Launch Claude Code' : `Launch in ${ideList[launchTarget]?.name || 'IDE'}`} <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" /></svg></>}
                </button>
              </div>
            </div>
          )}

        </main>

        {showCustomModal && <CustomProviderModal customProviders={customProviders} onClose={() => setShowCustomModal(false)} onSaved={() => { setShowCustomModal(false); fetchCustomProviders(); fetch('/api/available-models').then(r=>r.json()).then(d=>setAvailableModels(d)).catch(()=>{}); }} />}

        <footer className="border-t border-border py-5 px-6">
          <div className="flex items-center justify-between gap-2 text-xs text-text-muted">
            <a href="https://github.com/momadhuynh04/key-router.git" target="_blank" rel="noopener noreferrer" className="hover:text-text-secondary transition-colors inline-flex items-center gap-1.5">
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/></svg>
              github.com/momadhuynh04/key-router
            </a>
            <span>© {new Date().getFullYear()} HUYNHHOANG04</span>
          </div>
        </footer>
      </div>
    </div>
  );
}
export default App;
