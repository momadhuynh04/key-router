import { useState, useEffect } from 'react'
import CustomProvidersPanel from './CustomProvidersPanel'
import SearchableDropdown from './SearchableDropdown'
import AgentModal from './AgentModal'
import BridgePanel, { type BridgeSettings } from './BridgePanel'
import SkillsPanel from './SkillsPanel'
import SettingsPanel from './SettingsPanel'
import TestLabPanel from './TestLabPanel'
import type { Agent } from './AgentModal'

const FAMILY_KEYS = ['claude', 'codex']
const APP_VERSION = '0.1.32'

function GitHubIcon({ className = 'h-3.5 w-3.5' }: { className?: string }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
    </svg>
  )
}

function NavButton({ active, onClick, icon, label, collapsed }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string; collapsed: boolean }) {
  return (
    <button
      onClick={onClick}
      title={collapsed ? label : undefined}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      className={`flex items-center rounded-xl text-sm font-medium transition-colors ${collapsed ? 'h-11 w-11 justify-center' : 'w-full gap-3 px-3 py-2.5'} ${active ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:bg-white/[0.06] hover:text-text-secondary'}`}
    >
      <span className="shrink-0">{icon}</span>
      {!collapsed && <span className="truncate">{label}</span>}
    </button>
  )
}

