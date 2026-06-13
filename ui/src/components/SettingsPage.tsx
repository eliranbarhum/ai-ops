import { useState, useEffect, useRef } from 'react'
import { Save, TestTube2, CheckCircle, XCircle, Loader2, Eye, EyeOff, ArrowLeft, Play, Trash2, RefreshCw, Plus, Clock } from 'lucide-react'
import { useToast } from './Toast'
import { useVisibilityPolling } from '../hooks/useVisibilityPolling'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface Config {
  vcenter_host: string
  vcenter_user: string
  vcenter_password: string
  vcenter_verify_ssl: boolean
  vrops_host: string
  vrops_user: string
  vrops_password: string
  vrops_verify_ssl: boolean
  sddc_host: string
  sddc_user: string
  sddc_password: string
  sddc_verify_ssl: boolean
  nsx_host: string
  nsx_user: string
  nsx_password: string
  nsx_verify_ssl: boolean
  ad_host: string
  ad_user: string
  ad_password: string
  ad_domain: string
  llm_provider: string
  anthropic_api_key: string
  anthropic_model: string
  openai_api_key: string
  openai_model: string
  gemini_api_key: string
  gemini_model: string
  vllm_url: string
  vllm_model: string
  vcf_target_version: string
  agent_llm_provider: string
  agent_anthropic_api_key: string
  agent_anthropic_model: string
  agent_openai_api_key: string
  agent_openai_model: string
  agent_gemini_api_key: string
  agent_gemini_model: string
  agent_ollama_url: string
  agent_ollama_model: string
}

interface TestResult { ok: boolean; message: string }

const ANTHROPIC_MODELS = ['claude-sonnet-4-6', 'claude-opus-4-7', 'claude-haiku-4-5-20251001']
const OPENAI_MODELS = ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'o1', 'o3-mini']
const GEMINI_MODELS = ['gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-1.5-pro', 'gemini-1.5-flash']
const VCF_VERSIONS = ['9.1', '9.0', '5.2', '5.1', '5.0']
type LLMProvider = 'anthropic' | 'openai' | 'gemini' | 'ollama'

interface OllamaStatus {
  deployed: boolean
  status: string
  loaded_models?: string[]
  ready_replicas?: number
  desired_replicas?: number
  vllm_url?: string
  message?: string
}

interface PullLogs {
  status: string   // idle | deploying | pulling | ready | error
  model: string
  logs: string[]
  progress_pct: number
}

const OLLAMA_MODELS = [
  { tag: 'smollm2:1.7b',  label: 'SmolLM2 1.7B',  size_gb: 1,  ram_recommended: 16 },
  { tag: 'qwen2.5:7b',    label: 'Qwen2.5 7B',     size_gb: 5,  ram_recommended: 16 },
  { tag: 'qwen2.5:14b',   label: 'Qwen2.5 14B',    size_gb: 9,  ram_recommended: 32 },
  { tag: 'qwen2.5:32b',   label: 'Qwen2.5 32B',    size_gb: 20, ram_recommended: 48 },
  { tag: 'llama3.1:8b',   label: 'Llama 3.1 8B',   size_gb: 5,  ram_recommended: 16 },
  { tag: 'llama3.1:70b',  label: 'Llama 3.1 70B',  size_gb: 43, ram_recommended: 96 },
  { tag: 'phi4:14b',      label: 'Phi-4 14B',       size_gb: 9,  ram_recommended: 32 },
  { tag: 'mistral:7b',    label: 'Mistral 7B',      size_gb: 5,  ram_recommended: 16 },
  { tag: 'gemma3:9b',     label: 'Gemma3 9B',       size_gb: 6,  ram_recommended: 16 },
]
const OLLAMA_RAM_OPTIONS = [16, 32, 48, 64, 96]

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`w-2 h-2 rounded-full flex-shrink-0 ${ok ? 'bg-green-400' : 'bg-red-400'}`} />
}

