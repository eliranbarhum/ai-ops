import { useState, useEffect, useRef } from 'react'
import { Zap, Server, Activity, Archive, Code2, Settings, Play, Trash2, Loader2, RefreshCw, ChevronDown, ChevronUp, AlertTriangle, BotMessageSquare, Upload, Laptop, Terminal, Radar, TrendingUp } from 'lucide-react'
import { useVisibilityPolling } from '../hooks/useVisibilityPolling'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface ApiSpec {
  target: 'vcenter' | 'vrops' | 'powercli' | 'sddc_manager' | 'ad'
  method: 'GET' | 'POST' | 'PUT'
  path: string
  description: string
  body: object | null
  query_params: Record<string, string>
}

interface ExecuteResult {
  status_code: number
  elapsed_ms: number
  response: unknown
}

interface WorkspaceEntry {
  id: string
  timestamp: string
  description: string
  spec: ApiSpec | null
  bodyText: string       // editable body
  result: ExecuteResult | null
  generating: boolean
  executing: boolean
  responseOpen: boolean
  error: string | null
  persisted: boolean
}

function uuid(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
}

function useElapsedTimer(active: boolean): string {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    if (!active) { setSeconds(0); return }
    setSeconds(0)
    const id = setInterval(() => setSeconds(s => s + 1), 1000)
    return () => clearInterval(id)
  }, [active])
  if (!active) return ''
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

const EXAMPLES = [
  // ── vCenter ──────────────────────────────────────────────────────────
  'List all virtual machines',
  'List powered-on virtual machines',
  'Get datastore capacity and free space',
  'List ESXi hosts',
  'List connected ESXi hosts only',
  'Get vCenter version and build number',
  'List all clusters',
  'List all networks and port groups',
  'List all folders',
  'List all resource pools',
  'List storage policies',
  'Get vCenter appliance health status',
  'Get vCenter networking configuration',
  'List VMFS datastores only',
  'List distributed port groups only',
  'Find VM named web-server-01',
  'List all supervisor namespaces',
  'List all tag categories',
  'List all content library items',
  // ── SDDC Manager ─────────────────────────────────────────────────────
  'List all VCF workload domains',
  'List all hosts in SDDC Manager',
  'List all SDDC Manager clusters',
  'List all NSX-T clusters in SDDC Manager',
  'List available upgrade bundles',
  'Get upgrade precheck status',
  'Get SDDC Manager NTP configuration',
  'Get SDDC Manager DNS configuration',
  'Get SDDC Manager system info and version',
  // ── VM Creation ───────────────────────────────────────────────────────
  'Create a VM named test-vm with 4 vCPU 16GB RAM 100GB disk Ubuntu',
  'Create a Windows Server 2022 VM with 8 vCPU 32GB RAM 200GB disk',
  'Create a Photon OS VM named k8s-node-01 with 4 vCPU 8GB RAM 50GB disk',
]

const METHOD_COLORS: Record<string, string> = {
  GET:  'bg-green-900/30 text-green-400 border border-green-800/50',
  POST: 'bg-blue-900/30 text-blue-400 border border-blue-800/50',
  PUT:  'bg-yellow-900/30 text-yellow-400 border border-yellow-800/50',
}

const TARGET_COLORS: Record<string, string> = {
  vcenter:      'bg-indigo-900/30 text-indigo-400 border border-indigo-800/50',
  vrops:        'bg-orange-900/30 text-orange-400 border border-orange-800/50',
  powercli:     'bg-purple-900/30 text-purple-400 border border-purple-800/50',
  sddc_manager: 'bg-cyan-900/30 text-cyan-400 border border-cyan-800/50',
}

function statusColor(code: number): string {
  if (code >= 200 && code < 300) return 'text-green-400'
  if (code >= 400 && code < 500) return 'text-yellow-400'
  return 'text-red-400'
}

function fmt(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' })
}

function newEntry(description: string): WorkspaceEntry {
  return {
    id: uuid(),
    timestamp: new Date().toISOString(),
    description,
    spec: null,
    bodyText: '',
    result: null,
    generating: true,
    executing: false,
    responseOpen: false,
    error: null,
    persisted: false,
  }
}

function specBodyText(spec: ApiSpec): string {
  if (!spec?.body) return ''
  if (spec.target === 'powercli') {
    const s = (spec.body as Record<string, unknown>).script
    return typeof s === 'string' ? s : JSON.stringify(spec.body, null, 2)
  }
  return JSON.stringify(spec.body, null, 2)
}

