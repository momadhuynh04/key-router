import { useState, useEffect, useRef } from 'react'
import CustomProviderModal from './CustomProviderModal'
import AgentModal from './AgentModal'
import BridgePanel, { type BridgeSettings } from './BridgePanel'
import type { Agent } from './AgentModal'

type AdCraftStatus = {
  alias: string;
  mapping: string | null;
  provider: string | null;
  model: string | null;
  configured: boolean;
  endpoint: string;
  chat_completions_url: string;
  api_key: string;
  api_key_env?: string;
}

type AdCraftTestResult = {
  status: string;
  error?: string;
  target_model?: string | null;
  response_format_sent?: { type: string } | null;
  tool_called?: boolean;
  finish_reason?: string;
  content?: string | null;
  tool_calls?: Array<{ function?: { name?: string; arguments?: string } }>;
  usage?: Record<string, number>;
}

type ImageModelOption = {
  id: string;
  name: string;
  target: string;
  max_references: number;
  aspect_ratios: string[];
  supports_output_format: boolean;
}

type AdCraftImageStatus = {
  alias: string;
  mapping: string | null;
  backend: string | null;
  upstream_model: string | null;
  configured: boolean;
  endpoint: string;
  alias_endpoint: string;
  api_key: string;
  api_key_env?: string;
  openrouter_configured: boolean;
  replicate_configured: boolean;
}

type AdCraftImageTestResult = {
  status: string;
  error?: { code?: string; message?: string };
  b64_json?: string;
  media_type?: string;
  elapsed_ms?: number;
  bytes?: number;
  width?: number | null;
  height?: number | null;
  backend?: string;
  upstream_model?: string;
  references_used?: number;
  with_reference?: boolean;
  http_status?: number;
}

type VideoProfileOption = {
  id: string;
  name: string;
  driver: string;
  family: string | null;
  quality_tier: string | null;
  max_references: number | null;
  resolutions: string[] | null;
  aspect_ratios: string[] | null;
  duration_range: number[] | null;
  supports_native_audio: boolean | null;
  current?: boolean;
}

type AdCraftVideoStatus = {
  alias: string;
  configured: boolean;
  driver: string | null;
  family: string | null;
  quality_tier: string | null;
  endpoint: string;
  content_endpoint: string;
  api_key: string;
  auth_required: boolean;
  gateway_configured: boolean;
  gateway_url: string | null;
}

type VideoJobView = {
  id: string;
  status: string;
  progress?: number;
  output?: { url?: string; resolution?: string; duration_seconds?: number; seed?: number } | null;
  usage?: Record<string, number | string | null> | null;
  error?: { code?: string; message?: string } | null;
}

type AdCraftVideoTestResult = {
  status: string;
  error?: { code?: string; message?: string };
  job?: VideoJobView;
  http_status?: number;
  with_reference?: boolean;
}

type AudioJobView = {
  id: string;
  status: string;
  progress?: number;
  output?: {
    url?: string;
    duration_seconds?: number;
    sample_rate?: number;
    channels?: number;
    size_bytes?: number;
  } | null;
  usage?: Record<string, number | string | null> | null;
  error?: { code?: string; message?: string } | null;
}

type AdCraftAudioStatus = {
  alias: string;
  configured: boolean;
  driver: string | null;
  family: string | null;
  endpoint: string;
  api_key: string;
  auth_required: boolean;
  gateway_configured: boolean;
  gateway_url: string | null;
  video_allow_hosts: string;
}

type AdCraftAudioTestResult = {
  status: string;
  error?: { code?: string; message?: string };
  job?: AudioJobView;
  video_source?: string | null;
  http_status?: number;
}

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

type AdCraftSection = 'chat' | 'image' | 'video' | 'sfx'

const ADCRAFT_SECTIONS: { id: AdCraftSection; label: string }[] = [
  { id: 'chat', label: 'Chat' },
  { id: 'image', label: 'Image' },
  { id: 'video', label: 'Video' },
  { id: 'sfx', label: 'SFX' },
]