function Field({
  label, value, onChange, placeholder = '', type = 'text', sensitive = false, mono = false
}: {
  label: string; value: string; onChange: (v: string) => void
  placeholder?: string; type?: string; sensitive?: boolean; mono?: boolean
}) {
  const [show, setShow] = useState(false)
  const inputType = sensitive ? (show ? 'text' : 'password') : type
  return (
    <div>
      <label className="block text-xs text-slate-400 mb-1">{label}</label>
      <div className="relative">
        <input
          type={inputType}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className={`w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500 pr-9${mono ? ' font-mono' : ''}`}
        />
        {sensitive && (
          <button
            type="button"
            onClick={() => setShow(s => !s)}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
          >
            {show ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        )}
      </div>
    </div>
  )
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-slate-400">{label}</span>
      <button
        onClick={() => onChange(!value)}
        className={`w-10 h-5 rounded-full transition-colors relative ${value ? 'bg-blue-600' : 'bg-slate-600'}`}
      >
        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${value ? 'left-5.5 translate-x-0.5' : 'left-0.5'}`} />
      </button>
    </div>
  )
}

function IntegrationCard({
  title, badge, children, onTest, testResult, testing
}: {
  title: string; badge: string; children: React.ReactNode
  onTest: () => void; testResult: TestResult | null; testing: boolean
}) {
  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-white">{title}</h3>
          <span className="text-xs text-slate-400">{badge}</span>
        </div>
        {testResult && (
          <div className="flex items-center gap-1.5">
            <StatusDot ok={testResult.ok} />
            <span className={`text-xs ${testResult.ok ? 'text-green-400' : 'text-red-400'}`}>
              {testResult.ok ? 'Connected' : 'Failed'}
            </span>
          </div>
        )}
      </div>

      {children}

      <div className="flex items-center gap-3 pt-1">
        <button
          onClick={onTest}
          disabled={testing}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors disabled:opacity-40"
        >
          {testing ? <Loader2 size={12} className="animate-spin" /> : <TestTube2 size={12} />}
          Test Connection
        </button>
        {testResult && (
          <p className={`text-xs ${testResult.ok ? 'text-green-400' : 'text-red-400'}`}>
            {testResult.message}
          </p>
        )}
      </div>
    </div>
  )
}

export function SettingsPage() {
  const [cfg, setCfg] = useState<Config>({
    vcenter_host: '', vcenter_user: 'administrator@vsphere.local', vcenter_password: '', vcenter_verify_ssl: false,
    vrops_host: '', vrops_user: 'admin', vrops_password: '', vrops_verify_ssl: false,

    sddc_host: '', sddc_user: 'administrator@vsphere.local', sddc_password: '', sddc_verify_ssl: false,
    nsx_host: '', nsx_user: 'admin', nsx_password: '', nsx_verify_ssl: false,
    ad_host: '', ad_user: '', ad_password: '', ad_domain: '',
    llm_provider: 'anthropic',
    anthropic_api_key: '', anthropic_model: 'claude-sonnet-4-6',
    openai_api_key: '', openai_model: 'gpt-4o',
    gemini_api_key: '', gemini_model: 'gemini-2.0-flash',
    vllm_url: 'http://vllm-server:11434', vllm_model: 'qwen2.5:14b',
    vcf_target_version: '9.0',
    agent_llm_provider: 'anthropic',
    agent_anthropic_api_key: '', agent_anthropic_model: 'claude-sonnet-4-6',
    agent_openai_api_key: '', agent_openai_model: 'gpt-4o',
    agent_gemini_api_key: '', agent_gemini_model: 'gemini-2.0-flash',
    agent_ollama_url: 'http://vllm-server:11434', agent_ollama_model: 'qwen2.5-coder:7b',
  })
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [testing, setTesting] = useState<Record<string, boolean>>({})
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({})

  // Ollama state
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null)
  const [pullLogs, setPullLogs] = useState<PullLogs>({ status: 'idle', model: '', logs: [], progress_pct: 0 })
  const [selectedModel, setSelectedModel] = useState('smollm2:1.7b')
  const [selectedRam, setSelectedRam] = useState(16)
  const [showModelPicker, setShowModelPicker] = useState(false)
  const [pulling, setPulling] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [loadedOllamaModels, setLoadedOllamaModels] = useState<string[]>([])
  const logRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/config`)
      .then(r => r.json())
      .then(data => {
        setCfg(prev => ({ ...prev, ...data }))
        if (data.vllm_model) setSelectedModel(data.vllm_model)
      })
      .catch(() => setLoadError('Could not load saved settings (config-store may not be running)'))
  }, [])

  // Load actually-pulled Ollama models for dropdowns
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/ollama/models`)
      .then(r => r.json())
      .then(data => { if (data.models?.length) setLoadedOllamaModels(data.models) })
      .catch(() => {})
  }, [])

  // Poll Ollama status every 5 s when on the Ollama tab — pauses when the tab
  // is hidden, backs off on repeated failures.
  useVisibilityPolling(async () => {
    const r = await fetch(`${API_BASE}/api/v1/ollama/status`)
    const data: OllamaStatus = await r.json()
    setOllamaStatus(data)
    if (data.vllm_url) setCfg(prev => prev.vllm_url === data.vllm_url ? prev : { ...prev, vllm_url: data.vllm_url! })
  }, 5000, { enabled: cfg.llm_provider === 'ollama' })

  // Poll pull logs every 2 s while a pull is active; stop when done
  useVisibilityPolling(async () => {
    const r = await fetch(`${API_BASE}/api/v1/ollama/pull/logs`)
    const data: PullLogs = await r.json()
    setPullLogs(data)
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
    if (data.status === 'ready') {
      // Update cfg model on success
      setCfg(prev => ({ ...prev, vllm_model: data.model }))
    }
  }, 2000, { enabled: pullLogs.status === 'deploying' || pullLogs.status === 'pulling' })

  // Warn before closing the tab with unsaved settings edits
  useUnsavedGuard(dirty)

  async function startPull() {
    setPulling(true)
    try {
      const r = await fetch(`${API_BASE}/api/v1/ollama/pull`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: selectedModel, ram_gb: selectedRam }),
      })
      const data = await r.json()
      if (data.ok) {
        setPullLogs({ status: 'deploying', model: selectedModel, logs: [data.message], progress_pct: 0 })
        setShowModelPicker(false)
      } else {
        setPullLogs(prev => ({ ...prev, status: 'error', logs: [...prev.logs, `✗ ${data.message}`] }))
      }
    } catch (e) {
      setPullLogs(prev => ({ ...prev, status: 'error', logs: [...prev.logs, `✗ ${e}`] }))
    } finally {
      setPulling(false)
    }
  }

  async function ollamaRemove() {
    if (!confirm('Remove Ollama? All model weights will be lost (stored in RAM only).')) return
    setRemoving(true)
    try {
      await fetch(`${API_BASE}/api/v1/ollama`, { method: 'DELETE' })
      setOllamaStatus(null)
      setPullLogs({ status: 'idle', model: '', logs: [], progress_pct: 0 })
    } catch { /* ignore */ } finally {
      setRemoving(false)
    }
  }

  const set = (key: keyof Config) => (val: string | boolean) => {
    setDirty(true)
    setCfg(prev => ({ ...prev, [key]: val }))
  }

  async function save() {
    setSaving(true)
    try {
      const res = await fetch(`${API_BASE}/api/v1/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      })
      if (!res.ok) throw new Error(await res.text())
      setSaved(true)
      setDirty(false)
      toast.success('Settings saved')
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  async function testService(name: string) {
    setTesting(t => ({ ...t, [name]: true }))
    try {
      const res = await fetch(`${API_BASE}/api/v1/config/test/${name}`, { method: 'POST' })
      const data: TestResult = await res.json()
      setTestResults(r => ({ ...r, [name]: data }))
    } catch (e) {
      setTestResults(r => ({ ...r, [name]: { ok: false, message: String(e) } }))
    } finally {
      setTesting(t => ({ ...t, [name]: false }))
    }
  }

  return (
    <div className="min-h-screen bg-vmware-dark">
      <main className="max-w-4xl mx-auto px-6 py-6 space-y-5">
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-sm font-bold text-white">Settings</h1>
          <div className="flex items-center gap-3">
            {saved && (
              <span className="flex items-center gap-1.5 text-xs text-green-400">
                <CheckCircle size={12} /> Saved
              </span>
            )}
            <button
              onClick={save}
              disabled={saving}
              className="flex items-center gap-1.5 bg-vmware-blue hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold px-4 py-1.5 rounded-lg transition-colors"
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              Save All
            </button>
          </div>
        </div>
        {loadError && (
          <div className="rounded-lg border border-yellow-800 bg-yellow-900/20 px-4 py-3 text-sm text-yellow-400">
            {loadError}
          </div>
        )}

        {/* SSL-off warning — shown when any service has SSL verification disabled */}
        {(!cfg.vcenter_verify_ssl || !cfg.sddc_verify_ssl || !cfg.nsx_verify_ssl || !cfg.vrops_verify_ssl) && (
          <div className="flex items-start gap-2.5 px-3.5 py-2.5 rounded-lg bg-amber-900/20 border border-amber-700/40 text-amber-400 text-xs">
            <span className="mt-0.5 flex-shrink-0">⚠</span>
            <span>SSL certificate verification is disabled on one or more services. Only acceptable in trusted lab environments — do not use against production endpoints.</span>
          </div>
        )}

        {/* vCenter */}
        <IntegrationCard
          title="vCenter Server" badge="VCENTER · ESXI"
          onTest={() => testService('vcenter')}
          testResult={testResults['vcenter'] ?? null}
          testing={testing['vcenter'] ?? false}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Host / FQDN" value={cfg.vcenter_host} onChange={set('vcenter_host')} placeholder="vcenter.domain.local" mono />
            <Field label="Username" value={cfg.vcenter_user} onChange={set('vcenter_user')} placeholder="administrator@vsphere.local" />
            <Field label="Password" value={cfg.vcenter_password} onChange={set('vcenter_password')} sensitive />
            <Toggle label="Verify SSL Certificate" value={cfg.vcenter_verify_ssl} onChange={set('vcenter_verify_ssl')} />
          </div>
        </IntegrationCard>

        {/* SDDC Manager */}
        <IntegrationCard
          title="SDDC Manager" badge="VCF · DOMAINS · UPGRADES"
          onTest={() => testService('sddc')}
          testResult={testResults['sddc'] ?? null}
          testing={testing['sddc'] ?? false}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Host / FQDN" value={cfg.sddc_host} onChange={set('sddc_host')} placeholder="sddc-manager.domain.local" mono />
            <Field label="Username" value={cfg.sddc_user} onChange={set('sddc_user')} placeholder="administrator@vsphere.local" />
            <Field label="Password" value={cfg.sddc_password} onChange={set('sddc_password')} sensitive />
            <Toggle label="Verify SSL Certificate" value={cfg.sddc_verify_ssl} onChange={set('sddc_verify_ssl')} />
          </div>
        </IntegrationCard>

        {/* NSX Manager */}
        <IntegrationCard
          title="NSX Manager" badge="SEGMENTS · NETWORK · DISCOVERY"
          onTest={() => testService('nsx')}
          testResult={testResults['nsx'] ?? null}
          testing={testing['nsx'] ?? false}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Host / FQDN" value={cfg.nsx_host} onChange={set('nsx_host')} placeholder="nsx-manager.domain.local" mono />
            <Field label="Username" value={cfg.nsx_user} onChange={set('nsx_user')} placeholder="admin" />
            <Field label="Password" value={cfg.nsx_password} onChange={set('nsx_password')} sensitive />
            <Toggle label="Verify SSL Certificate" value={cfg.nsx_verify_ssl} onChange={set('nsx_verify_ssl')} />
          </div>
        </IntegrationCard>

        {/* VCF Operations */}
        <IntegrationCard
          title="VCF Operations" badge="METRICS · CAPACITY · HEALTH"
          onTest={() => testService('vrops')}
          testResult={testResults['vrops'] ?? null}
          testing={testing['vrops'] ?? false}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Host / FQDN" value={cfg.vrops_host} onChange={set('vrops_host')} placeholder="vrops.domain.local" mono />
            <Field label="Username" value={cfg.vrops_user} onChange={set('vrops_user')} placeholder="admin" />
            <Field label="Password" value={cfg.vrops_password} onChange={set('vrops_password')} sensitive />
            <Toggle label="Verify SSL Certificate" value={cfg.vrops_verify_ssl} onChange={set('vrops_verify_ssl')} />
          </div>
        </IntegrationCard>

        {/* Active Directory */}
        <IntegrationCard
          title="Active Directory" badge="LDAP · USERS · BULK PROVISIONING"
          onTest={() => testService('ad')}
          testResult={testResults['ad'] ?? null}
          testing={testing['ad'] ?? false}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Domain Controller (FQDN)" value={cfg.ad_host} onChange={set('ad_host')} placeholder="dc01.prglab.site" />
            <Field label="Domain" value={cfg.ad_domain} onChange={set('ad_domain')} placeholder="prglab.site" />
            <Field label="Username" value={cfg.ad_user} onChange={set('ad_user')} placeholder="PRGLAB\svc-mco or svc-mco@prglab.site" />
            <Field label="Password" value={cfg.ad_password} onChange={set('ad_password')} sensitive />
          </div>
        </IntegrationCard>

        {/* AI / LLM — multi-provider */}
        <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-white">AI / LLM</h3>
              <span className="text-xs text-slate-400">Select provider and configure credentials</span>
            </div>
            {testResults['llm'] && (
              <div className="flex items-center gap-1.5">
                <StatusDot ok={testResults['llm'].ok} />
                <span className={`text-xs ${testResults['llm'].ok ? 'text-green-400' : 'text-red-400'}`}>
                  {testResults['llm'].ok ? 'Connected' : 'Failed'}
                </span>
              </div>
            )}
          </div>

          {/* Provider tabs */}
          <div className="flex gap-1 bg-slate-800/60 p-1 rounded-lg w-fit">
            {(['anthropic', 'openai', 'gemini', 'ollama'] as LLMProvider[]).map(p => (
              <button
                key={p}
                onClick={() => set('llm_provider')(p)}
                className={`text-xs px-3 py-1.5 rounded-md transition-colors font-medium ${
                  cfg.llm_provider === p
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {p === 'anthropic' ? 'Anthropic' : p === 'openai' ? 'OpenAI' : p === 'gemini' ? 'Gemini' : 'Local (Ollama)'}
              </button>
            ))}
          </div>

          {/* Provider-specific fields */}
          {cfg.llm_provider === 'anthropic' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Anthropic API Key" value={cfg.anthropic_api_key} onChange={set('anthropic_api_key')} placeholder="sk-ant-..." sensitive />
              <div>
                <label className="block text-xs text-slate-400 mb-1">Model</label>
                <select value={cfg.anthropic_model} onChange={e => set('anthropic_model')(e.target.value)}
                  className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500">
                  {ANTHROPIC_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            </div>
          )}

          {cfg.llm_provider === 'openai' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="OpenAI API Key" value={cfg.openai_api_key} onChange={set('openai_api_key')} placeholder="sk-..." sensitive />
              <div>
                <label className="block text-xs text-slate-400 mb-1">Model</label>
                <select value={cfg.openai_model} onChange={e => set('openai_model')(e.target.value)}
                  className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500">
                  {OPENAI_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            </div>
          )}

          {cfg.llm_provider === 'gemini' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Google Gemini API Key" value={cfg.gemini_api_key} onChange={set('gemini_api_key')} placeholder="AIza..." sensitive />
              <div>
                <label className="block text-xs text-slate-400 mb-1">Model</label>
                <select value={cfg.gemini_model} onChange={e => set('gemini_model')(e.target.value)}
                  className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500">
                  {GEMINI_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            </div>
          )}

          {cfg.llm_provider === 'ollama' && (() => {
            const pullActive  = pullLogs.status === 'deploying' || pullLogs.status === 'pulling'
            const pullReady   = pullLogs.status === 'ready'
            const pullError   = pullLogs.status === 'error'
            const podReady    = ollamaStatus?.status === 'ready'
            const podDeployed = ollamaStatus?.deployed
            const loadedModel = ollamaStatus?.loaded_models?.[0] ?? ''

            // Overall display state
            const overallReady = pullReady || (podReady && !!loadedModel && pullLogs.status === 'idle')
            const showConsole  = pullActive || pullReady || pullError || (pullLogs.logs.length > 0)
            const selMeta      = OLLAMA_MODELS.find(m => m.tag === selectedModel)

            return (
              <div className="space-y-4">
                {/* ── Status banner ── */}
                <div className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs
                  ${overallReady ? 'bg-green-900/30 border border-green-800'
                  : pullActive   ? 'bg-yellow-900/30 border border-yellow-800'
                  : pullError    ? 'bg-red-900/20 border border-red-800'
                  :                'bg-slate-800/60 border border-vmware-border'}`}>
                  <div className="flex items-center gap-2">
                    {overallReady
                      ? <span className="w-2 h-2 rounded-full bg-green-400" />
                      : pullActive
                        ? <Loader2 size={12} className="animate-spin text-yellow-400" />
                        : pullError
                          ? <span className="w-2 h-2 rounded-full bg-red-400" />
                          : <span className="w-2 h-2 rounded-full bg-slate-500" />}
                    <span className={overallReady ? 'text-green-300' : pullActive ? 'text-yellow-300' : pullError ? 'text-red-300' : 'text-slate-400'}>
                      {overallReady
                        ? `Ready — ${loadedModel || pullLogs.model}`
                        : pullLogs.status === 'deploying'
                          ? 'Pod starting…'
                          : pullLogs.status === 'pulling'
                            ? `Pulling ${pullLogs.model} into RAM… ${pullLogs.progress_pct}%`
                            : pullError
                              ? 'Pull failed'
                              : podDeployed
                                ? 'Pod running — no model loaded'
                                : 'Not deployed'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {podDeployed && (
                      <button onClick={ollamaRemove} disabled={removing}
                        className="flex items-center gap-1 text-red-400 hover:text-red-300 disabled:opacity-40 text-xs">
                        {removing ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
                        Remove
                      </button>
                    )}
                    <button
                      onClick={() => fetch(`${API_BASE}/api/v1/ollama/status`).then(r => r.json()).then(setOllamaStatus).catch(() => {})}
                      className="text-slate-500 hover:text-slate-300">
                      <RefreshCw size={11} />
                    </button>
                  </div>
                </div>

                {/* ── Progress bar (visible during pull) ── */}
                {pullActive && (
                  <div className="space-y-1">
                    <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-blue-500 transition-all duration-500 rounded-full"
                        style={{ width: `${pullLogs.progress_pct}%` }}
                      />
                    </div>
                    <p className="text-xs text-slate-400 text-right">{pullLogs.progress_pct}% downloaded</p>
                  </div>
                )}

                {/* ── Model picker (always visible OR in change-model mode) ── */}
                {(!overallReady || showModelPicker) && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-slate-400 mb-1">Model</label>
                      <select
                        value={selectedModel}
                        onChange={e => {
                          setSelectedModel(e.target.value)
                          const m = OLLAMA_MODELS.find(x => x.tag === e.target.value)
                          if (m) setSelectedRam(m.ram_recommended)
                        }}
                        className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                      >
                        {OLLAMA_MODELS.map(m => (
                          <option key={m.tag} value={m.tag}>{m.label} (~{m.size_gb} GB)</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs text-slate-400 mb-1">Pod RAM limit</label>
                      <select
                        value={selectedRam}
                        onChange={e => setSelectedRam(Number(e.target.value))}
                        className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                      >
                        {OLLAMA_RAM_OPTIONS.map(r => (
                          <option key={r} value={r}>
                            {r} GB{selMeta && r === selMeta.ram_recommended ? ' (recommended)' : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="sm:col-span-2 flex items-center gap-3">
                      <button
                        onClick={startPull}
                        disabled={pulling || pullActive}
                        className="flex items-center gap-1.5 bg-blue-700 hover:bg-blue-600 disabled:opacity-40 text-white text-xs font-semibold px-4 py-2 rounded-lg transition-colors"
                      >
                        {pulling || pullActive ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                        {showModelPicker ? 'Pull New Model' : 'Pull Model'}
                      </button>
                      {showModelPicker && (
                        <button onClick={() => setShowModelPicker(false)}
                          className="text-xs text-slate-500 hover:text-slate-300">
                          Cancel
                        </button>
                      )}
                      <span className="text-xs text-slate-500">
                        Weights stored in pod RAM only — lost on pod restart
                      </span>
                    </div>
                  </div>
                )}

                {/* ── Change model button (shown when ready) ── */}
                {overallReady && !showModelPicker && (
                  <button
                    onClick={() => setShowModelPicker(true)}
                    className="text-xs text-blue-400 hover:text-blue-300 border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors"
                  >
                    Change Model
                  </button>
                )}

                {/* ── Log console ── */}
                {showConsole && (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-slate-300">Pull console</span>
                      {pullActive && <span className="text-xs text-slate-400 flex items-center gap-1"><Loader2 size={10} className="animate-spin" /> live</span>}
                    </div>
                    <pre
                      ref={logRef}
                      className="bg-black/60 border border-slate-700 rounded-lg p-3 text-xs text-green-300 font-mono h-56 overflow-y-auto whitespace-pre-wrap leading-5"
                    >
                      {pullLogs.logs.join('\n') || 'Waiting…'}
                    </pre>
                  </div>
                )}

                {/* ── Separator + endpoint fields ── */}
                <div className="border-t border-vmware-border" />
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="Endpoint URL" value={cfg.vllm_url} onChange={set('vllm_url')} placeholder="http://vllm-server:11434" mono />
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">Model for Workspace generation</label>
                    <select
                      value={cfg.vllm_model}
                      onChange={e => set('vllm_model')(e.target.value)}
                      className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                    >
                      {loadedOllamaModels.length === 0 && <option value={cfg.vllm_model}>{cfg.vllm_model || 'Loading…'}</option>}
                      {loadedOllamaModels.map(m => <option key={m} value={m}>{m}</option>)}
                    </select>
                    {loadedOllamaModels.length > 0 && <p className="text-xs text-slate-500 mt-1">Showing models pulled in Ollama</p>}
                  </div>
                </div>
              </div>
            )
          })()}

          <div className="flex items-center gap-3 pt-1">
            {(() => {
              const svc = cfg.llm_provider === 'anthropic' ? 'anthropic' : cfg.llm_provider === 'ollama' ? 'ollama' : cfg.llm_provider
              const res = testResults[svc]
              const busy = testing[svc] ?? false
              return (
                <>
                  <button
                    onClick={() => testService(svc)}
                    disabled={busy}
                    className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors disabled:opacity-40"
                  >
                    {busy ? <Loader2 size={12} className="animate-spin" /> : <TestTube2 size={12} />}
                    {busy && svc === 'ollama' ? 'Sending "hi"…' : 'Test Connection'}
                  </button>
                  {res && (
                    <p className={`text-xs ${res.ok ? 'text-green-400' : 'text-red-400'}`}>
                      {res.message}
                    </p>
                  )}
                </>
              )
            })()}
          </div>
        </div>

        {/* MCP AI Agent LLM */}
        <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 space-y-4">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              MCP AI Agent LLM
            </h3>
            <span className="text-xs text-slate-400">Independent provider for the MCP AI Agent tab — use a cloud model or the local Qwen-Coder running on the VKS cluster</span>
          </div>

          <div className="flex gap-1 bg-slate-800/60 p-1 rounded-lg w-fit">
            {(['anthropic', 'openai', 'gemini', 'ollama'] as const).map(p => (
              <button
                key={p}
                onClick={() => set('agent_llm_provider')(p)}
                className={`text-xs px-3 py-1.5 rounded-md transition-colors font-medium flex items-center gap-1.5 ${
                  cfg.agent_llm_provider === p
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {p === 'anthropic' ? 'Anthropic' : p === 'openai' ? 'OpenAI' : p === 'gemini' ? 'Gemini' : (
                  <><span className="w-1.5 h-1.5 rounded-full bg-green-400" />Local (Ollama)</>
                )}
              </button>
            ))}
          </div>

          {cfg.agent_llm_provider === 'anthropic' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Anthropic API Key" value={cfg.agent_anthropic_api_key} onChange={set('agent_anthropic_api_key')} placeholder="sk-ant-..." sensitive />
              <div>
                <label className="block text-xs text-slate-400 mb-1">Model</label>
                <select value={cfg.agent_anthropic_model} onChange={e => set('agent_anthropic_model')(e.target.value)}
                  className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500">
                  {ANTHROPIC_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            </div>
          )}
          {cfg.agent_llm_provider === 'openai' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="OpenAI API Key" value={cfg.agent_openai_api_key} onChange={set('agent_openai_api_key')} placeholder="sk-..." sensitive />
              <div>
                <label className="block text-xs text-slate-400 mb-1">Model</label>
                <select value={cfg.agent_openai_model} onChange={e => set('agent_openai_model')(e.target.value)}
                  className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500">
                  {OPENAI_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            </div>
          )}
          {cfg.agent_llm_provider === 'gemini' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Google Gemini API Key" value={cfg.agent_gemini_api_key} onChange={set('agent_gemini_api_key')} placeholder="AIza..." sensitive />
              <div>
                <label className="block text-xs text-slate-400 mb-1">Model</label>
                <select value={cfg.agent_gemini_model} onChange={e => set('agent_gemini_model')(e.target.value)}
                  className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500">
                  {GEMINI_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            </div>
          )}
          {cfg.agent_llm_provider === 'ollama' && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs text-green-400 bg-green-900/20 border border-green-800/40 px-3 py-2 rounded-lg">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                Runs fully on-cluster via vllm-server — no internet, no API keys needed
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="Ollama URL (in-cluster)" value={cfg.agent_ollama_url} onChange={set('agent_ollama_url')} placeholder="http://vllm-server:11434" mono />
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Model for tool calling</label>
                  <select
                    value={cfg.agent_ollama_model}
                    onChange={e => set('agent_ollama_model')(e.target.value)}
                    className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                  >
                    {loadedOllamaModels.length === 0 && <option value={cfg.agent_ollama_model}>{cfg.agent_ollama_model || 'Loading…'}</option>}
                    {loadedOllamaModels.map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                  {loadedOllamaModels.length > 0 && <p className="text-xs text-slate-500 mt-1">Showing models pulled in Ollama</p>}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => testService('agent-ollama')}
                  disabled={testing['agent-ollama']}
                  className="flex items-center gap-1.5 text-xs border border-vmware-border text-slate-400 hover:text-white hover:border-slate-500 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                >
                  {testing['agent-ollama'] ? <Loader2 size={12} className="animate-spin" /> : <TestTube2 size={12} />}
                  {testing['agent-ollama'] ? 'Sending "hi"…' : 'Test Connection'}
                </button>
                {testResults['agent-ollama'] && (
                  <span className={`text-xs ${testResults['agent-ollama'].ok ? 'text-green-400' : 'text-red-400'}`}>
                    {testResults['agent-ollama'].ok ? '✓' : '✗'} {testResults['agent-ollama'].message}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Platform */}
        <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">Platform</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">VCF Target Version</label>
              <select
                value={cfg.vcf_target_version}
                onChange={e => set('vcf_target_version')(e.target.value)}
                className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
              >
                {VCF_VERSIONS.map(v => <option key={v} value={v}>VCF {v}</option>)}
              </select>
            </div>
          </div>
        </div>

        {/* Maintenance Windows */}
        <MaintenanceWindowsPanel />

        <p className="text-xs text-slate-400 text-center pb-4">
          Credentials are encrypted with AES-128 (Fernet) and stored on a persistent volume.
          Sensitive fields never leave the server in plaintext.
        </p>
      </main>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Maintenance Windows Panel
// ---------------------------------------------------------------------------

interface MaintenanceWindow {
  id: string
  name: string
  day_of_week: number
  start_hour: number
  start_minute: number
  duration_minutes: number
  enabled: boolean
}

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

function MaintenanceWindowsPanel() {
  const [windows, setWindows] = useState<MaintenanceWindow[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', day_of_week: 6, start_hour: 2, start_minute: 0, duration_minutes: 120 })

  async function loadWindows() {
    try {
      const r = await fetch(`${API_BASE}/api/v1/maintenance-windows`)
      if (r.ok) setWindows((await r.json()).windows || [])
    } catch {}
    setLoading(false)
  }

  useEffect(() => { loadWindows() }, [])

  async function save() {
    setSaving(true)
    await fetch(`${API_BASE}/api/v1/maintenance-windows`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...form, enabled: true }),
    })
    setSaving(false)
    setShowForm(false)
    loadWindows()
  }

  async function toggle(w: MaintenanceWindow) {
    await fetch(`${API_BASE}/api/v1/maintenance-windows/${w.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !w.enabled }),
    })
    loadWindows()
  }

  async function remove(id: string) {
    await fetch(`${API_BASE}/api/v1/maintenance-windows/${id}`, { method: 'DELETE' })
    loadWindows()
  }

  function fmtWindow(w: MaintenanceWindow) {
    const d = DAYS[w.day_of_week] || `Day ${w.day_of_week}`
    const h = String(w.start_hour).padStart(2, '0')
    const m = String(w.start_minute).padStart(2, '0')
    const endMin = w.start_minute + w.duration_minutes
    const eh = String(w.start_hour + Math.floor(endMin / 60)).padStart(2, '0')
    const em = String(endMin % 60).padStart(2, '0')
    return `${d} ${h}:${m}–${eh}:${em} UTC`
  }

  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Clock size={14} className="text-blue-400" /> Maintenance Windows
        </h3>
        <button onClick={() => setShowForm(s => !s)}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-2.5 py-1.5 rounded-lg hover:border-slate-500 transition-colors">
          <Plus size={11} /> Add Window
        </button>
      </div>
      <p className="text-xs text-slate-500">
        Mutating actions (kubectl apply/delete, bulk exec, vuln scans) are gated by these windows.
        Outside a window the API returns 423 Locked. If no windows are defined, all actions are allowed.
      </p>

      {showForm && (
        <div className="rounded-lg border border-blue-800/40 bg-blue-900/10 p-4 space-y-3">
          <p className="text-xs font-semibold text-blue-300">New Window</p>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Name</label>
              <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Weekend maintenance"
                className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-600" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Day of Week</label>
              <select value={form.day_of_week} onChange={e => setForm(f => ({ ...f, day_of_week: Number(e.target.value) }))}
                className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-blue-600">
                {DAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Duration (minutes)</label>
              <input type="number" value={form.duration_minutes} min={30} max={480}
                onChange={e => setForm(f => ({ ...f, duration_minutes: Number(e.target.value) }))}
                className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-blue-600" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Start Hour (UTC)</label>
              <input type="number" value={form.start_hour} min={0} max={23}
                onChange={e => setForm(f => ({ ...f, start_hour: Number(e.target.value) }))}
                className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-blue-600" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Start Minute</label>
              <input type="number" value={form.start_minute} min={0} max={59} step={15}
                onChange={e => setForm(f => ({ ...f, start_minute: Number(e.target.value) }))}
                className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-blue-600" />
            </div>
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowForm(false)} className="text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-lg border border-vmware-border transition-colors">Cancel</button>
            <button onClick={save} disabled={saving || !form.name}
              className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1.5">
              {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} Save
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-xs text-slate-500 py-2">Loading…</div>
      ) : windows.length === 0 ? (
        <div className="text-xs text-slate-500 py-2 text-center">No windows defined — all actions currently allowed</div>
      ) : (
        <div className="space-y-2">
          {windows.map(w => (
            <div key={w.id} className={`flex items-center gap-3 rounded-lg px-3 py-2.5 border transition-colors ${w.enabled ? 'border-blue-800/40 bg-blue-900/10' : 'border-vmware-border bg-slate-800/20 opacity-60'}`}>
              <Clock size={12} className={w.enabled ? 'text-blue-400' : 'text-slate-600'} />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-white">{w.name}</p>
                <p className="text-[10px] text-slate-500 font-mono">{fmtWindow(w)}</p>
              </div>
              <button onClick={() => toggle(w)}
                className={`text-[10px] px-2 py-1 rounded border transition-colors ${w.enabled ? 'border-green-800/40 text-green-400 hover:bg-green-900/20' : 'border-slate-700 text-slate-500 hover:text-white'}`}>
                {w.enabled ? 'Enabled' : 'Disabled'}
              </button>
              <button onClick={() => remove(w.id)} className="text-slate-600 hover:text-red-400 transition-colors p-1">
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