function fromHistory(h: { id: string; timestamp: string; description: string; spec: ApiSpec; result: ExecuteResult }): WorkspaceEntry {
  return {
    id: h.id,
    timestamp: h.timestamp,
    description: h.description,
    spec: h.spec,
    bodyText: specBodyText(h.spec),
    result: h.result,
    generating: false,
    executing: false,
    responseOpen: false,
    error: null,
    persisted: true,
  }
}

export function WorkspacePage() {
  const [description, setDescription] = useState('')
  const [entries, setEntries] = useState<WorkspaceEntry[]>([])
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [llmStatus, setLlmStatus] = useState<{ provider: string; model?: string; status: string; detail?: string; slow_warning?: boolean } | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/workspace/history`)
      .then(r => r.json())
      .then(data => setEntries((data.entries ?? []).map(fromHistory)))
      .catch(() => {})
      .finally(() => setLoadingHistory(false))
  }, [])

  // LLM status poll — pauses when the tab is hidden, backs off on failures
  useVisibilityPolling(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/workspace/llm-status`)
      setLlmStatus(await r.json())
    } catch (err) {
      setLlmStatus({ provider: 'unknown', status: 'unreachable' })
      throw err
    }
  }, 10000)

  function updateEntry(id: string, patch: Partial<WorkspaceEntry>) {
    setEntries(prev => prev.map(e => e.id === id ? { ...e, ...patch } : e))
  }

  async function handleGenerate() {
    const desc = description.trim()
    if (!desc) return
    setGenerating(true)
    const entry = newEntry(desc)
    setEntries(prev => [entry, ...prev])
    setDescription('')

    try {
      const resp = await fetch(`${API_BASE}/api/v1/workspace/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: desc }),
      })
      const data = await resp.json()
      if (!resp.ok) throw new Error(data.detail || 'Generate failed')
      const spec: ApiSpec = data.spec
      updateEntry(entry.id, {
        spec,
        bodyText: specBodyText(spec),
        generating: false,
      })
    } catch (err) {
      updateEntry(entry.id, {
        generating: false,
        error: err instanceof Error ? err.message : 'Failed to generate',
      })
    } finally {
      setGenerating(false)
    }
  }

  async function handleRun(entry: WorkspaceEntry) {
    if (!entry.spec) return
    updateEntry(entry.id, { executing: true, result: null, error: null, responseOpen: true })

    // Merge edited body back into spec
    let body = entry.spec.body
    if (entry.bodyText.trim()) {
      if (entry.spec.target === 'powercli') {
        body = { script: entry.bodyText }
      } else {
        try { body = JSON.parse(entry.bodyText) } catch { /* keep original */ }
      }
    }
    const spec = { ...entry.spec, body }

    try {
      const resp = await fetch(`${API_BASE}/api/v1/workspace/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: entry.description, spec }),
      })
      const result: ExecuteResult = await resp.json()
      updateEntry(entry.id, { executing: false, result, persisted: true })
    } catch (err) {
      updateEntry(entry.id, {
        executing: false,
        error: err instanceof Error ? err.message : 'Execute failed',
      })
    }
  }

  async function handleDelete(id: string, persisted: boolean) {
    if (persisted) {
      await fetch(`${API_BASE}/api/v1/workspace/history/${id}`, { method: 'DELETE' }).catch(() => {})
    }
    setEntries(prev => prev.filter(e => e.id !== id))
  }

  return (
    <div className="min-h-screen bg-vmware-dark">
      <main className="max-w-7xl mx-auto px-6 py-6">
        {llmStatus && (
          <div className="flex items-center gap-1.5 text-xs mb-4">
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
              llmStatus.status === 'ready' ? 'bg-green-400' :
              llmStatus.status === 'no_key' || llmStatus.status === 'model_not_loaded' ? 'bg-yellow-400' :
              'bg-red-400'
            }`} />
            <span className="text-slate-400">
              {llmStatus.provider}{llmStatus.model ? ` · ${llmStatus.model}` : ''}
            </span>
            {llmStatus.slow_warning && llmStatus.status === 'ready' && (
              <span className="text-slate-400">· CPU mode</span>
            )}
          </div>
        )}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">

          {/* Left: generate panel */}
          <div className="lg:col-span-1 space-y-4">
            <div className="rounded-xl border border-vmware-border bg-vmware-card p-4 space-y-3">
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Code2 size={12} className="text-blue-400" /> Build API Call or PowerCLI
              </h3>
              <textarea
                ref={textareaRef}
                value={description}
                onChange={e => setDescription(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleGenerate() }}
                placeholder="Describe the vCenter API call or PowerCLI script to build…"
                rows={4}
                className="w-full bg-vmware-dark border border-vmware-border rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 resize-none"
              />
              <button
                onClick={handleGenerate}
                disabled={generating || !description.trim()}
                className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-semibold py-2 rounded-lg transition-colors"
              >
                {generating ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
                {generating ? 'Generating…' : 'Generate  ⌘↵'}
              </button>
              {llmStatus?.slow_warning && (
                <p className="text-[10px] text-slate-400 text-center">CPU inference · may take several minutes</p>
              )}
            </div>

            <div className="rounded-xl border border-vmware-border bg-vmware-card p-4 space-y-2">
              <p className="text-xs text-slate-300 uppercase tracking-wider font-semibold">Quick examples</p>
              {EXAMPLES.map(ex => (
                <button
                  key={ex}
                  onClick={() => { setDescription(ex); textareaRef.current?.focus() }}
                  className="w-full text-left text-xs text-slate-400 hover:text-white hover:bg-slate-800 px-2.5 py-1.5 rounded-lg transition-colors"
                >
                  {ex}
                </button>
              ))}
            </div>

            <div className="rounded-xl border border-amber-900/40 bg-amber-900/10 p-3 flex gap-2">
              <AlertTriangle size={12} className="text-amber-500 flex-shrink-0 mt-0.5" />
              <p className="text-xs text-amber-500/80">DELETE calls are blocked. Only GET, POST, and PUT are permitted.</p>
            </div>
          </div>

          {/* Right: call history */}
          <div className="lg:col-span-3 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
                <RefreshCw size={13} className="text-blue-400" />
                API &amp; PowerCLI History
                <span className="text-slate-400 font-normal">· {entries.length} item{entries.length !== 1 ? 's' : ''}</span>
              </h2>
            </div>

            {loadingHistory ? (
              <div className="rounded-xl border border-vmware-border bg-vmware-card p-12 flex items-center justify-center gap-2 text-slate-500">
                <Loader2 size={18} className="animate-spin" />
                <span className="text-sm">Loading history…</span>
              </div>
            ) : entries.length === 0 && !generating && (
              <div className="rounded-xl border border-vmware-border bg-vmware-card p-12 flex flex-col items-center gap-3 text-center">
                <Code2 size={32} className="text-slate-500" />
                <p className="text-slate-300 text-sm">No calls or scripts yet</p>
                <p className="text-slate-400 text-xs">Describe an API call or PowerCLI script on the left and click Generate</p>
              </div>
            )}

            {entries.map(entry => (
              <CallCard
                key={entry.id}
                entry={entry}
                onRun={() => handleRun(entry)}
                onDelete={() => handleDelete(entry.id, entry.persisted)}
                onBodyChange={text => updateEntry(entry.id, { bodyText: text })}
                onToggleResponse={() => updateEntry(entry.id, { responseOpen: !entry.responseOpen })}
              />
            ))}
          </div>
        </div>
      </main>
    </div>
  )
}

function PowerCliResponse({ response }: { response: Record<string, unknown> }) {
  const output = response?.output
  const error  = response?.error
  const exitCode = response?.exit_code

  return (
    <>
      {/* stdout */}
      {output && typeof output === 'string' && output.trim() ? (
        <pre className="text-xs text-green-300 font-mono overflow-auto max-h-80 leading-relaxed whitespace-pre-wrap">
          {output}
        </pre>
      ) : (
        <p className="text-xs text-slate-400 italic">
          {exitCode === 0 ? '(script ran successfully — no output returned)' : '(no output)'}
        </p>
      )}

      {/* stderr */}
      {error && typeof error === 'string' && error.trim() && (
        <pre className="text-xs text-red-400 font-mono overflow-auto max-h-40 leading-relaxed whitespace-pre-wrap">
          {error}
        </pre>
      )}

      {/* exit code when non-zero */}
      {typeof exitCode === 'number' && exitCode !== 0 && (
        <p className="text-[10px] text-slate-400">exit code {exitCode}</p>
      )}
    </>
  )
}

function CallCard({
  entry,
  onRun,
  onDelete,
  onBodyChange,
  onToggleResponse,
}: {
  entry: WorkspaceEntry
  onRun: () => void
  onDelete: () => void
  onBodyChange: (t: string) => void
  onToggleResponse: () => void
}) {
  const { spec, result, generating, executing, error, responseOpen, bodyText } = entry
  const execTimer = useElapsedTimer(executing)

  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
      {/* Call header */}
      <div className="px-4 py-3 flex items-start gap-3">
        <div className="flex-1 min-w-0 space-y-1.5">
          {generating ? (
            <div className="flex items-center gap-2 text-slate-400 text-xs">
              <Loader2 size={12} className="animate-spin text-blue-400" />
              Generating…
            </div>
          ) : error && !spec ? (
            <div className="text-xs text-red-400 flex items-center gap-1.5">
              <AlertTriangle size={11} /> {error}
            </div>
          ) : spec ? (
            <>
              <div className="flex items-center gap-2 flex-wrap">
                {spec.target !== 'powercli' && (
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-md ${METHOD_COLORS[spec.method] ?? ''}`}>
                    {spec.method}
                  </span>
                )}
                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-md ${TARGET_COLORS[spec.target] ?? ''}`}>
                  {spec.target === 'vcenter' ? 'vCenter' : spec.target === 'vrops' ? 'VCF Operations' : spec.target === 'sddc_manager' ? 'SDDC Manager' : spec.target === 'ad' ? 'Active Directory' : 'PowerCLI'}
                </span>
                {spec.target !== 'powercli' && (
                  <code className="text-xs text-blue-300 font-mono">{spec.path}</code>
                )}
              </div>
              <p className="text-xs text-slate-400">{spec.description}</p>
            </>
          ) : null}

          <p className="text-[10px] text-slate-400">{fmt(entry.timestamp)} · {entry.description}</p>
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0 mt-0.5">
          {spec && (
            <button
              onClick={onRun}
              disabled={executing || generating}
              className="flex items-center gap-1.5 text-xs bg-blue-600/20 hover:bg-blue-600/40 border border-blue-700/50 text-blue-300 hover:text-blue-200 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
            >
              {executing ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
              {executing ? `Running… ${execTimer}` : 'Run'}
            </button>
          )}
          <button
            onClick={onDelete}
            className="flex items-center text-slate-500 hover:text-red-400 border border-vmware-border hover:border-red-800/50 p-1.5 rounded-lg transition-colors"
          >
            <Trash2 size={11} />
          </button>
        </div>
      </div>

      {/* Editable body for POST/PUT or PowerCLI script */}
      {spec && (spec.target === 'powercli' ? true : (spec.method === 'POST' || spec.method === 'PUT')) && (
        <div className="border-t border-vmware-border px-4 py-3 space-y-1">
          <p className="text-[10px] text-slate-300 uppercase tracking-wider font-semibold">
            {spec.target === 'powercli' ? 'PowerShell Script' : 'Request Body (JSON)'}
          </p>
          <textarea
            value={bodyText}
            onChange={e => onBodyChange(e.target.value)}
            rows={spec.target === 'powercli' ? 8 : 5}
            className={`w-full bg-vmware-dark border border-vmware-border rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:border-blue-500 resize-y ${
              spec.target === 'powercli' ? 'text-purple-300' : 'text-blue-200'
            }`}
            spellCheck={false}
          />
        </div>
      )}

      {/* Response panel */}
      {result && (
        <>
          <button
            onClick={onToggleResponse}
            className="w-full border-t border-vmware-border px-4 py-2 flex items-center gap-2 hover:bg-slate-800/30 transition-colors"
          >
            <span className={`text-xs font-bold ${statusColor(result.status_code)}`}>
              {result.status_code}
            </span>
            <span className="text-xs text-slate-400">{result.elapsed_ms}ms</span>
            <span className="ml-auto text-slate-400">
              {responseOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            </span>
          </button>
          {responseOpen && (
            <div className="border-t border-vmware-border px-4 py-3 space-y-2">
              {spec?.target === 'powercli' ? (
                <PowerCliResponse response={result.response as Record<string, unknown>} />
              ) : (
                <pre className="text-xs text-slate-300 font-mono overflow-auto max-h-80 leading-relaxed">
                  {JSON.stringify(result.response, null, 2)}
                </pre>
              )}
            </div>
          )}
        </>
      )}

      {/* Execution error */}
      {error && spec && (
        <div className="border-t border-vmware-border px-4 py-2 text-xs text-red-400 flex items-center gap-1.5">
          <AlertTriangle size={11} /> {error}
        </div>
      )}
    </div>
  )
}