function App() {
  const [activeTab, setActiveTab] = useState<'routing' | 'launcher' | 'agents' | 'adcraft'>('routing');
  const [adcraftSection, setAdcraftSection] = useState<AdCraftSection>('chat');
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
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentSettings, setAgentSettings] = useState<BridgeSettings | null>(null);
  const [agentWarnings, setAgentWarnings] = useState<string[]>([]);
  const [bridgeRunning, setBridgeRunning] = useState(false);
  const [showAgentModal, setShowAgentModal] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [adcraft, setAdcraft] = useState<AdCraftStatus | null>(null);
  const [adcraftProvider, setAdcraftProvider] = useState("openrouter");
  const [adcraftModel, setAdcraftModel] = useState("");
  const [adcraftSaving, setAdcraftSaving] = useState(false);
  const [adcraftTesting, setAdcraftTesting] = useState(false);
  const [adcraftTestResult, setAdcraftTestResult] = useState<AdCraftTestResult | null>(null);

  const fetchAdcraft = () => {
    fetch('/api/adcraft/status').then(r => r.json()).then(d => {
      setAdcraft(d);
      if (d.provider) setAdcraftProvider(d.provider);
    }).catch(() => {});
  };
  useEffect(() => { fetchAdcraft(); }, []);

  const saveAdcraftModel = async () => {
    if (!adcraftModel) return;
    const cleanTarget = adcraftModel.startsWith(adcraftProvider + "/") ? adcraftModel.replace(adcraftProvider + "/", "") : adcraftModel;
    setAdcraftSaving(true);
    try {
      const res = await fetch('/api/adcraft/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target: `${adcraftProvider}/${cleanTarget}` }) });
      const d = await res.json();
      if (d.status === 'success') { setAdcraft(d); setAdcraftModel(""); }
      else { alert(d.detail || d.error || 'Failed to save AdCraft model'); }
    } catch (e) { console.error(e); }
    setAdcraftSaving(false);
  };

  const testAdcraft = async () => {
    setAdcraftTesting(true);
    setAdcraftTestResult(null);
    try {
      const res = await fetch('/api/adcraft/test', { method: 'POST' });
      setAdcraftTestResult(await res.json());
      fetchAdcraft();
    } catch (e) { setAdcraftTestResult({ status: 'error', error: String(e) }); }
    setAdcraftTesting(false);
  };

  const [adcraftImage, setAdcraftImage] = useState<AdCraftImageStatus | null>(null);
  const [imageModels, setImageModels] = useState<{ openrouter: ImageModelOption[]; replicate: ImageModelOption[] }>({ openrouter: [], replicate: [] });
  const [imageProvider, setImageProvider] = useState("openrouter");
  const [imageModel, setImageModel] = useState("");
  const [imageSaving, setImageSaving] = useState(false);
  const [imageTesting, setImageTesting] = useState(false);
  const [imageTestResult, setImageTestResult] = useState<AdCraftImageTestResult | null>(null);

  const fetchAdcraftImage = () => {
    fetch('/api/adcraft-image/status').then(r => r.json()).then(d => {
      setAdcraftImage(d);
      if (d.backend) setImageProvider(d.backend);
    }).catch(() => {});
    fetch('/api/adcraft-image/models').then(r => r.json()).then(d => {
      setImageModels({ openrouter: d.openrouter || [], replicate: d.replicate || [] });
    }).catch(() => {});
  };
  useEffect(() => { fetchAdcraftImage(); }, []);

  const saveImageModel = async () => {
    if (!imageModel) return;
    const options = imageModels[imageProvider as 'openrouter' | 'replicate'] || [];
    const selected = options.find(o => o.id === imageModel);
    const target = selected ? selected.target : `${imageProvider}/${imageModel}`;
    setImageSaving(true);
    try {
      const res = await fetch('/api/adcraft-image/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target }) });
      const d = await res.json();
      if (d.status === 'success') { setAdcraftImage(d); setImageModel(""); }
      else { alert(d.detail || d.error || 'Failed to save image model'); }
    } catch (e) { console.error(e); }
    setImageSaving(false);
  };

  const testImage = async (withReference: boolean) => {
    setImageTesting(true);
    setImageTestResult(null);
    try {
      const res = await fetch('/api/adcraft-image/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ with_reference: withReference }) });
      setImageTestResult(await res.json());
      fetchAdcraftImage();
    } catch (e) {
      setImageTestResult({ status: 'error', error: { code: 'request_failed', message: String(e) }, with_reference: withReference });
    }
    setImageTesting(false);
  };

  const [videoStatus, setVideoStatus] = useState<AdCraftVideoStatus | null>(null);
  const [videoProfiles, setVideoProfiles] = useState<VideoProfileOption[]>([]);
  const [videoProfile, setVideoProfile] = useState("");
  const [videoSaving, setVideoSaving] = useState(false);
  const [videoTesting, setVideoTesting] = useState(false);
  const [videoTestResult, setVideoTestResult] = useState<AdCraftVideoTestResult | null>(null);
  const [videoJobs, setVideoJobs] = useState<VideoJobView[]>([]);

  const fetchVideo = () => {
    fetch('/api/adcraft-video/status').then(r => r.json()).then(setVideoStatus).catch(() => {});
    fetch('/api/adcraft-video/models').then(r => r.json()).then(d => setVideoProfiles(d.models || [])).catch(() => {});
    fetch('/api/adcraft-video/jobs?limit=5').then(r => r.json()).then(d => setVideoJobs(d.jobs || [])).catch(() => {});
  };
  useEffect(() => { fetchVideo(); }, []);

  const saveVideoProfile = async () => {
    if (!videoProfile) return;
    setVideoSaving(true);
    try {
      const res = await fetch('/api/adcraft-video/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target: videoProfile }) });
      const d = await res.json();
      if (d.status === 'success') { setVideoStatus(d); setVideoProfile(""); fetchVideo(); }
      else { alert(d.detail || 'Failed to save video profile'); }
    } catch (e) { console.error(e); }
    setVideoSaving(false);
  };

  const testVideo = async (withReference: boolean) => {
    setVideoTesting(true);
    setVideoTestResult(null);
    try {
      const res = await fetch('/api/adcraft-video/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ wait_seconds: 0, with_reference: withReference }) });
      const data = await res.json();
      setVideoTestResult(data);

      // Poll the job until it finishes (cold start + generation can take ~2 min).
      const jobId = data?.job?.id;
      if (jobId) {
        const deadline = Date.now() + 15 * 60 * 1000;
        while (Date.now() < deadline) {
          await new Promise(r => setTimeout(r, 3000));
          const jr = await fetch(`/api/adcraft-video/jobs/${jobId}`);
          if (!jr.ok) break;
          const job = await jr.json();
          setVideoTestResult(prev => ({
            ...(prev || {}),
            status: job.status === 'completed' ? 'completed' : 'pending',
            job,
          }));
          if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') break;
        }
      }
      fetchVideo();
    } catch (e) {
      setVideoTestResult({ status: 'error', error: { code: 'request_failed', message: String(e) } });
    }
    setVideoTesting(false);
  };

  const [audioStatus, setAudioStatus] = useState<AdCraftAudioStatus | null>(null);
  const [audioTesting, setAudioTesting] = useState(false);
  const [audioTestResult, setAudioTestResult] = useState<AdCraftAudioTestResult | null>(null);
  const [audioJobs, setAudioJobs] = useState<AudioJobView[]>([]);

  const fetchAudio = () => {
    fetch('/api/adcraft-audio/status').then(r => r.json()).then(setAudioStatus).catch(() => {});
    fetch('/api/adcraft-audio/jobs?limit=5').then(r => r.json()).then(d => setAudioJobs(d.jobs || [])).catch(() => {});
  };
  useEffect(() => { fetchAudio(); }, []);

  const testAudio = async () => {
    setAudioTesting(true);
    setAudioTestResult(null);
    try {
      const res = await fetch('/api/adcraft-audio/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ wait_seconds: 0 }) });
      const data = await res.json();
      setAudioTestResult(data);

      const jobId = data?.job?.id;
      if (jobId) {
        const deadline = Date.now() + 15 * 60 * 1000;
        while (Date.now() < deadline) {
          await new Promise(r => setTimeout(r, 3000));
          const jr = await fetch(`/api/adcraft-audio/jobs/${jobId}`);
          if (!jr.ok) break;
          const job = await jr.json();
          setAudioTestResult(prev => ({
            ...(prev || {}),
            status: job.status === 'succeeded' ? 'succeeded' : 'pending',
            job,
          }));
          if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') break;
        }
      }
      fetchAudio();
    } catch (e) {
      setAudioTestResult({ status: 'error', error: { code: 'request_failed', message: String(e) } });
    }
    setAudioTesting(false);
  };

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
  const launcherIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
  const agentsIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z" /></svg>
  const adcraftIcon = <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M15.75 10.5l4.72-2.36A.75.75 0 0121.75 9v6a.75.75 0 01-1.28.55l-4.72-4.05zM3.75 6.75h9A2.25 2.25 0 0115 9v6a2.25 2.25 0 01-2.25 2.25h-9A2.25 2.25 0 011.5 15V9a2.25 2.25 0 012.25-2.25z" /></svg>

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
          <div className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-surface-850 border border-border px-3 py-1 text-xs text-text-muted">
            <span className={`w-2 h-2 rounded-full ${bridgeRunning ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
            bridge · :{agentSettings?.port ?? 8083}
          </div>
        </div>
        <nav className="px-3 py-2 space-y-1 flex-1">
          <NavButton active={activeTab === 'routing'} onClick={() => setActiveTab('routing')} icon={routingIcon} label="Model routing" />
          <NavButton active={activeTab === 'launcher'} onClick={() => setActiveTab('launcher')} icon={launcherIcon} label="Agent launcher" />
          <NavButton active={activeTab === 'agents'} onClick={() => setActiveTab('agents')} icon={agentsIcon} label="Agents" />
          <NavButton active={activeTab === 'adcraft'} onClick={() => setActiveTab('adcraft')} icon={adcraftIcon} label="AdCraft" />
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

          {/* ADCRAFT SECTION NAV */}
          {activeTab === 'adcraft' && (
            <div className="inline-flex flex-wrap p-1 mb-6 bg-surface-850 rounded-full border border-border gap-1">
              {ADCRAFT_SECTIONS.map(s => (
                <button key={s.id} onClick={() => setAdcraftSection(s.id)} className={`px-4 py-1.5 rounded-full text-xs font-medium transition-colors ${adcraftSection === s.id ? 'bg-white text-surface-950 shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}>{s.label}</button>
              ))}
            </div>
          )}

          {/* ADCRAFT */}
          {activeTab === 'adcraft' && adcraftSection === 'chat' && (
            <div className="space-y-6">
              <div className="border border-border bg-surface-900 p-6 rounded-2xl">
                <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
                  <div>
                    <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">AdCraft integration</h2>
                    <p className="text-sm text-text-secondary mt-1.5 max-w-2xl leading-relaxed">
                      Point AdCraft at the proxy and it only ever needs to send <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">model: "adcraft"</code>. The real target model is chosen here.
                    </p>
                  </div>
                  <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${adcraft?.configured ? 'border-success/30 bg-success/10 text-success' : 'border-border bg-surface-850 text-text-muted'}`}>
                    <span className={`w-2 h-2 rounded-full ${adcraft?.configured ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
                    {adcraft?.configured ? 'Active' : 'Not configured'}
                  </span>
                </div>

                <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850">
                  <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <span className="inline-flex items-center rounded-full bg-accent-bg border border-accent-border text-accent text-xs font-medium px-3 py-1 font-mono">ADCRAFT</span>
                    <span className="text-text-muted">→</span>
                    <span className="inline-flex items-center rounded-full bg-brand-bg border border-brand-border text-brand text-xs font-mono px-3 py-1 break-all">{adcraft?.mapping || 'not set'}</span>
                  </div>
                  <div className="px-4 py-3 text-xs text-text-muted font-mono break-all space-y-1">
                    <div>
                      AdCraft endpoint → <span className="text-text-secondary">{adcraft?.endpoint || 'http://127.0.0.1:8082/v1'}</span>
                      <span> · POST </span>
                      <span className="text-text-secondary">{adcraft?.chat_completions_url || 'http://127.0.0.1:8082/v1/chat/completions'}</span>
                    </div>
                    <div>
                      API key → <span className="text-text-secondary">{adcraft?.api_key || 'key-router'}</span>
                      <span className="font-sans"> (any value works — the proxy performs no auth)</span>
                    </div>
                  </div>
                </div>

                <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                  <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary mb-4">Target model for AdCraft</h3>
                  <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.7fr_auto] gap-4 items-end">
                    <div>
                      <label className="block text-[11px] font-medium tracking-widest uppercase text-text-secondary mb-1.5">Target provider</label>
                      <select value={adcraftProvider} onChange={e => { setAdcraftProvider(e.target.value); setAdcraftModel(""); }} className="w-full h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand">
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
                      <SearchableDropdown options={availableModels[adcraftProvider] || []} value={adcraftModel} onChange={setAdcraftModel} placeholder="Search and select a model…" />
                    </div>
                    <button onClick={saveAdcraftModel} disabled={!adcraftModel || adcraftSaving} className="h-9 rounded-full bg-brand text-white hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-sm font-medium px-6 transition-colors">{adcraftSaving ? 'Saving…' : 'Save'}</button>
                  </div>
                </div>

                <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                  <div className="flex flex-wrap items-center justify-between gap-4 mb-3">
                    <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary">Connection test</h3>
                    <button onClick={testAdcraft} disabled={adcraftTesting || !adcraft?.configured} className="rounded-full border border-border bg-surface-900 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">{adcraftTesting ? 'Testing…' : 'Run test'}</button>
                  </div>
                  <p className="text-xs text-text-muted leading-relaxed mb-3">Sends a simulated AdCraft request through the proxy: <code className="font-mono">response_format: json_object</code> plus the <code className="font-mono">submit_structured_result</code> tool.</p>
                  {adcraftTestResult && (
                    <div className={`rounded-lg border p-3 text-xs space-y-1.5 ${adcraftTestResult.status === 'success' ? 'border-success/20 bg-success/10' : 'border-danger/20 bg-danger/10'}`}>
                      {adcraftTestResult.status === 'success' ? (
                        <>
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-text-secondary">
                            <span className={`font-semibold ${adcraftTestResult.tool_called ? 'text-success' : 'text-accent'}`}>{adcraftTestResult.tool_called ? '✓ tool called' : '⚠ no tool call'}</span>
                            <span className="text-text-muted">model: <span className="font-mono">{adcraftTestResult.target_model}</span></span>
                            <span className="text-text-muted">finish: <span className="font-mono">{adcraftTestResult.finish_reason}</span></span>
                            <span className="text-text-muted">response_format: <span className="font-mono">{adcraftTestResult.response_format_sent?.type || 'none'}</span></span>
                          </div>
                          {adcraftTestResult.tool_calls && adcraftTestResult.tool_calls.length > 0 && (
                            <pre className="font-mono text-[11px] text-text-secondary bg-surface-900 border border-border rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">{adcraftTestResult.tool_calls.map(tc => `${tc.function?.name}(${tc.function?.arguments})`).join('\n')}</pre>
                          )}
                          {adcraftTestResult.content && (
                            <p className="text-text-muted whitespace-pre-wrap break-words">{adcraftTestResult.content}</p>
                          )}
                        </>
                      ) : (
                        <p className="text-danger break-words">{adcraftTestResult.error || 'Test failed'}</p>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ADCRAFT IMAGE */}
          {activeTab === 'adcraft' && adcraftSection === 'image' && (
            <div className="space-y-6">
              <div className="border border-border bg-surface-900 p-6 rounded-2xl">
                <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
                  <div>
                    <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">AdCraft image</h2>
                    <p className="text-sm text-text-secondary mt-1.5 max-w-2xl leading-relaxed">
                      AdCraft sends image requests with <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">model: "adcraft-image"</code>. The real backend and model are chosen here.
                    </p>
                  </div>
                  <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${adcraftImage?.configured ? 'border-success/30 bg-success/10 text-success' : 'border-border bg-surface-850 text-text-muted'}`}>
                    <span className={`w-2 h-2 rounded-full ${adcraftImage?.configured ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
                    {adcraftImage?.configured ? 'Active' : 'Not configured'}
                  </span>
                </div>

                <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850">
                  <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <span className="inline-flex items-center rounded-full bg-accent-bg border border-accent-border text-accent text-xs font-medium px-3 py-1 font-mono">ADCRAFT-IMAGE</span>
                    <span className="text-text-muted">→</span>
                    <span className="inline-flex items-center rounded-full bg-brand-bg border border-brand-border text-brand text-xs font-mono px-3 py-1 break-all">{adcraftImage?.mapping || 'not set'}</span>
                  </div>
                  <div className="px-4 py-3 text-xs text-text-muted font-mono break-all space-y-1">
                    <div>
                      AdCraft endpoint → <span className="text-text-secondary">{adcraftImage?.endpoint || 'http://127.0.0.1:8082/v1/images/generations'}</span>
                      <span> · alias </span>
                      <span className="text-text-secondary">{adcraftImage?.alias_endpoint || 'http://127.0.0.1:8082/v1/images'}</span>
                    </div>
                    <div>
                      API key → <span className="text-text-secondary">{adcraftImage?.api_key || 'key-router'}</span>
                      <span className="font-sans"> (any value works — the proxy performs no auth)</span>
                    </div>
                    <div className="font-sans">
                      Backend keys →{' '}
                      <span className={adcraftImage?.openrouter_configured ? 'text-success' : 'text-danger'}>{adcraftImage?.openrouter_configured ? 'OpenRouter ✓' : 'OpenRouter missing'}</span>
                      <span className="text-text-muted"> · </span>
                      <span className={adcraftImage?.replicate_configured ? 'text-success' : 'text-danger'}>{adcraftImage?.replicate_configured ? 'Replicate ✓' : 'Replicate token missing'}</span>
                    </div>
                  </div>
                </div>

                <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                  <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary mb-4">Image model for AdCraft</h3>
                  <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.7fr_auto] gap-4 items-end">
                    <div>
                      <label className="block text-[11px] font-medium tracking-widest uppercase text-text-secondary mb-1.5">Backend</label>
                      <select value={imageProvider} onChange={e => { setImageProvider(e.target.value); setImageModel(""); }} className="w-full h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand">
                        <option value="openrouter">OPENROUTER</option>
                        <option value="replicate">REPLICATE</option>
                      </select>
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <label className="text-[11px] font-medium tracking-widest uppercase text-text-secondary">Model</label>
                        <span className="text-xs text-text-muted">{(imageModels[imageProvider as 'openrouter' | 'replicate'] || []).length} available</span>
                      </div>
                      <SearchableDropdown options={(imageModels[imageProvider as 'openrouter' | 'replicate'] || []).map(o => o.id)} value={imageModel} onChange={setImageModel} placeholder="Search and select an image model…" />
                    </div>
                    <button onClick={saveImageModel} disabled={!imageModel || imageSaving} className="h-9 rounded-full bg-brand text-white hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-sm font-medium px-6 transition-colors">{imageSaving ? 'Saving…' : 'Save'}</button>
                  </div>
                </div>

                <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                  <div className="flex flex-wrap items-center justify-between gap-4 mb-3">
                    <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary">Connection test</h3>
                    <div className="flex gap-2">
                      <button onClick={() => testImage(false)} disabled={imageTesting || !adcraftImage?.configured} className="rounded-full border border-border bg-surface-900 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">{imageTesting ? 'Testing…' : 'Run test'}</button>
                      <button onClick={() => testImage(true)} disabled={imageTesting || !adcraftImage?.configured} className="rounded-full border border-border bg-surface-900 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">Test with reference</button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted leading-relaxed mb-3">Generates a small 1:1 test image. The reference test sends a tiny reference image to verify the backend accepts references.</p>

                  {imageTestResult && (
                    imageTestResult.status === 'success' && imageTestResult.b64_json ? (
                      <div className="rounded-lg border border-success/20 bg-success/10 p-3">
                        <div className="flex flex-wrap gap-4 items-start">
                          <img
                            src={`data:${imageTestResult.media_type || 'image/png'};base64,${imageTestResult.b64_json}`}
                            alt="AdCraft image test"
                            className="rounded-lg border border-border max-w-[320px] max-h-[320px] object-contain bg-surface-900"
                          />
                          <div className="text-xs space-y-1 text-text-secondary">
                            <div>backend: <span className="font-mono text-brand">{imageTestResult.backend}</span></div>
                            <div>model: <span className="font-mono text-brand">{imageTestResult.upstream_model}</span></div>
                            <div>time: <span className="font-mono">{imageTestResult.elapsed_ms} ms</span></div>
                            <div>size: <span className="font-mono">{imageTestResult.width ?? '?'}×{imageTestResult.height ?? '?'} · {(Number(imageTestResult.bytes || 0) / 1024).toFixed(1)} KB</span></div>
                            <div>references used: <span className="font-mono">{imageTestResult.references_used ?? 0}</span>{imageTestResult.with_reference ? ' (reference test)' : ''}</div>
                            <div>media: <span className="font-mono">{imageTestResult.media_type}</span></div>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="rounded-lg border border-danger/20 bg-danger/10 p-3 text-xs text-danger break-words">
                        <span className="font-mono">{imageTestResult.error?.code || 'error'}</span>
                        {imageTestResult.http_status ? <span className="text-text-muted"> · HTTP {imageTestResult.http_status}</span> : null}
                        <div className="mt-1 text-danger/90">{imageTestResult.error?.message || 'Test failed'}</div>
                      </div>
                    )
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ADCRAFT VIDEO */}
          {activeTab === 'adcraft' && adcraftSection === 'video' && (
            <div className="space-y-6">
              <div className="border border-border bg-surface-900 p-6 rounded-2xl">
                <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
                  <div>
                    <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">AdCraft video</h2>
                    <p className="text-sm text-text-secondary mt-1.5 max-w-2xl leading-relaxed">
                      Async route: AdCraft sends <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">model: "adcraft-video"</code> to <span className="font-mono text-xs">{videoStatus?.endpoint || 'http://127.0.0.1:8082/v1/videos'}</span> then polls. The real backend is chosen here.
                    </p>
                  </div>
                  <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${videoStatus?.configured ? 'border-success/30 bg-success/10 text-success' : 'border-border bg-surface-850 text-text-muted'}`}>
                    <span className={`w-2 h-2 rounded-full ${videoStatus?.configured ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
                    {videoStatus?.configured ? 'Configured' : 'Not configured'}
                  </span>
                </div>

                <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850">
                  <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <span className="inline-flex items-center rounded-full bg-accent-bg border border-accent-border text-accent text-xs font-medium px-3 py-1 font-mono">ADCRAFT-VIDEO</span>
                    <span className="text-text-muted">→</span>
                    <span className="inline-flex items-center rounded-full bg-brand-bg border border-brand-border text-brand text-xs font-mono px-3 py-1 break-all">
                      {videoStatus?.driver ? `${videoStatus.driver}${videoStatus.family ? ` · ${videoStatus.family}` : ''}` : 'not set'}
                    </span>
                  </div>
                  <div className="px-4 py-3 text-xs text-text-muted font-mono break-all space-y-1">
                    <div>
                      POST → <span className="text-text-secondary">{videoStatus?.endpoint || 'http://127.0.0.1:8082/v1/videos'}</span>
                    </div>
                    <div>
                      Content → <span className="text-text-secondary">{videoStatus?.content_endpoint || 'http://127.0.0.1:8082/v1/videos/{id}/content'}</span>
                    </div>
                    <div>
                      API key → <span className="text-text-secondary">{videoStatus?.api_key || 'key-router'}</span>
                      <span className="font-sans"> · auth {videoStatus?.auth_required ? 'required' : 'off'}</span>
                    </div>
                    <div className="font-sans">
                      Gateway →{' '}
                      <span className={videoStatus?.gateway_configured ? 'text-success' : 'text-danger'}>
                        {videoStatus?.gateway_configured ? (videoStatus?.gateway_url || 'configured') : 'MODAL_VIDEO_GATEWAY_URL not set'}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                  <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary mb-4">Video profile for AdCraft</h3>
                  <div className="grid grid-cols-1 lg:grid-cols-[1.7fr_auto] gap-4 items-end">
                    <div>
                      <label className="block text-[11px] font-medium tracking-widest uppercase text-text-secondary mb-1.5">Profile</label>
                      <select value={videoProfile} onChange={e => setVideoProfile(e.target.value)} className="w-full h-9 rounded-lg bg-surface-900 border border-border px-3 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand">
                        <option value="">— keep current —</option>
                        {videoProfiles.map(p => (
                          <option key={p.id} value={p.id}>{p.id}{p.current ? ' · current' : ''} · {p.driver}{p.family ? ` · ${p.family}` : ''}</option>
                        ))}
                      </select>
                    </div>
                    <button onClick={saveVideoProfile} disabled={!videoProfile || videoSaving} className="h-9 rounded-full bg-brand text-white hover:bg-brand-muted disabled:opacity-40 disabled:cursor-not-allowed text-sm font-medium px-6 transition-colors">{videoSaving ? 'Saving…' : 'Save'}</button>
                  </div>
                </div>

                <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                  <div className="flex flex-wrap items-center justify-between gap-4 mb-3">
                    <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary">Connection test</h3>
                    <div className="flex gap-2">
                      <button onClick={() => testVideo(false)} disabled={videoTesting || !videoStatus?.configured} className="rounded-full border border-border bg-surface-900 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">{videoTesting ? 'Testing…' : 'Run test'}</button>
                      <button onClick={() => testVideo(true)} disabled={videoTesting || !videoStatus?.configured} className="rounded-full border border-border bg-surface-900 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">Test with image</button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted leading-relaxed mb-3">
                    Submits a real 5s / 480p job and polls until it finishes. <span className="text-text-secondary">Test with image</span> sends a 256×256 reference frame, exercising image-to-video (AdCraft's keyframe/reference path).
                  </p>

                  {videoTestResult && (
                    <div className={`rounded-lg border p-3 text-xs space-y-2 ${videoTestResult.status === 'error' ? 'border-danger/20 bg-danger/10' : 'border-border bg-surface-900'}`}>
                      {videoTestResult.status === 'error' ? (
                        <div className="text-danger break-words">
                          <span className="font-mono">{videoTestResult.error?.code || 'error'}</span>
                          {videoTestResult.http_status ? <span className="text-text-muted"> · HTTP {videoTestResult.http_status}</span> : null}
                          <div className="mt-1 text-danger/90">{videoTestResult.error?.message || 'Test failed'}</div>
                        </div>
                      ) : (
                        <>
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-text-secondary">
                            <span className="font-mono">{videoTestResult.job?.id}</span>
                            <span className={`font-semibold ${videoTestResult.job?.status === 'completed' ? 'text-success' : 'text-accent'}`}>{videoTestResult.job?.status}</span>
                            <span className="text-text-muted">progress {Math.round((videoTestResult.job?.progress || 0) * 100)}%</span>
                            {videoTestResult.with_reference ? <span className="text-accent">image reference</span> : null}
                            {videoTestResult.job?.usage?.gpu ? <span className="text-text-muted">gpu <span className="font-mono">{String(videoTestResult.job.usage.gpu)}</span></span> : null}
                          </div>
                          {videoTestResult.job?.error ? (
                            <div className="text-danger break-words">{videoTestResult.job.error.code}: {videoTestResult.job.error.message}</div>
                          ) : null}
                          {videoTestResult.job?.status === 'completed' ? (
                            <video src={`/api/adcraft-video/jobs/${videoTestResult.job.id}/content`} controls className="rounded-lg border border-border max-w-full max-h-[360px] bg-black" />
                          ) : (
                            <div className="text-text-muted">Job is <span className="font-mono">{videoTestResult.job?.status || 'unknown'}</span> — polling automatically…</div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>

                {videoJobs.length > 0 && (
                  <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                    <div className="flex items-center justify-between gap-3 mb-3">
                      <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary">Recent jobs</h3>
                      <button onClick={fetchVideo} className="rounded-full border border-border bg-surface-900 hover:bg-surface-800 text-text-secondary text-xs font-medium px-3 py-1 transition-colors">Refresh</button>
                    </div>
                    <div className="rounded-lg border border-border overflow-hidden divide-y divide-border">
                      {videoJobs.map(job => (
                        <div key={job.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-xs">
                          <span className="font-mono text-text-secondary">{job.id}</span>
                          <span className={`font-medium ${job.status === 'completed' ? 'text-success' : job.status === 'failed' ? 'text-danger' : 'text-accent'}`}>{job.status}</span>
                          <span className="text-text-muted">{Math.round((job.progress || 0) * 100)}%</span>
                          {job.output?.resolution ? <span className="text-text-muted font-mono">{job.output.resolution}</span> : null}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ADCRAFT SFX */}
          {activeTab === 'adcraft' && adcraftSection === 'sfx' && (
            <div className="space-y-6">
              <div className="border border-border bg-surface-900 p-6 rounded-2xl">
                <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
                  <div>
                    <h2 className="text-[13px] font-semibold tracking-widest uppercase text-text-secondary">AdCraft SFX</h2>
                    <p className="text-sm text-text-secondary mt-1.5 max-w-2xl leading-relaxed">
                      Video-to-SFX (ambience / foley, <span className="text-text-secondary">no speech, no music</span>). AdCraft sends <code className="font-mono text-xs bg-surface-850 border border-border px-1.5 py-0.5 rounded">video_url</code>; the proxy fetches it locally and forwards the bytes to Modal.
                    </p>
                  </div>
                  <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${audioStatus?.configured ? 'border-success/30 bg-success/10 text-success' : 'border-border bg-surface-850 text-text-muted'}`}>
                    <span className={`w-2 h-2 rounded-full ${audioStatus?.configured ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
                    {audioStatus?.configured ? 'Configured' : 'Not configured'}
                  </span>
                </div>

                <div className="rounded-xl border border-border overflow-hidden divide-y divide-border bg-surface-850">
                  <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <span className="inline-flex items-center rounded-full bg-accent-bg border border-accent-border text-accent text-xs font-medium px-3 py-1 font-mono">ADCRAFT-AUDIO</span>
                    <span className="text-text-muted">→</span>
                    <span className="inline-flex items-center rounded-full bg-brand-bg border border-brand-border text-brand text-xs font-mono px-3 py-1 break-all">
                      {audioStatus?.driver ? `${audioStatus.driver}${audioStatus.family ? ` · ${audioStatus.family}` : ''}` : 'not set'}
                    </span>
                  </div>
                  <div className="px-4 py-3 text-xs text-text-muted font-mono break-all space-y-1">
                    <div>POST → <span className="text-text-secondary">{audioStatus?.endpoint || 'http://127.0.0.1:8082/v1/audio/video-to-sfx'}</span></div>
                    <div>API key → <span className="text-text-secondary">{audioStatus?.api_key || 'key-router'}</span><span className="font-sans"> · auth {audioStatus?.auth_required ? 'required' : 'off'}</span></div>
                    <div className="font-sans">Gateway → <span className={audioStatus?.gateway_configured ? 'text-success' : 'text-danger'}>{audioStatus?.gateway_configured ? (audioStatus?.gateway_url || 'configured') : 'MODAL_AUDIO_GATEWAY_URL not set'}</span></div>
                    <div className="font-sans">Video allowlist → <span className="font-mono text-text-secondary">{audioStatus?.video_allow_hosts || '—'}</span></div>
                  </div>
                </div>

                <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                  <div className="flex flex-wrap items-center justify-between gap-4 mb-3">
                    <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary">Connection test</h3>
                    <button onClick={testAudio} disabled={audioTesting || !audioStatus?.configured} className="rounded-full border border-border bg-surface-900 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed text-text-secondary text-xs font-medium px-4 py-1.5 transition-colors">{audioTesting ? 'Testing…' : 'Test SFX (latest video)'}</button>
                  </div>
                  <p className="text-xs text-text-muted leading-relaxed mb-3">
                    Uses the newest completed video job (or a 2s generated clip) and runs it through the SFX pipeline. Polls automatically and shows a player when the WAV is ready.
                  </p>

                  {audioTestResult && (
                    <div className={`rounded-lg border p-3 text-xs space-y-2 ${audioTestResult.status === 'error' ? 'border-danger/20 bg-danger/10' : 'border-border bg-surface-900'}`}>
                      {audioTestResult.status === 'error' ? (
                        <div className="text-danger break-words">
                          <span className="font-mono">{audioTestResult.error?.code || 'error'}</span>
                          {audioTestResult.http_status ? <span className="text-text-muted"> · HTTP {audioTestResult.http_status}</span> : null}
                          <div className="mt-1 text-danger/90">{audioTestResult.error?.message || 'Test failed'}</div>
                        </div>
                      ) : (
                        <>
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-text-secondary">
                            <span className="font-mono">{audioTestResult.job?.id}</span>
                            <span className={`font-semibold ${audioTestResult.job?.status === 'succeeded' ? 'text-success' : 'text-accent'}`}>{audioTestResult.job?.status}</span>
                            {audioTestResult.video_source ? <span className="text-text-muted font-mono">{audioTestResult.video_source}</span> : null}
                          </div>
                          {audioTestResult.job?.error ? (
                            <div className="text-danger break-words">{audioTestResult.job.error.code}: {audioTestResult.job.error.message}</div>
                          ) : null}
                          {audioTestResult.job?.status === 'succeeded' ? (
                            <div className="space-y-2">
                              <audio src={`/api/adcraft-audio/jobs/${audioTestResult.job.id}/content`} controls className="w-full" />
                              <div className="text-text-muted">
                                {audioTestResult.job.output?.duration_seconds?.toFixed(2)}s · {audioTestResult.job.output?.sample_rate} Hz · {audioTestResult.job.output?.channels} ch · {Math.round((audioTestResult.job.output?.size_bytes || 0) / 1024)} KB
                              </div>
                            </div>
                          ) : (
                            <div className="text-text-muted">Job is <span className="font-mono">{audioTestResult.job?.status || 'unknown'}</span> — polling automatically…</div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>

                {audioJobs.length > 0 && (
                  <div className="rounded-xl bg-surface-850 border border-border p-5 mt-6">
                    <div className="flex items-center justify-between gap-3 mb-3">
                      <h3 className="text-xs font-semibold tracking-widest uppercase text-text-secondary">Recent audio jobs</h3>
                      <button onClick={fetchAudio} className="rounded-full border border-border bg-surface-900 hover:bg-surface-800 text-text-secondary text-xs font-medium px-3 py-1 transition-colors">Refresh</button>
                    </div>
                    <div className="rounded-lg border border-border overflow-hidden divide-y divide-border">
                      {audioJobs.map(job => (
                        <div key={job.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-xs">
                          <span className="font-mono text-text-secondary">{job.id}</span>
                          <span className={`font-medium ${job.status === 'succeeded' ? 'text-success' : job.status === 'failed' ? 'text-danger' : 'text-accent'}`}>{job.status}</span>
                          {job.output?.duration_seconds ? <span className="text-text-muted font-mono">{job.output.duration_seconds.toFixed(2)}s</span> : null}
                          {job.output?.sample_rate ? <span className="text-text-muted font-mono">{job.output.sample_rate} Hz</span> : null}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
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
                        <span className="text-xs text-text-muted px-2 py-0.5 rounded-full border border-border">{a.permission}</span>
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
              </div>

              <BridgePanel
                settings={agentSettings}
                agentIds={agents.map(a => a.id)}
                onSettingsChanged={fetchAgents}
                onStatusChange={setBridgeRunning}
              />
            </div>
          )}

        </main>

        {showCustomModal && <CustomProviderModal customProviders={customProviders} onClose={() => setShowCustomModal(false)} onSaved={() => { setShowCustomModal(false); fetchCustomProviders(); fetch('/api/available-models').then(r=>r.json()).then(d=>setAvailableModels(d)).catch(()=>{}); }} />}

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