function App() {
  const [activeTab, setActiveTab] = useState<'routing' | 'providers' | 'testlab' | 'launcher' | 'agents' | 'settings'>('routing');
  const [mappings, setMappings] = useState<Record<string, string>>({});
  const [sourceModel, setSourceModel] = useState("claude");
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
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentSettings, setAgentSettings] = useState<BridgeSettings | null>(null);
  const [agentWarnings, setAgentWarnings] = useState<string[]>([]);
  const [bridgeRunning, setBridgeRunning] = useState(false);
  const [showAgentModal, setShowAgentModal] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [recentDirs, setRecentDirs] = useState<string[]>([]);

  const overrideKeys = Object.keys(mappings).filter(k => !FAMILY_KEYS.includes(k));
  useEffect(() => {
    if (!FAMILY_KEYS.includes(sourceModel) && !(sourceModel in mappings)) {
      setSourceModel('claude');
    }
  }, [mappings, sourceModel]);

  const fetchMappings = () => {
    fetch('/api/models').then(r => r.json()).then(d => setMappings(d.mappings || {}));
  }
  useEffect(() => { fetchMappings(); }, []);
  const fetchRecentDirs = () => {
    fetch('/api/recent-dirs').then(r => r.json()).then(d => setRecentDirs(d.dirs || [])).catch(() => {});
  }
  useEffect(() => { fetchRecentDirs(); }, []);
  useEffect(() => {
    fetch('/api/ide-detect').then(r => r.json()).then(d => setIdeList(d.detected || {})).catch(() => {});
    fetch('/api/codex-detect').then(r => r.json()).then(d => setCodexInfo(d.detected?.binary ? d.detected : null)).catch(() => {});
  }, []);
  const fetchCustomProviders = () => {
    fetch('/api/custom-providers').then(r => r.json()).then(d => setCustomProviders(d.providers || {})).catch(() => {});
  };
  const refreshCustomProviders = () => {
    fetchCustomProviders();
    fetch('/api/available-models').then(r => r.json()).then(d => setAvailableModels(d)).catch(() => {});
  };
  useEffect(() => { fetchCustomProviders(); }, []);
  const fetchAgents = () => {
    fetch('/api/agents').then(r => r.json()).then(d => {
      setAgents(d.agents || []);
      setAgentSettings(d.settings || null);
      setAgentWarnings(d.warnings || []);
    }).catch(() => {});
  };
  useEffect(() => { fetchAgents(); }, []);
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
    fetchRecentDirs();
    setTimeout(() => setIsLaunching(false), 1000);
  };

  const currentProviderModels = availableModels[provider] || [];

  const deleteAgent = async (id: string) => {
    if (!confirm(`Delete agent "${id}"?`)) return;
    try {
      const res = await fetch(`/api/agents/${id}`, { method: 'DELETE' });
      if (res.ok) fetchAgents();
    } catch (e) { console.error(e); }
  };

  const routingIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" /></svg>
  const providersIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M6 3v4m12-4v4M4 7h16v4a8 8 0 01-8 8 8 8 0 01-8-8V7zm8 12v3" /></svg>
  const testlabIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3v6.75L4.5 18.75A1.5 1.5 0 005.8 21h12.4a1.5 1.5 0 001.3-2.25l-5.25-9V3M9 3h6M7.5 14.5h9" /></svg>
  const launcherIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
  const agentsIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z" /></svg>
  const settingsIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.28z" /><path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>

  return (
    <div className="min-h-screen bg-surface-950 flex">
      <aside className={`${sidebarOpen ? 'w-[260px]' : 'w-[72px]'} shrink-0 flex flex-col overflow-hidden border-r border-border bg-surface-900 sticky top-0 h-screen transition-[width] duration-300 ease-in-out`}>
        <div className={`flex pt-6 pb-4 ${sidebarOpen ? 'items-start justify-between gap-2 px-6' : 'justify-center px-3'}`}>
          {sidebarOpen && (
            <div className="min-w-0">
              <h1 className="text-xl font-semibold tracking-tight text-text-primary whitespace-nowrap">key-router</h1>
              <p className="text-[11px] tracking-widest uppercase text-text-muted mt-1 whitespace-nowrap">Universal proxy</p>
            </div>
          )}
          <button
            onClick={() => setSidebarOpen(v => !v)}
            aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            aria-expanded={sidebarOpen}
            title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            className="shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-muted hover:bg-white/[0.06] hover:text-text-secondary transition-colors"
          >
            <svg className={`h-4 w-4 transition-transform duration-300 ${sidebarOpen ? '' : 'rotate-180'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
            </svg>
          </button>
        </div>

        {sidebarOpen ? (
          <div className="px-6 pb-4 flex flex-col items-start gap-2">
            <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-850 border border-border px-3 py-1 text-xs text-text-muted whitespace-nowrap">
              <span className="w-2 h-2 rounded-full bg-success animate-pulse" />
              proxy · :8082
            </div>
            <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-850 border border-border px-3 py-1 text-xs text-text-muted whitespace-nowrap">
              <span className={`w-2 h-2 rounded-full ${bridgeRunning ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
              bridge · :{agentSettings?.port ?? 8083}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 pb-4">
            <span title="proxy · :8082" className="flex h-7 w-7 items-center justify-center rounded-full border border-border bg-surface-850">
              <span className="h-2 w-2 rounded-full bg-success animate-pulse" />
            </span>
            <span title={`bridge · :${agentSettings?.port ?? 8083}`} className="flex h-7 w-7 items-center justify-center rounded-full border border-border bg-surface-850">
              <span className={`h-2 w-2 rounded-full ${bridgeRunning ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
            </span>
          </div>
        )}

        <nav className={`flex flex-1 flex-col gap-1 py-2 ${sidebarOpen ? 'px-3' : 'items-center px-3'}`}>
          <NavButton active={activeTab === 'routing'} onClick={() => setActiveTab('routing')} icon={routingIcon} label="Model routing" collapsed={!sidebarOpen} />
          <NavButton active={activeTab === 'providers'} onClick={() => setActiveTab('providers')} icon={providersIcon} label="Providers" collapsed={!sidebarOpen} />
          <NavButton active={activeTab === 'testlab'} onClick={() => setActiveTab('testlab')} icon={testlabIcon} label="Test lab" collapsed={!sidebarOpen} />
          <NavButton active={activeTab === 'launcher'} onClick={() => setActiveTab('launcher')} icon={launcherIcon} label="Agent launcher" collapsed={!sidebarOpen} />
          <NavButton active={activeTab === 'agents'} onClick={() => setActiveTab('agents')} icon={agentsIcon} label="Agents" collapsed={!sidebarOpen} />
          <NavButton active={activeTab === 'settings'} onClick={() => setActiveTab('settings')} icon={settingsIcon} label="Settings" collapsed={!sidebarOpen} />
        </nav>

        <div className={`py-4 border-t border-border ${sidebarOpen ? 'px-4' : 'flex justify-center px-3'}`}>
          <a
            href="https://github.com/momadhuynh04/key-router.git"
            target="_blank"
            rel="noopener noreferrer"
            title="momadhuynh04/key-router"
            className={`flex items-center gap-2 text-xs text-text-muted hover:text-text-secondary transition-colors ${sidebarOpen ? '' : 'h-9 w-9 justify-center rounded-lg hover:bg-white/[0.06]'}`}
          >
            <svg className="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 24 24"><path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/></svg>
            {sidebarOpen && <span className="truncate">momadhuynh04/key-router</span>}
          </a>
          {sidebarOpen && <p className="text-[11px] text-text-muted mt-2 whitespace-nowrap">© {new Date().getFullYear()} HUYNHHOANG04</p>}
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <main className="flex-1 px-6 py-8">
          {activeTab === 'routing' && (
            <div className="flex min-h-[calc(100vh-4rem)] flex-col gap-8">
            <div className="border border-border bg-surface-900 p-6 rounded-2xl">
              <div className="flex items-center justify-between gap-4 mb-5">
                <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Active model routes</h2>
                <button onClick={() => setActiveTab('providers')} className="text-xs text-text-muted hover:text-brand transition-colors">Manage providers →</button>
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
                      <option value="claude">CLAUDE — ALL MODELS</option>
                      <option value="codex">CODEX — ALL MODELS</option>
                      {overrideKeys.map(key => (
                        <option key={key} value={key}>{key.toUpperCase()}</option>
                      ))}
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
                <p className="text-xs text-text-muted mt-3">
                  {sourceModel === 'claude' || sourceModel === 'codex'
                    ? `One route covers every ${sourceModel === 'claude' ? 'Claude' : 'Codex'} model, so upstream renames (opus/sonnet/haiku, gpt-5.x…) keep working without edits.`
                    : 'Explicit override — applies to this source key only; the family route still covers everything else.'}
                </p>
              </div>
            </div>

            <div className="relative flex min-h-[460px] flex-1 items-center overflow-hidden py-12">
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 opacity-30"
                style={{
                  backgroundImage: 'radial-gradient(circle, rgba(148,163,184,0.18) 1px, transparent 1px)',
                  backgroundSize: '26px 26px',
                }}
              />
              <div className="relative flex w-full flex-col items-center gap-12 xl:flex-row xl:items-center xl:justify-center xl:gap-12 min-[1440px]:gap-16">
                <div className="flex flex-col items-center text-center xl:items-start xl:text-left">
                  <a
                    href="https://github.com/momadhuynh04/key-router"
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Open the key-router repository on GitHub"
                    className="group inline-flex items-start gap-2 whitespace-nowrap font-tech text-6xl font-bold tracking-tighter text-text-primary transition-colors hover:text-brand sm:text-7xl xl:text-7xl min-[1440px]:text-8xl min-[1700px]:text-9xl"
                  >
                    key-router
                    <span aria-hidden="true" className="mt-1 text-xl text-text-muted transition-colors group-hover:text-brand sm:mt-2 sm:text-2xl">↗</span>
                  </a>
                  <p className="mt-3 font-tech text-[11px] uppercase tracking-[0.32em] text-text-muted sm:text-xs">
                    Universal proxy · Claude Code + Codex
                  </p>
                  <div className="mt-6 flex flex-wrap items-center justify-center gap-2 text-xs text-text-muted xl:justify-start">
                    <a
                      href="https://github.com/momadhuynh04/key-router"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-900/70 px-4 py-2 font-tech transition-colors hover:text-text-secondary"
                    >
                      <GitHubIcon className="h-4 w-4" />
                      momadhuynh04/key-router
                    </a>
                    <span className="rounded-full border border-border bg-surface-900/70 px-4 py-2 font-tech">v{APP_VERSION}</span>
                  </div>
                </div>

                <div className="grid w-full max-w-xl grid-cols-2 gap-3 xl:w-[500px] xl:max-w-none xl:shrink-0">
                  {[
                    ['Model routing', 'Claude · Codex → any provider'],
                    ['Agent bridge', 'Run Claude Code from chat'],
                    ['Skills manager', 'Install and toggle skills'],
                    ['Custom providers', 'Bring your own OpenAI API'],
                  ].map(([title, desc]) => (
                    <div key={title} className="rounded-2xl border border-border/70 bg-surface-900/40 px-4 py-4">
                      <p className="font-tech text-xs font-medium uppercase tracking-wider text-text-secondary">{title}</p>
                      <p className="mt-1 text-xs leading-relaxed text-text-muted">{desc}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            </div>
          )}

          {/* PROVIDERS */}
          {activeTab === 'providers' && (
            <CustomProvidersPanel
              providers={customProviders}
              mappings={mappings}
              onRefresh={refreshCustomProviders}
              onMappingsChanged={fetchMappings}
            />
          )}

          {/* TEST LAB */}
          {activeTab === 'testlab' && (
            <TestLabPanel />
          )}

          {/* LAUNCHER */}
          {activeTab === 'launcher' && (
            <div className="space-y-6">
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
                    {recentDirs.length > 0 && (
                      <div className="mb-4">
                        <p className="text-[11px] font-medium tracking-widest uppercase text-text-secondary mb-2">Recent folders</p>
                        <div className="flex flex-wrap gap-2">
                          {recentDirs.map(dir => {
                            const selected = localPath === dir;
                            return (
                              <button
                                key={dir}
                                type="button"
                                onClick={() => setLocalPath(dir)}
                                title={dir}
                                className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-mono transition-colors ${selected ? 'border-brand-border bg-brand-bg text-brand' : 'border-border bg-surface-900 text-text-muted hover:bg-surface-800 hover:text-text-secondary'}`}
                              >
                                <svg className="h-3.5 w-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75} aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" /></svg>
                                <span className="max-w-[280px] truncate">{dir}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
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
            <SkillsPanel projectPath={launchMode === 'local' ? localPath : ''} onOpenSettings={() => setActiveTab('agents')} />
            </div>
          )}

          {/* AGENTS */}
          {activeTab === 'agents' && (
            <div className="space-y-6">
              <div className="rounded-2xl border border-border bg-surface-900 p-5 lg:p-6">
                <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
                  <div>
                    <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">Agent config</h2>
                    <p className="text-sm text-text-secondary mt-1.5 max-w-2xl leading-relaxed">
                      Each agent is a CLI + model + workdir + permission combination. Pick one in Open WebUI with
                      <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded mx-1">/start &lt;id&gt;</code>,
                      stop it with <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">/stop</code>.
                    </p>
                  </div>
                  <button onClick={() => { setEditingAgent(null); setShowAgentModal(true); }} className="rounded-full border border-border bg-surface-850 hover:bg-surface-800 text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">+ Add agent</button>
                </div>

                {agentWarnings.length > 0 && (
                  <div className="text-sm text-danger rounded-lg bg-danger/10 border border-danger/20 p-3 mb-4 space-y-1">
                    {agentWarnings.map((w, i) => <p key={i}>{w}</p>)}
                  </div>
                )}

                {agents.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-border bg-surface-850 py-10 text-center text-sm text-text-muted">
                    No agents yet. Add one so Open WebUI has something to run.
                  </div>
                ) : (
                  <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850">
                    {agents.map(a => (
                      <div key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-surface-800/50 transition-colors">
                        <span className="inline-flex items-center rounded-full bg-brand-bg border border-brand-border text-brand text-xs font-mono px-3 py-1">{a.id}</span>
                        {a.id === agentSettings?.default_agent && (
                          <span className="text-[10px] uppercase tracking-wider text-text-muted border border-border rounded-full px-2 py-0.5">default</span>
                        )}
                        <span className="text-xs text-text-secondary font-medium">{a.cli}</span>
                        <span className="text-xs text-text-muted">{a.model || '(default)'}</span>
                        <span className="text-xs text-text-muted px-2 py-0.5 rounded-full border border-border" title="Permission mode">{a.permission}</span>
                        <span className="text-xs text-text-muted px-2 py-0.5 rounded-full border border-border" title="Tool profile">{a.tools}</span>
                        <span className="text-xs text-text-muted">{a.effort}</span>
                        <span className="text-xs text-text-muted font-mono truncate max-w-[220px]" title={a.workdir}>{a.workdir}</span>
                        <div className="ml-auto flex gap-3">
                          <button onClick={() => { setEditingAgent(a); setShowAgentModal(true); }} className="text-xs text-text-secondary hover:text-brand transition-colors">Edit</button>
                          <button onClick={() => deleteAgent(a.id)} className="text-xs text-text-muted hover:text-danger transition-colors">Delete</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                <div className="mt-5 rounded-xl border border-border bg-surface-850 p-4">
                  <p className="text-[11px] font-medium tracking-widest uppercase text-text-muted mb-1">Permission cheat sheet</p>
                  <p className="text-xs text-text-muted mb-3 leading-relaxed">
                    Headless runs never prompt: anything that would ask is denied instead, and the chat shows a red
                    <span className="text-danger"> Blocked by permissions</span> line. Tools decide which tools exist; permission decides which ones may run.
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
                    {[
                      { mode: 'plan', text: 'Read-only. No edits, no shell.' },
                      { mode: 'edit', text: 'Edits files in the workdir. Shell commands such as rg, find, and git are denied.' },
                      { mode: 'auto', text: 'A classifier approves ordinary actions, including safe shell commands.' },
                      { mode: 'ci', text: 'Only pre-approved tools. Add patterns like Bash(rg:*) to allow shell.' },
                      { mode: 'bypass', text: 'No checks at all. Requires allow_bypass in Bridge settings.' },
                    ].map(p => (
                      <div key={p.mode} className="rounded-lg border border-border bg-surface-900 px-3 py-2">
                        <code className="text-xs font-mono text-brand">{p.mode}</code>
                        <p className="text-xs text-text-muted mt-1 leading-relaxed">{p.text}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <BridgePanel
                settings={agentSettings}
                agentIds={agents.map(a => a.id)}
                onSettingsChanged={fetchAgents}
                onStatusChange={setBridgeRunning}
              />
            </div>
          )}

          {/* SETTINGS */}
          {activeTab === 'settings' && (
            <SettingsPanel
              onOpenProviders={() => setActiveTab('providers')}
              onKeysChanged={refreshCustomProviders}
            />
          )}

        </main>

        {showAgentModal && <AgentModal agent={editingAgent} existingIds={agents.map(a => a.id)} allowedRoots={agentSettings?.allowed_roots || []} modelMappings={mappings} onClose={() => setShowAgentModal(false)} onSaved={() => { setShowAgentModal(false); fetchAgents(); }} />}

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
