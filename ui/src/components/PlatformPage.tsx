import { useState, useEffect, useRef, useCallback, useMemo, Component } from 'react'
import type { ReactNode, ErrorInfo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Server, Box, Layers, Network, HardDrive, Settings2, Activity, Radio, Package, Search, DollarSign, AlertCircle, ShieldAlert, Flame, Ghost, LayoutGrid, Clock, ShieldCheck,
  RefreshCw, ChevronDown, ChevronRight, CheckCircle, XCircle, AlertTriangle,
  Loader2, Plus, Download, Terminal, Trash2, RotateCcw, Minus, Lock, Eye,
  Upload, Cpu, MemoryStick, Globe, FileText, Zap, BotMessageSquare, X,
  ArrowUpDown, Play, Square, MinusCircle, Info, Shield, Copy, Tag, BookOpen, Pencil,
  SendHorizontal, GitCompare, Workflow, Sparkles,
} from 'lucide-react'
import { PageHeader, Skeleton, SkeletonRows, EmptyState } from './ui'
import { useToast } from './Toast'

const API = import.meta.env.VITE_API_URL || ''

// ── Resilience helpers ────────────────────────────────────────────────────────

function isRetryable(e: unknown): boolean {
  if (e instanceof Error) {
    const msg = e.message
    return msg.startsWith('503') || msg.startsWith('504') || msg.includes('fetch')
  }
  return false
}

async function withRetry<T>(fn: () => Promise<T>, retries = 1, delayMs = 600): Promise<T> {
  try {
    return await fn()
  } catch (e: unknown) {
    if (retries > 0 && isRetryable(e)) {
      await new Promise(r => setTimeout(r, delayMs))
      return withRetry(fn, retries - 1, delayMs)
    }
    throw e
  }
}

// ── Section Error Boundary ────────────────────────────────────────────────────

interface EBState { error: Error | null }

class SectionErrorBoundary extends Component<{ children: ReactNode; title: string }, EBState> {
  state: EBState = { error: null }

  static getDerivedStateFromError(error: Error): EBState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[PlatformConsole] Section "${this.props.title}" crashed:`, error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
          <AlertTriangle size={28} className="text-red-400" />
          <div>
            <p className="text-sm font-medium text-white mb-1">"{this.props.title}" failed to render</p>
            <p className="text-xs text-slate-500 font-mono max-w-md">{this.state.error.message}</p>
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs rounded-lg"
          >
            <RefreshCw size={12} /> Retry section
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface Cluster {
  id: string
  name: string
  namespace: string
  phase: string
  ready: boolean
  available: boolean
  k8s_version: string
  control_plane_ready: boolean
  replicas: number
  ready_replicas: number
  created_at: string
  source?: 'supervisor' | 'imported'
  server?: string
}

interface PodInfo {
  name: string
  namespace: string
  phase: string
  pod_ip: string
  host_ip: string
  node: string
  owner: string
  ready: string
  restarts: number
  crashloop: boolean
  containers: { name: string; image: string; ready: boolean; restarts: number; state: string }[]
  created_at: string
  req_cpu_m?: number
  req_mem_mib?: number
  lim_cpu_m?: number
  lim_mem_mib?: number
}

interface PodEvent {
  name: string
  namespace: string
  type: string
  reason: string
  message: string
  object: string
  count: number
  last_time: string
  source: string
}

// ── Table sort + filter hooks ─────────────────────────────────────────────────

type SortDir = 'asc' | 'desc' | null

function useSortedFiltered<T>(
  items: T[],
  filterText: string,
  filterKeys: string[],
  sortKey: string | null,
  sortDir: SortDir,
): T[] {
  const lower = filterText.toLowerCase()
  const asMap = (item: T) => item as Record<string, unknown>

  let result = filterText
    ? items.filter(item =>
        filterKeys.some(k => String(asMap(item)[k] ?? '').toLowerCase().includes(lower))
      )
    : items

  if (sortKey && sortDir) {
    result = [...result].sort((a, b) => {
      const av = asMap(a)[sortKey] ?? ''
      const bv = asMap(b)[sortKey] ?? ''
      const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true })
      return sortDir === 'asc' ? cmp : -cmp
    })
  }
  return result
}

function SortableHeader({
  label, col, sortKey, sortDir, onSort, className = '',
}: {
  label: string; col: string; sortKey: string | null; sortDir: SortDir
  onSort: (col: string) => void; className?: string
}) {
  const active = sortKey === col
  return (
    <th
      className={`px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400 cursor-pointer select-none hover:text-slate-300 ${className}`}
      onClick={() => onSort(col)}
    >
      <span className="flex items-center gap-1">
        {label}
        <span className={`text-[8px] ${active ? 'text-blue-400' : 'text-slate-700'}`}>
          {active && sortDir === 'asc' ? '▲' : active && sortDir === 'desc' ? '▼' : '⇅'}
        </span>
      </span>
    </th>
  )
}

function useSort(initial: string | null = null) {
  const [sortKey, setSortKey] = useState<string | null>(initial)
  const [sortDir, setSortDir] = useState<SortDir>(null)

  const onSort = useCallback((col: string) => {
    setSortKey(prev => {
      if (prev !== col) { setSortDir('asc'); return col }
      setSortDir(d => d === 'asc' ? 'desc' : d === 'desc' ? null : 'asc')
      return col
    })
  }, [])

  return { sortKey, sortDir, onSort }
}

// ── Copy-to-clipboard ─────────────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <button
      onClick={e => { e.stopPropagation(); copy() }}
      className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded text-slate-500 hover:text-slate-300"
      title="Copy to clipboard"
    >
      {copied
        ? <CheckCircle size={11} className="text-emerald-400" />
        : <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
      }
    </button>
  )
}

// ── Auto-refresh hook ─────────────────────────────────────────────────────────

function useAutoRefresh(callback: () => void, intervalMs: number, enabled = true) {
  const savedCb = useRef(callback)
  useEffect(() => { savedCb.current = callback }, [callback])
  useEffect(() => {
    if (!enabled) return
    const id = setInterval(() => {
      if (!document.hidden) savedCb.current()
    }, intervalMs)
    return () => clearInterval(id)
  }, [intervalMs, enabled])
}

interface WorkloadItem {
  name: string
  namespace: string
  kind: string
  replicas?: number
  ready_replicas?: number
  images: string[]
  selector: Record<string, string>
  labels: Record<string, string>
  created_at: string
  raw: Record<string, unknown>
  // Job-specific
  succeeded?: number
  failed_count?: number
  active?: number
  completions?: number
  start_time?: string
  completion_time?: string
  job_status?: string
  // CronJob-specific
  schedule?: string
  last_schedule_time?: string
  last_successful_time?: string
  active_jobs?: number
  suspend?: boolean
  annotations?: Record<string, string>
}

interface NodeCondition {
  type: string; status: string; reason: string; message: string; last_transition: string
}
interface NodeInfo {
  name: string
  ready: boolean
  unschedulable: boolean
  roles: string[]
  os: string
  kernel: string
  container_runtime: string
  kubelet_version: string
  allocatable_cpu_m: number
  allocatable_mem_mib: number
  capacity_cpu_m: number
  capacity_mem_mib: number
  taints: { key: string; effect: string; value?: string }[]
  labels: Record<string, string>
  created_at: string
  conditions: NodeCondition[]
}

interface ConfirmState {
  token: string
  action: string
  target: string
  description: string
  onConfirm: (token: string) => void
}

interface LogState {
  cluster: string
  namespace: string
  pod: string
  container?: string
}

interface ExecState {
  cluster: string
  namespace: string
  pod: string
  container?: string
}

interface DiagnoseState {
  cluster: string
  namespace: string
  pod: string
  result: string
  loading: boolean
}

type PlatformSection = 'overview' | 'namespaces' | 'workloads' | 'pods' | 'nodes' |
  'services' | 'ingresses' | 'networkpolicies' | 'storage' | 'quotas' | 'config' | 'secrets' | 'serviceaccounts' | 'rbac' | 'events' | 'hpa' | 'pdbs' | 'limitranges' | 'images' | 'topology' | 'crds' | 'pod-resources' | 'event-stream' | 'helm' | 'search' | 'cost' | 'netpol-analyzer' | 'tls-certs' | 'oom-detector' | 'orphans' | 'fleet-health' | 'fleet-diff' | 'scheduling' | 'rbac-risks' | 'node-pressure' | 'log-search' | 'audit' | 'pvc-analysis' | 'restart-timeline' | 'pdb-coverage' | 'affinity-coverage' | 'security-audit' | 'namespace-labels' | 'pod-traffic'

// ── API helpers ───────────────────────────────────────────────────────────────

export class KubeForbiddenError extends Error {
  constructor(public verb: string, public resource: string, public namespace: string) {
    super(`Forbidden: ${verb} ${resource}${namespace ? ` in ${namespace}` : ''}`)
    this.name = 'KubeForbiddenError'
  }
}

async function _parseError(r: Response): Promise<Error> {
  try {
    const body = await r.json()
    if (body.error_type === 'forbidden') {
      return new KubeForbiddenError(body.verb ?? '', body.resource ?? '', body.namespace ?? '')
    }
    return new Error(`${r.status}: ${body.detail ?? JSON.stringify(body)}`)
  } catch {
    return new Error(`${r.status}: ${r.statusText}`)
  }
}

async function vksGet<T = unknown>(path: string): Promise<T> {
  return withRetry(async () => {
    const r = await fetch(`${API}/api/v1/vks/${path}`)
    if (!r.ok) throw await _parseError(r)
    return r.json() as Promise<T>
  })
}

async function vksPost<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  return withRetry(async () => {
    const r = await fetch(`${API}/api/v1/vks/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw await _parseError(r)
    return r.json() as Promise<T>
  })
}

async function vksDelete<T = unknown>(path: string): Promise<T> {
  const r = await fetch(`${API}/api/v1/vks/${path}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text().catch(() => r.statusText)}`)
  return r.json()
}

async function vksPut<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(`${API}/api/v1/vks/${path}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!r.ok) throw await _parseError(r)
  return r.json()
}

async function vksPatch<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  return withRetry(async () => {
    const r = await fetch(`${API}/api/v1/vks/${path}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw await _parseError(r)
    return r.json() as Promise<T>
  })
}

// ── Section error display ─────────────────────────────────────────────────────

function SectionError({ error, onRetry }: { error: string | Error | null; onRetry?: () => void }) {
  if (!error) return null
  const isForbidden = error instanceof KubeForbiddenError
  return (
    <div className={`flex items-start gap-3 p-4 rounded-xl border ${isForbidden ? 'bg-orange-500/10 border-orange-500/30' : 'bg-red-500/10 border-red-500/30'}`}>
      <Shield size={16} className={`flex-shrink-0 mt-0.5 ${isForbidden ? 'text-orange-400' : 'text-red-400'}`} />
      <div className="flex-1 min-w-0">
        {isForbidden ? (
          <>
            <p className="text-sm font-medium text-orange-300">Insufficient permissions</p>
            <p className="text-xs text-orange-400/80 mt-0.5">
              Your service account cannot <strong>{(error as KubeForbiddenError).verb}</strong> <strong>{(error as KubeForbiddenError).resource}</strong>
              {(error as KubeForbiddenError).namespace && <> in namespace <strong>{(error as KubeForbiddenError).namespace}</strong></>}.
            </p>
            <p className="text-[10px] text-orange-500/60 mt-1">Ask your cluster admin to grant the required RBAC permission.</p>
          </>
        ) : (
          <>
            <p className="text-sm font-medium text-red-300">Failed to load</p>
            <p className="text-xs text-red-400/80 mt-0.5 font-mono">{String(error)}</p>
          </>
        )}
      </div>
      {onRetry && (
        <button onClick={onRetry} className="flex-shrink-0 flex items-center gap-1 px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-xs text-slate-300">
          <RefreshCw size={11} /> Retry
        </button>
      )}
    </div>
  )
}

// ── Keyboard shortcuts overlay ────────────────────────────────────────────────

const SHORTCUTS = [
  { key: '?', desc: 'Toggle this overlay' },
  { key: 'r', desc: 'Refresh current section' },
  { key: 'Esc', desc: 'Close modal / dialog' },
  { key: '/', desc: 'Focus filter input' },
  { key: 'g o', desc: 'Go to Overview' },
  { key: 'g p', desc: 'Go to Pods' },
  { key: 'g w', desc: 'Go to Workloads' },
  { key: 'g n', desc: 'Go to Nodes' },
  { key: 'g s', desc: 'Go to Services' },
  { key: 'g i', desc: 'Go to Ingresses' },
  { key: 'g c', desc: 'Go to ConfigMaps' },
  { key: 'g k', desc: 'Go to Secrets' },
  { key: 'g q', desc: 'Go to Quotas' },
  { key: 'g x', desc: 'Go to Service Accounts' },
  { key: 'g a', desc: 'Go to Audit Log' },
  { key: 'g e', desc: 'Go to Events' },
]

function KeyboardShortcutsOverlay({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape' || e.key === '?') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-80 shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-white">Keyboard Shortcuts</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white"><X size={14} /></button>
        </div>
        <div className="space-y-2">
          {SHORTCUTS.map(s => (
            <div key={s.key} className="flex items-center justify-between text-xs">
              <span className="text-slate-400">{s.desc}</span>
              <kbd className="px-1.5 py-0.5 bg-slate-800 border border-slate-600 rounded text-slate-300 font-mono text-[10px]">{s.key}</kbd>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── YAML Viewer/Editor Modal ─────────────────────────────────────────────────

interface YamlViewerTarget { clusterId: string; kind: string; name: string; namespace?: string }

function YamlViewerModal({ target, onClose }: { target: YamlViewerTarget; onClose: () => void }) {
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<Error | null>(null)
  const [dirty, setDirty] = useState(false)
  const toast = useToast()

  useEffect(() => {
    const ns = target.namespace ? `?namespace=${target.namespace}` : ''
    vksGet<{ yaml: string }>(`${target.clusterId}/raw/${target.kind}/${target.name}${ns}`)
      .then(d => { setYaml(d.yaml); setErr(null) })
      .catch(e => setErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [target])

  async function save() {
    setSaving(true)
    const ns = target.namespace ? `?namespace=${target.namespace}` : ''
    try {
      const r = await fetch(`${API}/api/v1/vks/${target.clusterId}/raw/${target.kind}/${target.name}${ns}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml }),
      })
      if (!r.ok) throw await _parseError(r)
      toast.success(`${target.name} updated`)
      setDirty(false)
      onClose()
    } catch (e) {
      toast.error(`Save failed: ${e}`)
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-3 px-5 py-3 border-b border-slate-800">
          <FileText size={14} className="text-slate-400" />
          <span className="text-sm font-medium text-white font-mono">{target.kind}/{target.name}</span>
          {target.namespace && <span className="text-xs text-slate-500">ns: {target.namespace}</span>}
          {dirty && <span className="ml-1 px-1.5 py-0.5 text-[10px] bg-yellow-900/30 text-yellow-400 rounded border border-yellow-700/30">unsaved</span>}
          <div className="ml-auto flex gap-2">
            {dirty && (
              <button onClick={save} disabled={saving}
                className="flex items-center gap-1 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50">
                {saving ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
                Apply
              </button>
            )}
            <button onClick={onClose} className="p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
          </div>
        </div>
        <div className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center h-32 text-slate-400 text-sm">
              <Loader2 size={14} className="animate-spin mr-2" /> Loading…
            </div>
          ) : err ? (
            <div className="p-4"><SectionError error={err} /></div>
          ) : (
            <textarea
              value={yaml}
              onChange={e => { setYaml(e.target.value); setDirty(true) }}
              className="w-full h-full min-h-[60vh] bg-slate-950 font-mono text-xs text-green-300 p-4 resize-none focus:outline-none leading-relaxed"
              spellCheck={false}
            />
          )}
        </div>
      </div>
    </div>
  )
}

// ── Confirm Dialog ────────────────────────────────────────────────────────────

function ConfirmDialog({ state, onDone }: { state: ConfirmState; onDone: () => void }) {
  const btnRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    btnRef.current?.focus()
    const prev = document.activeElement as HTMLElement
    return () => prev?.focus()
  }, [])
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog" aria-modal="true"
      onKeyDown={e => e.key === 'Escape' && onDone()}
    >
      <div className="bg-slate-900 border border-red-500/40 rounded-xl p-6 w-full max-w-md shadow-2xl">
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle size={18} className="text-red-400" />
          <h3 className="font-bold text-white text-sm">Confirm Destructive Action</h3>
        </div>
        <p className="text-slate-300 text-sm mb-5">{state.description}</p>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onDone}
            className="px-4 py-2 rounded-lg text-sm bg-slate-700 hover:bg-slate-600 text-slate-300"
          >
            Cancel
          </button>
          <button
            ref={btnRef}
            onClick={() => { state.onConfirm(state.token); onDone() }}
            className="px-4 py-2 rounded-lg text-sm bg-red-600 hover:bg-red-500 text-white font-medium"
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Scale Dialog ─────────────────────────────────────────────────────────────

function ScaleDialog({
  name, current, onConfirm, onClose,
}: { name: string; current: number; onConfirm: (n: number) => void; onClose: () => void }) {
  const [val, setVal] = useState(String(current))
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { inputRef.current?.select() }, [])

  function submit() {
    const n = parseInt(val)
    if (isNaN(n) || n < 0) return
    onConfirm(n)
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog" aria-modal="true"
      onKeyDown={e => { if (e.key === 'Escape') onClose(); if (e.key === 'Enter') submit() }}
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-5 w-full max-w-xs shadow-2xl">
        <h3 className="font-semibold text-white text-sm mb-1">Scale "{name}"</h3>
        <p className="text-xs text-slate-400 mb-3">Set desired replica count</p>
        <input
          ref={inputRef}
          type="number"
          min={0}
          value={val}
          onChange={e => setVal(e.target.value)}
          className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-blue-500 mb-4"
        />
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-3 py-1.5 rounded-lg text-xs bg-slate-700 hover:bg-slate-600 text-slate-300">Cancel</button>
          <button onClick={submit} className="px-3 py-1.5 rounded-lg text-xs bg-blue-600 hover:bg-blue-500 text-white font-medium">Scale</button>
        </div>
      </div>
    </div>
  )
}

// ── Log Viewer (SSE) ──────────────────────────────────────────────────────────

// ── Pod Exec Terminal ─────────────────────────────────────────────────────────

function ExecTerminal({ state, onClose }: { state: ExecState; onClose: () => void }) {
  const outputRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [output, setOutput] = useState<string>('Connecting…\r\n')
  const [connected, setConnected] = useState(false)
  const [inputLine, setInputLine] = useState('')
  const enc = new TextEncoder()
  const dec = new TextDecoder()

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const container = state.container ? `&container=${encodeURIComponent(state.container)}` : ''
    const url = `${protocol}//${host}/api/v1/vks/${state.cluster}/pods/${encodeURIComponent(state.pod)}/exec?namespace=${encodeURIComponent(state.namespace)}&command=/bin/sh${container}`

    const ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      setOutput('Connected\r\n$ ')
      inputRef.current?.focus()
    }

    ws.onmessage = (ev) => {
      const data = ev.data instanceof ArrayBuffer ? dec.decode(ev.data) : String(ev.data)
      setOutput(prev => prev + data)
      setTimeout(() => {
        if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
      }, 0)
    }

    ws.onerror = () => setOutput(prev => prev + '\r\n[WebSocket error]\r\n')
    ws.onclose = () => { setConnected(false); setOutput(prev => prev + '\r\n[Connection closed]\r\n') }

    return () => { ws.close() }
  }, [state.cluster, state.pod, state.namespace, state.container])

  function sendInput(text: string) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(enc.encode(text))
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      sendInput(inputLine + '\n')
      setOutput(prev => prev + inputLine + '\r\n')
      setInputLine('')
    } else if (e.key === 'c' && e.ctrlKey) {
      sendInput('\x03')
      setOutput(prev => prev + '^C\r\n')
      setInputLine('')
      e.preventDefault()
    } else if (e.key === 'l' && e.ctrlKey) {
      setOutput('')
      e.preventDefault()
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-slate-950 border border-slate-700/60 rounded-xl w-full max-w-4xl shadow-2xl flex flex-col" style={{ height: '70vh' }}>
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-800">
          <Terminal size={14} className="text-emerald-400" />
          <span className="text-sm font-medium text-white font-mono">{state.pod}</span>
          {state.container && <span className="text-xs text-slate-400">{state.container}</span>}
          <div className={`ml-2 w-2 h-2 rounded-full ${connected ? 'bg-emerald-400' : 'bg-red-400'}`} />
          <span className="text-xs text-slate-500">{connected ? 'connected' : 'disconnected'}</span>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div
          ref={outputRef}
          className="flex-1 overflow-y-auto px-4 py-3 font-mono text-xs text-emerald-300 whitespace-pre-wrap leading-relaxed"
          style={{ background: 'rgba(2,6,12,0.98)' }}
        >
          {output}
        </div>
        <div className="flex items-center gap-2 px-4 py-2 border-t border-slate-800 bg-slate-900/80">
          <span className="text-emerald-400 font-mono text-xs">$</span>
          <input
            ref={inputRef}
            type="text"
            value={inputLine}
            onChange={e => setInputLine(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!connected}
            className="flex-1 bg-transparent font-mono text-xs text-white outline-none placeholder-slate-600 disabled:opacity-40"
            placeholder={connected ? 'Type command…' : 'Connecting…'}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      </div>
    </div>
  )
}

function LogViewer({ state, onClose }: { state: LogState; onClose: () => void }) {
  const [lines, setLines] = useState<string[]>([])
  const [following, setFollowing] = useState(false)
  const [tailLines, setTailLines] = useState(300)
  const [activeContainer, setActiveContainer] = useState(state.container ?? '')
  const [containers, setContainers] = useState<string[]>(state.container ? [state.container] : [])
  const [logSearch, setLogSearch] = useState('')
  const [levelFilter, setLevelFilter] = useState<'all' | 'error' | 'warn' | 'info'>('all')
  const [wrapLines, setWrapLines] = useState(true)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Fetch container list once
  useEffect(() => {
    vksGet<{ pods: PodInfo[] }>(`${state.cluster}/pods?namespace=${state.namespace}`)
      .then(d => {
        const pod = d.pods.find(p => p.name === state.pod)
        if (pod && pod.containers.length > 0) {
          const names = pod.containers.map(c => c.name)
          setContainers(names)
          if (!activeContainer) setActiveContainer(names[0])
        }
      })
      .catch(() => {})
  }, [state.cluster, state.namespace, state.pod])

  const load = useCallback((follow: boolean, container: string, tail: number) => {
    setLines([])
    const params = new URLSearchParams({
      namespace: state.namespace,
      tail_lines: String(tail),
      follow: String(follow),
      ...(container ? { container } : {}),
    })
    const es = new EventSource(`${API}/api/v1/vks/${state.cluster}/pods/${state.pod}/logs?${params}`)
    es.onmessage = ev => {
      try {
        const d = JSON.parse(ev.data)
        if (d.done) { es.close(); return }
        if (d.line != null) setLines(prev => [...prev.slice(-2000), d.line])
      } catch { /* ignore */ }
    }
    es.onerror = () => es.close()
    return es
  }, [state])

  useEffect(() => {
    const es = load(following, activeContainer, tailLines)
    return () => es?.close()
  }, [load, following, activeContainer, tailLines])

  useEffect(() => {
    if (following) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines, following])

  function handleDownload() {
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${state.pod}${activeContainer ? `-${activeContainer}` : ''}.log`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  function lineColor(l: string) {
    const up = l.toUpperCase()
    if (/\bERROR\b|\bFATAL\b|\bPANIC\b/.test(up)) return 'text-red-400'
    if (/\bWARN(ING)?\b/.test(up)) return 'text-yellow-400'
    if (/\bINFO\b/.test(up)) return 'text-emerald-300'
    if (/\bDEBUG\b|\bTRACE\b/.test(up)) return 'text-slate-500'
    return 'text-emerald-300'
  }

  const lowerSearch = logSearch.toLowerCase()
  const visibleLines = lines.filter(l => {
    const up = l.toUpperCase()
    if (levelFilter === 'error' && !/\bERROR\b|\bFATAL\b/.test(up)) return false
    if (levelFilter === 'warn' && !/\bWARN(ING)?\b/.test(up)) return false
    if (levelFilter === 'info' && !/\bINFO\b/.test(up)) return false
    if (lowerSearch && !l.toLowerCase().includes(lowerSearch)) return false
    return true
  })

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-slate-950" role="dialog" aria-modal="true">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-800 bg-slate-900 flex-wrap">
        <Terminal size={16} className="text-emerald-400" />
        <span className="text-white text-sm font-mono">{state.pod}</span>
        <span className="text-slate-500 text-xs">{state.namespace}</span>
        {containers.length > 1 && (
          <div className="flex bg-slate-800 rounded p-0.5 gap-0.5">
            {containers.map(c => (
              <button key={c} onClick={() => setActiveContainer(c)}
                className={`px-2 py-1 text-xs rounded transition-colors ${activeContainer === c ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}>
                {c}
              </button>
            ))}
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          <select
            value={tailLines}
            onChange={e => setTailLines(Number(e.target.value))}
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300 focus:outline-none"
          >
            {[100, 300, 500, 1000].map(n => <option key={n} value={n}>{n} lines</option>)}
          </select>
          <button onClick={handleDownload} title="Download log"
            className="p-1.5 rounded hover:bg-slate-700 text-slate-400">
            <Download size={14} />
          </button>
          <button onClick={() => setLines([])} title="Clear"
            className="p-1.5 rounded hover:bg-slate-700 text-slate-400">
            <Trash2 size={14} />
          </button>
          <button
            onClick={() => setFollowing(f => !f)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${following ? 'bg-emerald-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
          >
            {following ? <Square size={12} /> : <Play size={12} />}
            {following ? 'Streaming' : 'Stream'}
          </button>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-slate-700 text-slate-400">
            <X size={16} />
          </button>
        </div>
      </div>
      {/* Search + level filter row */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-800 bg-slate-900/80 flex-wrap">
        <input
          type="text"
          placeholder="Search logs…"
          value={logSearch}
          onChange={e => setLogSearch(e.target.value)}
          className="flex-1 min-w-[140px] px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-white placeholder-slate-500 font-mono focus:outline-none focus:border-blue-500"
        />
        <div className="flex bg-slate-800 rounded p-0.5 gap-0.5">
          {(['all', 'error', 'warn', 'info'] as const).map(lv => (
            <button key={lv} onClick={() => setLevelFilter(lv)}
              className={`px-2 py-1 text-xs rounded transition-colors ${levelFilter === lv ? (
                lv === 'error' ? 'bg-red-800 text-red-200' : lv === 'warn' ? 'bg-yellow-800 text-yellow-200' : lv === 'info' ? 'bg-emerald-800 text-emerald-200' : 'bg-blue-600 text-white'
              ) : 'text-slate-400 hover:text-white'}`}>
              {lv.toUpperCase()}
            </button>
          ))}
        </div>
        <button onClick={() => setWrapLines(w => !w)}
          className={`px-2 py-1 rounded text-xs transition-colors ${wrapLines ? 'bg-slate-700 text-slate-300' : 'bg-slate-800 text-slate-500'}`}>
          Wrap
        </button>
        <span className="text-[10px] text-slate-600">{visibleLines.length}/{lines.length} lines</span>
      </div>
      <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed overflow-x-auto">
        {visibleLines.length === 0 ? (
          <span className="text-slate-500">{lines.length > 0 ? 'No lines match filter.' : 'No logs yet…'}</span>
        ) : (
          visibleLines.map((l, i) => (
            <div key={i} className={`${wrapLines ? 'break-all' : 'whitespace-nowrap'} ${lineColor(l)}`}>
              {lowerSearch ? (
                l.split(new RegExp(`(${logSearch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')).map((part, pi) =>
                  part.toLowerCase() === lowerSearch
                    ? <mark key={pi} className="bg-yellow-500/30 text-yellow-200">{part}</mark>
                    : part
                )
              ) : (l || ' ')}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ── NL Command Bar ────────────────────────────────────────────────────────────

function NLCommandBar({ clusterId, onClose }: { clusterId: string; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<{ action: string; description: string } | null>(null)
  const [loading, setLoading] = useState(false)

  async function run() {
    if (!query.trim()) return
    setLoading(true)
    try {
      const data = await vksPost<{ parsed: { action: string; description: string } }>(
        'nl/action', { query, cluster_id: clusterId }
      )
      setResult(data.parsed)
    } catch (e) {
      setResult({ action: 'error', description: String(e) })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-32 bg-black/60 backdrop-blur-sm"
      role="dialog" aria-modal="true" onKeyDown={e => e.key === 'Escape' && onClose()}>
      <div className="bg-slate-900 border border-blue-500/40 rounded-xl w-full max-w-lg shadow-2xl overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-800">
          <BotMessageSquare size={16} className="text-blue-400" />
          <span className="text-sm text-white font-medium">Natural Language Actions</span>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div className="p-4">
          <div className="flex gap-2">
            <input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && run()}
              placeholder='e.g. "scale frontend to 3 in production" or "restart the crashlooping pod"'
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={run}
              disabled={loading || !query.trim()}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg flex items-center gap-1.5"
            >
              {loading ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
              Run
            </button>
          </div>
          {result && (
            <div className={`mt-4 p-3 rounded-lg text-sm ${result.action === 'unknown' || result.action === 'error' ? 'bg-yellow-900/30 border border-yellow-600/30 text-yellow-200' : 'bg-emerald-900/30 border border-emerald-600/30 text-emerald-200'}`}>
              {result.action !== 'unknown' && result.action !== 'error' && (
                <div className="font-mono text-xs text-slate-400 mb-1">action: {result.action}</div>
              )}
              {result.description}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Cluster Picker ────────────────────────────────────────────────────────────

function ClusterPicker({ clusters, selected, onSelect }: {
  clusters: Cluster[]
  selected: Cluster | null
  onSelect: (c: Cluster) => void
}) {
  if (!clusters.length) return (
    <EmptyState icon={Server} title="No VKS clusters found"
      hint="Clusters must be provisioned via CAPI on the supervisor." />
  )
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {clusters.map(c => (
        <button
          key={c.id}
          onClick={() => onSelect(c)}
          className={`text-left p-4 rounded-xl border transition-all hover:border-blue-500/60 hover:bg-slate-800/60 ${selected?.id === c.id ? 'border-blue-500 bg-slate-800/80' : 'border-slate-700/60 bg-slate-900/60'}`}
        >
          <div className="flex items-center gap-2 mb-3">
            <div className={`w-2 h-2 rounded-full ${c.ready ? 'bg-emerald-400' : 'bg-red-400'}`} />
            <span className="font-medium text-white text-sm">{c.name}</span>
          </div>
          <div className="text-xs text-slate-400 space-y-0.5">
            <div>Namespace: <span className="text-slate-300">{c.namespace}</span></div>
            <div>K8s: <span className="text-slate-300 font-mono">{c.k8s_version || '—'}</span></div>
            <div>Nodes: <span className="text-slate-300">{c.replicas === 0 && c.ready_replicas === 0 ? '—' : `${c.ready_replicas}/${c.replicas}`}</span></div>
            <div>Phase: <span className={c.phase === 'Provisioned' ? 'text-emerald-400' : 'text-yellow-400'}>{c.phase}</span></div>
          </div>
        </button>
      ))}
    </div>
  )
}

// ── Overview Section ──────────────────────────────────────────────────────────

interface QuotaAlert { namespace: string; quota: string; resource: string; used: string; hard: string; pct: number }

function _parseResource(v: string): number {
  if (!v) return 0
  if (v.endsWith('m')) return parseFloat(v) / 1000
  if (v.endsWith('Ki')) return parseFloat(v) * 1024
  if (v.endsWith('Mi')) return parseFloat(v) * 1024 * 1024
  if (v.endsWith('Gi')) return parseFloat(v) * 1024 * 1024 * 1024
  return parseFloat(v) || 0
}

function OverviewSection({ clusterId, onNavigate }: { clusterId: string; onNavigate?: (s: PlatformSection) => void }) {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<Error | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [exporting, setExporting] = useState(false)
  const [quotaAlerts, setQuotaAlerts] = useState<QuotaAlert[]>([])
  const toast = useToast()

  const load = useCallback(() => {
    vksGet(`${clusterId}/overview`)
      .then(d => { setData(d as Record<string, unknown>); setErr(null); setLastRefresh(new Date()) })
      .catch(e => setErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
    vksGet<{ quotas: QuotaInfo[] }>(`${clusterId}/quotas`)
      .then(d => {
        const alerts: QuotaAlert[] = []
        for (const q of d.quotas) {
          for (const [res, hardVal] of Object.entries(q.hard)) {
            const usedVal = q.used[res] ?? '0'
            const hardN = _parseResource(hardVal)
            const usedN = _parseResource(usedVal)
            const pct = hardN > 0 ? Math.round(usedN / hardN * 100) : 0
            if (pct >= 80) alerts.push({ namespace: q.namespace, quota: q.name, resource: res, used: usedVal, hard: hardVal, pct })
          }
        }
        alerts.sort((a, b) => b.pct - a.pct)
        setQuotaAlerts(alerts.slice(0, 8))
      })
      .catch(() => {})
  }, [clusterId])

  useEffect(() => { setLoading(true); load() }, [load])
  useAutoRefresh(load, 30_000)

  async function handleExport() {
    setExporting(true)
    try {
      const report = await vksGet<Record<string, unknown>>(`${clusterId}/health-report`)
      const json = JSON.stringify(report, null, 2)
      const blob = new Blob([json], { type: 'application/json' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `health-report-${clusterId.split('/').pop()}-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(a.href)
      toast.success('Health report downloaded')
    } catch (e) {
      toast.error(`Export failed: ${e}`)
    } finally {
      setExporting(false)
    }
  }

  if (loading) return <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-24 rounded-xl" /></div>
  if (err) return <SectionError error={err} onRetry={load} />
  if (!data) return null

  const nodes = data.nodes as { total: number; ready: number }
  const pods = data.pods as { running: number; pending: number; failed: number; total: number }
  const wl = data.workloads as { deployments: number; statefulsets: number; daemonsets: number }
  const alloc = data.allocatable as { cpu_cores: number; memory_mib: number }
  const events = (data.recent_events as Record<string, unknown>[]) || []
  const degraded = (data.degraded_deployments as { name: string; namespace: string; ready: number; desired: number }[]) || []
  const crashloops = (data.crashloop_pods as { name: string; namespace: string }[]) || []

  const nsCount = typeof data.namespaces === 'number' ? data.namespaces as number : 0
  const cards: { label: string; value: string | number; sub: string; icon: React.ElementType; color: string; nav?: PlatformSection }[] = [
    { label: 'Nodes', value: `${nodes.ready}/${nodes.total}`, sub: 'Ready', icon: Server, color: nodes.ready === nodes.total ? 'emerald' : 'yellow', nav: 'nodes' },
    { label: 'Pods Running', value: pods.running, sub: `${pods.pending} pending · ${pods.failed} failed`, icon: Box, color: pods.failed > 0 || crashloops.length > 0 ? 'red' : 'emerald', nav: 'pods' },
    { label: 'Workloads', value: wl.deployments + wl.statefulsets + wl.daemonsets, sub: `${wl.deployments} dep · ${wl.statefulsets} sts · ${wl.daemonsets} ds`, icon: Layers, color: degraded.length > 0 ? 'yellow' : 'blue', nav: 'workloads' },
    { label: 'Namespaces', value: nsCount, sub: `${alloc.cpu_cores}c · ${Math.round(alloc.memory_mib / 1024)} GiB RAM`, icon: Cpu, color: 'purple', nav: 'namespaces' },
  ]

  const clusterUnreachable = nodes.total === 0 && pods.total === 0 && wl.deployments === 0
  const issueCount = degraded.length + crashloops.length + (nodes.ready < nodes.total ? 1 : 0) + (pods.failed > 0 ? 1 : 0)
  const healthStatus = clusterUnreachable ? 'critical' : issueCount === 0 ? 'healthy' : issueCount <= 2 ? 'degraded' : 'critical'
  const warningEventCount = events.filter(e => e.type === 'Warning').length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium ${
          healthStatus === 'healthy' ? 'bg-emerald-900/30 border border-emerald-700/40 text-emerald-400' :
          healthStatus === 'degraded' ? 'bg-yellow-900/30 border border-yellow-700/40 text-yellow-400' :
          'bg-red-900/30 border border-red-700/40 text-red-400'
        }`}>
          <div className={`w-1.5 h-1.5 rounded-full ${
            healthStatus === 'healthy' ? 'bg-emerald-400' :
            healthStatus === 'degraded' ? 'bg-yellow-400 animate-pulse' : 'bg-red-400 animate-pulse'
          }`} />
          {clusterUnreachable ? 'Cluster unreachable — no data' :
           healthStatus === 'healthy' ? 'All systems operational' :
           healthStatus === 'degraded' ? `${issueCount} issue${issueCount > 1 ? 's' : ''} detected` :
           `${issueCount} critical issues`}
        </div>
        <div className="flex items-center gap-3">
          {warningEventCount > 0 && (
            <button onClick={() => onNavigate?.('events')}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-yellow-900/30 border border-yellow-700/40 text-yellow-400 text-xs hover:bg-yellow-900/50 transition-colors">
              <AlertTriangle size={11} />{warningEventCount} warning{warningEventCount !== 1 ? 's' : ''}
            </button>
          )}
          {lastRefresh && (
            <span className="text-[10px] text-slate-600">Updated {_relTime(lastRefresh.toISOString())}</span>
          )}
          <button
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 text-xs disabled:opacity-50"
            title="Export health report as JSON"
          >
            {exporting ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
            Export Report
          </button>
        </div>
      </div>
      {degraded.length > 0 && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={14} className="text-yellow-400" />
            <span className="text-sm font-medium text-yellow-300">{degraded.length} degraded deployment{degraded.length > 1 ? 's' : ''}</span>
          </div>
          <div className="grid gap-1.5">
            {degraded.map(d => (
              <div key={`${d.namespace}/${d.name}`} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-white">{d.name}</span>
                {d.namespace && <span className="text-slate-400">in {d.namespace}</span>}
                <span className="ml-auto text-yellow-400">{d.ready}/{d.desired} ready</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {crashloops.length > 0 && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={14} className="text-red-400 animate-pulse" />
            <span className="text-sm font-medium text-red-300">{crashloops.length} CrashLooping pod{crashloops.length > 1 ? 's' : ''}</span>
          </div>
          <div className="grid gap-1.5">
            {crashloops.slice(0, 8).map(p => (
              <div key={`${p.namespace}/${p.name}`} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-white">{p.name}</span>
                {p.namespace && <span className="text-slate-400">in {p.namespace}</span>}
              </div>
            ))}
            {crashloops.length > 8 && <span className="text-red-400 text-xs">+{crashloops.length - 8} more</span>}
          </div>
        </div>
      )}
      {quotaAlerts.length > 0 && (
        <div className="bg-orange-500/10 border border-orange-500/30 rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <MemoryStick size={14} className="text-orange-400" />
              <span className="text-sm font-medium text-orange-300">{quotaAlerts.length} quota dimension{quotaAlerts.length > 1 ? 's' : ''} near limit (≥80%)</span>
            </div>
            {onNavigate && (
              <button onClick={() => onNavigate('quotas')}
                className="text-[10px] px-2 py-0.5 rounded bg-orange-900/40 text-orange-300 hover:bg-orange-900/60 border border-orange-700/40">
                → Quotas
              </button>
            )}
          </div>
          <div className="grid gap-1.5">
            {quotaAlerts.map((a, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className={`tabular-nums font-bold ${a.pct >= 95 ? 'text-red-400' : 'text-orange-400'}`}>{a.pct}%</span>
                <span className="font-mono text-slate-300">{a.resource}</span>
                <span className="text-slate-500">in {a.namespace || 'default'}</span>
                <span className="ml-auto text-slate-400 font-mono">{a.used}/{a.hard}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map(card => (
          <div
            key={card.label}
            onClick={() => card.nav && onNavigate?.(card.nav)}
            className={`bg-slate-900/60 border border-slate-700/50 rounded-xl p-4 ${card.nav && onNavigate ? 'cursor-pointer hover:border-slate-600 hover:bg-slate-800/60 transition-colors' : ''}`}
          >
            <div className="flex items-center gap-2 mb-2">
              <card.icon size={16} className={`text-${card.color}-400`} />
              <span className="text-xs text-slate-400">{card.label}</span>
              {card.nav && onNavigate && <ArrowUpDown size={10} className="ml-auto text-slate-600" />}
            </div>
            <div className="text-2xl font-bold text-white mb-0.5">{card.value}</div>
            <div className="text-xs text-slate-500">{card.sub}</div>
          </div>
        ))}
      </div>
      <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-800 text-sm font-medium text-slate-300">Recent Events</div>
        {events.length === 0 ? (
          <div className="px-4 py-6 text-sm text-slate-500 text-center">No recent events</div>
        ) : (
          <table className="w-full text-xs">
            <tbody>
              {events.slice(0, 10).map((e, i) => (
                <tr key={i} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                  <td className="px-4 py-2">
                    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${(e.type as string) === 'Warning' ? 'bg-yellow-900/40 text-yellow-400' : 'bg-slate-800 text-slate-400'}`}>
                      {e.type as string}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-slate-400 font-mono">{e.object as string}</td>
                  <td className="px-2 py-2 text-slate-300 max-w-xs truncate">{e.message as string}</td>
                  <td className="px-4 py-2 text-slate-500 text-right whitespace-nowrap">{_relTime(e.last_time as string)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <RecentActionsWidget clusterId={clusterId} />
    </div>
  )
}

// ── Recent Actions Widget (used by OverviewSection) ───────────────────────────

function RecentActionsWidget({ clusterId }: { clusterId: string }) {
  const [events, setEvents] = useState<AuditEvent[]>([])

  useEffect(() => {
    vksGet<{ events: AuditEvent[] }>('audit?limit=50')
      .then(d => {
        const filtered = d.events.filter(e => !clusterId || e.cluster === clusterId || e.cluster.endsWith('/' + clusterId.split('/').pop()))
        setEvents(filtered.slice(0, 5))
      })
      .catch(() => {})
  }, [clusterId])

  if (events.length === 0) return null

  function verbColor(verb: string) {
    if (['delete', 'delete-secret', 'delete-pvc'].some(v => verb.startsWith(v))) return 'bg-red-900/40 text-red-400'
    if (['scale', 'restart', 'cordon', 'drain'].includes(verb)) return 'bg-yellow-900/40 text-yellow-400'
    if (['reveal-secret', 'yaml_edit', 'configmap_update'].includes(verb)) return 'bg-orange-900/40 text-orange-400'
    return 'bg-blue-900/40 text-blue-400'
  }

  return (
    <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-2">
        <Shield size={13} className="text-slate-500" />
        <span className="text-sm font-medium text-slate-300">Recent Actions</span>
        <span className="text-xs text-slate-500 ml-auto">{events.length} action{events.length !== 1 ? 's' : ''}</span>
      </div>
      <div className="divide-y divide-slate-800/50">
        {events.map((ev, i) => (
          <div key={i} className="flex items-center gap-3 px-4 py-2 text-xs hover:bg-slate-800/20">
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium whitespace-nowrap ${verbColor(ev.verb)}`}>{ev.verb}</span>
            <span className="font-mono text-white truncate">{ev.name}</span>
            <span className="text-slate-500">{ev.kind}</span>
            <span className="ml-auto text-slate-600 whitespace-nowrap">{_relTime(new Date(ev.timestamp * 1000).toISOString())}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Workload Detail Panel ─────────────────────────────────────────────────────

interface WlContainer {
  name: string
  image: string
  resources?: { requests?: { cpu?: string; memory?: string }; limits?: { cpu?: string; memory?: string } }
  env?: { name: string; value?: string; valueFrom?: unknown }[]
}

function WorkloadDetailPanel({ item, clusterId, namespace, onClose, onRefresh, podMetrics }: {
  item: WorkloadItem
  clusterId: string
  namespace: string
  onClose: () => void
  onRefresh: () => void
  podMetrics?: Record<string, { cpu_cores: number; mem_bytes: number; cpu_limit_cores: number; mem_limit_bytes: number; containers: { name: string; cpu_cores: number; mem_bytes: number }[] }>
}) {
  const isDeployment = item.kind === 'deployments' || !item.kind
  const isDegraded = (item.ready_replicas ?? 0) < (item.replicas ?? 0)
  const [tab, setTab] = useState<'overview' | 'edit' | 'env' | 'history' | 'diagnose'>(
    isDegraded ? 'diagnose' : 'overview'
  )
  const toast = useToast()

  // Extract containers from raw spec
  const rawContainers: WlContainer[] = ((item.raw as any)?.spec?.template?.spec?.containers ?? [])
  const [selContainer, setSelContainer] = useState(rawContainers[0]?.name ?? '')

  // Edit tab state (image + resources)
  const [images, setImages] = useState<Record<string, string>>(
    Object.fromEntries(rawContainers.map(c => [c.name, c.image]))
  )
  const [resources, setResources] = useState<Record<string, { reqCpu: string; reqMem: string; limCpu: string; limMem: string }>>(
    Object.fromEntries(rawContainers.map(c => [c.name, {
      reqCpu:  c.resources?.requests?.cpu    ?? '',
      reqMem:  c.resources?.requests?.memory ?? '',
      limCpu:  c.resources?.limits?.cpu      ?? '',
      limMem:  c.resources?.limits?.memory   ?? '',
    }]))
  )
  const [editConfirm, setEditConfirm] = useState<ConfirmState | null>(null)

  // Env tab state
  const [envRows, setEnvRows] = useState<{ name: string; value: string; valueFrom?: unknown }[]>([])
  const [envConfirm, setEnvConfirm] = useState<ConfirmState | null>(null)
  const [addEnvKey, setAddEnvKey] = useState('')
  const [addEnvVal, setAddEnvVal] = useState('')

  useEffect(() => {
    const ctr = rawContainers.find(c => c.name === selContainer)
    setEnvRows((ctr?.env ?? []).map(e => ({ name: e.name, value: (e as any).value ?? '', valueFrom: (e as any).valueFrom })))
  }, [selContainer, item.name, namespace])

  // History tab
  interface Revision { revision: number; name: string; created_at: string; images: string[]; change_cause: string }
  const [revisions, setRevisions] = useState<Revision[]>([])
  const [histLoading, setHistLoading] = useState(false)
  const [rollbackConfirm, setRollbackConfirm] = useState<ConfirmState | null>(null)

  useEffect(() => {
    if (tab !== 'history' || !isDeployment) return
    setHistLoading(true)
    vksGet<{ revisions: Revision[] }>(`${clusterId}/deployments/${item.name}/history?namespace=${namespace}`)
      .then(d => setRevisions(d.revisions))
      .catch(() => {})
      .finally(() => setHistLoading(false))
  }, [tab, item.name, namespace, clusterId])

  // AI diagnose
  const [diagnoseText, setDiagnoseText] = useState('')
  const [diagnoseLoading, setDiagnoseLoading] = useState(false)
  const [teachMode, setTeachMode] = useState(false)
  const diagnoseEs = useRef<EventSource | null>(null)

  function runDiagnose() {
    if (diagnoseEs.current) diagnoseEs.current.close()
    setDiagnoseText(''); setDiagnoseLoading(true)
    const mode = teachMode ? 'teach' : 'diagnose'
    const kind = item.kind || 'deployments'
    const es = new EventSource(
      `${API}/api/v1/vks/${encodeURIComponent(clusterId)}/workloads/${kind}/${encodeURIComponent(item.name)}/diagnose?namespace=${encodeURIComponent(namespace)}&mode=${mode}`
    )
    diagnoseEs.current = es
    let text = ''
    es.onmessage = ev => {
      try {
        const d = JSON.parse(ev.data)
        if (d.text) { text += d.text; setDiagnoseText(text) }
        if (d.done) { es.close(); setDiagnoseLoading(false) }
        if (d.error) { setDiagnoseText(`⚠ LLM unavailable — ${d.error}`); es.close(); setDiagnoseLoading(false) }
      } catch {}
    }
    es.onerror = () => { if (!text) setDiagnoseText('⚠ LLM unavailable — check llm-gateway logs'); es.close(); setDiagnoseLoading(false) }
  }
  useEffect(() => {
    if (tab === 'diagnose' && !diagnoseText && !diagnoseLoading) runDiagnose()
    return () => { if (diagnoseEs.current) diagnoseEs.current.close() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])
  useEffect(() => () => { if (diagnoseEs.current) diagnoseEs.current.close() }, [])

  // Handlers
  async function submitImageAndResources() {
    const containers = rawContainers.map(c => {
      const img = images[c.name] || c.image
      const r = resources[c.name]
      return { name: c.name, image: img, requests: { cpu: r?.reqCpu, memory: r?.reqMem }, limits: { cpu: r?.limCpu, memory: r?.limMem } }
    })
    try {
      // Patch resources
      const resResp = await vksPatch<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/workloads/${item.kind || 'deployments'}/${item.name}/resources?namespace=${namespace}`,
        { containers: containers.map(c => ({ name: c.name, requests: c.requests, limits: c.limits })) }
      )
      if (resResp.requires_confirm) {
        setEditConfirm({ ...resResp, onConfirm: async (token) => {
          await vksPatch(`${clusterId}/workloads/${item.kind || 'deployments'}/${item.name}/resources?namespace=${namespace}&token=${token}`,
            { containers: containers.map(c => ({ name: c.name, requests: c.requests, limits: c.limits })) })
          // Continue to image patches after resource confirm
          for (const c of rawContainers) {
            if (images[c.name] && images[c.name] !== c.image) {
              const imgResp2 = await vksPatch<ConfirmState & { requires_confirm?: boolean }>(
                `${clusterId}/workloads/${item.kind || 'deployments'}/${item.name}/image?namespace=${namespace}`,
                { container: c.name, image: images[c.name] }
              )
              if (imgResp2.requires_confirm) {
                setEditConfirm({ ...imgResp2, onConfirm: async (t2) => {
                  await vksPatch(`${clusterId}/workloads/${item.kind || 'deployments'}/${item.name}/image?namespace=${namespace}&token=${t2}`,
                    { container: c.name, image: images[c.name] })
                  toast.success(`Image updated for ${c.name}`); onRefresh()
                }})
                return
              }
            }
          }
          toast.success('Workload updated'); onRefresh()
        }})
        return
      }
      // Patch images (one per container that changed)
      for (const c of rawContainers) {
        if (images[c.name] && images[c.name] !== c.image) {
          const imgResp = await vksPatch<ConfirmState & { requires_confirm?: boolean }>(
            `${clusterId}/workloads/${item.kind || 'deployments'}/${item.name}/image?namespace=${namespace}`,
            { container: c.name, image: images[c.name] }
          )
          if (imgResp.requires_confirm) {
            setEditConfirm({ ...imgResp, onConfirm: async (token) => {
              await vksPatch(`${clusterId}/workloads/${item.kind || 'deployments'}/${item.name}/image?namespace=${namespace}&token=${token}`,
                { container: c.name, image: images[c.name] })
              toast.success(`Image updated for ${c.name}`); onRefresh()
            }})
            return
          }
        }
      }
      toast.success('Workload updated'); onRefresh()
    } catch (e) { toast.error(`Update failed: ${e}`) }
  }

  async function submitEnvVars() {
    const env = envRows.filter(r => r.name.trim()).map(r => r.valueFrom ? { name: r.name, valueFrom: r.valueFrom } : { name: r.name, value: r.value })
    try {
      const resp = await vksPatch<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/workloads/${item.kind || 'deployments'}/${item.name}/env?namespace=${namespace}`,
        { container: selContainer, env }
      )
      if (resp.requires_confirm) {
        setEnvConfirm({ ...resp, onConfirm: async (token) => {
          await vksPatch(`${clusterId}/workloads/${item.kind || 'deployments'}/${item.name}/env?namespace=${namespace}&token=${token}`,
            { container: selContainer, env })
          toast.success('Env vars updated'); onRefresh()
        }})
      } else {
        toast.success('Env vars updated'); onRefresh()
      }
    } catch (e) { toast.error(`Env update failed: ${e}`) }
  }

  async function doRollback(rsName: string) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/deployments/${item.name}/rollback?namespace=${namespace}`,
        { rs_name: rsName }
      )
      if (resp.requires_confirm) {
        setRollbackConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/deployments/${item.name}/rollback?namespace=${namespace}&token=${token}`,
            { rs_name: rsName })
          toast.success(`Rolled back to revision ${rsName}`); onRefresh()
        }})
      }
    } catch (e) { toast.error(`Rollback failed: ${e}`) }
  }

  const kindLabel = (item.kind || 'Deployment').replace('deployments', 'Deployment').replace('statefulsets', 'StatefulSet').replace('daemonsets', 'DaemonSet')
  const ready = item.ready_replicas ?? 0
  const desired = item.replicas ?? 0
  const healthColor = ready === desired ? 'text-emerald-400' : ready === 0 ? 'text-red-400' : 'text-yellow-400'

  const TABS = [
    { id: 'overview' as const, label: 'Overview' },
    { id: 'edit'     as const, label: 'Edit' },
    { id: 'env'      as const, label: 'Env Vars' },
    ...(isDeployment ? [{ id: 'history' as const, label: 'History' }] : []),
    { id: 'diagnose' as const, label: '✦ AI Diagnose' },
  ]

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <>
      {editConfirm    && <ConfirmDialog state={editConfirm}    onDone={() => setEditConfirm(null)} />}
      {envConfirm     && <ConfirmDialog state={envConfirm}     onDone={() => setEnvConfirm(null)} />}
      {rollbackConfirm && <ConfirmDialog state={rollbackConfirm} onDone={() => setRollbackConfirm(null)} />}
      {/* pointer-events-none: visual dim only, never blocks table interaction */}
      <div className="fixed inset-0 z-40 bg-black/30 pointer-events-none" />
      <div className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-[600px] bg-[#0d1117] border-l border-slate-700/60 shadow-2xl flex flex-col">

        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-slate-800 flex-shrink-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{kindLabel}</span>
              <span className="font-mono text-sm font-bold text-white truncate">{item.name}</span>
            </div>
            <div className="flex items-center gap-2 mt-0.5 text-[11px]">
              <span className="text-slate-500">{item.namespace || namespace}</span>
              <span className="text-slate-700">·</span>
              <span className={healthColor}>{ready}/{desired} ready</span>
              <span className="text-slate-700">·</span>
              <span className="text-slate-500">{_relTime(item.created_at)}</span>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors flex-shrink-0">
            <X size={15} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-800 flex-shrink-0 overflow-x-auto">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id as typeof tab)}
              className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors whitespace-nowrap flex-shrink-0 ${
                tab === t.id ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}>
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto min-h-0">

          {/* Overview */}
          {tab === 'overview' && (
            <div className="p-5 space-y-5">
              <div className="grid grid-cols-2 gap-2.5">
                {[
                  { label: 'Replicas',         value: `${ready}/${desired}` },
                  { label: 'Updated replicas',  value: String((item.raw as any)?.status?.updatedReplicas ?? '—') },
                  { label: 'Available replicas',value: String((item.raw as any)?.status?.availableReplicas ?? '—') },
                  { label: 'Namespace',         value: item.namespace || namespace },
                  { label: 'Age',               value: _relTime(item.created_at) },
                  { label: 'Selector',          value: Object.entries(item.selector ?? {}).map(([k,v]) => `${k}=${v}`).join(', ') || '—' },
                ].map(({ label, value }) => (
                  <div key={label} className="bg-slate-800/40 rounded-lg px-3 py-2.5">
                    <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-0.5">{label}</p>
                    <p className="text-xs font-mono text-slate-200 truncate" title={value}>{value}</p>
                  </div>
                ))}
              </div>
              {/* Conditions */}
              {((item.raw as any)?.status?.conditions ?? []).length > 0 && (
                <div>
                  <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Conditions</p>
                  <div className="space-y-1.5">
                    {((item.raw as any)?.status?.conditions ?? []).map((c: any, i: number) => (
                      <div key={i} className="flex items-start gap-2 text-xs">
                        <span className={`mt-0.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${c.status === 'True' ? 'bg-emerald-400' : 'bg-red-400'}`} />
                        <span className="text-slate-300 font-medium w-28 flex-shrink-0">{c.type}</span>
                        <span className="text-slate-500 leading-relaxed">{c.message || c.reason || '—'}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {/* Rollout strategy */}
              {isDeployment && (() => {
                const strat = (item.raw as any)?.spec?.strategy
                const stype = strat?.type ?? 'RollingUpdate'
                const ru = strat?.rollingUpdate
                const maxSurge = ru?.maxSurge ?? '25%'
                const maxUnavail = ru?.maxUnavailable ?? '25%'
                return (
                  <div>
                    <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Rollout Strategy</p>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-blue-900/40 text-blue-300 border border-blue-800/40">{stype}</span>
                      {stype === 'RollingUpdate' && (
                        <>
                          <span className="text-xs text-slate-400">maxSurge <span className="font-mono text-slate-200">{String(maxSurge)}</span></span>
                          <span className="text-xs text-slate-400">maxUnavailable <span className={`font-mono ${String(maxUnavail) === '0' || maxUnavail === '0%' ? 'text-emerald-400' : 'text-slate-200'}`}>{String(maxUnavail)}</span></span>
                          {(String(maxUnavail) === '0' || maxUnavail === '0%') && (
                            <span className="text-[10px] text-emerald-600 italic">zero-downtime</span>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                )
              })()}

              {/* Scheduling constraints */}
              {(() => {
                const podSpec = (item.raw as any)?.spec?.template?.spec ?? {}
                const nodeSelector: Record<string, string> = podSpec.nodeSelector ?? {}
                const tolerations: { key?: string; value?: string; effect?: string; operator?: string }[] = podSpec.tolerations ?? []
                const naRequired: { nodeSelectorTerms?: { matchExpressions?: { key: string; operator: string; values?: string[] }[] }[] }[] =
                  podSpec.affinity?.nodeAffinity?.requiredDuringSchedulingIgnoredDuringExecution?.nodeSelectorTerms ?? []
                const naPreferred: { preference?: { matchExpressions?: { key: string; operator: string; values?: string[] }[] }; weight?: number }[] =
                  podSpec.affinity?.nodeAffinity?.preferredDuringSchedulingIgnoredDuringExecution ?? []
                const paaRequired = podSpec.affinity?.podAntiAffinity?.requiredDuringSchedulingIgnoredDuringExecution ?? []
                const paaPreferred = podSpec.affinity?.podAntiAffinity?.preferredDuringSchedulingIgnoredDuringExecution ?? []
                const hasAny = Object.keys(nodeSelector).length > 0 || tolerations.length > 0 || naRequired.length > 0 || naPreferred.length > 0 || paaRequired.length > 0 || paaPreferred.length > 0
                return (
                  <div>
                    <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Scheduling Constraints</p>
                    {!hasAny ? (
                      <p className="text-[11px] text-slate-600 italic">No node selectors, tolerations, or affinity rules</p>
                    ) : (
                      <div className="space-y-2">
                        {Object.keys(nodeSelector).length > 0 && (
                          <div>
                            <p className="text-[10px] text-slate-600 mb-1">nodeSelector</p>
                            <div className="flex flex-wrap gap-1">
                              {Object.entries(nodeSelector).map(([k, v]) => (
                                <span key={k} className="px-1.5 py-0.5 rounded text-[10px] bg-blue-900/30 text-blue-300 font-mono">{k}={v}</span>
                              ))}
                            </div>
                          </div>
                        )}
                        {tolerations.length > 0 && (
                          <div>
                            <p className="text-[10px] text-slate-600 mb-1">Tolerations ({tolerations.length})</p>
                            <div className="space-y-0.5">
                              {tolerations.map((t, i) => (
                                <div key={i} className="flex items-center gap-1 text-[10px] font-mono">
                                  <span className="text-slate-400">{t.key ?? '*'}{t.value != null ? `=${t.value}` : ''}</span>
                                  {t.effect && <span className="px-1 rounded bg-slate-800 text-slate-500">{t.effect}</span>}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                        {(naRequired.length > 0 || naPreferred.length > 0) && (
                          <div>
                            <p className="text-[10px] text-slate-600 mb-1">Node Affinity</p>
                            {naRequired.map((term, i) => (
                              <div key={i} className="text-[10px] text-slate-400">
                                <span className="px-1 rounded bg-slate-800 text-emerald-500 mr-1">required</span>
                                {(term.nodeSelectorTerms ?? []).flatMap(t => t.matchExpressions ?? []).map((e, j) => (
                                  <span key={j} className="font-mono">{e.key} {e.operator} {(e.values ?? []).join(',')}</span>
                                ))}
                              </div>
                            ))}
                            {naPreferred.map((p, i) => (
                              <div key={i} className="text-[10px] text-slate-400">
                                <span className="px-1 rounded bg-slate-800 text-blue-400 mr-1">preferred w={p.weight}</span>
                                {(p.preference?.matchExpressions ?? []).map((e, j) => (
                                  <span key={j} className="font-mono">{e.key} {e.operator} {(e.values ?? []).join(',')}</span>
                                ))}
                              </div>
                            ))}
                          </div>
                        )}
                        {(paaRequired.length > 0 || paaPreferred.length > 0) && (
                          <div>
                            <p className="text-[10px] text-slate-600 mb-1">Pod Anti-Affinity</p>
                            {paaRequired.map((r: any, i: number) => (
                              <div key={i} className="text-[10px] text-slate-400">
                                <span className="px-1 rounded bg-red-900/40 text-red-400 mr-1">required</span>
                                <span className="font-mono">topology: {r.topologyKey ?? '—'}</span>
                              </div>
                            ))}
                            {paaPreferred.map((p: any, i: number) => (
                              <div key={i} className="text-[10px] text-slate-400">
                                <span className="px-1 rounded bg-slate-800 text-slate-400 mr-1">preferred w={p.weight}</span>
                                <span className="font-mono">topology: {p.podAffinityTerm?.topologyKey ?? '—'}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })()}

              {/* Containers summary */}
              <div>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Containers ({rawContainers.length})</p>
                <div className="space-y-2">
                  {rawContainers.map(c => {
                    // Aggregate per-container metrics across all matching pods
                    let cCpu = 0, cMem = 0, podCount = 0
                    const ns = item.namespace; const prefix = `${ns}/${item.name}-`
                    if (podMetrics) {
                      for (const [k, v] of Object.entries(podMetrics)) {
                        if (k.startsWith(prefix) || k === `${ns}/${item.name}`) {
                          const match = v.containers.find(x => x.name === c.name)
                          if (match) { cCpu += match.cpu_cores; cMem += match.mem_bytes; podCount++ }
                        }
                      }
                    }
                    const limCpuCores = c.resources?.limits?.cpu ? parseFloat(c.resources.limits.cpu.replace('m', '')) / (c.resources.limits.cpu.endsWith('m') ? 1000 : 1) * (podCount || 1) : 0
                    const limMemBytes = c.resources?.limits?.memory ? (() => {
                      const m = c.resources!.limits!.memory!
                      if (m.endsWith('Mi')) return parseInt(m) * 1048576 * (podCount || 1)
                      if (m.endsWith('Gi')) return parseInt(m) * 1073741824 * (podCount || 1)
                      if (m.endsWith('Ki')) return parseInt(m) * 1024 * (podCount || 1)
                      return parseInt(m) * (podCount || 1)
                    })() : 0
                    const cpuPct = limCpuCores > 0 ? Math.min(100, Math.round((cCpu / limCpuCores) * 100)) : null
                    const memPct = limMemBytes > 0 ? Math.min(100, Math.round((cMem / limMemBytes) * 100)) : null
                    const hasMetrics = podCount > 0
                    return (
                    <div key={c.name} className="bg-slate-800/40 rounded-lg px-3 py-2.5">
                      <p className="font-mono text-sm text-white font-medium mb-0.5">{c.name}</p>
                      <p className={`text-[11px] font-mono truncate ${c.image?.endsWith(':latest') ? 'text-yellow-400' : 'text-slate-500'}`}>{c.image}</p>
                      {c.image?.endsWith(':latest') && (
                        <p className="text-[10px] text-yellow-600 mt-0.5">⚠ Using :latest tag — pin to a specific version for reproducible deployments</p>
                      )}
                      {hasMetrics && (
                        <div className="mt-2 grid grid-cols-2 gap-2">
                          {[
                            { label: 'CPU', val: Math.round(cCpu * 1000), unit: 'm', pct: cpuPct },
                            { label: 'Mem', val: Math.round(cMem / 1048576), unit: 'Mi', pct: memPct },
                          ].map(({ label, val, unit, pct }) => (
                            <div key={label}>
                              <div className="flex justify-between text-[10px] mb-0.5">
                                <span className="text-slate-500">{label}</span>
                                <span className={pct !== null && pct > 80 ? 'text-red-400' : pct !== null && pct > 60 ? 'text-amber-400' : 'text-emerald-400'}>
                                  {pct !== null ? `${pct}%` : `${val}${unit}`}
                                </span>
                              </div>
                              <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
                                <div className={`h-full rounded-full transition-all ${pct !== null && pct > 80 ? 'bg-red-500' : pct !== null && pct > 60 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                                  style={{ width: `${pct ?? Math.min(100, val / 10)}%` }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}

          {/* Edit — image + resources per container */}
          {tab === 'edit' && (
            <div className="p-5 space-y-5">
              <p className="text-xs text-slate-500">Edit image and resource limits for each container. Changes apply with a confirm step.</p>
              {rawContainers.map(c => (
                <div key={c.name} className="bg-slate-800/30 rounded-xl border border-slate-700/40 p-4 space-y-4">
                  <p className="text-sm font-mono text-white font-semibold">{c.name}</p>
                  {/* Image */}
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Image</label>
                    <input
                      value={images[c.name] ?? c.image}
                      onChange={e => setImages(prev => ({ ...prev, [c.name]: e.target.value }))}
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 outline-none focus:border-blue-500"
                    />
                    {(images[c.name] || c.image).endsWith(':latest') && (
                      <p className="text-[10px] text-yellow-500 mt-1">⚠ :latest is mutable — pin to a digest or version tag</p>
                    )}
                  </div>
                  {/* Resources */}
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-2">Resources</label>
                    <div className="grid grid-cols-2 gap-3">
                      {([
                        { key: 'reqCpu',  label: 'CPU Request',  placeholder: '100m' },
                        { key: 'reqMem',  label: 'Mem Request',  placeholder: '128Mi' },
                        { key: 'limCpu',  label: 'CPU Limit',    placeholder: '500m' },
                        { key: 'limMem',  label: 'Mem Limit',    placeholder: '512Mi' },
                      ] as const).map(({ key, label, placeholder }) => (
                        <div key={key}>
                          <label className="text-[10px] text-slate-600 block mb-1">{label}</label>
                          <input
                            value={resources[c.name]?.[key] ?? ''}
                            onChange={e => setResources(prev => ({ ...prev, [c.name]: { ...prev[c.name], [key]: e.target.value } }))}
                            placeholder={placeholder}
                            className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600"
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
              <button onClick={submitImageAndResources}
                className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors font-medium">
                Apply Changes
              </button>
            </div>
          )}

          {/* Env Vars */}
          {tab === 'env' && (
            <div className="p-5 space-y-4">
              {rawContainers.length > 1 && (
                <div className="flex gap-1 flex-wrap">
                  {rawContainers.map(c => (
                    <button key={c.name} onClick={() => setSelContainer(c.name)}
                      className={`px-2.5 py-1 rounded text-[11px] font-mono transition-colors ${
                        selContainer === c.name
                          ? 'bg-blue-600/30 text-blue-300 border border-blue-500/40'
                          : 'bg-slate-800 text-slate-400 border border-slate-700/50 hover:bg-slate-700'
                      }`}>
                      {c.name}
                    </button>
                  ))}
                </div>
              )}
              <div className="space-y-1.5">
                {envRows.map((row, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <input
                      value={row.name}
                      onChange={e => setEnvRows(prev => prev.map((r, j) => j === i ? { ...r, name: e.target.value } : r))}
                      placeholder="KEY"
                      className="w-32 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-[11px] font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600"
                    />
                    <span className="text-slate-600 text-xs">=</span>
                    {row.valueFrom ? (
                      <span className="flex-1 text-[11px] font-mono text-violet-400 bg-violet-900/20 border border-violet-700/30 rounded px-2 py-1.5 truncate">
                        {JSON.stringify(row.valueFrom)}
                      </span>
                    ) : (
                      <input
                        value={row.value}
                        onChange={e => setEnvRows(prev => prev.map((r, j) => j === i ? { ...r, value: e.target.value } : r))}
                        placeholder="value"
                        className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-[11px] font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600"
                      />
                    )}
                    <button onClick={() => setEnvRows(prev => prev.filter((_, j) => j !== i))}
                      className="p-1 text-slate-600 hover:text-red-400 transition-colors flex-shrink-0">
                      <Minus size={12} />
                    </button>
                  </div>
                ))}
              </div>
              {/* Add new */}
              <div className="flex items-center gap-2 pt-1 border-t border-slate-800">
                <input value={addEnvKey} onChange={e => setAddEnvKey(e.target.value)}
                  placeholder="KEY"
                  className="w-32 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-[11px] font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                <span className="text-slate-600 text-xs">=</span>
                <input value={addEnvVal} onChange={e => setAddEnvVal(e.target.value)}
                  placeholder="value"
                  className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-[11px] font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                <button onClick={() => {
                  if (!addEnvKey.trim()) return
                  setEnvRows(prev => [...prev, { name: addEnvKey.trim(), value: addEnvVal }])
                  setAddEnvKey(''); setAddEnvVal('')
                }} className="p-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors flex-shrink-0">
                  <Plus size={12} />
                </button>
              </div>
              <button onClick={submitEnvVars}
                className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors font-medium">
                Apply Env Changes
              </button>
            </div>
          )}

          {/* History / Rollback */}
          {tab === 'history' && (
            <div className="p-4">
              {histLoading ? <SkeletonRows rows={4} /> : revisions.length === 0 ? (
                <EmptyState icon={RotateCcw} title="No history" hint="No rollout revisions found" />
              ) : (
                <div className="space-y-2">
                  {revisions.map((rev, i) => (
                    <div key={rev.name} className={`rounded-lg px-3 py-3 border text-xs ${i === 0 ? 'border-blue-500/30 bg-blue-500/5' : 'border-slate-700/40 bg-slate-800/40'}`}>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-bold text-slate-200">Revision {rev.revision}</span>
                        {i === 0 && <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-600/30 text-blue-400 border border-blue-500/30">current</span>}
                        <span className="ml-auto text-slate-500">{_relTime(rev.created_at)}</span>
                      </div>
                      {rev.change_cause && <p className="text-slate-400 mb-1">{rev.change_cause}</p>}
                      {rev.images.map(img => (
                        <p key={img} className={`font-mono text-[10px] truncate ${img.endsWith(':latest') ? 'text-yellow-400' : 'text-slate-500'}`}>{img}</p>
                      ))}
                      {i > 0 && (
                        <button onClick={() => doRollback(rev.name)}
                          className="mt-2 flex items-center gap-1 px-2.5 py-1 bg-orange-600/20 hover:bg-orange-600/30 text-orange-400 border border-orange-500/30 rounded text-[10px] transition-colors">
                          <RotateCcw size={10} /> Rollback to this
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* AI Diagnose */}
          {tab === 'diagnose' && (
            <div className="p-5 space-y-4">
              <div className="flex items-center gap-2 flex-wrap">
                <div className="flex rounded-lg border border-slate-700 overflow-hidden text-[11px]">
                  <button onClick={() => { if (teachMode) { setTeachMode(false); setDiagnoseText('') } }}
                    className={`px-3 py-1.5 transition-colors ${!teachMode ? 'bg-blue-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>
                    Diagnose
                  </button>
                  <button onClick={() => { if (!teachMode) { setTeachMode(true); setDiagnoseText('') } }}
                    className={`px-3 py-1.5 flex items-center gap-1 transition-colors ${teachMode ? 'bg-violet-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>
                    <BookOpen size={10} /> Teach me
                  </button>
                </div>
                <button onClick={runDiagnose} disabled={diagnoseLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] rounded-lg border border-slate-700 disabled:opacity-50 transition-colors">
                  <RotateCcw size={10} className={diagnoseLoading ? 'animate-spin' : ''} />
                  {diagnoseLoading ? 'Analyzing…' : 'Re-run'}
                </button>
              </div>
              {diagnoseLoading && !diagnoseText && (
                <div className="flex items-center gap-2 text-slate-400 py-6 justify-center">
                  <Loader2 size={16} className="animate-spin text-blue-400" />
                  <span className="text-sm">{teachMode ? 'Preparing explanation…' : 'Analyzing workload…'}</span>
                </div>
              )}
              {!diagnoseLoading && !diagnoseText && (
                <button onClick={runDiagnose}
                  className="flex items-center gap-2 px-4 py-3 rounded-xl border border-blue-500/30 bg-blue-500/5 text-blue-400 hover:bg-blue-500/10 w-full justify-center text-sm">
                  <BotMessageSquare size={14} /> Start AI Analysis
                </button>
              )}
              {diagnoseText && (
                <div className="text-sm text-slate-300 leading-relaxed
                  [&_h2]:text-slate-100 [&_h2]:font-semibold [&_h2]:text-sm [&_h2]:mt-4 [&_h2]:mb-1.5
                  [&_h3]:text-slate-200 [&_h3]:font-medium [&_h3]:text-sm [&_h3]:mt-3 [&_h3]:mb-1
                  [&_strong]:text-slate-200
                  [&_code]:text-blue-300 [&_code]:bg-slate-800 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs [&_code]:font-mono
                  [&_pre]:bg-slate-800/70 [&_pre]:border [&_pre]:border-slate-700/50 [&_pre]:rounded-lg [&_pre]:p-3 [&_pre]:overflow-x-auto [&_pre]:my-2
                  [&_pre_code]:bg-transparent [&_pre_code]:p-0
                  [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:space-y-1 [&_ul]:my-2
                  [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:space-y-1 [&_ol]:my-2
                  [&_li]:text-slate-300 [&_p]:my-1.5">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{diagnoseText}</ReactMarkdown>
                  {diagnoseLoading && <span className="inline-block w-1.5 h-4 bg-blue-400 animate-pulse ml-0.5 align-middle rounded-sm" />}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  )
}

// ── Workload Diff Modal ───────────────────────────────────────────────────────

function computeUnifiedDiff(aText: string, bText: string): { line: string; type: 'add' | 'remove' | 'context' }[] {
  const aLines = aText.split('\n')
  const bLines = bText.split('\n')
  const result: { line: string; type: 'add' | 'remove' | 'context' }[] = []
  let ai = 0, bi = 0
  while (ai < aLines.length || bi < bLines.length) {
    if (ai < aLines.length && bi < bLines.length && aLines[ai] === bLines[bi]) {
      result.push({ line: aLines[ai], type: 'context' })
      ai++; bi++
    } else {
      if (ai < aLines.length) result.push({ line: aLines[ai++], type: 'remove' })
      if (bi < bLines.length) result.push({ line: bLines[bi++], type: 'add' })
    }
  }
  return result
}

function WorkloadDiffModal({ clusterId, kind, name, namespace, onClose }: {
  clusterId: string; kind: string; name: string; namespace: string; onClose: () => void
}) {
  const [diff, setDiff] = useState<{ has_annotation: boolean; current: string; last_applied: string | null } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    vksGet<typeof diff>(`${clusterId}/workloads/${kind}/${name}/diff?namespace=${encodeURIComponent(namespace)}`)
      .then(d => { setDiff(d); setError('') })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [clusterId, kind, name, namespace])

  const lines = diff?.has_annotation && diff.last_applied
    ? computeUnifiedDiff(diff.last_applied, diff.current)
    : null

  const hasChanges = lines ? lines.some(l => l.type !== 'context') : false

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl flex flex-col" style={{ maxHeight: '80vh' }}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700 shrink-0">
          <div className="flex items-center gap-2">
            <GitCompare size={15} className="text-blue-400" />
            <span className="text-sm font-semibold text-white">{name}</span>
            <span className="text-xs text-slate-500 font-mono">{kind} / {namespace}</span>
          </div>
          <button onClick={onClose} className="p-1 text-slate-400 hover:text-white rounded"><X size={14} /></button>
        </div>

        <div className="flex-1 min-h-0 overflow-auto p-3">
          {loading && <div className="flex justify-center py-12"><Loader2 size={20} className="animate-spin text-slate-500" /></div>}
          {error && <div className="text-red-400 text-sm p-3 bg-red-900/20 rounded-lg">{error}</div>}
          {!loading && diff && !diff.has_annotation && (
            <div className="text-center py-10 text-slate-500 text-sm">
              <GitCompare size={32} className="mx-auto mb-3 text-slate-600" />
              No last-applied annotation found.<br />
              <span className="text-xs text-slate-600">Resource was likely created imperatively (not via kubectl apply).</span>
            </div>
          )}
          {!loading && diff?.has_annotation && lines && !hasChanges && (
            <div className="text-center py-10 text-emerald-500 text-sm">
              <CheckCircle size={28} className="mx-auto mb-3" />
              No diff — live spec matches last-applied configuration.
            </div>
          )}
          {!loading && diff?.has_annotation && lines && hasChanges && (
            <div className="font-mono text-[11px] leading-5">
              {lines.map((l, i) => (
                <div key={i} className={`px-3 whitespace-pre-wrap break-all ${
                  l.type === 'add' ? 'bg-emerald-950/60 text-emerald-300' :
                  l.type === 'remove' ? 'bg-red-950/60 text-red-300' :
                  'text-slate-500'
                }`}>
                  {l.type === 'add' ? '+ ' : l.type === 'remove' ? '- ' : '  '}{l.line}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="px-4 py-2 border-t border-slate-700 shrink-0 flex items-center justify-between">
          <span className="text-xs text-slate-600">
            {lines && hasChanges ? (
              <><span className="text-emerald-400">+{lines.filter(l => l.type === 'add').length}</span>{' / '}
              <span className="text-red-400">-{lines.filter(l => l.type === 'remove').length}</span></>
            ) : null}
          </span>
          <button onClick={onClose} className="px-3 py-1 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg">Close</button>
        </div>
      </div>
    </div>
  )
}

// ── Workloads Section ─────────────────────────────────────────────────────────

const WORKLOAD_KINDS = ['deployments', 'statefulsets', 'daemonsets', 'jobs', 'cronjobs'] as const
type WorkloadKind = typeof WORKLOAD_KINDS[number]

function WorkloadsSection({ clusterId, namespace, onViewPods }: { clusterId: string; namespace: string; onViewPods?: (name: string) => void }) {
  const [kind, setKind] = useState<WorkloadKind>('deployments')
  const [items, setItems] = useState<WorkloadItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [scaleTarget, setScaleTarget] = useState<{ name: string; current: number } | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [logState, setLogState] = useState<LogState | null>(null)
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [filterText, setFilterText] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'unhealthy' | 'scaled-down'>('all')
  const [pdbMap, setPdbMap] = useState<Map<string, string>>(new Map())
  const [podMetrics, setPodMetrics] = useState<Record<string, { cpu_cores: number; mem_bytes: number; cpu_limit_cores: number; mem_limit_bytes: number; containers: { name: string; cpu_cores: number; mem_bytes: number }[] }>>({})
  const [historyTarget, setHistoryTarget] = useState<{ name: string; namespace: string } | null>(null)
  const [annotationsTarget, setAnnotationsTarget] = useState<WorkloadItem | null>(null)
  const [detailWorkload, setDetailWorkload] = useState<WorkloadItem | null>(null)
  const [diffTarget, setDiffTarget] = useState<WorkloadItem | null>(null)
  const { sortKey, sortDir, onSort } = useSort()
  const toast = useToast()

  // Pre-compute per-workload metrics once when items or podMetrics change (avoids O(n²) on every state change)
  const workloadMetrics = useMemo(() => {
    const map = new Map<string, { cpuMs: number; memMi: number; cpuPct: number | null; memPct: number | null }>()
    if (Object.keys(podMetrics).length === 0) return map
    for (const item of items) {
      const ns = item.namespace; const prefix = `${ns}/${item.name}-`
      let totalCpu = 0, totalMem = 0, totalCpuLim = 0, totalMemLim = 0, podCount = 0
      for (const [k, v] of Object.entries(podMetrics)) {
        if (k.startsWith(prefix) || k === `${ns}/${item.name}`) {
          totalCpu += v.cpu_cores; totalMem += v.mem_bytes
          totalCpuLim += v.cpu_limit_cores; totalMemLim += v.mem_limit_bytes
          podCount++
        }
      }
      if (podCount > 0) {
        const cpuPct = totalCpuLim > 0 ? Math.round((totalCpu / totalCpuLim) * 100) : null
        const memPct = totalMemLim > 0 ? Math.round((totalMem / totalMemLim) * 100) : null
        map.set(`${ns}/${item.name}`, { cpuMs: Math.round(totalCpu * 1000), memMi: Math.round(totalMem / 1048576), cpuPct, memPct })
      }
    }
    return map
  }, [items, podMetrics])

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ items: WorkloadItem[] }>(`${clusterId}/workloads?namespace=${namespace}&kind=${kind}`)
      .then(d => { setItems(d.items); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace, kind])

  useEffect(() => {
    if (kind !== 'deployments' || items.length === 0) { setPdbMap(new Map()); return }
    vksGet<{ pdbs: PDBInfo[] }>(`${clusterId}/pdbs${namespace ? `?namespace=${namespace}` : ''}`)
      .then(d => {
        const m = new Map<string, string>()
        for (const pdb of d.pdbs) {
          for (const item of items) {
            if (item.namespace !== pdb.namespace) continue
            const sel = pdb.selector
            if (Object.keys(sel).length > 0 && Object.entries(sel).every(([k, v]) => item.selector[k] === v)) {
              m.set(`${item.namespace}/${item.name}`, pdb.name)
            }
          }
        }
        setPdbMap(m)
      })
      .catch(() => {})
  }, [clusterId, namespace, kind, items])

  useEffect(() => {
    vksGet<{ metrics: Record<string, { cpu_cores: number; mem_bytes: number; cpu_limit_cores: number; mem_limit_bytes: number; containers: { name: string; cpu_cores: number; mem_bytes: number }[] }> }>(
      `${clusterId}/pod-metrics${namespace ? `?namespace=${namespace}` : ''}`
    ).then(d => setPodMetrics(d.metrics)).catch(() => {})
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  async function handleScale(name: string, replicas: number) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/deployments/${name}/scale?namespace=${namespace}`, { replicas }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/deployments/${name}/scale?namespace=${namespace}&token=${token}`, { replicas })
          toast.success(`Scaled ${name} to ${replicas}`)
          load()
        }})
      } else {
        toast.success(`Scaled ${name}`); load()
      }
    } catch (e) { toast.error(`Scale failed: ${e}`) }
  }

  async function handleRestart(name: string) {
    const kindPath = kind === 'daemonsets' ? 'daemonsets' : kind === 'statefulsets' ? 'statefulsets' : 'deployments'
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/${kindPath}/${name}/restart?namespace=${namespace}`, {}
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/${kindPath}/${name}/restart?namespace=${namespace}&token=${token}`, {})
          toast.success(`Restarted ${name}`); load()
        }})
      } else {
        toast.success(`Restarted ${name}`); load()
      }
    } catch (e) { toast.error(`Restart failed: ${e}`) }
  }

  async function handleDelete(name: string) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/workloads/${kind}/${name}/delete?namespace=${namespace}`, {}
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/workloads/${kind}/${name}/delete?namespace=${namespace}&token=${token}`, {})
          toast.success(`Deleted ${name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  async function handleTrigger(name: string) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean; job_name?: string }>(
        `${clusterId}/cronjobs/${name}/trigger?namespace=${namespace}`, {}
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          const r = await vksPost<{ job_name: string }>(`${clusterId}/cronjobs/${name}/trigger?namespace=${namespace}&token=${token}`, {})
          toast.success(`Triggered: ${r.job_name}`); load()
        }})
      }
    } catch (e) { toast.error(`Trigger failed: ${e}`) }
  }

  async function handleSuspend(name: string, currentlySuspended: boolean) {
    const next = !currentlySuspended
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/cronjobs/${name}/suspend?namespace=${namespace}&suspend=${next}`, {}
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/cronjobs/${name}/suspend?namespace=${namespace}&suspend=${next}&token=${token}`, {})
          toast.success(`CronJob ${name} ${next ? 'suspended' : 'resumed'}`); load()
        }})
      }
    } catch (e) { toast.error(`Suspend toggle failed: ${e}`) }
  }

  const statusBadge = (item: WorkloadItem) => {
    if (item.job_status != null) {
      const color = item.job_status === 'Complete' ? 'bg-emerald-900/40 text-emerald-400' : item.job_status === 'Failed' ? 'bg-red-900/40 text-red-400' : item.job_status === 'Running' ? 'bg-blue-900/40 text-blue-400' : 'bg-slate-800 text-slate-400'
      const label = item.job_status === 'Complete' ? `✓ ${item.succeeded ?? 0}/${item.completions ?? '?'}` : item.job_status === 'Failed' ? `✗ Failed` : `${item.active ?? 0} active`
      return <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${color}`}>{label}</span>
    }
    if (item.schedule != null) {
      const color = item.suspend ? 'bg-yellow-900/40 text-yellow-400' : 'bg-blue-900/40 text-blue-400'
      return <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${color}`}>{item.schedule}</span>
    }
    const ready = item.ready_replicas ?? 0
    const desired = item.replicas ?? 0
    if (desired === 0) return <span className="text-xs text-slate-500">0/0</span>
    return ready === desired
      ? <span className="text-xs text-emerald-400">{ready}/{desired}</span>
      : <span className="text-xs text-yellow-400">{ready}/{desired}</span>
  }

  return (
    <div className="space-y-4">
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {annotationsTarget && (
        <AnnotationsEditorModal
          clusterId={clusterId}
          workload={annotationsTarget}
          onClose={() => setAnnotationsTarget(null)}
          onSaved={() => { setAnnotationsTarget(null); load() }}
        />
      )}
      {scaleTarget && <ScaleDialog name={scaleTarget.name} current={scaleTarget.current} onConfirm={n => handleScale(scaleTarget.name, n)} onClose={() => setScaleTarget(null)} />}
      {logState && <LogViewer state={logState} onClose={() => setLogState(null)} />}
      {showCreate && <CreateWorkloadModal clusterId={clusterId} namespace={namespace} onClose={() => { setShowCreate(false); load() }} />}
      {showImport && <ImportYamlModal clusterId={clusterId} onClose={() => { setShowImport(false); load() }} />}
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {historyTarget && (
        <RolloutHistoryModal
          clusterId={clusterId}
          deploymentName={historyTarget.name}
          namespace={historyTarget.namespace || namespace}
          onClose={() => setHistoryTarget(null)}
        />
      )}
      {detailWorkload && (
        <WorkloadDetailPanel
          item={detailWorkload}
          clusterId={clusterId}
          namespace={detailWorkload.namespace || namespace}
          onClose={() => setDetailWorkload(null)}
          onRefresh={() => { setDetailWorkload(null); load() }}
          podMetrics={podMetrics}
        />
      )}
      {diffTarget && (
        <WorkloadDiffModal
          clusterId={clusterId}
          kind={diffTarget.kind || kind}
          name={diffTarget.name}
          namespace={diffTarget.namespace || namespace}
          onClose={() => setDiffTarget(null)}
        />
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex bg-slate-800 rounded-lg p-0.5">
          {WORKLOAD_KINDS.map(k => (
            <button key={k} onClick={() => { setKind(k); setFilterText(''); setStatusFilter('all') }}
              className={`px-3 py-1.5 text-xs rounded-md capitalize transition-colors ${kind === k ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}>
              {k}
            </button>
          ))}
        </div>
        {['deployments', 'statefulsets', 'daemonsets'].includes(kind) && (
          <div className="flex gap-1">
            {([
              { key: 'all', label: 'All' },
              { key: 'unhealthy', label: '⚠ Degraded' },
              { key: 'scaled-down', label: '◻ Scaled-Down' },
            ] as const).map(f => (
              <button key={f.key} onClick={() => setStatusFilter(f.key)}
                className={`px-2.5 py-1 rounded-md text-[11px] transition-colors ${statusFilter === f.key
                  ? f.key === 'unhealthy' ? 'bg-amber-900/60 text-amber-300 border border-amber-700/50'
                  : f.key === 'scaled-down' ? 'bg-slate-700 text-slate-300 border border-slate-600'
                  : 'bg-slate-700 text-slate-300'
                  : 'text-slate-500 hover:text-slate-300'
                }`}>{f.label}</button>
            ))}
          </div>
        )}
        <input
          value={filterText}
          onChange={e => setFilterText(e.target.value)}
          placeholder="Filter by name…"
          className="flex-1 min-w-[140px] bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-300 placeholder-slate-500 outline-none focus:border-blue-500"
        />
        <div className="flex gap-2">
          <button onClick={() => setShowImport(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700">
            <Upload size={12} /> Import YAML
          </button>
          <button onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg">
            <Plus size={12} /> Create
          </button>
          <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {(() => {
        const pressured = [...workloadMetrics.entries()].filter(([, m]) => (m.memPct ?? 0) >= 80)
        if (pressured.length === 0) return null
        return (
          <div className="flex items-start gap-3 px-4 py-3 rounded-xl border border-red-700/40 bg-red-900/20 mb-2">
            <AlertTriangle size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
            <div className="min-w-0">
              <span className="text-xs font-medium text-red-300">Memory pressure: {pressured.length} workload{pressured.length > 1 ? 's' : ''} above 80% memory limit</span>
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {pressured.map(([key, m]) => (
                  <span key={key} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-900/40 text-red-300 border border-red-700/40">
                    {key.split('/')[1]} {m.memPct}%
                  </span>
                ))}
              </div>
            </div>
          </div>
        )
      })()}
      {loading ? <SkeletonRows rows={5} /> : !loadErr && items.length === 0 ? (
        <EmptyState icon={Layers} title={`No ${kind}`}
          hint={`No ${kind} in ${namespace || 'all namespaces'}`}
          action={<button onClick={() => setShowCreate(true)} className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg">Create</button>} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <SortableHeader label="Name" col="name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                {!namespace && <SortableHeader label="Namespace" col="namespace" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />}
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">{kind === 'jobs' ? 'Status' : kind === 'cronjobs' ? 'Schedule' : 'Ready'}</th>
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">{kind === 'cronjobs' ? 'Last Run' : 'Image'}</th>
                {workloadMetrics.size > 0 && <>
                  <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">CPU</th>
                  <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Mem</th>
                </>}
                <SortableHeader label="Age" col="created_at" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {useSortedFiltered(
                items.filter(item => {
                  if (statusFilter === 'unhealthy') return (item.ready_replicas ?? 0) < (item.replicas ?? 0) && (item.replicas ?? 0) > 0
                  if (statusFilter === 'scaled-down') return (item.replicas ?? 0) === 0
                  return true
                }),
                filterText, ['name', 'namespace'], sortKey, sortDir
              ).map(item => (
                <tr key={`${item.namespace}/${item.name}`} className="group border-b border-slate-800/40 hover:bg-slate-800/20">
                  <td className="px-4 py-3 font-mono text-white text-sm">
                    <span className="inline-flex items-center gap-1">
                      <button onClick={() => setDetailWorkload(item)}
                        className="hover:text-blue-400 transition-colors text-left">{item.name}</button>
                      <CopyButton text={item.name} />
                      {['deployments', 'statefulsets', 'daemonsets'].includes(kind) && (() => {
                        const labels = item.labels ?? {}
                        const hasApp = 'app' in labels || 'app.kubernetes.io/name' in labels
                        const hasVer = 'version' in labels || 'app.kubernetes.io/version' in labels
                        if (!hasApp || !hasVer) {
                          const missing = [!hasApp && 'app', !hasVer && 'version'].filter(Boolean).join(', ')
                          return <span className="text-[9px] px-1 py-0.5 rounded bg-slate-700/60 text-slate-500 border border-slate-600/40 cursor-default" title={`Missing labels: ${missing}`}>no {missing}</span>
                        }
                        return null
                      })()}
                      {kind === 'deployments' && (() => {
                        const pdbName = pdbMap.get(`${item.namespace}/${item.name}`)
                        return pdbName
                          ? <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-900/40 text-emerald-500 border border-emerald-800/40 cursor-default" title={`PDB: ${pdbName}`}>✓ PDB</span>
                          : null
                      })()}
                    </span>
                  </td>
                  {!namespace && <td className="px-4 py-3 text-slate-400 text-xs">{item.namespace}</td>}
                  <td className="px-4 py-3">{statusBadge(item)}</td>
                  <td className="px-4 py-3 font-mono text-xs max-w-xs truncate">
                    {kind === 'cronjobs' ? (
                      <span className="text-slate-400 flex flex-col gap-0.5">
                        {item.active_jobs && item.active_jobs > 0 ? (
                          <span className="text-blue-400 flex items-center gap-1"><Loader2 size={10} className="animate-spin" />running</span>
                        ) : item.last_schedule_time ? (
                          <span className={item.last_successful_time && item.last_successful_time >= item.last_schedule_time ? 'text-emerald-400' : 'text-yellow-400'}>
                            {item.last_successful_time && item.last_successful_time >= item.last_schedule_time ? '✓' : '!'} {_relTime(item.last_schedule_time)} ago
                          </span>
                        ) : <span className="text-slate-500 italic">never</span>}
                        {item.schedule && !item.suspend && <span className="text-slate-500 text-[10px]">{_cronNextRun(item.schedule)}</span>}
                      </span>
                    ) : (
                      <>
                        <span className={item.images[0]?.endsWith(':latest') ? 'text-yellow-400' : 'text-slate-400'}>
                          {item.images[0] || '—'}
                        </span>
                        {item.images[0]?.endsWith(':latest') && (
                          <span className="ml-1 px-1 py-0.5 rounded text-[9px] bg-yellow-900/30 text-yellow-500 border border-yellow-700/30">:latest</span>
                        )}
                      </>
                    )}
                  </td>
                  {workloadMetrics.size > 0 && (() => {
                    const m = workloadMetrics.get(`${item.namespace}/${item.name}`)
                    if (!m) return <><td /><td /></>
                    const cpuColor = (m.cpuPct ?? m.cpuMs / 10) > 80 ? 'text-red-400' : (m.cpuPct ?? m.cpuMs / 10) > 60 ? 'text-amber-400' : 'text-emerald-400'
                    const memColor = (m.memPct ?? 0) > 80 ? 'text-red-400' : (m.memPct ?? 0) > 60 ? 'text-amber-400' : 'text-emerald-400'
                    return <>
                      <td className={`px-4 py-3 text-xs font-mono ${cpuColor}`}>{m.cpuPct !== null ? `${m.cpuPct}%` : `${m.cpuMs}m`}</td>
                      <td className={`px-4 py-3 text-xs font-mono ${memColor}`}>{m.memPct !== null ? `${m.memPct}%` : `${m.memMi}Mi`}</td>
                    </>
                  })()}
                  <td className="px-4 py-3 text-xs text-slate-500">{_relTime(item.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      {kind === 'deployments' && (
                        <ActionBtn icon={ArrowUpDown} title="Scale" onClick={() => setScaleTarget({ name: item.name, current: item.replicas ?? 0 })} />
                      )}
                      {['deployments', 'statefulsets', 'daemonsets'].includes(kind) && (
                        <ActionBtn icon={RotateCcw} title="Restart" onClick={() => handleRestart(item.name)} />
                      )}
                      {kind === 'cronjobs' && (
                        <>
                          <ActionBtn icon={Play} title="Trigger now" onClick={() => handleTrigger(item.name)} />
                          <ActionBtn
                            icon={(item.raw as any)?.spec?.suspend ? Play : Square}
                            title={(item.raw as any)?.spec?.suspend ? 'Resume CronJob' : 'Suspend CronJob'}
                            onClick={() => handleSuspend(item.name, !!(item.raw as any)?.spec?.suspend)}
                          />
                        </>
                      )}
                      {kind === 'deployments' && (
                        <ActionBtn icon={Radio} title="Rollout History" onClick={() => setHistoryTarget({ name: item.name, namespace: item.namespace || namespace })} />
                      )}
                      {['deployments', 'statefulsets', 'daemonsets'].includes(kind) && onViewPods && (
                        <ActionBtn icon={Eye} title="View Pods" onClick={() => onViewPods(item.name)} />
                      )}
                      <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: item.kind || kind, name: item.name, namespace: item.namespace || namespace })} />
                      {['deployments', 'statefulsets', 'daemonsets'].includes(kind) && (
                        <ActionBtn icon={Info} title="Annotations" onClick={() => setAnnotationsTarget(item)} />
                      )}
                      {['deployments', 'statefulsets', 'daemonsets'].includes(kind) && (
                        <ActionBtn icon={GitCompare} title="YAML Diff (vs last-applied)" onClick={() => setDiffTarget(item)} />
                      )}
                      {['deployments', 'statefulsets', 'daemonsets'].includes(kind) && (
                        <ActionBtn icon={BotMessageSquare} title="AI Diagnose / Details" onClick={() => setDetailWorkload(item)} />
                      )}
                      <ActionBtn icon={Trash2} title="Delete" danger onClick={() => handleDelete(item.name)} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Pod Detail Panel ──────────────────────────────────────────────────────────

function PodDetailPanel({ pod, clusterId, onClose }: {
  pod: PodInfo
  clusterId: string
  onClose: () => void
}) {
  const defaultTab = (pod.crashloop || pod.restarts > 2) ? 'diagnose' : 'overview'
  const [tab, setTab] = useState<'overview' | 'events' | 'logs' | 'diagnose' | 'spec'>(defaultTab)
  const [events, setEvents] = useState<PodEvent[]>([])
  const [eventsLoading, setEventsLoading] = useState(false)
  const [logs, setLogs] = useState<string[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [logContainer, setLogContainer] = useState(pod.containers[0]?.name ?? '')
  const [diagnoseText, setDiagnoseText] = useState('')
  const [diagnoseLoading, setDiagnoseLoading] = useState(false)
  const [teachMode, setTeachMode] = useState(false)
  const [specDetail, setSpecDetail] = useState<any>(null)
  const [specLoading, setSpecLoading] = useState(false)
  const logsRef = useRef<HTMLDivElement>(null)
  const diagnoseEs = useRef<EventSource | null>(null)

  function podPhaseColor(phase: string) {
    if (phase === 'Running') return 'text-emerald-400'
    if (phase === 'Pending') return 'text-yellow-400'
    if (phase === 'Failed') return 'text-red-400'
    return 'text-slate-400'
  }

  function containerStateColor(state: string) {
    if (state === 'Running') return 'text-emerald-400'
    if (state === 'Waiting') return 'text-yellow-400'
    return 'text-red-400'
  }

  // Events tab
  useEffect(() => {
    if (tab !== 'events') return
    setEventsLoading(true)
    vksGet<{ events: PodEvent[] }>(
      `${clusterId}/events?namespace=${pod.namespace}&pod=${pod.name}`
    )
      .then(d => setEvents(d.events))
      .catch(() => {})
      .finally(() => setEventsLoading(false))
  }, [tab, clusterId, pod.namespace, pod.name])

  // Logs tab
  useEffect(() => {
    if (tab !== 'logs') return
    setLogs([])
    setLogsLoading(true)
    const container = encodeURIComponent(logContainer)
    const es = new EventSource(
      `${API}/api/v1/vks/${encodeURIComponent(clusterId)}/pods/${encodeURIComponent(pod.name)}/logs?namespace=${encodeURIComponent(pod.namespace)}&container=${container}&tail_lines=300`
    )
    const lines: string[] = []
    es.onmessage = ev => {
      try {
        const d = JSON.parse(ev.data)
        if (d.line !== undefined) { lines.push(d.line); setLogs([...lines]) }
        if (d.done || d.error) { es.close(); setLogsLoading(false) }
      } catch { /* ignore parse errors */ }
    }
    es.onerror = () => { es.close(); setLogsLoading(false) }
    return () => es.close()
  }, [tab, clusterId, pod.namespace, pod.name, logContainer])

  useEffect(() => {
    if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight
  }, [logs])

  // Spec tab — env vars, volume mounts, probes
  useEffect(() => {
    if (tab !== 'spec' || specDetail) return
    setSpecLoading(true)
    vksGet<any>(`${clusterId}/pods/detail?name=${pod.name}&namespace=${pod.namespace}`)
      .then(d => setSpecDetail(d))
      .catch(() => {})
      .finally(() => setSpecLoading(false))
  }, [tab, clusterId, pod.name, pod.namespace, specDetail])

  // Diagnose tab
  function runDiagnose() {
    if (diagnoseEs.current) { diagnoseEs.current.close() }
    setDiagnoseText('')
    setDiagnoseLoading(true)
    const mode = teachMode ? 'teach' : 'diagnose'
    const es = new EventSource(
      `${API}/api/v1/vks/${encodeURIComponent(clusterId)}/pods/${encodeURIComponent(pod.name)}/diagnose?namespace=${encodeURIComponent(pod.namespace)}&mode=${mode}`
    )
    diagnoseEs.current = es
    let text = ''
    es.onmessage = ev => {
      try {
        const d = JSON.parse(ev.data)
        if (d.text) { text += d.text; setDiagnoseText(text) }
        if (d.done) { es.close(); setDiagnoseLoading(false) }
        if (d.error) {
          setDiagnoseText(`⚠ LLM unavailable — ${d.error}`)
          es.close(); setDiagnoseLoading(false)
        }
      } catch { /* ignore */ }
    }
    es.onerror = () => {
      if (!text) setDiagnoseText('⚠ LLM unavailable — check llm-gateway logs')
      es.close(); setDiagnoseLoading(false)
    }
  }

  useEffect(() => {
    if (tab === 'diagnose' && !diagnoseText && !diagnoseLoading) runDiagnose()
    return () => { if (diagnoseEs.current) { diagnoseEs.current.close() } }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  useEffect(() => () => { if (diagnoseEs.current) diagnoseEs.current.close() }, [])

  const TABS = [
    { id: 'overview' as const, label: 'Overview' },
    { id: 'spec'     as const, label: 'Env & Volumes' },
    { id: 'events'   as const, label: 'Events'   },
    { id: 'logs'     as const, label: 'Logs'     },
    { id: 'diagnose' as const, label: '✦ AI Diagnose' },
  ]

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px]" onClick={onClose} />
      <div className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-[560px] bg-[#0d1117] border-l border-slate-700/60 shadow-2xl flex flex-col">

        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-slate-800 flex-shrink-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-sm font-bold text-white truncate">{pod.name}</span>
              {pod.crashloop && (
                <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-red-500/20 text-red-400 border border-red-500/30 animate-pulse whitespace-nowrap">
                  CRASHLOOP
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-0.5 text-[11px]">
              <span className="text-slate-500">{pod.namespace}</span>
              <span className="text-slate-700">·</span>
              <span className={podPhaseColor(pod.phase)}>{pod.phase}</span>
              {pod.restarts > 0 && (
                <><span className="text-slate-700">·</span>
                <span className="text-red-400">{pod.restarts} restart{pod.restarts !== 1 ? 's' : ''}</span></>
              )}
              {pod.node && (
                <><span className="text-slate-700">·</span>
                <span className="text-slate-500 font-mono text-[10px] truncate max-w-[120px]">{pod.node}</span></>
              )}
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors flex-shrink-0">
            <X size={15} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-800 flex-shrink-0 overflow-x-auto">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors whitespace-nowrap flex-shrink-0 ${
                tab === t.id
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}>
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto min-h-0">

          {/* Overview */}
          {tab === 'overview' && (
            <div className="p-5 space-y-5">
              <div className="grid grid-cols-2 gap-2.5">
                {[
                  { label: 'Pod IP',  value: pod.pod_ip  || '—' },
                  { label: 'Node IP', value: pod.host_ip || '—' },
                  { label: 'Node',    value: pod.node    || '—' },
                  { label: 'Owner',   value: pod.owner   || '—' },
                  { label: 'Ready',   value: pod.ready           },
                  { label: 'Age',     value: _relTime(pod.created_at) },
                ].map(({ label, value }) => (
                  <div key={label} className="bg-slate-800/40 rounded-lg px-3 py-2.5">
                    <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-0.5">{label}</p>
                    <p className="text-xs font-mono text-slate-200 truncate" title={value}>{value}</p>
                  </div>
                ))}
              </div>

              {((pod.req_cpu_m ?? 0) > 0 || (pod.req_mem_mib ?? 0) > 0) && (
                <div>
                  <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Resources</p>
                  <div className="grid grid-cols-2 gap-2.5">
                    <div className="bg-slate-800/40 rounded-lg px-3 py-2.5">
                      <p className="text-[10px] text-slate-500 mb-0.5">CPU Request / Limit</p>
                      <p className="text-xs font-mono text-slate-200">
                        {pod.req_cpu_m ?? 0}m&nbsp;/&nbsp;{pod.lim_cpu_m ? `${pod.lim_cpu_m}m` : '∞'}
                      </p>
                    </div>
                    <div className="bg-slate-800/40 rounded-lg px-3 py-2.5">
                      <p className="text-[10px] text-slate-500 mb-0.5">Mem Request / Limit</p>
                      <p className="text-xs font-mono text-slate-200">
                        {pod.req_mem_mib ?? 0}Mi&nbsp;/&nbsp;{pod.lim_mem_mib ? `${pod.lim_mem_mib}Mi` : '∞'}
                      </p>
                    </div>
                  </div>
                </div>
              )}

              <div>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">
                  Containers ({pod.containers.length})
                </p>
                <div className="space-y-2">
                  {pod.containers.map(c => (
                    <div key={c.name} className="bg-slate-800/40 rounded-lg px-3 py-2.5">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${c.ready ? 'bg-emerald-400' : 'bg-red-400'}`} />
                        <span className="font-mono text-sm text-white font-medium">{c.name}</span>
                        <span className={`text-xs ml-auto ${containerStateColor(c.state)}`}>{c.state}</span>
                        {c.restarts > 0 && (
                          <span className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 px-1.5 py-0.5 rounded">
                            {c.restarts}↺
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] font-mono text-slate-500 truncate">{c.image}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Env & Volumes */}
          {tab === 'spec' && (
            <div className="p-5 space-y-5">
              {specLoading && <Skeleton className="h-48" />}
              {!specLoading && specDetail && specDetail.containers?.map((c: any) => (
                <div key={c.name} className="space-y-3">
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">{c.name} — env vars</div>
                  {c.env?.length ? (
                    <div className="space-y-1">
                      {c.env.map((e: any, i: number) => (
                        <div key={i} className="flex items-start gap-2 px-3 py-1.5 bg-slate-900 rounded-lg text-xs">
                          <span className="text-blue-300 font-mono w-36 shrink-0 truncate" title={e.name}>{e.name}</span>
                          {e.source ? (
                            <span className="text-slate-500 italic truncate flex-1">{e.value || e.source}</span>
                          ) : (
                            <span className="text-slate-300 font-mono break-all flex-1">{e.value}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : <div className="text-xs text-slate-500">No env vars defined</div>}

                  <div className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">{c.name} — volume mounts</div>
                  {c.volume_mounts?.length ? (
                    <div className="space-y-1">
                      {c.volume_mounts.map((v: any, i: number) => (
                        <div key={i} className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 rounded-lg text-xs">
                          <HardDrive size={10} className="text-slate-500 shrink-0" />
                          <span className="text-slate-300 font-mono flex-1 truncate">{v.mount_path}</span>
                          <span className="text-slate-500 text-[10px]">{v.name}</span>
                          {v.read_only && <span className="text-[10px] text-orange-400 shrink-0">RO</span>}
                        </div>
                      ))}
                    </div>
                  ) : <div className="text-xs text-slate-500">No volume mounts</div>}

                  {(c.liveness_probe || c.readiness_probe) && (
                    <div className="grid grid-cols-2 gap-2">
                      {c.liveness_probe && (
                        <div className="bg-slate-900 rounded-lg p-2.5 text-xs">
                          <div className="text-slate-500 text-[10px] uppercase tracking-wider mb-1">Liveness</div>
                          <div className="text-slate-300 font-mono text-[10px]">
                            {c.liveness_probe.type === 'httpGet' && `GET ${c.liveness_probe.path}:${c.liveness_probe.port}`}
                            {c.liveness_probe.type === 'exec' && (c.liveness_probe.command ?? []).join(' ')}
                            {c.liveness_probe.type === 'tcpSocket' && `TCP :${c.liveness_probe.port}`}
                          </div>
                          <div className="text-slate-600 mt-1">delay {c.liveness_probe.initial_delay}s · every {c.liveness_probe.period}s</div>
                        </div>
                      )}
                      {c.readiness_probe && (
                        <div className="bg-slate-900 rounded-lg p-2.5 text-xs">
                          <div className="text-slate-500 text-[10px] uppercase tracking-wider mb-1">Readiness</div>
                          <div className="text-slate-300 font-mono text-[10px]">
                            {c.readiness_probe.type === 'httpGet' && `GET ${c.readiness_probe.path}:${c.readiness_probe.port}`}
                            {c.readiness_probe.type === 'exec' && (c.readiness_probe.command ?? []).join(' ')}
                            {c.readiness_probe.type === 'tcpSocket' && `TCP :${c.readiness_probe.port}`}
                          </div>
                          <div className="text-slate-600 mt-1">delay {c.readiness_probe.initial_delay}s · every {c.readiness_probe.period}s</div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Events */}
          {tab === 'events' && (
            <div className="p-4">
              {eventsLoading ? <SkeletonRows rows={5} /> : events.length === 0 ? (
                <EmptyState icon={Radio} title="No events" hint="No events found for this pod" />
              ) : (
                <div className="space-y-2">
                  {events.map((ev, i) => (
                    <div key={i} className={`rounded-lg px-3 py-2.5 border text-xs ${
                      ev.type === 'Warning'
                        ? 'bg-yellow-500/5 border-yellow-500/20'
                        : 'bg-slate-800/40 border-slate-700/40'
                    }`}>
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <span className={`font-bold ${ev.type === 'Warning' ? 'text-yellow-400' : 'text-blue-400'}`}>
                          {ev.type}
                        </span>
                        <span className="text-slate-200 font-medium">{ev.reason}</span>
                        <span className="ml-auto text-slate-600 text-[10px] whitespace-nowrap">{_relTime(ev.last_time)}</span>
                        {ev.count > 1 && (
                          <span className="text-[10px] text-slate-500 bg-slate-700/50 px-1.5 py-0.5 rounded">×{ev.count}</span>
                        )}
                      </div>
                      <p className="text-slate-400 leading-relaxed">{ev.message}</p>
                      {ev.source && <p className="text-slate-600 text-[10px] mt-1">Source: {ev.source}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Logs */}
          {tab === 'logs' && (
            <div className="flex flex-col h-full">
              {pod.containers.length > 1 && (
                <div className="flex gap-1 px-4 pt-3 flex-wrap flex-shrink-0 border-b border-slate-800 pb-3">
                  {pod.containers.map(c => (
                    <button key={c.name} onClick={() => setLogContainer(c.name)}
                      className={`px-2.5 py-1 rounded text-[11px] font-mono transition-colors ${
                        logContainer === c.name
                          ? 'bg-blue-600/30 text-blue-300 border border-blue-500/40'
                          : 'bg-slate-800 text-slate-400 border border-slate-700/50 hover:bg-slate-700'
                      }`}>
                      {c.name}
                    </button>
                  ))}
                </div>
              )}
              <div ref={logsRef} className="flex-1 overflow-y-auto p-4 font-mono text-[11px] leading-5 bg-slate-950/50">
                {logsLoading && logs.length === 0 ? (
                  <div className="flex items-center gap-2 text-slate-500 py-2">
                    <Loader2 size={12} className="animate-spin" /> Loading logs…
                  </div>
                ) : logs.length === 0 ? (
                  <span className="text-slate-600">No log output</span>
                ) : logs.map((line, i) => (
                  <div key={i} className={
                    /error|exception|fatal|panic/i.test(line) ? 'text-red-400' :
                    /warn/i.test(line) ? 'text-yellow-400' :
                    'text-slate-300'
                  }>{line || ' '}</div>
                ))}
                {logsLoading && logs.length > 0 && (
                  <div className="flex items-center gap-1 text-slate-600 mt-1 text-[10px]">
                    <Loader2 size={10} className="animate-spin" /> streaming…
                  </div>
                )}
              </div>
            </div>
          )}

          {/* AI Diagnose */}
          {tab === 'diagnose' && (
            <div className="p-5 space-y-4">
              {/* Mode + re-run controls */}
              <div className="flex items-center gap-2 flex-wrap">
                <div className="flex rounded-lg border border-slate-700 overflow-hidden text-[11px]">
                  <button
                    onClick={() => { if (teachMode) { setTeachMode(false); setDiagnoseText('') } }}
                    className={`px-3 py-1.5 transition-colors ${!teachMode ? 'bg-blue-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>
                    Diagnose
                  </button>
                  <button
                    onClick={() => { if (!teachMode) { setTeachMode(true); setDiagnoseText('') } }}
                    className={`px-3 py-1.5 flex items-center gap-1 transition-colors ${teachMode ? 'bg-violet-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>
                    <BookOpen size={10} /> Teach me
                  </button>
                </div>
                <button onClick={runDiagnose} disabled={diagnoseLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] rounded-lg border border-slate-700 transition-colors disabled:opacity-50">
                  <RotateCcw size={10} className={diagnoseLoading ? 'animate-spin' : ''} />
                  {diagnoseLoading ? 'Analyzing…' : 'Re-run'}
                </button>
                {teachMode && (
                  <span className="text-[10px] text-violet-400 bg-violet-500/10 border border-violet-500/20 px-2 py-1 rounded">
                    Learning mode — explains K8s concepts
                  </span>
                )}
              </div>

              {diagnoseLoading && !diagnoseText && (
                <div className="flex items-center gap-2 text-slate-400 py-6 justify-center">
                  <Loader2 size={16} className="animate-spin text-blue-400" />
                  <span className="text-sm">{teachMode ? 'Preparing explanation…' : 'Analyzing pod state and logs…'}</span>
                </div>
              )}

              {!diagnoseLoading && !diagnoseText && (
                <button onClick={runDiagnose}
                  className="flex items-center gap-2 px-4 py-3 rounded-xl border border-blue-500/30 bg-blue-500/5 text-blue-400 hover:bg-blue-500/10 transition-colors text-sm w-full justify-center">
                  <BotMessageSquare size={14} /> Start AI Analysis
                </button>
              )}

              {diagnoseText && (
                <div className="text-sm text-slate-300 leading-relaxed
                  [&_h2]:text-slate-100 [&_h2]:font-semibold [&_h2]:text-sm [&_h2]:mt-4 [&_h2]:mb-1.5
                  [&_h3]:text-slate-200 [&_h3]:font-medium [&_h3]:text-sm [&_h3]:mt-3 [&_h3]:mb-1
                  [&_strong]:text-slate-200
                  [&_code]:text-blue-300 [&_code]:bg-slate-800 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs [&_code]:font-mono
                  [&_pre]:bg-slate-800/70 [&_pre]:border [&_pre]:border-slate-700/50 [&_pre]:rounded-lg [&_pre]:p-3 [&_pre]:overflow-x-auto [&_pre]:my-2
                  [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-xs
                  [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:space-y-1 [&_ul]:my-2
                  [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:space-y-1 [&_ol]:my-2
                  [&_li]:text-slate-300 [&_p]:my-1.5
                ">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {diagnoseText}
                  </ReactMarkdown>
                  {diagnoseLoading && (
                    <span className="inline-block w-1.5 h-4 bg-blue-400 animate-pulse ml-0.5 align-middle rounded-sm" />
                  )}
                </div>
              )}
            </div>
          )}

        </div>
      </div>
    </>
  )
}


// ── Pods Section ──────────────────────────────────────────────────────────────

interface PodMetric { name: string; namespace: string; cpu_m: number; mem_mib: number }

function PodsSection({ clusterId, namespace, externalFilter }: { clusterId: string; namespace: string; externalFilter?: string }) {
  const [pods, setPods] = useState<PodInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [logState, setLogState] = useState<LogState | null>(null)
  const [execState, setExecState] = useState<ExecState | null>(null)
  const [containerPickPod, setContainerPickPod] = useState<PodInfo | null>(null)
  const [detailPod, setDetailPod] = useState<PodInfo | null>(null)
  const [expandedPod, setExpandedPod] = useState<string | null>(null)
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [filterText, setFilterText] = useState(externalFilter || '')
  const [selectedPods, setSelectedPods] = useState<Set<string>>(new Set())
  const [batchConfirm, setBatchConfirm] = useState<ConfirmState | null>(null)

  useEffect(() => { if (externalFilter !== undefined) setFilterText(externalFilter) }, [externalFilter])
  const [phaseFilter, setPhaseFilter] = useState<string>('all')
  const [metricsMap, setMetricsMap] = useState<Map<string, PodMetric>>(new Map())
  const { sortKey, sortDir, onSort } = useSort()
  const toast = useToast()

  const loadMetrics = useCallback(() => {
    vksGet<{ available: boolean; pods: PodMetric[] }>(`${clusterId}/pods/metrics?namespace=${namespace}`)
      .then(d => {
        if (d.available) {
          const map = new Map(d.pods.map(p => [`${p.namespace}/${p.name}`, p]))
          setMetricsMap(map)
        }
      })
      .catch(() => {})
  }, [clusterId, namespace])

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ pods: PodInfo[] }>(`${clusterId}/pods?namespace=${namespace}`)
      .then(d => {
        // crashlooping pods sort to the top
        const sorted = [...d.pods].sort((a, b) => {
          if (a.crashloop && !b.crashloop) return -1
          if (!a.crashloop && b.crashloop) return 1
          return b.restarts - a.restarts
        })
        setPods(sorted)
        setLoadErr(null)
        loadMetrics()
      })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace, loadMetrics])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  async function handleBatchRestart() {
    if (selectedPods.size === 0) return
    const podsList = [...selectedPods].map(key => {
      const [ns, ...rest] = key.split('/')
      return { namespace: ns, name: rest.join('/') }
    })
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/pods/batch-restart`, { pods: podsList }
      )
      if (resp.requires_confirm) {
        setBatchConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/pods/batch-restart?token=${token}`, { pods: podsList })
          toast.success(`Restarted ${podsList.length} pods`)
          setSelectedPods(new Set())
          load()
        }})
      }
    } catch (e) { toast.error(`Batch restart failed: ${e}`) }
  }

  async function handleDelete(pod: PodInfo) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/pods/${pod.name}/delete?namespace=${pod.namespace}`, {}
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/pods/${pod.name}/delete?namespace=${pod.namespace}&token=${token}`, {})
          toast.success(`Deleted pod ${pod.name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  const phaseColor = (phase: string) => {
    if (phase === 'Running') return 'text-emerald-400'
    if (phase === 'Pending') return 'text-yellow-400'
    if (phase === 'Failed' || phase === 'CrashLoopBackOff') return 'text-red-400'
    return 'text-slate-400'
  }

  return (
    <div className="space-y-4">
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {logState && <LogViewer state={logState} onClose={() => setLogState(null)} />}
      {execState && <ExecTerminal state={execState} onClose={() => setExecState(null)} />}
      {containerPickPod && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setContainerPickPod(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-sm shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
              <Terminal size={14} className="text-slate-400" />
              <span className="text-sm font-medium text-white">Select container</span>
              <button onClick={() => setContainerPickPod(null)} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
            </div>
            <div className="p-3 space-y-2">
              <p className="text-xs text-slate-500 font-mono mb-2">{containerPickPod.name}</p>
              {containerPickPod.containers.map(c => (
                <button key={c.name} onClick={() => { setExecState({ cluster: clusterId, namespace: containerPickPod.namespace, pod: containerPickPod.name, container: c.name }); setContainerPickPod(null) }}
                  className="w-full text-left px-3 py-2 rounded-lg hover:bg-slate-800 border border-slate-700/50 transition-colors">
                  <div className="font-mono text-sm text-white">{c.name}</div>
                  <div className="text-xs text-slate-500 truncate">{c.image}</div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {detailPod && <PodDetailPanel pod={detailPod} clusterId={clusterId} onClose={() => setDetailPod(null)} />}

      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={filterText}
          onChange={e => setFilterText(e.target.value)}
          placeholder="Filter by name, namespace, or node…"
          className="flex-1 min-w-[160px] bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-300 placeholder-slate-500 outline-none focus:border-blue-500"
        />
        {(['all', 'Running', 'Pending', 'Failed', 'Succeeded'] as const).map(p => (
          <button key={p} onClick={() => setPhaseFilter(p)}
            className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${phaseFilter === p ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>
            {p === 'all' ? 'All' : p}
          </button>
        ))}
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
        {selectedPods.size > 0 && (
          <button onClick={handleBatchRestart}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-600 hover:bg-orange-500 text-white text-xs rounded-lg">
            <RotateCcw size={12} /> Restart {selectedPods.size} selected
          </button>
        )}
        {selectedPods.size > 0 && (
          <button onClick={() => setSelectedPods(new Set())} className="px-2 py-1.5 text-xs text-slate-400 hover:text-white">
            Clear
          </button>
        )}
      </div>

      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={8} /> : !loadErr && pods.length === 0 ? (
        <EmptyState icon={Box} title="No pods" hint={`No pods in ${namespace || 'any namespace'}`} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-2 w-8">
                  <input type="checkbox" className="rounded"
                    onChange={e => {
                      if (e.target.checked) {
                        const visible = useSortedFiltered(phaseFilter === 'all' ? pods : pods.filter(p => p.phase === phaseFilter), filterText, ['name', 'namespace', 'phase', 'node'], sortKey, sortDir)
                        setSelectedPods(new Set(visible.map(p => `${p.namespace}/${p.name}`)))
                      } else {
                        setSelectedPods(new Set())
                      }
                    }} />
                </th>
                <th className="px-4 py-2 w-6"></th>
                <SortableHeader label="Name" col="name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                {!namespace && <SortableHeader label="Namespace" col="namespace" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />}
                <SortableHeader label="Status" col="phase" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Ready</th>
                <SortableHeader label="Restarts" col="restarts" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <SortableHeader label="Node" col="node" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <SortableHeader label="Age" col="created_at" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                {metricsMap.size > 0 && <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">CPU</th>}
                {metricsMap.size > 0 && <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Mem</th>}
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {useSortedFiltered(
                phaseFilter === 'all' ? pods : pods.filter(p => p.phase === phaseFilter),
                filterText, ['name', 'namespace', 'phase', 'node'], sortKey, sortDir
              ).map(pod => {
                const isExpanded = expandedPod === pod.name
                const unhealthy = pod.phase !== 'Running' || pod.restarts > 5
                const podMetric = metricsMap.get(`${pod.namespace}/${pod.name}`)
                return (
                  <>
                    <tr key={pod.name} className={`group border-b border-slate-800/40 hover:bg-slate-800/20 ${isExpanded ? 'bg-slate-800/30' : ''} ${pod.crashloop || pod.restarts > 9 ? 'bg-red-950/20' : pod.restarts > 2 ? 'bg-orange-950/10' : ''}`}>
                      <td className="px-2 py-3" onClick={e => e.stopPropagation()}>
                        <input type="checkbox" className="rounded"
                          checked={selectedPods.has(`${pod.namespace}/${pod.name}`)}
                          onChange={e => {
                            const key = `${pod.namespace}/${pod.name}`
                            setSelectedPods(prev => {
                              const s = new Set(prev)
                              e.target.checked ? s.add(key) : s.delete(key)
                              return s
                            })
                          }} />
                      </td>
                      <td className="px-2 py-3">
                        <button onClick={() => setExpandedPod(isExpanded ? null : pod.name)} className="text-slate-500 hover:text-slate-300">
                          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        </button>
                      </td>
                      <td className="px-4 py-3 font-mono text-white text-sm">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => setDetailPod(pod)}
                            className="text-left hover:text-blue-300 transition-colors inline-flex items-center gap-1"
                            title="Open details panel">
                            {pod.name}
                          </button>
                          <CopyButton text={pod.name} />
                          {pod.containers.length > 1 && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] bg-slate-800 text-slate-400">{pod.containers.length} ctr</span>
                          )}
                          {pod.crashloop && (
                            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-red-500/20 text-red-400 animate-pulse border border-red-500/30">
                              ⚠ CRASHLOOP
                            </span>
                          )}
                        </div>
                      </td>
                      {!namespace && <td className="px-4 py-3 text-slate-400 text-xs">{pod.namespace}</td>}
                      <td className={`px-4 py-3 text-xs font-medium ${phaseColor(pod.phase)}`}>{pod.phase}</td>
                      <td className="px-4 py-3 text-xs text-slate-400">{pod.ready}</td>
                      <td className="px-4 py-3 text-xs">
                        <span className={`inline-block px-1.5 py-0.5 rounded font-medium tabular-nums ${
                          pod.restarts === 0 ? 'text-slate-500' :
                          pod.restarts <= 2 ? 'bg-yellow-900/40 text-yellow-300' :
                          pod.restarts <= 9 ? 'bg-orange-900/50 text-orange-300' :
                          'bg-red-900/60 text-red-300 font-bold'
                        }`}>
                          {pod.restarts}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-400 font-mono">{pod.node}</td>
                      <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">{_relTime(pod.created_at)}</td>
                      {metricsMap.size > 0 && (
                        <td className="px-4 py-3 text-xs font-mono">
                          {podMetric ? <span className={podMetric.cpu_m > 500 ? 'text-yellow-400' : 'text-slate-300'}>{podMetric.cpu_m}m</span> : <span className="text-slate-600">—</span>}
                        </td>
                      )}
                      {metricsMap.size > 0 && (
                        <td className="px-4 py-3 text-xs font-mono">
                          {podMetric ? <span className={podMetric.mem_mib > 512 ? 'text-orange-400' : 'text-slate-300'}>{podMetric.mem_mib}Mi</span> : <span className="text-slate-600">—</span>}
                        </td>
                      )}
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1 justify-end">
                          <ActionBtn icon={BotMessageSquare} title="AI Diagnose / Details" onClick={() => setDetailPod(pod)} />
                          <ActionBtn icon={Terminal} title="Logs" onClick={() => setLogState({ cluster: clusterId, namespace: pod.namespace, pod: pod.name })} />
                          <ActionBtn icon={Play} title="Exec shell" onClick={() => {
                            if (pod.containers.length > 1) setContainerPickPod(pod)
                            else setExecState({ cluster: clusterId, namespace: pod.namespace, pod: pod.name, container: pod.containers[0]?.name })
                          }} />
                          <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'pods', name: pod.name, namespace: pod.namespace })} />
                          <ActionBtn icon={Trash2} title="Delete" danger onClick={() => handleDelete(pod)} />
                        </div>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${pod.name}-expanded`} className="border-b border-slate-800/40 bg-slate-900/40">
                        <td colSpan={metricsMap.size > 0 ? 11 : 9} className="px-8 py-3 space-y-3">
                          <div className="flex flex-wrap gap-4 text-xs text-slate-400">
                            {pod.pod_ip && <span>Pod IP: <span className="text-slate-200 font-mono">{pod.pod_ip}<CopyButton text={pod.pod_ip} /></span></span>}
                            {pod.host_ip && <span>Node IP: <span className="text-slate-200 font-mono">{pod.host_ip}</span></span>}
                            {pod.owner && <span>Owner: <span className="text-blue-400 font-mono">{pod.owner}</span></span>}
                          </div>
                          {(pod.req_cpu_m != null && pod.req_cpu_m > 0) || (pod.req_mem_mib != null && pod.req_mem_mib > 0) ? (
                            <div className="flex gap-4 text-xs text-slate-400">
                              <span>Requests: <span className="text-slate-200">{pod.req_cpu_m}m CPU / {pod.req_mem_mib}Mi Mem</span></span>
                              {(pod.lim_cpu_m ?? 0) > 0 && <span>Limits: <span className="text-slate-200">{pod.lim_cpu_m}m CPU / {pod.lim_mem_mib}Mi Mem</span></span>}
                            </div>
                          ) : null}
                          <div className="grid gap-2">
                            {pod.containers.map(c => (
                              <div key={c.name} className="flex items-center gap-3 text-xs">
                                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${c.ready ? 'bg-emerald-400' : 'bg-red-400'}`} />
                                <span className="font-mono text-white w-32 truncate">{c.name}</span>
                                <span className={`${c.state !== 'Running' ? 'text-yellow-400' : 'text-slate-400'}`}>{c.state}</span>
                                <span className="text-slate-500 font-mono truncate max-w-xs">{c.image}</span>
                                {c.restarts > 0 && <span className="text-red-400">{c.restarts} restarts</span>}
                                <div className="ml-auto flex items-center gap-2">
                                  <button onClick={() => setLogState({ cluster: clusterId, namespace: pod.namespace, pod: pod.name, container: c.name })}
                                    className="flex items-center gap-1 text-slate-400 hover:text-white">
                                    <Terminal size={11} /> logs
                                  </button>
                                  <button onClick={() => setExecState({ cluster: clusterId, namespace: pod.namespace, pod: pod.name, container: c.name })}
                                    className="flex items-center gap-1 text-slate-400 hover:text-emerald-400">
                                    <Play size={11} /> exec
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Node Detail Drawer ────────────────────────────────────────────────────────

function NodeDetailDrawer({ node, clusterId, onClose, onCordon, onUncordon }: { node: NodeInfo; clusterId: string; onClose: () => void; onCordon?: () => void; onUncordon?: () => void }) {
  const [tab, setTab] = useState<'overview' | 'pods' | 'events'>('overview')
  const [pods, setPods] = useState<any[]>([])
  const [events, setEvents] = useState<any[]>([])
  const [loadingPods, setLoadingPods] = useState(false)
  const [loadingEvents, setLoadingEvents] = useState(false)

  useEffect(() => {
    if (tab === 'pods' && pods.length === 0) {
      setLoadingPods(true)
      vksGet<{ pods: any[] }>(`${clusterId}/pods`)
        .then(d => setPods((d.pods ?? []).filter((p: any) => p.node === node.name)))
        .finally(() => setLoadingPods(false))
    }
    if (tab === 'events' && events.length === 0) {
      setLoadingEvents(true)
      vksGet<{ events: any[] }>(`${clusterId}/events?namespace=`)
        .then(d => setEvents((d.events ?? []).filter((e: any) => e.name === node.name || e.object_name === node.name || e.regarding === node.name || (e.object || '').includes(node.name))))
        .finally(() => setLoadingEvents(false))
    }
  }, [tab, clusterId, node.name, pods.length, events.length])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  function conditionColor(type: string, status: string) {
    const isTrue = status === 'True'
    if (type === 'Ready') return isTrue ? 'bg-emerald-900/40 text-emerald-300 border-emerald-700/40' : 'bg-red-900/40 text-red-300 border-red-700/40'
    return isTrue ? 'bg-red-900/40 text-red-300 border-red-700/40' : 'bg-emerald-900/40 text-emerald-300 border-emerald-700/40'
  }

  function taintColor(effect: string) {
    if (effect === 'NoExecute') return 'bg-red-900/40 text-red-300'
    if (effect === 'NoSchedule') return 'bg-orange-900/40 text-orange-300'
    return 'bg-yellow-900/40 text-yellow-300'
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative w-[480px] h-full bg-slate-950 border-l border-slate-800 flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${node.ready ? 'bg-emerald-400' : 'bg-red-400'}`} />
              <span className="font-mono font-semibold text-white text-sm">{node.name}</span>
              {node.unschedulable && <span className="text-[10px] px-1.5 py-0.5 bg-yellow-900/40 text-yellow-400 rounded-full">Cordoned</span>}
            </div>
            <div className="text-xs text-slate-500 mt-0.5">{node.roles.join(', ')} · {node.kubelet_version}</div>
          </div>
          <div className="flex items-center gap-1.5">
            {(onCordon || onUncordon) && (
              node.unschedulable ? (
                <button onClick={() => { onUncordon?.(); onClose() }}
                  className="flex items-center gap-1 px-2.5 py-1 text-[10px] bg-emerald-900/40 hover:bg-emerald-900/60 text-emerald-400 rounded-lg border border-emerald-700/40 transition-colors">
                  <CheckCircle size={10} /> Uncordon
                </button>
              ) : (
                <button onClick={() => { onCordon?.(); onClose() }}
                  className="flex items-center gap-1 px-2.5 py-1 text-[10px] bg-yellow-900/40 hover:bg-yellow-900/60 text-yellow-400 rounded-lg border border-yellow-700/40 transition-colors">
                  <AlertTriangle size={10} /> Cordon
                </button>
              )
            )}
            <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors"><X size={16} /></button>
          </div>
        </div>

        <div className="flex gap-0 px-5 border-b border-slate-800">
          {(['overview', 'pods', 'events'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-3 py-2.5 text-xs font-medium capitalize border-b-2 transition-colors ${tab === t ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-500 hover:text-slate-300'}`}>
              {t}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {tab === 'overview' && (
            <>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">Conditions</div>
                <div className="space-y-2">
                  {(node.conditions ?? []).map(c => (
                    <div key={c.type} className={`flex items-start gap-2 px-3 py-2 rounded-lg border text-xs ${conditionColor(c.type, c.status)}`}>
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold">{c.type}</span>
                          <span className="opacity-70">{c.status}</span>
                          {c.reason && <span className="opacity-60">· {c.reason}</span>}
                        </div>
                        {c.message && <div className="mt-0.5 opacity-70 text-[10px] leading-tight">{c.message}</div>}
                      </div>
                    </div>
                  ))}
                  {!node.conditions?.length && <div className="text-xs text-slate-500">No conditions available</div>}
                </div>
              </div>

              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">Resources</div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="bg-slate-900 rounded-lg p-3">
                    <div className="text-slate-400 mb-1 flex items-center gap-1"><Cpu size={11} /> CPU</div>
                    <div className="text-white font-mono">{Math.round(node.allocatable_cpu_m / 1000 * 10) / 10} cores allocatable</div>
                    <div className="text-slate-500">{Math.round(node.capacity_cpu_m / 1000 * 10) / 10} cores capacity</div>
                  </div>
                  <div className="bg-slate-900 rounded-lg p-3">
                    <div className="text-slate-400 mb-1 flex items-center gap-1"><MemoryStick size={11} /> Memory</div>
                    <div className="text-white font-mono">{Math.round(node.allocatable_mem_mib / 1024)} GiB allocatable</div>
                    <div className="text-slate-500">{Math.round(node.capacity_mem_mib / 1024)} GiB capacity</div>
                  </div>
                </div>
              </div>

              {node.taints?.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">Taints</div>
                  <div className="space-y-1.5">
                    {node.taints.map((t, i) => (
                      <div key={i} className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs ${taintColor(t.effect)}`}>
                        <span className="font-mono font-medium">{t.key}{t.value ? `=${t.value}` : ''}</span>
                        <span className="opacity-70">:{t.effect}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">System</div>
                <div className="space-y-1 text-xs">
                  {[['OS', node.os], ['Kernel', node.kernel], ['Runtime', node.container_runtime]].filter(([, v]) => v).map(([k, v]) => (
                    <div key={k} className="flex gap-3"><span className="text-slate-500 w-20 shrink-0">{k}</span><span className="text-slate-300 font-mono">{v}</span></div>
                  ))}
                </div>
              </div>
            </>
          )}

          {tab === 'pods' && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">{pods.length} pods on this node</div>
              {loadingPods ? <Skeleton className="h-32" /> : (
                <div className="space-y-1.5">
                  {pods.map(p => (
                    <div key={`${p.namespace}/${p.name}`} className="flex items-center gap-2 px-3 py-2 bg-slate-900 rounded-lg text-xs">
                      <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${p.phase === 'Running' ? 'bg-emerald-400' : p.phase === 'Pending' ? 'bg-yellow-400' : 'bg-red-400'}`} />
                      <div className="flex-1 min-w-0">
                        <div className="text-white font-mono truncate">{p.name}</div>
                        <div className="text-slate-500">{p.namespace}</div>
                      </div>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${p.phase === 'Running' ? 'bg-emerald-900/40 text-emerald-400' : 'bg-slate-800 text-slate-400'}`}>{p.phase}</span>
                      {p.restarts > 0 && <span className="text-[10px] text-orange-400">{p.restarts}↺</span>}
                    </div>
                  ))}
                  {!pods.length && !loadingPods && <div className="text-xs text-slate-500">No pods found on this node</div>}
                </div>
              )}
            </div>
          )}

          {tab === 'events' && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">Events for {node.name}</div>
              {loadingEvents ? <Skeleton className="h-32" /> : (
                <div className="space-y-1.5">
                  {events.map((e, i) => (
                    <div key={i} className={`px-3 py-2 rounded-lg text-xs border ${e.type === 'Warning' ? 'bg-yellow-900/20 border-yellow-800/30' : 'bg-slate-900 border-slate-800'}`}>
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className={`font-medium ${e.type === 'Warning' ? 'text-yellow-300' : 'text-slate-300'}`}>{e.reason}</span>
                        <span className="text-slate-600">{e.age || e.last_seen}</span>
                      </div>
                      <div className="text-slate-400">{e.message}</div>
                    </div>
                  ))}
                  {!events.length && !loadingEvents && <div className="text-xs text-slate-500">No events found for this node</div>}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Nodes Section ─────────────────────────────────────────────────────────────

interface NodeMetric { name: string; cpu_m: number; mem_mib: number }

function NodesSection({ clusterId }: { clusterId: string }) {
  const [nodes, setNodes] = useState<NodeInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [expandedNode, setExpandedNode] = useState<string | null>(null)
  const [drawerNode, setDrawerNode] = useState<NodeInfo | null>(null)
  const [nodeMetrics, setNodeMetrics] = useState<Map<string, NodeMetric>>(new Map())
  const [editingLabels, setEditingLabels] = useState<string | null>(null)
  const [newLabelKey, setNewLabelKey] = useState('')
  const [newLabelVal, setNewLabelVal] = useState('')
  const [editingTaints, setEditingTaints] = useState<string | null>(null)
  const [newTaintKey, setNewTaintKey] = useState('')
  const [newTaintEffect, setNewTaintEffect] = useState<'NoSchedule' | 'PreferNoSchedule' | 'NoExecute'>('NoSchedule')
  const [newTaintVal, setNewTaintVal] = useState('')
  const toast = useToast()

  const loadMetrics = useCallback(() => {
    vksGet<{ available: boolean; nodes: NodeMetric[] }>(`${clusterId}/nodes/metrics`)
      .then(d => {
        if (d.available) {
          setNodeMetrics(new Map((d.nodes ?? []).map((n: any) => [n.name, n])))
        }
      })
      .catch(() => {})
  }, [clusterId])

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ nodes: NodeInfo[] }>(`${clusterId}/nodes`)
      .then(d => { setNodes(d.nodes); setLoadErr(null); loadMetrics() })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, loadMetrics])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 60_000)

  async function handleCordon(node: NodeInfo) {
    const action = node.unschedulable ? 'uncordon' : 'cordon'
    if (action === 'uncordon') {
      await vksPost(`${clusterId}/nodes/${node.name}/uncordon`, {})
      toast.success(`Uncordoned ${node.name}`); load(); return
    }
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/nodes/${node.name}/cordon`, {}
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/nodes/${node.name}/cordon?token=${token}`, {})
          toast.success(`Cordoned ${node.name}`); load()
        }})
      }
    } catch (e) { toast.error(`Cordon failed: ${e}`) }
  }

  async function handleDrain(name: string) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/nodes/${name}/drain`, {}
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          const r = await vksPost<{ evicted: number }>(`${clusterId}/nodes/${name}/drain?token=${token}`, {})
          toast.success(`Drained ${name} — ${(r as { evicted: number }).evicted} pods evicted`); load()
        }})
      }
    } catch (e) { toast.error(`Drain failed: ${e}`) }
  }

  async function handleAddLabel(nodeName: string) {
    const k = newLabelKey.trim(); const v = newLabelVal.trim()
    if (!k) return
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/nodes/${nodeName}/labels`, { add: { [k]: v } }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/nodes/${nodeName}/labels?token=${token}`, { add: { [k]: v } })
          toast.success(`Added label ${k}=${v} to ${nodeName}`)
          setNewLabelKey(''); setNewLabelVal(''); load()
        }})
      }
    } catch (e) { toast.error(`Label failed: ${e}`) }
  }

  async function handleRemoveLabel(nodeName: string, labelKey: string) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/nodes/${nodeName}/labels`, { remove: [labelKey] }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/nodes/${nodeName}/labels?token=${token}`, { remove: [labelKey] })
          toast.success(`Removed label ${labelKey} from ${nodeName}`); load()
        }})
      }
    } catch (e) { toast.error(`Remove label failed: ${e}`) }
  }

  async function handleTaintAction(nodeName: string, action: 'add' | 'remove', taint: { key: string; effect: string; value?: string }) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/nodes/${nodeName}/taints`, { action, taint }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/nodes/${nodeName}/taints?token=${token}`, { action, taint })
          toast.success(`${action === 'add' ? 'Added' : 'Removed'} taint ${taint.key}:${taint.effect} on ${nodeName}`)
          if (action === 'add') { setNewTaintKey(''); setNewTaintVal('') }
          load()
        }})
      }
    } catch (e) { toast.error(`Taint ${action} failed: ${e}`) }
  }

  return (
    <div className="space-y-4">
      {drawerNode && (
        <NodeDetailDrawer
          node={drawerNode}
          clusterId={clusterId}
          onClose={() => setDrawerNode(null)}
          onCordon={() => handleCordon(drawerNode)}
          onUncordon={() => handleCordon(drawerNode)}
        />
      )}
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      <div className="flex justify-end">
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={3} /> : !loadErr ? (
        <div className="grid gap-3">
          {nodes.map(node => {
            const isExpanded = expandedNode === node.name
            const nonRoleLabels = Object.entries(node.labels || {}).filter(([k]) => !k.startsWith('node-role.kubernetes.io/') && !k.startsWith('kubernetes.io/') && !k.startsWith('beta.kubernetes.io/'))
            return (
              <div key={node.name} className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
                <div className="p-4">
                  <div className="flex items-center gap-3 mb-3">
                    <button onClick={() => setExpandedNode(isExpanded ? null : node.name)} className="text-slate-500 hover:text-slate-300">
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </button>
                    <div className={`w-2.5 h-2.5 rounded-full ${node.ready ? 'bg-emerald-400' : 'bg-red-400'}`} />
                    <span className="font-mono text-white font-medium">{node.name}</span>
                    {node.unschedulable && (
                      <span className="px-2 py-0.5 bg-yellow-900/40 text-yellow-400 text-xs rounded-full">Cordoned</span>
                    )}
                    {node.roles.map(r => (
                      <span key={r} className="px-2 py-0.5 bg-blue-900/40 text-blue-400 text-xs rounded-full">{r}</span>
                    ))}
                    {(node.conditions ?? []).filter(c => c.type !== 'Ready' && c.status === 'True').map(c => (
                      <span key={c.type} className="px-2 py-0.5 bg-red-900/50 text-red-300 text-[10px] rounded-full border border-red-700/40" title={c.message || c.type}>{c.type}</span>
                    ))}
                    <div className="ml-auto flex gap-2">
                      <ActionBtn
                        icon={node.unschedulable ? CheckCircle : MinusCircle}
                        title={node.unschedulable ? 'Uncordon' : 'Cordon'}
                        onClick={() => handleCordon(node)}
                      />
                      <ActionBtn icon={Info} title="Details" onClick={() => setDrawerNode(node)} />
                      <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'nodes', name: node.name })} />
                      <ActionBtn icon={Square} title="Drain" danger onClick={() => handleDrain(node.name)} />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-slate-400">
                    <div>K8s: <span className="text-slate-200">{node.kubelet_version}</span></div>
                    <div>OS: <span className="text-slate-200">{node.os}</span></div>
                    <div className="col-span-1">
                      <div className="flex items-center gap-1 mb-0.5"><Cpu size={11} />{Math.round(node.allocatable_cpu_m / 1000 * 10) / 10} cores
                        {nodeMetrics.has(node.name) && <span className="text-slate-500">({nodeMetrics.get(node.name)!.cpu_m}m used)</span>}
                      </div>
                      {nodeMetrics.has(node.name) && node.allocatable_cpu_m > 0 && (
                        <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
                          <div className="h-full rounded-full transition-all"
                            style={{
                              width: `${Math.min(100, nodeMetrics.get(node.name)!.cpu_m / node.allocatable_cpu_m * 100)}%`,
                              backgroundColor: nodeMetrics.get(node.name)!.cpu_m / node.allocatable_cpu_m > 0.8 ? '#f59e0b' : '#3b82f6',
                            }} />
                        </div>
                      )}
                    </div>
                    <div className="col-span-1">
                      <div className="flex items-center gap-1 mb-0.5"><MemoryStick size={11} />{Math.round(node.allocatable_mem_mib / 1024)} GiB
                        {nodeMetrics.has(node.name) && <span className="text-slate-500">({Math.round(nodeMetrics.get(node.name)!.mem_mib / 1024 * 10) / 10}G used)</span>}
                      </div>
                      {nodeMetrics.has(node.name) && node.allocatable_mem_mib > 0 && (
                        <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
                          <div className="h-full rounded-full transition-all"
                            style={{
                              width: `${Math.min(100, nodeMetrics.get(node.name)!.mem_mib / node.allocatable_mem_mib * 100)}%`,
                              backgroundColor: nodeMetrics.get(node.name)!.mem_mib / node.allocatable_mem_mib > 0.8 ? '#f59e0b' : '#3b82f6',
                            }} />
                        </div>
                      )}
                    </div>
                  </div>
                  {node.taints?.length > 0 && (
                    <div className="mt-2 flex gap-1 flex-wrap">
                      {node.taints.map((t, i) => (
                        <span key={i} className="px-2 py-0.5 bg-orange-900/30 text-orange-400 text-[10px] rounded-full">{t.key}:{t.effect}</span>
                      ))}
                    </div>
                  )}
                </div>
                {isExpanded && (
                  <div className="border-t border-slate-800 px-6 py-4 bg-slate-800/20 space-y-4">
                    {node.kernel && (
                      <div className="flex gap-4 text-xs">
                        <span className="text-slate-500 w-28 flex-shrink-0">Kernel</span>
                        <span className="text-slate-300 font-mono">{node.kernel}</span>
                      </div>
                    )}
                    {node.container_runtime && (
                      <div className="flex gap-4 text-xs">
                        <span className="text-slate-500 w-28 flex-shrink-0">Runtime</span>
                        <span className="text-slate-300 font-mono">{node.container_runtime}</span>
                      </div>
                    )}

                    {/* Labels editor */}
                    <div className="text-xs">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-slate-500 w-28 flex-shrink-0">Labels</span>
                        <button
                          onClick={() => setEditingLabels(editingLabels === node.name ? null : node.name)}
                          className="text-[10px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300"
                        >{editingLabels === node.name ? 'Done' : 'Edit'}</button>
                      </div>
                      <div className="flex flex-wrap gap-1 ml-28">
                        {nonRoleLabels.map(([k, v]) => (
                          <span key={k} className="group flex items-center gap-1 px-1.5 py-0.5 bg-slate-800 text-slate-400 rounded text-[9px] font-mono">
                            {k}={v}
                            {editingLabels === node.name && (
                              <button onClick={() => handleRemoveLabel(node.name, k)} className="text-red-400 hover:text-red-300 ml-0.5">×</button>
                            )}
                          </span>
                        ))}
                        {nonRoleLabels.length === 0 && <span className="text-slate-600 text-[10px]">(none)</span>}
                      </div>
                      {editingLabels === node.name && (
                        <div className="flex items-center gap-2 mt-2 ml-28">
                          <input
                            value={newLabelKey} onChange={e => setNewLabelKey(e.target.value)}
                            placeholder="key" onKeyDown={e => e.key === 'Enter' && handleAddLabel(node.name)}
                            className="w-28 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-[10px] text-slate-200 font-mono focus:outline-none focus:border-blue-500"
                          />
                          <span className="text-slate-600">=</span>
                          <input
                            value={newLabelVal} onChange={e => setNewLabelVal(e.target.value)}
                            placeholder="value" onKeyDown={e => e.key === 'Enter' && handleAddLabel(node.name)}
                            className="w-28 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-[10px] text-slate-200 font-mono focus:outline-none focus:border-blue-500"
                          />
                          <button onClick={() => handleAddLabel(node.name)} className="px-2 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-[10px] flex items-center gap-1">
                            <Plus size={10} /> Add
                          </button>
                        </div>
                      )}
                    </div>

                    {/* Taints editor */}
                    <div className="text-xs">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-slate-500 w-28 flex-shrink-0">Taints</span>
                        <button
                          onClick={() => setEditingTaints(editingTaints === node.name ? null : node.name)}
                          className="text-[10px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300"
                        >{editingTaints === node.name ? 'Done' : 'Manage'}</button>
                      </div>
                      <div className="flex flex-wrap gap-1 ml-28">
                        {(node.taints || []).map((t, i) => (
                          <span key={i} className="flex items-center gap-1 px-2 py-0.5 bg-orange-900/30 text-orange-400 text-[10px] rounded-full">
                            {t.key}{t.value ? `=${t.value}` : ''}:{t.effect}
                            {editingTaints === node.name && (
                              <button onClick={() => handleTaintAction(node.name, 'remove', t)} className="text-red-400 hover:text-red-300 ml-0.5">×</button>
                            )}
                          </span>
                        ))}
                        {(!node.taints || node.taints.length === 0) && <span className="text-slate-600 text-[10px]">(none)</span>}
                      </div>
                      {editingTaints === node.name && (
                        <div className="flex items-center gap-2 mt-2 ml-28 flex-wrap">
                          <input
                            value={newTaintKey} onChange={e => setNewTaintKey(e.target.value)}
                            placeholder="key"
                            className="w-28 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-[10px] text-slate-200 font-mono focus:outline-none focus:border-blue-500"
                          />
                          <select
                            value={newTaintEffect} onChange={e => setNewTaintEffect(e.target.value as typeof newTaintEffect)}
                            className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-[10px] text-slate-200 focus:outline-none"
                          >
                            <option>NoSchedule</option>
                            <option>PreferNoSchedule</option>
                            <option>NoExecute</option>
                          </select>
                          <input
                            value={newTaintVal} onChange={e => setNewTaintVal(e.target.value)}
                            placeholder="value (opt)"
                            className="w-24 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-[10px] text-slate-200 font-mono focus:outline-none focus:border-blue-500"
                          />
                          <button
                            onClick={() => handleTaintAction(node.name, 'add', { key: newTaintKey.trim(), effect: newTaintEffect, ...(newTaintVal.trim() ? { value: newTaintVal.trim() } : {}) })}
                            disabled={!newTaintKey.trim()}
                            className="px-2 py-1 bg-orange-700 hover:bg-orange-600 disabled:opacity-40 text-white rounded text-[10px] flex items-center gap-1"
                          >
                            <Plus size={10} /> Add Taint
                          </button>
                        </div>
                      )}
                    </div>

                    <div className="text-xs text-slate-600">Added {_relTime(node.created_at)}</div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

// ── Namespace Wizard Modal ─────────────────────────────────────────────────────

const QUOTA_PRESETS = {
  none:   null,
  small:  { label: 'Small',  desc: '4 CPU / 8Gi / 20 pods' },
  medium: { label: 'Medium', desc: '16 CPU / 32Gi / 50 pods' },
  large:  { label: 'Large',  desc: '64 CPU / 128Gi / 200 pods' },
} as const
type QuotaPreset = keyof typeof QUOTA_PRESETS

const LIMIT_PRESETS = {
  none:   null,
  small:  { label: 'Small',  desc: 'req 100m/128Mi · limit 500m/512Mi' },
  medium: { label: 'Medium', desc: 'req 250m/256Mi · limit 2/2Gi' },
  large:  { label: 'Large',  desc: 'req 500m/512Mi · limit 4/8Gi' },
} as const
type LimitPreset = keyof typeof LIMIT_PRESETS

function CreateNamespaceWizard({ clusterId, onClose, onCreated }: {
  clusterId: string; onClose: () => void; onCreated: () => void
}) {
  const toast = useToast()
  const [nsName, setNsName] = useState('')
  const [labelKey, setLabelKey] = useState('')
  const [labelVal, setLabelVal] = useState('')
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [quotaPreset, setQuotaPreset] = useState<QuotaPreset>('none')
  const [limitsPreset, setLimitsPreset] = useState<LimitPreset>('none')
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  async function handleCreate() {
    if (!nsName.trim()) { toast.error('Namespace name is required'); return }
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/namespaces`,
        { name: nsName.trim(), quota_preset: quotaPreset === 'none' ? '' : quotaPreset,
          limits_preset: limitsPreset === 'none' ? '' : limitsPreset, labels }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/namespaces?token=${token}`,
            { name: nsName.trim(), quota_preset: quotaPreset === 'none' ? '' : quotaPreset,
              limits_preset: limitsPreset === 'none' ? '' : limitsPreset, labels })
          toast.success(`Namespace ${nsName} created`); onCreated()
        }})
      }
    } catch (e) { toast.error(`Create failed: ${e}`) }
  }

  return (
    <>
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
        <div className="bg-[#0d1117] border border-slate-700/60 rounded-2xl w-full max-w-lg max-h-[90vh] flex flex-col shadow-2xl">
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Box size={14} className="text-blue-400" /> Create Namespace
            </h3>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400"><X size={14} /></button>
          </div>
          <div className="flex-1 overflow-y-auto p-5 space-y-5">
            {/* Name */}
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1.5">Namespace Name</label>
              <input value={nsName} onChange={e => setNsName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                placeholder="my-namespace"
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-sm font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
              <p className="text-[10px] text-slate-600 mt-1">Lowercase letters, numbers, and hyphens only</p>
            </div>

            {/* Labels */}
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1.5">Labels (optional)</label>
              {Object.entries(labels).length > 0 && (
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {Object.entries(labels).map(([k, v]) => (
                    <span key={k} className="flex items-center gap-1 px-2 py-0.5 rounded bg-blue-900/30 border border-blue-700/30 text-blue-300 text-[10px] font-mono">
                      {k}={v}
                      <button onClick={() => setLabels(prev => { const n = { ...prev }; delete n[k]; return n })}
                        className="text-blue-500 hover:text-red-400 ml-0.5"><X size={8} /></button>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                <input value={labelKey} onChange={e => setLabelKey(e.target.value)} placeholder="key"
                  className="w-24 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                <span className="text-slate-600 text-xs self-center">=</span>
                <input value={labelVal} onChange={e => setLabelVal(e.target.value)} placeholder="value"
                  className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                <button onClick={() => { if (labelKey.trim()) { setLabels(prev => ({ ...prev, [labelKey]: labelVal })); setLabelKey(''); setLabelVal('') } }}
                  className="p-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors">
                  <Plus size={12} />
                </button>
              </div>
            </div>

            {/* ResourceQuota */}
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1.5">ResourceQuota</label>
              <p className="text-[10px] text-slate-600 mb-2">Like OpenShift project quotas — limits total resource consumption in this namespace</p>
              <div className="grid grid-cols-2 gap-2">
                {(Object.entries(QUOTA_PRESETS) as [QuotaPreset, typeof QUOTA_PRESETS[QuotaPreset]][]).map(([key, meta]) => (
                  <label key={key} className={`flex items-start gap-2 p-2.5 rounded-lg border cursor-pointer transition-colors ${quotaPreset === key ? 'border-blue-500/50 bg-blue-500/5' : 'border-slate-700/50 hover:border-slate-600'}`}>
                    <input type="radio" name="qp" value={key} checked={quotaPreset === key} onChange={() => setQuotaPreset(key)} className="mt-0.5 w-3 h-3" />
                    <div>
                      <p className="text-xs font-medium text-slate-200">{meta ? meta.label : 'None'}</p>
                      {meta && <p className="text-[10px] text-slate-500 mt-0.5">{meta.desc}</p>}
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* LimitRange */}
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1.5">Default Container Limits</label>
              <p className="text-[10px] text-slate-600 mb-2">LimitRange sets default CPU/memory requests + limits for containers that don't specify them</p>
              <div className="grid grid-cols-2 gap-2">
                {(Object.entries(LIMIT_PRESETS) as [LimitPreset, typeof LIMIT_PRESETS[LimitPreset]][]).map(([key, meta]) => (
                  <label key={key} className={`flex items-start gap-2 p-2.5 rounded-lg border cursor-pointer transition-colors ${limitsPreset === key ? 'border-emerald-500/50 bg-emerald-500/5' : 'border-slate-700/50 hover:border-slate-600'}`}>
                    <input type="radio" name="lp" value={key} checked={limitsPreset === key} onChange={() => setLimitsPreset(key)} className="mt-0.5 w-3 h-3" />
                    <div>
                      <p className="text-xs font-medium text-slate-200">{meta ? meta.label : 'None'}</p>
                      {meta && <p className="text-[10px] text-slate-500 mt-0.5">{meta.desc}</p>}
                    </div>
                  </label>
                ))}
              </div>
            </div>
          </div>
          <div className="p-5 border-t border-slate-800">
            <button onClick={handleCreate}
              className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors font-medium">
              Create Namespace
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

// ── Namespaces Section ────────────────────────────────────────────────────────

type NsQuota = { name: string; hard: Record<string, string>; used: Record<string, string> }
type NsInfo = { name: string; phase: string; is_system: boolean; pod_count: number; req_cpu_m?: number; req_mem_mib?: number; quotas: NsQuota[] }

function QuotaBar({ label, used, hard }: { label: string; used: string; hard: string }) {
  const parse = (v: string) => {
    if (!v) return 0
    if (v.endsWith('m')) return parseFloat(v) / 1000
    if (v.endsWith('Ki')) return parseFloat(v) * 1024
    if (v.endsWith('Mi')) return parseFloat(v) * 1024 * 1024
    if (v.endsWith('Gi')) return parseFloat(v) * 1024 * 1024 * 1024
    return parseFloat(v) || 0
  }
  const usedN = parse(used)
  const hardN = parse(hard)
  const pct = hardN > 0 ? Math.min(100, Math.round(usedN / hardN * 100)) : 0
  const color = pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-blue-500'
  return (
    <div className="text-[10px] text-slate-400">
      <div className="flex justify-between mb-0.5">
        <span>{label}</span>
        <span>{used}/{hard} ({pct}%)</span>
      </div>
      <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function NamespacesSection({ clusterId, onSelectNamespace }: { clusterId: string; onSelectNamespace?: (ns: string) => void }) {
  const [namespaces, setNamespaces] = useState<NsInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [showSystem, setShowSystem] = useState(false)
  const [showWizard, setShowWizard] = useState(false)
  const [cloning, setCloning] = useState<string | null>(null)
  const [cloneTarget, setCloneTarget] = useState('')
  const [cloneTypes, setCloneTypes] = useState<string[]>(['configmaps'])
  const [cloneLoading, setCloneLoading] = useState(false)
  const toast = useToast()

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ namespaces: typeof namespaces }>(`${clusterId}/namespaces`)
      .then(d => { setNamespaces(d.namespaces); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId])

  useEffect(() => { load() }, [load])

  async function deleteNs(name: string) {
    try {
      const resp = await vksDelete<ConfirmState & { requires_confirm?: boolean }>(`${clusterId}/namespaces/${name}`)
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await fetch(`${API}/api/v1/vks/${clusterId}/namespaces/${name}?token=${token}`, { method: 'DELETE' })
          toast.success(`Deleted namespace ${name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  async function handleClone(srcNs: string) {
    if (!cloneTarget.trim()) { toast.error('Target namespace required'); return }
    setCloneLoading(true)
    try {
      const init = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/namespaces/${srcNs}/clone`,
        { target_namespace: cloneTarget.trim(), resource_types: cloneTypes }
      )
      if (init.requires_confirm) {
        setConfirm({ ...init, onConfirm: async (token) => {
          const res = await vksPost<{ ok: boolean; cloned: string[]; errors: string[] }>(
            `${clusterId}/namespaces/${srcNs}/clone?token=${token}`,
            { target_namespace: cloneTarget.trim(), resource_types: cloneTypes }
          )
          toast.success(`Cloned ${res.cloned.length} resources to ${cloneTarget}`)
          if (res.errors.length) toast.error(`${res.errors.length} errors during clone`)
          setCloning(null); setCloneTarget('')
          load()
        }})
      }
    } catch (e) { toast.error(`Clone failed: ${e}`) } finally { setCloneLoading(false) }
  }

  const visible = showSystem ? namespaces : namespaces.filter(n => !n.is_system)

  return (
    <div className="space-y-4">
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {showWizard && (
        <CreateNamespaceWizard
          clusterId={clusterId}
          onClose={() => setShowWizard(false)}
          onCreated={() => { setShowWizard(false); load() }}
        />
      )}
      {cloning && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={e => e.target === e.currentTarget && setCloning(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-md mx-4 p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Layers size={15} className="text-blue-400" />
              <span className="text-white font-medium text-sm">Clone namespace: <span className="font-mono text-blue-300">{cloning}</span></span>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-slate-400 block mb-1">Target namespace (will be created if absent)</label>
                <input value={cloneTarget} onChange={e => setCloneTarget(e.target.value)} placeholder="new-namespace-name"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="text-xs text-slate-400 block mb-1">Resources to clone</label>
                <div className="flex gap-3">
                  {['configmaps', 'secrets'].map(t => (
                    <label key={t} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                      <input type="checkbox" checked={cloneTypes.includes(t)}
                        onChange={e => setCloneTypes((prev: string[]) => e.target.checked ? [...prev, t] : prev.filter((x: string) => x !== t))}
                        className="rounded" />
                      {t}
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button onClick={() => setCloning(null)} className="px-4 py-2 text-xs text-slate-400 hover:text-white rounded-lg">Cancel</button>
              <button onClick={() => handleClone(cloning!)} disabled={cloneLoading || !cloneTarget.trim()}
                className="px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg flex items-center gap-2">
                {cloneLoading && <Loader2 size={11} className="animate-spin" />}
                Clone
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer">
          <input type="checkbox" checked={showSystem} onChange={e => setShowSystem(e.target.checked)} className="rounded" />
          Show system namespaces
        </label>
        <div className="ml-auto flex gap-2">
          <button onClick={() => setShowWizard(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg">
            <Plus size={12} /> Create Namespace
          </button>
          <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={6} /> : !loadErr ? (
        <div className="grid gap-2">
          {visible.map(ns => (
            <div key={ns.name} className="bg-slate-900/60 border border-slate-700/50 rounded-xl px-4 py-3 space-y-2">
              <div className="flex items-center gap-3">
                <div className={`w-2 h-2 rounded-full ${ns.phase === 'Active' ? 'bg-emerald-400' : 'bg-red-400'}`} />
                <button
                  onClick={() => onSelectNamespace?.(ns.name)}
                  className={`font-mono text-white text-sm flex-1 text-left ${onSelectNamespace ? 'hover:text-blue-400 transition-colors' : ''}`}
                  title={onSelectNamespace ? `Filter by namespace ${ns.name}` : undefined}
                >{ns.name}</button>
                {ns.is_system && <span className="text-[10px] px-1.5 py-0.5 bg-slate-800 text-slate-500 rounded">system</span>}
                <span className="text-xs text-slate-400">{ns.pod_count} pods</span>
                {ns.req_cpu_m !== undefined && ns.req_cpu_m > 0 && (
                  <span className="text-[10px] text-slate-500 font-mono">{ns.req_cpu_m >= 1000 ? `${(ns.req_cpu_m/1000).toFixed(1)}` : `${ns.req_cpu_m}m`} CPU</span>
                )}
                {ns.req_mem_mib !== undefined && ns.req_mem_mib > 0 && (
                  <span className="text-[10px] text-slate-500 font-mono">{ns.req_mem_mib >= 1024 ? `${(ns.req_mem_mib/1024).toFixed(1)}Gi` : `${ns.req_mem_mib}Mi`} mem</span>
                )}
                {!ns.is_system && <ActionBtn icon={Trash2} title="Delete" danger onClick={() => deleteNs(ns.name)} />}
                {!ns.is_system && (
                  <button onClick={() => { setCloning(ns.name); setCloneTarget(''); setCloneTypes(['configmaps']) }}
                    className="text-[10px] px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors">
                    Clone
                  </button>
                )}
              </div>
              {ns.quotas && ns.quotas.length > 0 && (
                <div className="grid gap-1.5 pl-4 pt-1 border-t border-slate-800/60">
                  {ns.quotas.map(q => (
                    <div key={q.name} className="grid gap-1">
                      {Object.entries(q.hard).map(([key, hardVal]) => (
                        <QuotaBar key={key} label={key} hard={hardVal} used={q.used[key] || '0'} />
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

// ── Quota Section ─────────────────────────────────────────────────────────────

interface QuotaInfo { name: string; namespace: string; created_at: string; hard: Record<string, string>; used: Record<string, string> }

const QUOTA_KEYS = [
  'requests.cpu', 'limits.cpu', 'requests.memory', 'limits.memory',
  'pods', 'services', 'persistentvolumeclaims', 'configmaps', 'secrets',
]

function QuotaSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [quotas, setQuotas] = useState<QuotaInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ quotas: QuotaInfo[] }>(`${clusterId}/quotas?namespace=${namespace}`)
      .then(d => { setQuotas(d.quotas); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  return (
    <div className="space-y-4">
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      <div className="flex justify-end">
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loading ? <SkeletonRows rows={4} /> : !loadErr && quotas.length === 0 ? (
        <EmptyState icon={MemoryStick} title="No resource quotas" hint={`No ResourceQuotas in ${namespace || 'any namespace'}`} />
      ) : !loadErr ? (
        <div className="space-y-4">
          {quotas.map(q => {
            const relevantKeys = QUOTA_KEYS.filter(k => q.hard[k] !== undefined)
            const extraKeys = Object.keys(q.hard).filter(k => !QUOTA_KEYS.includes(k))
            return (
              <div key={`${q.namespace}/${q.name}`} className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-800">
                  <MemoryStick size={13} className="text-slate-500" />
                  <span className="font-mono text-sm text-white">{q.name}</span>
                  {q.namespace && <span className="text-xs text-slate-500">in {q.namespace}</span>}
                  <span className="ml-auto text-[10px] text-slate-600">{_relTime(q.created_at)}</span>
                </div>
                <div className="p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {[...relevantKeys, ...extraKeys].map(k => (
                    q.hard[k] !== undefined ? (
                      <QuotaBar key={k} label={k} used={q.used[k] || '0'} hard={q.hard[k]} />
                    ) : null
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

// ── ConfigMap Section ─────────────────────────────────────────────────────────

interface CmInfo { name: string; namespace: string; created_at: string; key_count: number; keys: string[]; data: Record<string, string> }

function _syntaxHighlight(content: string, lang: 'json' | 'yaml' | 'text'): { html: string; lang: string } {
  const escape = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  if (lang === 'json') {
    try {
      const pretty = JSON.stringify(JSON.parse(content), null, 2)
      const html = escape(pretty).replace(
        /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(\.\d+)?([eE][+-]?\d+)?)/g,
        m => {
          if (/^"/.test(m)) {
            if (/:$/.test(m)) return `<span style="color:#7dd3fc">${m}</span>`
            return `<span style="color:#86efac">${m}</span>`
          }
          if (/true|false/.test(m)) return `<span style="color:#f9a8d4">${m}</span>`
          if (/null/.test(m)) return `<span style="color:#94a3b8">${m}</span>`
          return `<span style="color:#fde68a">${m}</span>`
        }
      )
      return { html, lang: 'json' }
    } catch { /* fall through */ }
  }
  if (lang === 'yaml') {
    const html = escape(content).replace(/(^[^#\s][^:]*(?=:))/gm, '<span style="color:#7dd3fc">$1</span>')
      .replace(/(#.*)$/gm, '<span style="color:#64748b">$1</span>')
      .replace(/(:\s*)(\||\>)$/gm, '$1<span style="color:#fb923c">$2</span>')
    return { html, lang: 'yaml' }
  }
  return { html: escape(content), lang: 'text' }
}

function _detectLang(content: string): 'json' | 'yaml' | 'text' {
  const s = content.trim()
  if (s.startsWith('{') || s.startsWith('[')) {
    try { JSON.parse(s); return 'json' } catch { /* not json */ }
  }
  if (s.startsWith('---') || /^\w[^:]*:\s/m.test(s)) return 'yaml'
  return 'text'
}

function CmViewerModal({ cm, onClose }: { cm: { name: string; namespace: string; data: Record<string, string> }; onClose: () => void }) {
  const keys = Object.keys(cm.data)
  const [selectedKey, setSelectedKey] = useState(keys[0] ?? '')
  const content = cm.data[selectedKey] ?? ''
  const lang = _detectLang(content)
  const { html } = _syntaxHighlight(content, lang)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl flex flex-col" style={{ maxHeight: '80vh' }}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700 shrink-0">
          <div className="flex items-center gap-2">
            <Settings2 size={14} className="text-blue-400" />
            <span className="text-sm font-semibold text-white font-mono">{cm.name}</span>
            <span className="text-xs text-slate-500">{cm.namespace}</span>
          </div>
          <button onClick={onClose} className="p-1 text-slate-400 hover:text-white rounded"><X size={14} /></button>
        </div>
        {keys.length > 1 && (
          <div className="flex overflow-x-auto border-b border-slate-700 shrink-0 px-2">
            {keys.map(k => (
              <button key={k} onClick={() => setSelectedKey(k)}
                className={`px-3 py-2 text-xs font-mono whitespace-nowrap border-b-2 transition-colors ${selectedKey === k ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-400 hover:text-white'}`}>
                {k}
              </button>
            ))}
          </div>
        )}
        <div className="flex-1 min-h-0 overflow-auto p-4">
          {content ? (
            <pre className="text-[11px] leading-5 font-mono text-slate-300 whitespace-pre-wrap break-words"
              dangerouslySetInnerHTML={{ __html: html }} />
          ) : (
            <p className="text-slate-500 text-sm text-center py-8 italic">empty value</p>
          )}
        </div>
        <div className="px-4 py-2 border-t border-slate-700 shrink-0 flex items-center justify-between">
          <span className="text-[10px] text-slate-600 font-mono">{lang} · {content.length} chars</span>
          <button onClick={onClose} className="px-3 py-1 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg">Close</button>
        </div>
      </div>
    </div>
  )
}

function ConfigMapSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [configmaps, setConfigmaps] = useState<CmInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [editing, setEditing] = useState<CmInfo | null>(null)
  const [editData, setEditData] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [viewTarget, setViewTarget] = useState<CmInfo | null>(null)
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [filterText, setFilterText] = useState('')
  const { sortKey, sortDir, onSort } = useSort()
  const toast = useToast()

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ configmaps: CmInfo[] }>(`${clusterId}/configmaps?namespace=${namespace}`)
      .then(d => { setConfigmaps(d.configmaps); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  const visible = useSortedFiltered(
    filterText ? configmaps.filter(cm => cm.name.includes(filterText) || cm.namespace.includes(filterText) || cm.keys.some(k => k.includes(filterText))) : configmaps,
    '', [], sortKey, sortDir
  )

  useEffect(() => { load() }, [load])

  function startEdit(cm: CmInfo) {
    setEditing(cm)
    setEditData({ ...cm.data })
  }

  async function handleDelete(cm: CmInfo) {
    try {
      const resp = await fetch(`${API}/api/v1/vks/${clusterId}/configmaps/${encodeURIComponent(cm.name)}?namespace=${cm.namespace}`, { method: 'DELETE' })
      if (!resp.ok) throw await _parseError(resp)
      const body = await resp.json() as ConfirmState & { requires_confirm?: boolean }
      if (body.requires_confirm) {
        setConfirm({ ...body, onConfirm: async (token) => {
          const r = await fetch(`${API}/api/v1/vks/${clusterId}/configmaps/${encodeURIComponent(cm.name)}?namespace=${cm.namespace}&token=${token}`, { method: 'DELETE' })
          if (!r.ok) throw await _parseError(r)
          toast.success(`Deleted ConfigMap ${cm.name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  async function saveEdit() {
    if (!editing) return
    setSaving(true)
    try {
      await vksPut(`${clusterId}/configmaps/${editing.name}?namespace=${editing.namespace}`, { data: editData })
      toast.success(`ConfigMap ${editing.name} updated`)
      setEditing(null); load()
    } catch (e) { toast.error(`Update failed: ${e}`) }
    finally { setSaving(false) }
  }

  return (
    <div className="space-y-4">
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {viewTarget && <CmViewerModal cm={viewTarget} onClose={() => setViewTarget(null)} />}
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setEditing(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
              <Settings2 size={14} className="text-slate-400" />
              <span className="text-sm font-medium text-white font-mono">{editing.name}</span>
              <div className="ml-auto flex gap-2">
                <button onClick={saveEdit} disabled={saving}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50">
                  {saving ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} Save
                </button>
                <button onClick={() => setEditing(null)} className="p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {Object.entries(editData).map(([k, v]) => (
                <div key={k} className="grid gap-1">
                  <label className="text-[10px] text-slate-500 font-mono">{k}</label>
                  <textarea value={v} onChange={e => setEditData(prev => ({ ...prev, [k]: e.target.value }))}
                    className="bg-slate-800 border border-slate-700 rounded px-3 py-2 font-mono text-xs text-slate-200 resize-y min-h-[60px] focus:outline-none focus:border-blue-500"
                    rows={Math.min(8, v.split('\n').length + 1)} />
                </div>
              ))}
              {Object.keys(editData).length === 0 && (
                <p className="text-slate-500 text-sm text-center py-4">No data keys</p>
              )}
            </div>
          </div>
        </div>
      )}
      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Filter by name, namespace, or key…"
          value={filterText}
          onChange={e => setFilterText(e.target.value)}
          className="flex-1 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loading ? <SkeletonRows rows={5} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Settings2} title="No ConfigMaps" hint={`No ConfigMaps in ${namespace || 'any namespace'}`} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <SortableHeader label="Name" col="name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                {!namespace && <SortableHeader label="Namespace" col="namespace" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />}
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Keys</th>
                <SortableHeader label="Age" col="created_at" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(cm => (
                <tr key={`${cm.namespace}/${cm.name}`} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                  <td className="px-4 py-3 font-mono text-white text-sm">{cm.name}</td>
                  {!namespace && <td className="px-4 py-3 text-slate-400 text-xs">{cm.namespace}</td>}
                  <td className="px-4 py-3 text-xs text-slate-400">
                    <div className="flex flex-wrap gap-1 max-w-xs">
                      {cm.keys.slice(0, 5).map(k => (
                        <span key={k} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-slate-800 text-slate-400 rounded text-[10px] font-mono">
                          {k}<CopyButton text={k} />
                        </span>
                      ))}
                      {cm.keys.length > 5 && <span className="text-slate-500 text-[10px]">+{cm.keys.length - 5}</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">{_relTime(cm.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      <ActionBtn icon={Eye} title="View data" onClick={() => setViewTarget(cm)} />
                      <ActionBtn icon={Settings2} title="Edit" onClick={() => startEdit(cm)} />
                      <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'configmaps', name: cm.name, namespace: cm.namespace })} />
                      <ActionBtn icon={Trash2} title="Delete" danger onClick={() => handleDelete(cm)} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Service Detail Panel ──────────────────────────────────────────────────────

function ServiceDetailPanel({ svc, clusterId, onClose, onRefresh }: {
  svc: ServiceInfo
  clusterId: string
  onClose: () => void
  onRefresh: () => void
}) {
  const toast = useToast()
  const [tab, setTab] = useState<'endpoints' | 'edit' | 'expose'>('endpoints')

  // Endpoints tab
  interface EndpointAddr { ip: string; node: string; target: string; ports: string[] }
  const [eps, setEps] = useState<{ ready: EndpointAddr[]; not_ready: EndpointAddr[] } | null>(null)
  const [epsLoading, setEpsLoading] = useState(false)

  useEffect(() => {
    if (tab !== 'endpoints') return
    setEpsLoading(true)
    vksGet<{ ready: EndpointAddr[]; not_ready: EndpointAddr[] }>(
      `${clusterId}/services/${svc.name}/endpoints?namespace=${svc.namespace}`
    ).then(setEps).catch(() => setEps({ ready: [], not_ready: [] })).finally(() => setEpsLoading(false))
  }, [tab, svc.name, svc.namespace, clusterId])

  // Edit tab
  const [svcType, setSvcType] = useState(svc.type)
  const [ports, setPorts] = useState(svc.ports.map(p => ({
    port: String(p.port),
    targetPort: String(p.targetPort),
    protocol: p.protocol || 'TCP',
    nodePort: p.nodePort ? String(p.nodePort) : '',
  })))
  const [editConfirm, setEditConfirm] = useState<ConfirmState | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<ConfirmState | null>(null)

  async function handleEditSave() {
    const patchPorts = ports.map(p => ({
      port: parseInt(p.port) || 0,
      targetPort: isNaN(Number(p.targetPort)) ? p.targetPort : parseInt(p.targetPort),
      protocol: p.protocol,
      ...(p.nodePort ? { nodePort: parseInt(p.nodePort) } : {}),
    }))
    try {
      const resp = await vksPatch<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/services/${svc.name}?namespace=${svc.namespace}`,
        { type: svcType, ports: patchPorts }
      )
      if (resp.requires_confirm) {
        setEditConfirm({ ...resp, onConfirm: async (token) => {
          await vksPatch(`${clusterId}/services/${svc.name}?namespace=${svc.namespace}&token=${token}`,
            { type: svcType, ports: patchPorts })
          toast.success('Service updated'); onRefresh()
        }})
      }
    } catch (e) { toast.error(`Update failed: ${e}`) }
  }

  async function handleDelete() {
    try {
      const resp = await vksDelete<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/services/${svc.name}?namespace=${svc.namespace}`
      )
      if (resp.requires_confirm) {
        setDeleteConfirm({ ...resp, onConfirm: async (token) => {
          await vksDelete(`${clusterId}/services/${svc.name}?namespace=${svc.namespace}&token=${token}`)
          toast.success(`Service ${svc.name} deleted`); onRefresh(); onClose()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  // Expose (create ingress) tab
  const [ingName, setIngName] = useState(`${svc.name}-ingress`)
  const [ingHost, setIngHost] = useState('')
  const [ingPath, setIngPath] = useState('/')
  const [ingPathType, setIngPathType] = useState('Prefix')
  const [ingClass, setIngClass] = useState('')
  const [ingTLS, setIngTLS] = useState(false)
  const [ingPort, setIngPort] = useState(String(svc.ports[0]?.port ?? 80))
  const [exposeConfirm, setExposeConfirm] = useState<ConfirmState | null>(null)

  async function handleExpose() {
    if (!ingHost.trim()) { toast.error('Host is required'); return }
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/ingresses?namespace=${svc.namespace}`,
        { name: ingName, host: ingHost, service_name: svc.name, service_port: parseInt(ingPort),
          path: ingPath, path_type: ingPathType, ingress_class: ingClass, tls: ingTLS }
      )
      if (resp.requires_confirm) {
        setExposeConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/ingresses?namespace=${svc.namespace}&token=${token}`,
            { name: ingName, host: ingHost, service_name: svc.name, service_port: parseInt(ingPort),
              path: ingPath, path_type: ingPathType, ingress_class: ingClass, tls: ingTLS })
          toast.success(`Ingress ${ingName} created`); onRefresh()
        }})
      }
    } catch (e) { toast.error(`Expose failed: ${e}`) }
  }

  const typeColors: Record<string, string> = {
    ClusterIP: 'text-blue-400', NodePort: 'text-purple-400',
    LoadBalancer: 'text-emerald-400', ExternalName: 'text-orange-400'
  }

  return (
    <>
      {editConfirm   && <ConfirmDialog state={editConfirm}   onDone={() => setEditConfirm(null)} />}
      {deleteConfirm && <ConfirmDialog state={deleteConfirm} onDone={() => setDeleteConfirm(null)} />}
      {exposeConfirm && <ConfirmDialog state={exposeConfirm} onDone={() => setExposeConfirm(null)} />}
      <div className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px]" onClick={onClose} />
      <div className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-[540px] bg-[#0d1117] border-l border-slate-700/60 shadow-2xl flex flex-col">

        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-slate-800 flex-shrink-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <Globe size={12} className="text-slate-500 flex-shrink-0" />
              <span className="font-mono text-sm font-bold text-white truncate">{svc.name}</span>
              <span className={`text-xs font-medium ${typeColors[svc.type] ?? 'text-slate-400'}`}>{svc.type}</span>
            </div>
            <div className="flex items-center gap-2 mt-0.5 text-[11px]">
              <span className="text-slate-500">{svc.namespace}</span>
              {svc.cluster_ip && <><span className="text-slate-700">·</span><span className="font-mono text-slate-500">{svc.cluster_ip}</span></>}
              {svc.external_ips?.length > 0 && <><span className="text-slate-700">·</span><span className="font-mono text-emerald-400">{svc.external_ips[0]}</span></>}
            </div>
          </div>
          <button onClick={handleDelete} className="p-1.5 rounded-lg hover:bg-red-900/30 text-slate-600 hover:text-red-400 transition-colors" title="Delete service">
            <Trash2 size={14} />
          </button>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors">
            <X size={15} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-800 flex-shrink-0">
          {[{ id: 'endpoints', label: 'Endpoints' }, { id: 'edit', label: 'Edit' }, { id: 'expose', label: '+ Expose (Ingress)' }].map(t => (
            <button key={t.id} onClick={() => setTab(t.id as typeof tab)}
              className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors whitespace-nowrap ${
                tab === t.id ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}>
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">

          {/* Endpoints */}
          {tab === 'endpoints' && (
            <div className="p-5 space-y-4">
              <div>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Ports</p>
                <div className="flex flex-wrap gap-1.5">
                  {svc.ports.map((p, i) => (
                    <span key={i} className="font-mono text-xs px-2 py-1 rounded bg-slate-800 text-slate-300 border border-slate-700/50">
                      {p.port}{p.nodePort ? `:${p.nodePort}` : ''}→{p.targetPort}/{p.protocol}
                    </span>
                  ))}
                </div>
              </div>
              {Object.entries(svc.selector).length > 0 && (
                <div>
                  <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Selector</p>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(svc.selector).map(([k, v]) => (
                      <span key={k} className="font-mono text-[10px] px-2 py-0.5 rounded bg-blue-900/30 text-blue-300 border border-blue-700/30">{k}={v}</span>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Backing Pods</p>
                {epsLoading ? <SkeletonRows rows={2} /> : !eps ? null : eps.ready.length === 0 && eps.not_ready.length === 0 ? (
                  <p className="text-xs text-slate-500 italic">No backing pods — check selector labels match pod labels</p>
                ) : (
                  <div className="space-y-3">
                    {eps.ready.length > 0 && (
                      <div>
                        <p className="text-[10px] text-emerald-500 mb-1">Ready ({eps.ready.length})</p>
                        <div className="flex flex-wrap gap-1.5">
                          {eps.ready.map((e, i) => (
                            <span key={i} className="font-mono text-[10px] px-2 py-0.5 rounded bg-emerald-900/30 text-emerald-300 border border-emerald-700/30">
                              {e.ip}{e.target ? ` (${e.target})` : ''}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {eps.not_ready.length > 0 && (
                      <div>
                        <p className="text-[10px] text-red-500 mb-1">Not Ready ({eps.not_ready.length})</p>
                        <div className="flex flex-wrap gap-1.5">
                          {eps.not_ready.map((e, i) => (
                            <span key={i} className="font-mono text-[10px] px-2 py-0.5 rounded bg-red-900/30 text-red-300 border border-red-700/30">
                              {e.ip}{e.target ? ` (${e.target})` : ''}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Edit */}
          {tab === 'edit' && (
            <div className="p-5 space-y-5">
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-2">Service Type</label>
                <div className="flex gap-2 flex-wrap">
                  {['ClusterIP', 'NodePort', 'LoadBalancer'].map(t => (
                    <button key={t} onClick={() => setSvcType(t)}
                      className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                        svcType === t
                          ? 'border-blue-500 bg-blue-600/20 text-blue-300'
                          : 'border-slate-700 text-slate-400 hover:border-slate-500'
                      }`}>
                      {t}
                    </button>
                  ))}
                </div>
                {svcType === 'LoadBalancer' && (
                  <p className="text-[10px] text-yellow-500 mt-1.5">⚠ LoadBalancer provisions an external load balancer — may incur cloud costs</p>
                )}
              </div>
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-2">Ports</label>
                <div className="space-y-2">
                  {ports.map((p, i) => (
                    <div key={i} className="grid grid-cols-4 gap-2 items-center">
                      {(['port', 'targetPort', 'protocol', 'nodePort'] as const).map(field => (
                        <div key={field}>
                          <label className="text-[9px] text-slate-600 block mb-0.5 capitalize">{field === 'nodePort' ? 'nodePort' : field}</label>
                          {field === 'protocol' ? (
                            <select value={p[field]} onChange={e => setPorts(prev => prev.map((r, j) => j === i ? { ...r, [field]: e.target.value } : r))}
                              className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-[11px] text-slate-200 outline-none focus:border-blue-500">
                              {['TCP', 'UDP', 'SCTP'].map(pr => <option key={pr}>{pr}</option>)}
                            </select>
                          ) : (
                            <input value={p[field]} onChange={e => setPorts(prev => prev.map((r, j) => j === i ? { ...r, [field]: e.target.value } : r))}
                              placeholder={field === 'nodePort' ? 'auto' : '—'}
                              className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-[11px] font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
              <button onClick={handleEditSave}
                className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors font-medium">
                Apply Changes
              </button>
            </div>
          )}

          {/* Expose via Ingress */}
          {tab === 'expose' && (
            <div className="p-5 space-y-4">
              <p className="text-xs text-slate-400">Create a Kubernetes Ingress to expose <span className="font-mono text-white">{svc.name}</span> via a hostname. Coming from OpenShift? This is the equivalent of an OpenShift Route.</p>
              <div className="space-y-3">
                {[
                  { label: 'Ingress Name', value: ingName, setter: setIngName, placeholder: 'my-app-ingress' },
                  { label: 'Hostname', value: ingHost, setter: setIngHost, placeholder: 'app.example.com' },
                  { label: 'Path', value: ingPath, setter: setIngPath, placeholder: '/' },
                  { label: 'Ingress Class', value: ingClass, setter: setIngClass, placeholder: 'nginx (leave blank for cluster default)' },
                ].map(({ label, value, setter, placeholder }) => (
                  <div key={label}>
                    <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">{label}</label>
                    <input value={value} onChange={e => setter(e.target.value)} placeholder={placeholder}
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                  </div>
                ))}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Backend Port</label>
                    <select value={ingPort} onChange={e => setIngPort(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 outline-none focus:border-blue-500">
                      {svc.ports.map(p => <option key={p.port} value={String(p.port)}>{p.port}/{p.protocol}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Path Type</label>
                    <select value={ingPathType} onChange={e => setIngPathType(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 outline-none focus:border-blue-500">
                      {['Prefix', 'Exact', 'ImplementationSpecific'].map(pt => <option key={pt}>{pt}</option>)}
                    </select>
                  </div>
                </div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={ingTLS} onChange={e => setIngTLS(e.target.checked)}
                    className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800" />
                  <span className="text-xs text-slate-300">Enable TLS (creates <span className="font-mono">{ingName}-tls</span> secret reference)</span>
                </label>
              </div>
              <button onClick={handleExpose}
                className="w-full py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg transition-colors font-medium">
                Create Ingress
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

// ── Services Section ─────────────────────────────────────────────────────────

interface ServiceInfo {
  name: string
  namespace: string
  type: string
  cluster_ip: string
  external_ips: string[]
  ports: { port: number; targetPort: string | number; protocol: string; nodePort?: number }[]
  selector: Record<string, string>
  created_at: string
}

interface EndpointAddr { ip: string; node: string; target: string; ports: string[] }

function ServicesSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [services, setServices] = useState<ServiceInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filter, setFilter] = useState('')
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [expandedSvc, setExpandedSvc] = useState<string | null>(null)
  const [endpointMap, setEndpointMap] = useState<Record<string, { ready: EndpointAddr[]; not_ready: EndpointAddr[] }>>({})
  const [detailSvc, setDetailSvc] = useState<ServiceInfo | null>(null)

  async function loadEndpoints(svc: ServiceInfo) {
    const key = `${svc.namespace}/${svc.name}`
    if (expandedSvc === key) { setExpandedSvc(null); return }
    setExpandedSvc(key)
    if (endpointMap[key]) return
    try {
      const d = await vksGet<{ ready: EndpointAddr[]; not_ready: EndpointAddr[] }>(
        `${clusterId}/services/${svc.name}/endpoints?namespace=${svc.namespace}`
      )
      setEndpointMap(m => ({ ...m, [key]: d }))
    } catch { setEndpointMap(m => ({ ...m, [key]: { ready: [], not_ready: [] } })) }
  }

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ services: ServiceInfo[] }>(`${clusterId}/services?namespace=${namespace}`)
      .then(d => { setServices(d.services); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  const visible = filter
    ? services.filter(s => s.name.includes(filter) || s.namespace.includes(filter) || s.cluster_ip.includes(filter))
    : services

  function svcTypeBadge(type: string) {
    const colors: Record<string, string> = {
      ClusterIP: 'bg-blue-900/40 text-blue-400',
      NodePort: 'bg-purple-900/40 text-purple-400',
      LoadBalancer: 'bg-emerald-900/40 text-emerald-400',
      ExternalName: 'bg-orange-900/40 text-orange-400',
    }
    return `px-1.5 py-0.5 rounded text-[10px] font-medium ${colors[type] ?? 'bg-slate-800 text-slate-400'}`
  }

  function formatPorts(ports: ServiceInfo['ports']) {
    if (!ports?.length) return '—'
    return ports.map(p => `${p.port}${p.nodePort ? ':' + p.nodePort : ''}/${p.protocol}`).join(', ')
  }

  return (
    <div className="space-y-4">
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {detailSvc && (
        <ServiceDetailPanel
          svc={detailSvc}
          clusterId={clusterId}
          onClose={() => setDetailSvc(null)}
          onRefresh={() => { setDetailSvc(null); load() }}
        />
      )}
      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Filter by name, namespace, or IP…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={5} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Globe} title="No services" hint={`No services in ${namespace || 'any namespace'}`} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Name</th>
                {!namespace && <th className="px-4 py-3 text-xs font-medium text-slate-400">Namespace</th>}
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Type</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Cluster IP</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Ports</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Age</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((svc, i) => {
                const key = `${svc.namespace}/${svc.name}`
                const isExpanded = expandedSvc === key
                const eps = endpointMap[key]
                const colSpan = namespace ? 6 : 7
                return (
                  <>
                    <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20 cursor-pointer" onClick={() => loadEndpoints(svc)}>
                      <td className="px-4 py-3 font-mono text-white">
                        <div className="flex items-center gap-2">
                          {isExpanded ? <ChevronDown size={11} className="text-slate-500" /> : <ChevronRight size={11} className="text-slate-500" />}
                          <Globe size={11} className="text-slate-500" />
                          <button onClick={e => { e.stopPropagation(); setDetailSvc(svc) }}
                            className="inline-flex items-center gap-1 hover:text-blue-400 transition-colors">{svc.name}</button>
                          <CopyButton text={svc.name} />
                          {svc.external_ips?.length > 0 && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] bg-emerald-900/30 text-emerald-400 border border-emerald-700/30">
                              {svc.external_ips[0]}
                            </span>
                          )}
                        </div>
                      </td>
                      {!namespace && <td className="px-4 py-3 text-slate-400">{svc.namespace}</td>}
                      <td className="px-4 py-3">
                        <span className={svcTypeBadge(svc.type)}>{svc.type}</span>
                      </td>
                      <td className="px-4 py-3 font-mono text-slate-300">{svc.cluster_ip || '—'}</td>
                      <td className="px-4 py-3 text-slate-400">{formatPorts(svc.ports)}</td>
                      <td className="px-4 py-3 text-slate-500 whitespace-nowrap">{_relTime(svc.created_at)}</td>
                      <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-1 justify-end">
                          <ActionBtn icon={Settings2} title="Edit / Expose" onClick={() => setDetailSvc(svc)} />
                          <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'services', name: svc.name, namespace: svc.namespace })} />
                        </div>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${i}-ep`} className="bg-slate-950/60 border-b border-slate-800/60">
                        <td colSpan={colSpan} className="px-6 py-3">
                          {!eps ? (
                            <span className="text-slate-500 text-xs">Loading endpoints…</span>
                          ) : eps.ready.length === 0 && eps.not_ready.length === 0 ? (
                            <span className="text-slate-500 text-xs italic">No endpoints (no matching pods or headless service)</span>
                          ) : (
                            <div className="space-y-2">
                              {eps.ready.length > 0 && (
                                <div>
                                  <span className="text-[10px] uppercase tracking-wider text-slate-400 mr-2">Ready</span>
                                  <div className="flex flex-wrap gap-1.5 mt-1">
                                    {eps.ready.map((e, ei) => (
                                      <span key={ei} className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-900/30 border border-emerald-700/30 text-emerald-300 font-mono text-[10px]">
                                        {e.ip}{e.target && <span className="text-emerald-500">({e.target})</span>}
                                      </span>
                                    ))}
                                  </div>
                                </div>
                              )}
                              {eps.not_ready.length > 0 && (
                                <div>
                                  <span className="text-[10px] uppercase tracking-wider text-slate-400 mr-2">Not Ready</span>
                                  <div className="flex flex-wrap gap-1.5 mt-1">
                                    {eps.not_ready.map((e, ei) => (
                                      <span key={ei} className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-red-900/30 border border-red-700/30 text-red-300 font-mono text-[10px]">
                                        {e.ip}{e.target && <span className="text-red-500">({e.target})</span>}
                                      </span>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Storage Section ──────────────────────────────────────────────────────────

interface PvcInfo {
  name: string; namespace: string; status: string; capacity: string
  access_modes: string[]; storage_class: string; volume_name: string; created_at: string
  used_pct?: number; used_bytes?: number; capacity_bytes?: number
}

interface PvInfo { name: string; namespace: string; created_at: string; labels: Record<string, string> }
interface ScInfo { name: string; namespace: string; created_at: string; labels: Record<string, string> }

function StorageSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [pvcs, setPvcs] = useState<PvcInfo[]>([])
  const [pvs, setPvs] = useState<PvInfo[]>([])
  const [scs, setScs] = useState<ScInfo[]>([])
  const [tab, setTab] = useState<'pvcs' | 'pvs' | 'sc'>('pvcs')
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filter, setFilter] = useState('')
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [resizePvc, setResizePvc] = useState<PvcInfo | null>(null)
  const [resizeVal, setResizeVal] = useState('')
  const [resizing, setResizing] = useState(false)
  const { sortKey, sortDir, onSort } = useSort()
  const toast = useToast()

  async function handleResize() {
    if (!resizePvc || !resizeVal.trim()) return
    setResizing(true)
    try {
      const r1 = await vksPatch<{ requires_confirm: boolean; token: string }>(
        `${clusterId}/pvcs/${resizePvc.name}?namespace=${resizePvc.namespace}`, { storage: resizeVal.trim() }
      )
      if (r1.requires_confirm) {
        const r2 = await vksPatch<{ ok: boolean }>(
          `${clusterId}/pvcs/${resizePvc.name}?namespace=${resizePvc.namespace}&token=${r1.token}`, { storage: resizeVal.trim() }
        )
        if (r2.ok) { toast.success(`PVC ${resizePvc.name} resize requested to ${resizeVal}`); setResizePvc(null); load() }
      }
    } catch (e) { toast.error(`Resize failed: ${e}`) }
    finally { setResizing(false) }
  }

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      vksGet<{ pvcs: PvcInfo[]; pvs: PvInfo[]; storageclasses: ScInfo[] }>(`${clusterId}/pvcs?namespace=${namespace}`),
      vksGet<{ usage: Record<string, { used_bytes: number; capacity_bytes: number }> }>(`${clusterId}/pvc-usage`).catch(() => ({ usage: {} as Record<string, { used_bytes: number; capacity_bytes: number }> })),
    ]).then(([d, usage]) => {
      const enriched = d.pvcs.map(pvc => {
        const key = `${pvc.namespace}/${pvc.name}`
        const u = usage.usage[key]
        if (u && u.capacity_bytes > 0) {
          return { ...pvc, used_bytes: u.used_bytes, capacity_bytes: u.capacity_bytes, used_pct: Math.round(u.used_bytes / u.capacity_bytes * 100) }
        }
        return pvc
      })
      setPvcs(enriched); setPvs(d.pvs || []); setScs(d.storageclasses || []); setLoadErr(null)
    })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  async function handleDelete(pvc: PvcInfo) {
    try {
      const resp = await fetch(`${API}/api/v1/vks/${clusterId}/pvcs/${encodeURIComponent(pvc.name)}?namespace=${pvc.namespace}`, { method: 'DELETE' })
      if (!resp.ok) throw await _parseError(resp)
      const body = await resp.json() as ConfirmState & { requires_confirm?: boolean }
      if (body.requires_confirm) {
        setConfirm({ ...body, onConfirm: async (token) => {
          const r = await fetch(`${API}/api/v1/vks/${clusterId}/pvcs/${encodeURIComponent(pvc.name)}?namespace=${pvc.namespace}&token=${token}`, { method: 'DELETE' })
          if (!r.ok) throw await _parseError(r)
          toast.success(`Deleted PVC ${pvc.name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  useEffect(() => { load() }, [load])

  const rawItems: (PvcInfo | PvInfo | ScInfo)[] =
    tab === 'pvcs'
      ? (filter ? pvcs.filter(p => p.name.includes(filter) || p.namespace.includes(filter) || p.storage_class.includes(filter)) : pvcs)
      : tab === 'pvs'
      ? (filter ? pvs.filter(p => p.name.includes(filter)) : pvs)
      : (filter ? scs.filter(s => s.name.includes(filter)) : scs)
  const visible = useSortedFiltered(rawItems as PvcInfo[], '', [], sortKey, sortDir)

  function statusBadge(s: string) {
    if (s === 'Bound') return 'bg-emerald-900/40 text-emerald-400'
    if (s === 'Pending') return 'bg-yellow-900/40 text-yellow-400'
    if (s === 'Lost') return 'bg-red-900/40 text-red-400'
    return 'bg-slate-800 text-slate-400'
  }

  return (
    <div className="space-y-4">
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {resizePvc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setResizePvc(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-sm shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
              <HardDrive size={14} className="text-blue-400" />
              <span className="text-sm font-medium text-white">Resize PVC — <span className="font-mono text-blue-400">{resizePvc.name}</span></span>
              <button onClick={() => setResizePvc(null)} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
            </div>
            <div className="p-5 space-y-3">
              <p className="text-xs text-slate-400">Current size: <span className="font-mono text-white">{resizePvc.capacity || 'unknown'}</span></p>
              <div>
                <label className="text-[10px] text-slate-500 mb-1 block">New size</label>
                <input value={resizeVal} onChange={e => setResizeVal(e.target.value)} placeholder="e.g. 50Gi"
                  className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-blue-500" />
              </div>
              <p className="text-[10px] text-slate-500">PVC resize requires the StorageClass to have <span className="font-mono">allowVolumeExpansion: true</span>. Volume can only be expanded, not shrunk.</p>
            </div>
            <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-800">
              <button onClick={() => setResizePvc(null)} className="px-4 py-2 text-xs text-slate-400 hover:text-white">Cancel</button>
              <button onClick={handleResize} disabled={resizing || !resizeVal.trim()}
                className="flex items-center gap-1.5 px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50">
                {resizing ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} Apply Resize
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex bg-slate-800 rounded-lg p-0.5">
          {([['pvcs', `PVCs (${pvcs.length})`], ['pvs', `PVs (${pvs.length})`], ['sc', `StorageClasses (${scs.length})`]] as const).map(([t, label]) => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-xs rounded-md transition-colors ${tab === t ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}>
              {label}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Filter…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 min-w-[120px] px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={5} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={HardDrive} title={`No ${tab === 'pvcs' ? 'PVCs' : tab === 'pvs' ? 'PVs' : 'StorageClasses'}`}
          hint={tab === 'pvcs' ? `No PVCs in ${namespace || 'any namespace'}` : `None found`} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          {tab === 'pvcs' && (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-left">
                  <SortableHeader label="Name" col="name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                  {!namespace && <SortableHeader label="Namespace" col="namespace" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />}
                  <SortableHeader label="Status" col="status" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                  <th className="px-4 py-3 text-xs font-medium text-slate-400">Capacity</th>
                  <th className="px-4 py-3 text-xs font-medium text-slate-400">Usage</th>
                  <SortableHeader label="StorageClass" col="storage_class" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                  <th className="px-4 py-3 text-xs font-medium text-slate-400">Access</th>
                  <SortableHeader label="Age" col="created_at" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                  <th className="px-4 py-3 text-xs font-medium text-slate-400 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {(visible as PvcInfo[]).map((pvc, i) => (
                  <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                    <td className="px-4 py-3 font-mono text-white">
                      <div className="flex items-center gap-2"><HardDrive size={11} className="text-slate-500" />{pvc.name}</div>
                    </td>
                    {!namespace && <td className="px-4 py-3 text-slate-400">{pvc.namespace}</td>}
                    <td className="px-4 py-3">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${statusBadge(pvc.status)}`}>{pvc.status || '—'}</span>
                    </td>
                    <td className="px-4 py-3 text-slate-300 font-mono">{pvc.capacity || '—'}</td>
                    <td className="px-4 py-3">
                      {pvc.used_pct != null ? (
                        <div className="flex items-center gap-1.5" title={`${pvc.used_bytes != null ? Math.round(pvc.used_bytes / 1048576) + ' MiB' : ''} used`}>
                          <div className="w-16 h-1.5 rounded-full bg-slate-700 overflow-hidden">
                            <div
                              className={`h-full rounded-full ${pvc.used_pct >= 85 ? 'bg-red-500' : pvc.used_pct >= 70 ? 'bg-amber-400' : 'bg-emerald-400'}`}
                              style={{ width: `${pvc.used_pct}%` }}
                            />
                          </div>
                          <span className={`text-[10px] tabular-nums ${pvc.used_pct >= 85 ? 'text-red-400' : pvc.used_pct >= 70 ? 'text-amber-400' : 'text-slate-400'}`}>
                            {pvc.used_pct}%
                          </span>
                        </div>
                      ) : <span className="text-slate-600 text-[10px]">—</span>}
                    </td>
                    <td className="px-4 py-3 text-slate-400">{pvc.storage_class || '—'}</td>
                    <td className="px-4 py-3 text-slate-500">{pvc.access_modes?.join(', ') || '—'}</td>
                    <td className="px-4 py-3 text-slate-500 whitespace-nowrap">{_relTime(pvc.created_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1 justify-end">
                        <ActionBtn icon={Pencil} title="Resize PVC" onClick={() => { setResizePvc(pvc); setResizeVal(pvc.capacity || '') }} />
                        <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'persistentvolumeclaims', name: pvc.name, namespace: pvc.namespace })} />
                        <ActionBtn icon={Trash2} title="Delete PVC" danger onClick={() => handleDelete(pvc)} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {(tab === 'pvs' || tab === 'sc') && (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-left">
                  <th className="px-4 py-3 text-xs font-medium text-slate-400">Name</th>
                  <th className="px-4 py-3 text-xs font-medium text-slate-400">Age</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item, i) => (
                  <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                    <td className="px-4 py-3 font-mono text-white">
                      <span className="inline-flex items-center gap-1">{item.name}<CopyButton text={item.name} /></span>
                    </td>
                    <td className="px-4 py-3 text-slate-500 whitespace-nowrap">{_relTime(item.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </div>
  )
}

// ── PVC Analysis Section ─────────────────────────────────────────────────────

interface PvcAnalysisItem {
  name: string; namespace: string; phase: string; access_modes: string[]
  storage_class: string; capacity: string; capacity_gib: number
  mounting_pods: string[]; mount_count: number; orphaned: boolean; issues: string[]
}

interface PvcAnalysisSummary {
  total: number; bound: number; pending: number; lost: number
  orphaned: number; multi_mount_rwo: number; total_capacity_gib: number
}

const PVC_ISSUE_META: Record<string, { label: string; color: string }> = {
  bound_not_mounted: { label: 'Orphaned',      color: 'bg-amber-900/40 text-amber-300 border border-amber-700/40' },
  rwo_multi_mount:   { label: 'RWO Multi-Mount', color: 'bg-red-900/40 text-red-300 border border-red-700/40' },
  pending:           { label: 'Pending',       color: 'bg-yellow-900/40 text-yellow-300 border border-yellow-700/40' },
  lost:              { label: 'Lost',          color: 'bg-red-900/50 text-red-300 border border-red-700/50' },
}

function PvcAnalysisSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [data, setData] = useState<{ pvcs: PvcAnalysisItem[]; summary: PvcAnalysisSummary } | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [showOnlyIssues, setShowOnlyIssues] = useState(false)

  const load = () => {
    setLoading(true); setErr(null)
    vksGet<{ pvcs: PvcAnalysisItem[]; summary: PvcAnalysisSummary }>(
      `${clusterId}/pvcs/analysis${namespace ? `?namespace=${namespace}` : ''}`
    ).then(d => { setData(d); setLoading(false) })
     .catch(e => { setErr(String(e)); setLoading(false) })
  }

  useEffect(load, [clusterId, namespace])

  if (loading) return <SkeletonRows rows={5} />
  if (err) return <SectionError error={err} onRetry={load} />
  if (!data) return null

  const { pvcs, summary } = data
  const visible = pvcs
    .filter(p => !showOnlyIssues || p.issues.length > 0)
    .filter(p => !filter || p.name.includes(filter) || p.namespace.includes(filter))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <HardDrive size={16} className="text-blue-400" />
          <span className="text-base font-semibold text-white">PVC Analysis</span>
        </div>
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {([
          { label: 'Total PVCs',     val: summary.total,                            color: 'text-white' },
          { label: 'Bound',          val: summary.bound,                            color: 'text-emerald-400' },
          { label: 'Pending',        val: summary.pending,                          color: summary.pending > 0 ? 'text-yellow-400' : 'text-slate-400' },
          { label: 'Orphaned',       val: summary.orphaned,                         color: summary.orphaned > 0 ? 'text-amber-400' : 'text-slate-400' },
          { label: 'RWO Multi-Mount',val: summary.multi_mount_rwo,                  color: summary.multi_mount_rwo > 0 ? 'text-red-400' : 'text-slate-400' },
          { label: 'Total Capacity', val: `${summary.total_capacity_gib.toFixed(1)} GiB`, color: 'text-blue-400' },
        ] as const).map(({ label, val, color }) => (
          <div key={label} className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{val}</div>
            <div className="text-xs text-slate-500 mt-0.5">{label}</div>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter by name or namespace…"
          className="flex-1 min-w-40 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-300 placeholder-slate-500 focus:outline-none focus:border-blue-500" />
        <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
          <input type="checkbox" checked={showOnlyIssues} onChange={e => setShowOnlyIssues(e.target.checked)} className="w-3 h-3 accent-blue-500" />
          Issues only
        </label>
        <span className="text-xs text-slate-500">{visible.length} / {pvcs.length} PVCs</span>
      </div>

      {visible.length === 0 ? (
        <EmptyState icon={HardDrive} title="No PVCs found" hint={showOnlyIssues ? 'No PVCs with issues' : 'No PVCs match filters'} />
      ) : (
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead className="bg-slate-800/50 border-b border-slate-800">
              <tr className="text-[10px] text-slate-400 uppercase tracking-wider">
                <th className="px-4 py-2.5 text-left">Name</th>
                <th className="px-4 py-2.5 text-left">Namespace</th>
                <th className="px-4 py-2.5 text-left">Phase</th>
                <th className="px-4 py-2.5 text-left">Capacity</th>
                <th className="px-4 py-2.5 text-left">Access Modes</th>
                <th className="px-4 py-2.5 text-left">Mounting Pods</th>
                <th className="px-4 py-2.5 text-left">Issues</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40">
              {visible.map(pvc => (
                <tr key={`${pvc.namespace}/${pvc.name}`} className={`transition-colors ${pvc.issues.length > 0 ? 'bg-amber-900/10' : 'hover:bg-slate-800/20'}`}>
                  <td className="px-4 py-2.5 font-mono font-medium text-white whitespace-nowrap">
                    <div className="flex items-center gap-1.5">
                      <HardDrive size={12} className="text-slate-500 shrink-0" />
                      {pvc.name}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-slate-400 whitespace-nowrap">{pvc.namespace}</td>
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${
                      pvc.phase === 'Bound'   ? 'bg-emerald-900/40 text-emerald-300 border-emerald-700/40' :
                      pvc.phase === 'Pending' ? 'bg-yellow-900/40 text-yellow-300 border-yellow-700/40' :
                                                'bg-red-900/40 text-red-300 border-red-700/40'}`}>
                      {pvc.phase}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap text-slate-300 font-mono">{pvc.capacity}</td>
                  <td className="px-4 py-2.5 text-slate-400 whitespace-nowrap">
                    {pvc.access_modes.map(m => m.replace('ReadWrite', 'RW').replace('ReadOnly', 'RO').replace('Once', 'O').replace('Many', 'X')).join(', ')}
                  </td>
                  <td className="px-4 py-2.5">
                    {pvc.mount_count === 0 ? (
                      <span className="text-slate-600 italic">none</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {pvc.mounting_pods.slice(0, 3).map(p => (
                          <span key={p} className="bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded font-mono">{p}</span>
                        ))}
                        {pvc.mounting_pods.length > 3 && (
                          <span className="text-slate-500">+{pvc.mounting_pods.length - 3}</span>
                        )}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    {pvc.issues.length === 0 ? (
                      <span className="text-emerald-400 flex items-center gap-1"><CheckCircle size={11} /> OK</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {pvc.issues.map(issue => {
                          const m = PVC_ISSUE_META[issue] ?? { label: issue, color: 'bg-slate-700/60 text-slate-400 border border-slate-600/40' }
                          return <span key={issue} className={`px-1.5 py-0.5 rounded-full font-medium ${m.color}`}>{m.label}</span>
                        })}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Ingresses Section ────────────────────────────────────────────────────────

interface IngressInfo {
  name: string; namespace: string; hosts: string[]; tls: boolean; tls_hosts: string[]
  tls_secrets: string[]; paths: string[]; lb_ips: string[]; ingress_class: string; created_at: string
}

function IngressSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [ingresses, setIngresses] = useState<IngressInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filter, setFilter] = useState('')
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const toast = useToast()

  async function handleDelete(ing: IngressInfo) {
    try {
      const resp = await vksDelete<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/ingresses/${ing.name}?namespace=${ing.namespace}`
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksDelete(`${clusterId}/ingresses/${ing.name}?namespace=${ing.namespace}&token=${token}`)
          toast.success(`Ingress ${ing.name} deleted`)
          load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ ingresses: IngressInfo[] }>(`${clusterId}/ingresses?namespace=${namespace}`)
      .then(d => { setIngresses(d.ingresses); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  const visible = filter
    ? ingresses.filter(i => i.name.includes(filter) || i.namespace.includes(filter) || i.hosts.some(h => h.includes(filter)))
    : ingresses

  return (
    <div className="space-y-4">
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Filter by name, namespace, or host…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={4} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Network} title="No ingresses" hint={`No ingresses in ${namespace || 'any namespace'}`} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Name</th>
                {!namespace && <th className="px-4 py-3 text-xs font-medium text-slate-400">Namespace</th>}
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Hosts</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Paths</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">TLS</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">LB IP</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Age</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((ing, i) => (
                <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                  <td className="px-4 py-3 font-mono text-white">
                    <div className="flex items-center gap-2">
                      <Network size={11} className="text-slate-500" />
                      <span className="inline-flex items-center gap-1">{ing.name}<CopyButton text={ing.name} /></span>
                      {ing.ingress_class && (
                        <span className="px-1.5 py-0.5 rounded text-[9px] bg-slate-800 text-slate-400">{ing.ingress_class}</span>
                      )}
                    </div>
                  </td>
                  {!namespace && <td className="px-4 py-3 text-slate-400">{ing.namespace}</td>}
                  <td className="px-4 py-3 font-mono">
                    {ing.hosts.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {ing.hosts.map(h => (
                          <span key={h} className="inline-flex items-center gap-0.5 text-slate-300">{h}<CopyButton text={h} /></span>
                        ))}
                      </div>
                    ) : <span className="text-slate-600">*</span>}
                  </td>
                  <td className="px-4 py-3 text-slate-400 max-w-xs truncate">{ing.paths.join(' | ') || '—'}</td>
                  <td className="px-4 py-3">
                    {(() => {
                      if (!ing.tls) return <span className="text-slate-600 text-[10px]">none</span>
                      const uncovered = ing.hosts.filter(h => !ing.tls_hosts.includes(h))
                      const partial = uncovered.length > 0 && ing.tls_hosts.length > 0
                      const secretLine = ing.tls_secrets.length ? `Secret: ${ing.tls_secrets.join(', ')}` : ''
                      const tip = partial
                        ? `Partial TLS — uncovered: ${uncovered.join(', ')}${secretLine ? '\n' + secretLine : ''}`
                        : `All hosts covered${secretLine ? '\n' + secretLine : ''}`
                      return (
                        <span
                          title={tip}
                          className={`cursor-default px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            partial
                              ? 'bg-amber-900/40 text-amber-400'
                              : 'bg-emerald-900/40 text-emerald-400'
                          }`}
                        >
                          {partial ? '⚠ Partial TLS' : 'TLS ✓'}
                        </span>
                      )
                    })()}
                  </td>
                  <td className="px-4 py-3 font-mono text-slate-400">{ing.lb_ips.join(', ') || '—'}</td>
                  <td className="px-4 py-3 text-slate-500 whitespace-nowrap">{_relTime(ing.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'ingresses', name: ing.name, namespace: ing.namespace })} />
                      <ActionBtn icon={Trash2} title="Delete" danger onClick={() => handleDelete(ing)} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Network Policies Section ──────────────────────────────────────────────────

interface NetworkPolicyInfo {
  name: string; namespace: string
  pod_selector: Record<string, string>
  ingress_rules: number; egress_rules: number
  ingress_detail: { peers: string[]; ports: string[] }[]
  egress_detail: { peers: string[]; ports: string[] }[]
  policy_types: string[]; created_at: string
}

function NetworkPoliciesSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [policies, setPolicies] = useState<NetworkPolicyInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filter, setFilter] = useState('')
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  function toggleExpand(key: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ networkpolicies: NetworkPolicyInfo[] }>(`${clusterId}/networkpolicies?namespace=${namespace}`)
      .then(d => { setPolicies(d.networkpolicies); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  const visible = filter
    ? policies.filter(p => p.name.includes(filter) || p.namespace.includes(filter))
    : policies

  return (
    <div className="space-y-4">
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Filter by name or namespace…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={4} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={MinusCircle} title="No network policies" hint={`No NetworkPolicies in ${namespace || 'any namespace'}`} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Name</th>
                {!namespace && <th className="px-4 py-3 text-xs font-medium text-slate-400">Namespace</th>}
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Pod Selector</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Policy Types</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Ingress Rules</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Egress Rules</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Age</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((p, i) => {
                const key = `${p.namespace}/${p.name}`
                const isExpanded = expanded.has(key)
                const colSpan = namespace ? 7 : 8
                return (
                  <>
                    <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20 cursor-pointer" onClick={() => toggleExpand(key)}>
                      <td className="px-4 py-3 font-mono text-white">
                        <span className="inline-flex items-center gap-1.5">
                          {isExpanded ? <ChevronDown size={11} className="text-slate-500" /> : <ChevronRight size={11} className="text-slate-500" />}
                          {p.name}<CopyButton text={p.name} />
                        </span>
                      </td>
                      {!namespace && <td className="px-4 py-3 text-slate-400">{p.namespace}</td>}
                      <td className="px-4 py-3">
                        {Object.keys(p.pod_selector).length === 0
                          ? <span className="text-slate-500 italic text-[10px]">all pods</span>
                          : <div className="flex flex-wrap gap-1 max-w-[200px]">
                              {Object.entries(p.pod_selector).slice(0, 3).map(([k, v]) => (
                                <span key={k} className="px-1 py-0.5 bg-slate-800 text-slate-400 rounded text-[9px] font-mono">{k}={v}</span>
                              ))}
                            </div>
                        }
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex gap-1">
                          {p.policy_types.map(t => (
                            <span key={t} className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-900/40 text-blue-400">{t}</span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-slate-300 text-center">{p.ingress_rules}</td>
                      <td className="px-4 py-3 text-slate-300 text-center">{p.egress_rules}</td>
                      <td className="px-4 py-3 text-slate-500 whitespace-nowrap">{_relTime(p.created_at)}</td>
                      <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-1 justify-end">
                          <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'networkpolicies', name: p.name, namespace: p.namespace })} />
                        </div>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${i}-detail`} className="bg-slate-950/60 border-b border-slate-800/60">
                        <td colSpan={colSpan} className="px-6 py-3">
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
                            {p.ingress_detail.length > 0 && (
                              <div>
                                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">Ingress Rules</div>
                                {p.ingress_detail.map((rule, ri) => (
                                  <div key={ri} className="mb-1.5 pl-2 border-l border-slate-700">
                                    <span className="text-slate-400">from: </span>
                                    <span className="font-mono text-slate-300">{rule.peers.join(', ')}</span>
                                    {rule.ports[0] !== 'any' && <span className="text-slate-500 ml-2">ports: {rule.ports.join(', ')}</span>}
                                  </div>
                                ))}
                              </div>
                            )}
                            {p.egress_detail.length > 0 && (
                              <div>
                                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">Egress Rules</div>
                                {p.egress_detail.map((rule, ri) => (
                                  <div key={ri} className="mb-1.5 pl-2 border-l border-slate-700">
                                    <span className="text-slate-400">to: </span>
                                    <span className="font-mono text-slate-300">{rule.peers.join(', ')}</span>
                                    {rule.ports[0] !== 'any' && <span className="text-slate-500 ml-2">ports: {rule.ports.join(', ')}</span>}
                                  </div>
                                ))}
                              </div>
                            )}
                            {p.ingress_detail.length === 0 && p.egress_detail.length === 0 && (
                              <span className="text-slate-500 italic">No explicit rules (deny-all)</span>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Generic list section (misc.) ──────────────────────────────────────────────

function GenericListSection({ clusterId, namespace, endpoint, title, icon: Icon }: {
  clusterId: string
  namespace: string
  endpoint: string
  title: string
  icon: React.ElementType
}) {
  const [data, setData] = useState<Record<string, unknown[]>>({})
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    vksGet<Record<string, unknown[]>>(`${clusterId}/${endpoint}?namespace=${namespace}`)
      .then(d => { setData(d); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace, endpoint])

  useEffect(() => { load() }, [load])

  const lists = Object.entries(data).filter(([, v]) => Array.isArray(v))

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={4} /> : !loadErr && lists.map(([key, items]) => (
        <div key={key} className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-2">
            <Icon size={14} className="text-slate-400" />
            <span className="text-sm font-medium text-slate-300 capitalize">{key}</span>
            <span className="text-xs text-slate-500 ml-1">({(items as unknown[]).length})</span>
          </div>
          {(items as Array<{ name: string; namespace?: string; created_at?: string }>).length === 0 ? (
            <div className="px-4 py-6 text-sm text-slate-500 text-center">No {key}</div>
          ) : (
            <table className="w-full text-xs">
              <tbody>
                {(items as Array<{ name: string; namespace?: string; created_at?: string }>).map((item, i) => (
                  <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                    <td className="px-4 py-2.5 font-mono text-white">{item.name}</td>
                    {item.namespace && <td className="px-4 py-2.5 text-slate-400">{item.namespace}</td>}
                    <td className="px-4 py-2.5 text-slate-500 text-right">{item.created_at ? _relTime(item.created_at) : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Secret Create Modal ───────────────────────────────────────────────────────

type SecretCreateType = 'Opaque' | 'kubernetes.io/tls' | 'kubernetes.io/dockerconfigjson'

function CreateSecretModal({ clusterId, namespace, onClose, onCreated }: {
  clusterId: string; namespace: string; onClose: () => void; onCreated: () => void
}) {
  const toast = useToast()
  const [secretType, setSecretType] = useState<SecretCreateType>('Opaque')
  const [name, setName] = useState('')
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  // Opaque: dynamic key-value pairs
  const [kvRows, setKvRows] = useState([{ key: '', value: '' }])

  // TLS: cert + key text areas
  const [tlsCert, setTlsCert] = useState('')
  const [tlsKey, setTlsKey] = useState('')

  // Docker: server/username/password/email
  const [dockerServer, setDockerServer] = useState('')
  const [dockerUser, setDockerUser] = useState('')
  const [dockerPass, setDockerPass] = useState('')
  const [dockerEmail, setDockerEmail] = useState('')

  function buildData(): Record<string, string> | null {
    if (secretType === 'Opaque') {
      const d: Record<string, string> = {}
      for (const row of kvRows) {
        if (row.key.trim()) d[row.key.trim()] = row.value
      }
      return Object.keys(d).length ? d : null
    }
    if (secretType === 'kubernetes.io/tls') {
      if (!tlsCert.trim() || !tlsKey.trim()) return null
      return { 'tls.crt': tlsCert.trim(), 'tls.key': tlsKey.trim() }
    }
    if (secretType === 'kubernetes.io/dockerconfigjson') {
      if (!dockerServer || !dockerUser || !dockerPass) return null
      const auth = btoa(`${dockerUser}:${dockerPass}`)
      const cfg = { auths: { [dockerServer]: { username: dockerUser, password: dockerPass, email: dockerEmail, auth } } }
      return { '.dockerconfigjson': JSON.stringify(cfg) }
    }
    return null
  }

  async function handleCreate() {
    if (!name.trim()) { toast.error('Name is required'); return }
    const data = buildData()
    if (!data) { toast.error('Fill in all required fields'); return }
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/secrets?namespace=${namespace}`,
        { name: name.trim(), type: secretType, data }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/secrets?namespace=${namespace}&token=${token}`,
            { name: name.trim(), type: secretType, data })
          toast.success(`Secret ${name} created`); onCreated()
        }})
      }
    } catch (e) { toast.error(`Create failed: ${e}`) }
  }

  const TYPE_LABELS: Record<SecretCreateType, string> = {
    'Opaque': 'Opaque (key-value)',
    'kubernetes.io/tls': 'TLS Certificate',
    'kubernetes.io/dockerconfigjson': 'Docker Registry',
  }

  return (
    <>
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
        <div className="bg-[#0d1117] border border-slate-700/60 rounded-2xl w-full max-w-lg max-h-[90vh] flex flex-col shadow-2xl">
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
            <h3 className="text-sm font-semibold text-white">Create Secret</h3>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400"><X size={14} /></button>
          </div>
          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1.5">Secret Type</label>
              <div className="flex flex-col gap-1.5">
                {(Object.entries(TYPE_LABELS) as [SecretCreateType, string][]).map(([t, label]) => (
                  <label key={t} className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors ${secretType === t ? 'border-blue-500/50 bg-blue-500/5' : 'border-slate-700/50 hover:border-slate-600'}`}>
                    <input type="radio" name="stype" value={t} checked={secretType === t} onChange={() => setSecretType(t)} className="w-3 h-3" />
                    <span className="text-xs text-slate-300">{label}</span>
                  </label>
                ))}
              </div>
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Name</label>
              <input value={name} onChange={e => setName(e.target.value)} placeholder="my-secret"
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
            </div>

            {secretType === 'Opaque' && (
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-2">Key-Value Pairs</label>
                <div className="space-y-1.5">
                  {kvRows.map((row, i) => (
                    <div key={i} className="flex gap-2">
                      <input value={row.key} onChange={e => setKvRows(prev => prev.map((r, j) => j === i ? { ...r, key: e.target.value } : r))}
                        placeholder="KEY" className="w-32 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                      <span className="text-slate-600 text-xs self-center">=</span>
                      <input value={row.value} onChange={e => setKvRows(prev => prev.map((r, j) => j === i ? { ...r, value: e.target.value } : r))}
                        placeholder="value" type="password"
                        className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                      <button onClick={() => setKvRows(prev => prev.filter((_, j) => j !== i))}
                        className="p-1 text-slate-600 hover:text-red-400 transition-colors"><Minus size={12} /></button>
                    </div>
                  ))}
                  <button onClick={() => setKvRows(prev => [...prev, { key: '', value: '' }])}
                    className="flex items-center gap-1 text-[11px] text-blue-400 hover:text-blue-300 transition-colors">
                    <Plus size={11} /> Add key
                  </button>
                </div>
              </div>
            )}

            {secretType === 'kubernetes.io/tls' && (
              <div className="space-y-3">
                <div>
                  <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">TLS Certificate (PEM)</label>
                  <textarea value={tlsCert} onChange={e => setTlsCert(e.target.value)}
                    placeholder="-----BEGIN CERTIFICATE-----&#10;..." rows={5}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600 resize-none" />
                </div>
                <div>
                  <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Private Key (PEM)</label>
                  <textarea value={tlsKey} onChange={e => setTlsKey(e.target.value)}
                    placeholder="-----BEGIN PRIVATE KEY-----&#10;..." rows={5}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600 resize-none" />
                </div>
              </div>
            )}

            {secretType === 'kubernetes.io/dockerconfigjson' && (
              <div className="space-y-3">
                {[
                  { label: 'Registry Server', val: dockerServer, set: setDockerServer, placeholder: 'registry.example.com' },
                  { label: 'Username', val: dockerUser, set: setDockerUser, placeholder: 'myuser' },
                  { label: 'Password', val: dockerPass, set: setDockerPass, placeholder: '••••••••', type: 'password' },
                  { label: 'Email (optional)', val: dockerEmail, set: setDockerEmail, placeholder: 'user@example.com' },
                ].map(({ label, val, set, placeholder, type }) => (
                  <div key={label}>
                    <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">{label}</label>
                    <input value={val} onChange={e => set(e.target.value)} placeholder={placeholder}
                      type={type || 'text'}
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="p-5 border-t border-slate-800">
            <button onClick={handleCreate}
              className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors font-medium">
              Create Secret
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

// ── Secrets Section ───────────────────────────────────────────────────────────

interface SecretItem { name: string; namespace: string; created_at: string; type?: string; keys_count?: number }

function SecretsSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [secrets, setSecrets] = useState<SecretItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [revealed, setRevealed] = useState<Record<string, Record<string, string>>>({})
  const [editingKey, setEditingKey] = useState<Record<string, Record<string, string>>>({})
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [filter, setFilter] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const { sortKey, sortDir, onSort } = useSort()
  const toast = useToast()

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ secrets: SecretItem[] }>(`${clusterId}/secrets?namespace=${namespace}`)
      .then(d => { setSecrets(d.secrets); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])

  const visible = useSortedFiltered(
    filter ? secrets.filter(s => s.name.includes(filter) || s.namespace.includes(filter) || (s.type || '').includes(filter)) : secrets,
    '', [], sortKey, sortDir
  )

  async function handleReveal(name: string, ns: string) {
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean } & { data?: Record<string, string> }>(
        `${clusterId}/secrets/${name}/reveal?namespace=${ns}`, {}
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          const r = await vksPost<{ data: Record<string, string> }>(
            `${clusterId}/secrets/${name}/reveal?namespace=${ns}&token=${token}`, {}
          )
          setRevealed(prev => ({ ...prev, [`${ns}/${name}`]: r.data }))
          toast.info(`Revealed secret ${name}`)
        }})
      } else if (resp.data) {
        setRevealed(prev => ({ ...prev, [`${ns}/${name}`]: resp.data! }))
      }
    } catch (e) { toast.error(`Reveal failed: ${e}`) }
  }

  async function handleDelete(name: string, ns: string) {
    try {
      const resp = await fetch(`${API}/api/v1/vks/${clusterId}/secrets/${encodeURIComponent(name)}?namespace=${ns}`, { method: 'DELETE' })
      if (!resp.ok) throw await _parseError(resp)
      const body = await resp.json() as ConfirmState & { requires_confirm?: boolean }
      if (body.requires_confirm) {
        setConfirm({ ...body, onConfirm: async (token) => {
          const r = await fetch(`${API}/api/v1/vks/${clusterId}/secrets/${encodeURIComponent(name)}?namespace=${ns}&token=${token}`, { method: 'DELETE' })
          if (!r.ok) throw await _parseError(r)
          toast.success(`Deleted secret ${name}`)
          load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  async function handlePatchSecret(name: string, ns: string, data: Record<string, string>) {
    try {
      const resp = await vksPatch<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/secrets/${name}?namespace=${ns}`, { data }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPatch(`${clusterId}/secrets/${name}?namespace=${ns}&token=${token}`, { data })
          toast.success('Secret updated')
          setRevealed(prev => ({ ...prev, [`${ns}/${name}`]: { ...(prev[`${ns}/${name}`] ?? {}), ...data } }))
          setEditingKey(prev => { const n = { ...prev }; delete n[`${ns}/${name}`]; return n })
        }})
      }
    } catch (e) { toast.error(`Update failed: ${e}`) }
  }

  function secretTypeBadge(type: string) {
    if (type === 'kubernetes.io/tls') return 'bg-blue-900/40 text-blue-400'
    if (type === 'kubernetes.io/service-account-token') return 'bg-purple-900/40 text-purple-400'
    if (type === 'kubernetes.io/dockerconfigjson' || type === 'kubernetes.io/dockercfg') return 'bg-orange-900/40 text-orange-400'
    return 'bg-slate-800 text-slate-400'
  }

  function shortSecretType(type: string) {
    if (type === 'kubernetes.io/tls') return 'TLS'
    if (type === 'kubernetes.io/service-account-token') return 'SA Token'
    if (type === 'kubernetes.io/dockerconfigjson') return 'Docker'
    if (type === 'kubernetes.io/dockercfg') return 'Docker'
    if (type === 'Opaque') return 'Opaque'
    return type.split('/').pop() || type
  }

  return (
    <div className="space-y-4">
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {showCreate && (
        <CreateSecretModal
          clusterId={clusterId}
          namespace={namespace}
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); load() }}
        />
      )}
      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Filter by name, namespace, or type…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg">
          <Plus size={12} /> Create
        </button>
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={5} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Lock} title="No secrets" hint={`No secrets in ${namespace || 'any namespace'}`}
          action={<button onClick={() => setShowCreate(true)} className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg">Create Secret</button>} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <SortableHeader label="Name" col="name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                {!namespace && <SortableHeader label="Namespace" col="namespace" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />}
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Type</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Keys</th>
                <SortableHeader label="Age" col="created_at" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <th className="px-4 py-3 text-xs font-medium text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(secret => {
                const key = `${secret.namespace}/${secret.name}`
                return (
                  <>
                    <tr key={key} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                      <td className="px-4 py-3 font-mono text-white text-sm">
                        <div className="flex items-center gap-2">
                          <Lock size={12} className="text-slate-500" />
                          <span className="inline-flex items-center gap-1">{secret.name}<CopyButton text={secret.name} /></span>
                        </div>
                      </td>
                      {!namespace && <td className="px-4 py-3 text-slate-400 text-xs">{secret.namespace}</td>}
                      <td className="px-4 py-3">
                        {secret.type ? (
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${secretTypeBadge(secret.type)}`}>
                            {shortSecretType(secret.type)}
                          </span>
                        ) : '—'}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-500">{secret.keys_count ?? '—'}</td>
                      <td className="px-4 py-3 text-xs">
                        <span className="text-slate-500">{_relTime(secret.created_at)}</span>
                        {secret.created_at && (Date.now() - new Date(secret.created_at).getTime()) > 90 * 86400000 && (
                          <span className="ml-1.5 px-1 py-0.5 rounded text-[9px] bg-yellow-900/40 text-yellow-400 border border-yellow-700/30" title="Secret is older than 90 days — consider rotating">⚠ stale</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'secrets', name: secret.name, namespace: secret.namespace })} />
                          <ActionBtn icon={Eye} title="Reveal (audited)" onClick={() => handleReveal(secret.name, secret.namespace)} />
                          <ActionBtn icon={Trash2} title="Delete secret" danger onClick={() => handleDelete(secret.name, secret.namespace)} />
                        </div>
                      </td>
                    </tr>
                    {revealed[key] && (
                      <tr key={`${key}-data`} className="border-b border-slate-800/40 bg-slate-800/20">
                        <td colSpan={namespace ? 5 : 6} className="px-8 py-3">
                          <div className="grid gap-2">
                            {Object.entries(revealed[key]).map(([k, v]) => {
                              const editVal = editingKey[key]?.[k]
                              const isEditing = editVal !== undefined
                              return (
                                <div key={k} className="font-mono text-xs flex items-center gap-2">
                                  <span className="text-slate-400 w-28 flex-shrink-0 truncate">{k}:</span>
                                  {isEditing ? (
                                    <>
                                      <input
                                        value={editVal}
                                        onChange={e => setEditingKey(prev => ({ ...prev, [key]: { ...(prev[key] ?? {}), [k]: e.target.value } }))}
                                        className="flex-1 bg-slate-900 border border-blue-500/50 rounded px-2 py-1 text-xs font-mono text-slate-200 outline-none"
                                      />
                                      <button onClick={() => handlePatchSecret(secret.name, secret.namespace, { [k]: editVal })}
                                        className="px-2 py-0.5 bg-blue-600 hover:bg-blue-500 text-white text-[10px] rounded transition-colors flex-shrink-0">Save</button>
                                      <button onClick={() => setEditingKey(prev => { const n = { ...prev }; if (n[key]) { delete n[key][k]; if (!Object.keys(n[key]).length) delete n[key] } return n })}
                                        className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 text-slate-300 text-[10px] rounded transition-colors flex-shrink-0">Cancel</button>
                                    </>
                                  ) : (
                                    <>
                                      <span className="text-emerald-300 flex-1 truncate max-w-xs">{v}</span>
                                      <CopyButton text={v} />
                                      <button onClick={() => setEditingKey(prev => ({ ...prev, [key]: { ...(prev[key] ?? {}), [k]: v } }))}
                                        className="p-0.5 text-slate-600 hover:text-blue-400 transition-colors flex-shrink-0"><Pencil size={10} /></button>
                                    </>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Create RoleBinding Modal ───────────────────────────────────────────────────

function CreateRoleBindingModal({ clusterId, namespace, onClose, onCreated }: {
  clusterId: string; namespace: string; onClose: () => void; onCreated: () => void
}) {
  const toast = useToast()
  const [rbName, setRbName] = useState('')
  const [subjectKind, setSubjectKind] = useState<'User' | 'Group' | 'ServiceAccount'>('User')
  const [subjectName, setSubjectName] = useState('')
  const [subjectNs, setSubjectNs] = useState(namespace)
  const [roleRefKind, setRoleRefKind] = useState<'ClusterRole' | 'Role'>('ClusterRole')
  const [roleRefName, setRoleRefName] = useState('')
  const [clusterWide, setClusterWide] = useState(false)
  const [clusterRoles, setClusterRoles] = useState<{ name: string }[]>([])
  const [crLoading, setCrLoading] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  useEffect(() => {
    setCrLoading(true)
    vksGet<{ clusterroles: { name: string }[] }>(`${clusterId}/clusterroles`)
      .then(d => setClusterRoles(d.clusterroles))
      .catch(() => {})
      .finally(() => setCrLoading(false))
  }, [clusterId])

  async function handleCreate() {
    if (!rbName.trim() || !subjectName.trim() || !roleRefName.trim()) {
      toast.error('Name, subject, and role are required'); return
    }
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/rolebindings?namespace=${namespace}`,
        { name: rbName.trim(), subject_kind: subjectKind, subject_name: subjectName.trim(),
          subject_namespace: subjectNs, role_ref_kind: roleRefKind, role_ref_name: roleRefName,
          cluster_wide: clusterWide }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/rolebindings?namespace=${namespace}&token=${token}`,
            { name: rbName.trim(), subject_kind: subjectKind, subject_name: subjectName.trim(),
              subject_namespace: subjectNs, role_ref_kind: roleRefKind, role_ref_name: roleRefName,
              cluster_wide: clusterWide })
          toast.success(`RoleBinding ${rbName} created`); onCreated()
        }})
      }
    } catch (e) { toast.error(`Create failed: ${e}`) }
  }

  return (
    <>
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
        <div className="bg-[#0d1117] border border-slate-700/60 rounded-2xl w-full max-w-lg max-h-[90vh] flex flex-col shadow-2xl">
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Shield size={14} className="text-blue-400" /> Grant Access (RoleBinding)
            </h3>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400"><X size={14} /></button>
          </div>
          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            <div className="bg-blue-900/10 border border-blue-700/30 rounded-lg p-3 text-[11px] text-blue-300">
              <strong>OpenShift → Kubernetes:</strong> This is like adding a user/group to a project role in OpenShift.
              A RoleBinding grants a Role or ClusterRole to a subject within a namespace.
              A ClusterRoleBinding grants access cluster-wide.
            </div>

            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Binding Name</label>
              <input value={rbName} onChange={e => setRbName(e.target.value)} placeholder="alice-can-edit-default"
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Subject Kind</label>
                <select value={subjectKind} onChange={e => setSubjectKind(e.target.value as typeof subjectKind)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-blue-500">
                  <option>User</option>
                  <option>Group</option>
                  <option>ServiceAccount</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Subject Name</label>
                <input value={subjectName} onChange={e => setSubjectName(e.target.value)}
                  placeholder={subjectKind === 'ServiceAccount' ? 'default' : 'alice'}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
              </div>
            </div>
            {subjectKind === 'ServiceAccount' && (
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">ServiceAccount Namespace</label>
                <input value={subjectNs} onChange={e => setSubjectNs(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 outline-none focus:border-blue-500" />
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Role Kind</label>
                <select value={roleRefKind} onChange={e => setRoleRefKind(e.target.value as typeof roleRefKind)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-blue-500">
                  <option>ClusterRole</option>
                  <option>Role</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Role Name</label>
                {roleRefKind === 'ClusterRole' ? (
                  <select value={roleRefName} onChange={e => setRoleRefName(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-blue-500">
                    <option value="">Select role…</option>
                    {clusterRoles.map(cr => <option key={cr.name}>{cr.name}</option>)}
                  </select>
                ) : (
                  <input value={roleRefName} onChange={e => setRoleRefName(e.target.value)} placeholder="my-role"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 outline-none focus:border-blue-500 placeholder-slate-600" />
                )}
              </div>
            </div>

            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={clusterWide} onChange={e => setClusterWide(e.target.checked)}
                className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800" />
              <span className="text-xs text-slate-300">Create as <strong>ClusterRoleBinding</strong> (cluster-wide access)</span>
            </label>
            {clusterWide && (
              <div className="bg-red-900/10 border border-red-700/30 rounded-lg p-2.5 text-[10px] text-red-300">
                ⚠ ClusterRoleBindings grant access across ALL namespaces — use with caution
              </div>
            )}
          </div>
          <div className="p-5 border-t border-slate-800">
            <button onClick={handleCreate}
              className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors font-medium">
              Grant Access
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

// ── RBAC Explain Panel ─────────────────────────────────────────────────────────

function RBACExplainPanel({ rb, clusterId, onClose }: {
  rb: RBInfo; clusterId: string; onClose: () => void
}) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const clusterWide = !rb.namespace

  function run() {
    if (esRef.current) esRef.current.close()
    setText(''); setLoading(true)
    const url = `${API}/api/v1/vks/${encodeURIComponent(clusterId)}/rolebindings/${encodeURIComponent(rb.name)}/explain?namespace=${encodeURIComponent(rb.namespace || 'default')}&cluster_wide=${clusterWide}`
    const es = new EventSource(url)
    esRef.current = es
    let acc = ''
    es.onmessage = ev => {
      try {
        const d = JSON.parse(ev.data)
        if (d.text) { acc += d.text; setText(acc) }
        if (d.done) { es.close(); setLoading(false) }
        if (d.error) { setText(`⚠ ${d.error}`); es.close(); setLoading(false) }
      } catch {}
    }
    es.onerror = () => { if (!acc) setText('⚠ LLM unavailable'); es.close(); setLoading(false) }
  }

  useEffect(() => { run(); return () => { esRef.current?.close() } }, [])

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px]" onClick={onClose} />
      <div className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-[540px] bg-[#0d1117] border-l border-slate-700/60 shadow-2xl flex flex-col">
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-slate-800 flex-shrink-0">
          <Shield size={14} className="text-blue-400" />
          <div className="flex-1 min-w-0">
            <p className="font-mono text-sm font-bold text-white truncate">{rb.name}</p>
            <p className="text-[11px] text-slate-500">{clusterWide ? 'ClusterRoleBinding' : `RoleBinding · ${rb.namespace}`}</p>
          </div>
          <button onClick={() => { run() }} disabled={loading}
            className="flex items-center gap-1 px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] rounded-lg border border-slate-700 disabled:opacity-50">
            <RotateCcw size={10} className={loading ? 'animate-spin' : ''} />Re-run
          </button>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400"><X size={15} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          {loading && !text && (
            <div className="flex items-center gap-2 text-slate-400 py-6 justify-center">
              <Loader2 size={16} className="animate-spin text-blue-400" />
              <span className="text-sm">Analyzing RBAC…</span>
            </div>
          )}
          {text && (
            <div className="text-sm text-slate-300 leading-relaxed
              [&_h2]:text-slate-100 [&_h2]:font-semibold [&_h2]:text-sm [&_h2]:mt-4 [&_h2]:mb-1.5
              [&_h3]:text-slate-200 [&_h3]:font-medium [&_h3]:text-sm [&_h3]:mt-3 [&_h3]:mb-1
              [&_strong]:text-slate-200
              [&_code]:text-blue-300 [&_code]:bg-slate-800 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs [&_code]:font-mono
              [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:space-y-1 [&_ul]:my-2
              [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:space-y-1 [&_ol]:my-2
              [&_li]:text-slate-300 [&_p]:my-1.5">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
              {loading && <span className="inline-block w-1.5 h-4 bg-blue-400 animate-pulse ml-0.5 align-middle rounded-sm" />}
            </div>
          )}
        </div>
      </div>
    </>
  )
}

// ── RBAC Section ─────────────────────────────────────────────────────────────

interface RBSubject { kind: string; name: string; namespace?: string }
interface RBInfo {
  name: string; namespace: string
  role_ref_kind: string; role_ref_name: string
  subjects: RBSubject[]; subject_count: number; created_at: string
}

function RBACSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [data, setData] = useState<{ rolebindings: RBInfo[]; clusterrolebindings: RBInfo[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filter, setFilter] = useState('')
  const [tab, setTab] = useState<'rb' | 'crb'>('rb')
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [explainTarget, setExplainTarget] = useState<RBInfo | null>(null)
  const toast = useToast()

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ rolebindings: RBInfo[]; clusterrolebindings: RBInfo[] }>(`${clusterId}/rbac?namespace=${namespace}`)
      .then(d => { setData(d); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  const items = tab === 'rb' ? (data?.rolebindings ?? []) : (data?.clusterrolebindings ?? [])
  const visible = filter
    ? items.filter(i => i.name.includes(filter) || i.role_ref_name.includes(filter))
    : items

  async function handleDelete(rb: RBInfo) {
    const clusterWide = !rb.namespace
    try {
      const resp = await vksDelete<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/rolebindings/${rb.name}?namespace=${rb.namespace || namespace}&cluster_wide=${clusterWide}`
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksDelete(`${clusterId}/rolebindings/${rb.name}?namespace=${rb.namespace || namespace}&cluster_wide=${clusterWide}&token=${token}`)
          toast.success(`Deleted ${rb.name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  function subjectBadge(s: RBSubject) {
    const colors: Record<string, string> = {
      ServiceAccount: 'bg-blue-900/40 text-blue-400',
      User: 'bg-emerald-900/40 text-emerald-400',
      Group: 'bg-purple-900/40 text-purple-400',
    }
    return colors[s.kind] ?? 'bg-slate-800 text-slate-400'
  }

  return (
    <div className="space-y-4">
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {showCreate && (
        <CreateRoleBindingModal
          clusterId={clusterId}
          namespace={namespace}
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); load() }}
        />
      )}
      {explainTarget && (
        <RBACExplainPanel rb={explainTarget} clusterId={clusterId} onClose={() => setExplainTarget(null)} />
      )}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex bg-slate-800 rounded-lg p-0.5">
          {([['rb', 'RoleBindings'], ['crb', 'ClusterRoleBindings']] as const).map(([t, label]) => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-xs rounded-md transition-colors ${tab === t ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}>
              {label}
              {data && <span className="ml-1 text-[10px] opacity-60">({t === 'rb' ? (data.rolebindings?.length ?? 0) : (data.clusterrolebindings?.length ?? 0)})</span>}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Filter by name or role…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg">
          <Plus size={12} /> Grant Access
        </button>
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={6} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Lock} title={`No ${tab === 'rb' ? 'RoleBindings' : 'ClusterRoleBindings'}`} hint={`None found in ${namespace || 'any namespace'}`}
          action={<button onClick={() => setShowCreate(true)} className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg">Grant Access</button>} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Name</th>
                {tab === 'rb' && !namespace && <th className="px-4 py-3 text-xs font-medium text-slate-400">Namespace</th>}
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Bound Role</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Subjects</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Age</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((rb, i) => (
                <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                  <td className="px-4 py-3 font-mono text-white">
                    <span className="inline-flex items-center gap-1">{rb.name}<CopyButton text={rb.name} /></span>
                  </td>
                  {tab === 'rb' && !namespace && <td className="px-4 py-3 text-slate-400">{rb.namespace}</td>}
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${rb.role_ref_kind === 'ClusterRole' ? 'bg-purple-900/40 text-purple-400' : 'bg-blue-900/40 text-blue-400'}`}>{rb.role_ref_kind}</span>
                      <span className="font-mono text-slate-300">{rb.role_ref_name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {rb.subjects.slice(0, 4).map((s, j) => (
                        <span key={j} className={`px-1.5 py-0.5 rounded text-[9px] font-mono ${subjectBadge(s)}`}>
                          {s.kind.charAt(0)}: {s.name}
                        </span>
                      ))}
                      {rb.subject_count > 4 && <span className="text-slate-500 text-[10px]">+{rb.subject_count - 4}</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-500 whitespace-nowrap">{_relTime(rb.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      <ActionBtn icon={BotMessageSquare} title="AI Explain access" onClick={() => setExplainTarget(rb)} />
                      <ActionBtn icon={Trash2} title="Delete" danger onClick={() => handleDelete(rb)} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Service Accounts Section ──────────────────────────────────────────────────

interface ServiceAccountInfo {
  name: string; namespace: string; secrets_count: number
  image_pull_secrets: string[]; created_at: string
}

function CreateSAModal({ clusterId, namespace, onClose, onCreated }: {
  clusterId: string; namespace: string; onClose: () => void; onCreated: () => void
}) {
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [saName, setSaName] = useState('')
  const [pullSecretsRaw, setPullSecretsRaw] = useState('')
  const [labelsRaw, setLabelsRaw] = useState('')

  async function create() {
    if (!saName) { toast.error('Name is required'); return }
    const pull_secrets = pullSecretsRaw.split(',').map(s => s.trim()).filter(Boolean)
    const labels: Record<string, string> = {}
    for (const pair of labelsRaw.split(',')) {
      const [k, v] = pair.trim().split('=')
      if (k && v) labels[k.trim()] = v.trim()
    }
    const body = { name: saName, image_pull_secrets: pull_secrets, labels }
    setSaving(true)
    try {
      const ns = namespace || 'default'
      const r1 = await vksPost<{ requires_confirm: boolean; token: string }>(`${clusterId}/serviceaccounts?namespace=${ns}`, body)
      if (r1.requires_confirm) {
        const r2 = await vksPost<{ ok: boolean }>(`${clusterId}/serviceaccounts?namespace=${ns}&token=${r1.token}`, body)
        if (r2.ok) { toast.success(`ServiceAccount ${saName} created`); onCreated(); onClose() }
      }
    } catch (e) { toast.error(`Create failed: ${e}`) }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
          <Info size={14} className="text-blue-400" />
          <span className="text-sm font-medium text-white">Create ServiceAccount</span>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Name</label>
            <input value={saName} onChange={e => setSaName(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500"
              placeholder="my-service-account" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Image Pull Secrets (optional)</label>
            <input value={pullSecretsRaw} onChange={e => setPullSecretsRaw(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-blue-500"
              placeholder="harbor-creds, gcr-secret" />
            <p className="text-[10px] text-slate-600 mt-1">Comma-separated secret names for image pulls</p>
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Labels (optional)</label>
            <input value={labelsRaw} onChange={e => setLabelsRaw(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-blue-500"
              placeholder="team=platform,env=prod" />
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-800">
          <button onClick={onClose} className="px-4 py-2 text-xs text-slate-400 hover:text-white">Cancel</button>
          <button onClick={create} disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50">
            {saving ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} Create SA
          </button>
        </div>
      </div>
    </div>
  )
}

function ServiceAccountsSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [accounts, setAccounts] = useState<ServiceAccountInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filter, setFilter] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const toast = useToast()

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ serviceaccounts: ServiceAccountInfo[] }>(`${clusterId}/serviceaccounts?namespace=${namespace}`)
      .then(d => { setAccounts(d.serviceaccounts); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  const visible = filter
    ? accounts.filter(a => a.name.includes(filter) || a.namespace.includes(filter))
    : accounts

  async function handleDelete(sa: ServiceAccountInfo) {
    try {
      const r1 = await vksDelete<{ requires_confirm: boolean; token: string }>(`${clusterId}/serviceaccounts/${sa.name}?namespace=${sa.namespace}`)
      if (r1.requires_confirm) {
        setConfirm({ token: r1.token, action: 'delete', target: sa.name, description: `Delete ServiceAccount ${sa.name} in ${sa.namespace}`, onConfirm: async (token) => {
          await vksDelete(`${clusterId}/serviceaccounts/${sa.name}?namespace=${sa.namespace}&token=${token}`)
          toast.success(`Deleted SA ${sa.name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  return (
    <div className="space-y-4">
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {showCreate && <CreateSAModal clusterId={clusterId} namespace={namespace || 'default'} onClose={() => setShowCreate(false)} onCreated={load} />}
      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Filter by name or namespace…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 text-xs rounded-lg border border-blue-600/30">
          <Plus size={12} /> Create SA
        </button>
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={5} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Info} title="No service accounts" hint={`No ServiceAccounts in ${namespace || 'any namespace'}`}
          action={<button onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-4 py-2 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 text-xs rounded-lg border border-blue-600/30">
            <Plus size={12} /> Create SA
          </button>} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Name</th>
                {!namespace && <th className="px-4 py-3 text-xs font-medium text-slate-400">Namespace</th>}
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Secrets</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Image Pull Secrets</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Age</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((sa, i) => (
                <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                  <td className="px-4 py-3 font-mono text-white">
                    <span className="inline-flex items-center gap-1">{sa.name}<CopyButton text={sa.name} /></span>
                  </td>
                  {!namespace && <td className="px-4 py-3 text-slate-400">{sa.namespace}</td>}
                  <td className="px-4 py-3 text-slate-300">{sa.secrets_count > 0 ? sa.secrets_count : <span className="text-slate-600">—</span>}</td>
                  <td className="px-4 py-3">
                    {sa.image_pull_secrets.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {sa.image_pull_secrets.map(s => (
                          <span key={s} className="px-1.5 py-0.5 bg-purple-900/40 text-purple-400 rounded text-[10px] font-mono">{s}</span>
                        ))}
                      </div>
                    ) : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-4 py-3 text-slate-500 whitespace-nowrap">{_relTime(sa.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      {sa.name !== 'default' && (
                        <ActionBtn icon={Trash2} title="Delete SA" danger onClick={() => handleDelete(sa)} />
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── HPA Section ───────────────────────────────────────────────────────────────

interface HpaInfo {
  name: string; namespace: string; created_at: string
  target_kind: string; target_name: string
  min_replicas: number; max_replicas: number
  current_replicas: number; desired_replicas: number
  current_cpu_pct: number | null; target_cpu_pct: number | null
  conditions?: { type: string; status: string; reason: string }[]
}

interface PDBInfo {
  name: string; namespace: string; created_at: string
  selector: Record<string, string>
  min_available?: number | string | null
  max_unavailable?: number | string | null
  current_healthy: number; desired_healthy: number
  disruptions_allowed: number; expected_pods: number
}

interface LimitEntry {
  type: string
  default: Record<string, string>
  default_request: Record<string, string>
  max: Record<string, string>
  min: Record<string, string>
  max_limit_request_ratio: Record<string, string>
}

interface LimitRangeInfo {
  name: string; namespace: string; created_at: string
  limits: LimitEntry[]
}

interface ImageInfo {
  image: string; short: string; tag: string
  is_latest: boolean; is_pinned: boolean
  pod_count: number
  pods: { name: string; namespace: string; phase: string }[]
  namespaces: string[]
}

function CreateHPAModal({ clusterId, namespace, onClose, onCreated }: {
  clusterId: string; namespace: string; onClose: () => void; onCreated: () => void
}) {
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    name: '', target_kind: 'Deployment', target_name: '',
    min_replicas: 1, max_replicas: 10, target_cpu_pct: 80,
  })

  function set(k: string, v: string | number) { setForm(f => ({ ...f, [k]: v })) }

  async function create() {
    if (!form.name || !form.target_name) { toast.error('Name and target workload are required'); return }
    setSaving(true)
    try {
      const ns = namespace || 'default'
      const r1 = await vksPost<{ requires_confirm: boolean; token: string }>(`${clusterId}/hpa?namespace=${ns}`, form)
      if (r1.requires_confirm) {
        const r2 = await vksPost<{ ok: boolean }>(`${clusterId}/hpa?namespace=${ns}&token=${r1.token}`, form)
        if (r2.ok) { toast.success(`HPA ${form.name} created`); onCreated(); onClose() }
      }
    } catch (e) { toast.error(`Create failed: ${e}`) }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-md shadow-2xl flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
          <Zap size={14} className="text-blue-400" />
          <span className="text-sm font-medium text-white">Create HorizontalPodAutoscaler</span>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="text-[10px] text-slate-500 mb-1 block">HPA Name</label>
              <input value={form.name} onChange={e => set('name', e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500"
                placeholder="my-hpa" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Target Kind</label>
              <select value={form.target_kind} onChange={e => set('target_kind', e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500">
                <option>Deployment</option><option>StatefulSet</option><option>ReplicaSet</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Target Name</label>
              <input value={form.target_name} onChange={e => set('target_name', e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500"
                placeholder="my-deployment" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Min Replicas</label>
              <input type="number" min={1} value={form.min_replicas} onChange={e => set('min_replicas', +e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Max Replicas</label>
              <input type="number" min={1} value={form.max_replicas} onChange={e => set('max_replicas', +e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500" />
            </div>
            <div className="col-span-2">
              <label className="text-[10px] text-slate-500 mb-1 block">Target CPU Utilization (%)</label>
              <input type="number" min={1} max={100} value={form.target_cpu_pct} onChange={e => set('target_cpu_pct', +e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500" />
            </div>
          </div>
          <p className="text-[10px] text-slate-500">Coming from OpenShift? HPAs work the same way — scale targets are Deployments/StatefulSets.</p>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-800">
          <button onClick={onClose} className="px-4 py-2 text-xs text-slate-400 hover:text-white">Cancel</button>
          <button onClick={create} disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50">
            {saving ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} Create HPA
          </button>
        </div>
      </div>
    </div>
  )
}

function EditHPAModal({ hpa, clusterId, onClose, onSaved }: {
  hpa: HpaInfo; clusterId: string; onClose: () => void; onSaved: () => void
}) {
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [minR, setMinR] = useState(hpa.min_replicas)
  const [maxR, setMaxR] = useState(hpa.max_replicas)
  const [cpu, setCpu] = useState(hpa.target_cpu_pct ?? 80)

  async function save() {
    setSaving(true)
    try {
      const body = { min_replicas: minR, max_replicas: maxR, target_cpu_pct: cpu }
      const r1 = await vksPatch<{ requires_confirm: boolean; token: string }>(`${clusterId}/hpa/${hpa.name}?namespace=${hpa.namespace}`, body)
      if (r1.requires_confirm) {
        const r2 = await vksPatch<{ ok: boolean }>(`${clusterId}/hpa/${hpa.name}?namespace=${hpa.namespace}&token=${r1.token}`, body)
        if (r2.ok) { toast.success(`HPA ${hpa.name} updated`); onSaved(); onClose() }
      }
    } catch (e) { toast.error(`Update failed: ${e}`) }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-sm shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
          <Zap size={14} className="text-yellow-400" />
          <span className="text-sm font-medium text-white">Edit HPA — <span className="font-mono text-blue-400">{hpa.name}</span></span>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div className="p-5 space-y-4">
          <div className="text-xs text-slate-400">Target: <span className="font-mono text-slate-200">{hpa.target_kind}/{hpa.target_name}</span></div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Min Replicas</label>
              <input type="number" min={1} value={minR} onChange={e => setMinR(+e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Max Replicas</label>
              <input type="number" min={1} value={maxR} onChange={e => setMaxR(+e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500" />
            </div>
            <div className="col-span-2">
              <label className="text-[10px] text-slate-500 mb-1 block">Target CPU %</label>
              <input type="number" min={1} max={100} value={cpu} onChange={e => setCpu(+e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500" />
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-800">
          <button onClick={onClose} className="px-4 py-2 text-xs text-slate-400 hover:text-white">Cancel</button>
          <button onClick={save} disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50">
            {saving ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} Save
          </button>
        </div>
      </div>
    </div>
  )
}

function HPASection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [hpas, setHpas] = useState<HpaInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [filterText, setFilterText] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [editHpa, setEditHpa] = useState<HpaInfo | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const { sortKey, sortDir, onSort } = useSort()
  const toast = useToast()

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ hpa: HpaInfo[] }>(`${clusterId}/hpa?namespace=${namespace}`)
      .then(d => { setHpas(d.hpa); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  const visible = useSortedFiltered(
    filterText ? hpas.filter(h => h.name.includes(filterText) || h.target_name.includes(filterText) || h.namespace.includes(filterText)) : hpas,
    '', [], sortKey, sortDir
  )

  async function handleDelete(h: HpaInfo) {
    try {
      const r1 = await vksDelete<{ requires_confirm: boolean; token: string }>(`${clusterId}/hpa/${h.name}?namespace=${h.namespace}`)
      if (r1.requires_confirm) {
        setConfirm({ token: r1.token, action: 'delete', target: h.name, description: `Delete HPA ${h.name} in ${h.namespace}`, onConfirm: async (token) => {
          await vksDelete(`${clusterId}/hpa/${h.name}?namespace=${h.namespace}&token=${token}`)
          toast.success(`Deleted HPA ${h.name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  return (
    <div className="space-y-4">
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {showCreate && <CreateHPAModal clusterId={clusterId} namespace={namespace || 'default'} onClose={() => setShowCreate(false)} onCreated={load} />}
      {editHpa && <EditHPAModal hpa={editHpa} clusterId={clusterId} onClose={() => setEditHpa(null)} onSaved={load} />}
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      <div className="flex items-center gap-2">
        <input
          value={filterText} onChange={e => setFilterText(e.target.value)} placeholder="Filter HPAs…"
          className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <button onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 text-xs rounded-lg border border-blue-600/30">
          <Plus size={12} /> Add HPA
        </button>
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loading ? <SkeletonRows rows={4} /> : !loadErr && hpas.length === 0 ? (
        <EmptyState icon={Zap} title="No Autoscalers" hint="No HorizontalPodAutoscalers found."
          action={<button onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-4 py-2 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 text-xs rounded-lg border border-blue-600/30">
            <Plus size={12} /> Create HPA
          </button>} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <SortableHeader col="name" label="Name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="px-4 py-2" />
                {!namespace && <SortableHeader col="namespace" label="Namespace" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="px-4 py-2" />}
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Target</th>
                <SortableHeader col="current_replicas" label="Replicas" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="px-4 py-2" />
                <SortableHeader col="current_cpu_pct" label="CPU" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="px-4 py-2" />
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Status</th>
                <SortableHeader col="created_at" label="Age" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="px-4 py-2" />
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(h => {
                const cpuOk = h.current_cpu_pct != null && h.target_cpu_pct != null
                const cpuHigh = cpuOk && h.current_cpu_pct! >= h.target_cpu_pct!
                const scalingLimited = h.conditions?.some(c => c.type === 'ScalingLimited' && c.status === 'True')
                const ableToScale = h.conditions?.find(c => c.type === 'AbleToScale')
                const blocked = ableToScale?.status === 'False'
                return (
                  <tr key={`${h.namespace}/${h.name}`} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                    <td className="px-4 py-3 font-mono text-white text-sm">{h.name}</td>
                    {!namespace && <td className="px-4 py-3 text-slate-400 text-xs">{h.namespace}</td>}
                    <td className="px-4 py-3 text-xs text-slate-400">{h.target_kind}/{h.target_name}</td>
                    <td className="px-4 py-3 text-xs">
                      <div>
                        <div className="flex items-baseline gap-0.5 mb-1">
                          <span className="text-white font-medium">{h.current_replicas}</span>
                          <span className="text-slate-500">/{h.max_replicas}</span>
                          <span className="text-slate-600 text-[9px] ml-1">min {h.min_replicas}</span>
                          {h.desired_replicas !== h.current_replicas && (
                            <span className="ml-1 text-yellow-400 text-[10px]">→{h.desired_replicas}</span>
                          )}
                        </div>
                        {h.max_replicas > 0 && (
                          <div className="h-1 bg-slate-700 rounded-full overflow-hidden w-16">
                            <div className="h-full rounded-full bg-blue-500 transition-all"
                              style={{ width: `${Math.min(100, (h.current_replicas / h.max_replicas) * 100)}%` }} />
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs">
                      {cpuOk ? (
                        <div>
                          <span className={`text-[11px] font-medium ${cpuHigh ? 'text-yellow-400' : 'text-emerald-400'}`}>
                            {h.current_cpu_pct}%
                          </span>
                          <span className="text-slate-600 text-[9px]">/{h.target_cpu_pct}%</span>
                          <div className="h-1 bg-slate-700 rounded-full overflow-hidden w-16 mt-1">
                            <div className="h-full rounded-full transition-all"
                              style={{
                                width: `${Math.min(100, (h.current_cpu_pct! / h.target_cpu_pct!) * 100)}%`,
                                backgroundColor: cpuHigh ? '#f59e0b' : '#10b981',
                              }} />
                          </div>
                        </div>
                      ) : <span className="text-slate-500">—</span>}
                    </td>
                    <td className="px-4 py-3 text-xs">
                      {blocked ? (
                        <span className="px-1.5 py-0.5 rounded bg-red-900/40 text-red-400 text-[10px]" title={ableToScale?.reason}>Blocked</span>
                      ) : scalingLimited ? (
                        <span className="px-1.5 py-0.5 rounded bg-yellow-900/40 text-yellow-400 text-[10px]">Limited</span>
                      ) : (
                        <span className="px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-400 text-[10px]">Active</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">{_relTime(h.created_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1 justify-end">
                        <ActionBtn icon={Pencil} title="Edit min/max/CPU" onClick={() => setEditHpa(h)} />
                        <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'horizontalpodautoscalers', name: h.name, namespace: h.namespace })} />
                        <ActionBtn icon={Trash2} title="Delete HPA" danger onClick={() => handleDelete(h)} />
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── PDB Section ───────────────────────────────────────────────────────────────

function CreatePDBModal({ clusterId, namespace, onClose, onCreated }: {
  clusterId: string; namespace: string; onClose: () => void; onCreated: () => void
}) {
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [selectorRaw, setSelectorRaw] = useState('')  // "app=web,tier=frontend"
  const [mode, setMode] = useState<'min' | 'max'>('min')
  const [value, setValue] = useState('1')

  async function create() {
    if (!name || !selectorRaw) { toast.error('Name and selector labels are required'); return }
    const selector: Record<string, string> = {}
    for (const pair of selectorRaw.split(',')) {
      const [k, v] = pair.trim().split('=')
      if (k && v) selector[k.trim()] = v.trim()
    }
    if (!Object.keys(selector).length) { toast.error('Selector must be key=value pairs, e.g. app=web'); return }
    const body: Record<string, unknown> = { name, selector }
    const parsed = value.includes('%') ? value : (isNaN(+value) ? value : +value)
    if (mode === 'min') body.min_available = parsed
    else body.max_unavailable = parsed
    setSaving(true)
    try {
      const ns = namespace || 'default'
      const r1 = await vksPost<{ requires_confirm: boolean; token: string }>(`${clusterId}/pdbs?namespace=${ns}`, body)
      if (r1.requires_confirm) {
        const r2 = await vksPost<{ ok: boolean }>(`${clusterId}/pdbs?namespace=${ns}&token=${r1.token}`, body)
        if (r2.ok) { toast.success(`PDB ${name} created`); onCreated(); onClose() }
      }
    } catch (e) { toast.error(`Create failed: ${e}`) }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
          <MinusCircle size={14} className="text-blue-400" />
          <span className="text-sm font-medium text-white">Create PodDisruptionBudget</span>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">PDB Name</label>
            <input value={name} onChange={e => setName(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500"
              placeholder="my-pdb" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Selector Labels</label>
            <input value={selectorRaw} onChange={e => setSelectorRaw(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-blue-500"
              placeholder="app=web,tier=frontend" />
            <p className="text-[10px] text-slate-600 mt-1">Comma-separated key=value pairs matching pod labels</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Policy</label>
              <select value={mode} onChange={e => setMode(e.target.value as 'min' | 'max')}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-blue-500">
                <option value="min">minAvailable</option>
                <option value="max">maxUnavailable</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Value (int or %)</label>
              <input value={value} onChange={e => setValue(e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-blue-500"
                placeholder="1 or 50%" />
            </div>
          </div>
          <div className="p-3 bg-slate-800/60 rounded-lg text-[10px] text-slate-400">
            <span className="text-slate-300 font-medium">OpenShift note:</span> PDBs work identically in OpenShift and K8s — same policy/v1 API since OCP 4.9+.
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-800">
          <button onClick={onClose} className="px-4 py-2 text-xs text-slate-400 hover:text-white">Cancel</button>
          <button onClick={create} disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50">
            {saving ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} Create PDB
          </button>
        </div>
      </div>
    </div>
  )
}

function PDBSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [pdbs, setPdbs] = useState<PDBInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filterText, setFilterText] = useState('')
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const { sortKey, sortDir, onSort } = useSort()
  const toast = useToast()

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ pdbs: PDBInfo[] }>(`${clusterId}/pdbs?namespace=${namespace}`)
      .then(d => { setPdbs(d.pdbs); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  const visible = useSortedFiltered(pdbs, filterText, ['name', 'namespace'], sortKey, sortDir)

  async function handleDelete(p: PDBInfo) {
    try {
      const r1 = await vksDelete<{ requires_confirm: boolean; token: string }>(`${clusterId}/pdbs/${p.name}?namespace=${p.namespace}`)
      if (r1.requires_confirm) {
        setConfirm({ token: r1.token, action: 'delete', target: p.name, description: `Delete PDB ${p.name} in ${p.namespace}`, onConfirm: async (token) => {
          await vksDelete(`${clusterId}/pdbs/${p.name}?namespace=${p.namespace}&token=${token}`)
          toast.success(`Deleted PDB ${p.name}`); load()
        }})
      }
    } catch (e) { toast.error(`Delete failed: ${e}`) }
  }

  function pdbStatus(p: PDBInfo) {
    if (p.current_healthy < p.desired_healthy)
      return { label: 'Disrupted', cls: 'bg-red-900/40 text-red-400' }
    if (p.disruptions_allowed === 0)
      return { label: 'Protected', cls: 'bg-yellow-900/40 text-yellow-400' }
    return { label: 'Healthy', cls: 'bg-emerald-900/40 text-emerald-400' }
  }

  function selectorStr(sel: Record<string, string>) {
    const entries = Object.entries(sel)
    if (!entries.length) return '(all pods)'
    return entries.map(([k, v]) => `${k}=${v}`).join(', ')
  }

  return (
    <div className="space-y-4">
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
      {showCreate && <CreatePDBModal clusterId={clusterId} namespace={namespace || 'default'} onClose={() => setShowCreate(false)} onCreated={load} />}
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      <div className="flex items-center gap-2">
        <input
          value={filterText} onChange={e => setFilterText(e.target.value)} placeholder="Filter disruption budgets…"
          className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <button onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 text-xs rounded-lg border border-blue-600/30">
          <Plus size={12} /> Add PDB
        </button>
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loading ? <SkeletonRows rows={4} /> : !loadErr && pdbs.length === 0 ? (
        <EmptyState icon={MinusCircle} title="No Disruption Budgets" hint="No PodDisruptionBudgets found. PDBs protect workloads during node drains and rolling updates."
          action={<button onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-4 py-2 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 text-xs rounded-lg border border-blue-600/30">
            <Plus size={12} /> Create PDB
          </button>} />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <SortableHeader col="name" label="Name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                {!namespace && <SortableHeader col="namespace" label="Namespace" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />}
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Selector</th>
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Policy</th>
                <SortableHeader col="current_healthy" label="Pods" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <SortableHeader col="disruptions_allowed" label="Disruptions" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Status</th>
                <SortableHeader col="created_at" label="Age" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(p => {
                const st = pdbStatus(p)
                return (
                  <tr key={`${p.namespace}/${p.name}`} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                    <td className="px-4 py-3 font-mono text-white text-sm">{p.name}</td>
                    {!namespace && <td className="px-4 py-3 text-slate-400 text-xs">{p.namespace}</td>}
                    <td className="px-4 py-3 text-xs text-slate-400 font-mono max-w-[200px] truncate" title={selectorStr(p.selector)}>{selectorStr(p.selector)}</td>
                    <td className="px-4 py-3 text-xs text-slate-300">
                      {p.min_available != null
                        ? <span>min-available: <span className="text-white font-mono">{p.min_available}</span></span>
                        : p.max_unavailable != null
                          ? <span>max-unavailable: <span className="text-white font-mono">{p.max_unavailable}</span></span>
                          : <span className="text-slate-500">—</span>}
                    </td>
                    <td className="px-4 py-3 text-xs">
                      <span className="text-white">{p.current_healthy}</span>
                      <span className="text-slate-500">/{p.expected_pods}</span>
                      <span className="text-slate-600 ml-1">(need {p.desired_healthy})</span>
                    </td>
                    <td className="px-4 py-3 text-xs">
                      <span className={`font-mono font-semibold ${p.disruptions_allowed > 0 ? 'text-emerald-400' : 'text-yellow-400'}`}>
                        {p.disruptions_allowed}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] ${st.cls}`}>{st.label}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">{_relTime(p.created_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1 justify-end">
                        <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'poddisruptionbudgets', name: p.name, namespace: p.namespace })} />
                        <ActionBtn icon={Trash2} title="Delete PDB" danger onClick={() => handleDelete(p)} />
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── LimitRanges Section ───────────────────────────────────────────────────────

function LimitRangesSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [lrs, setLrs] = useState<LimitRangeInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [filterText, setFilterText] = useState('')
  const [yamlTarget, setYamlTarget] = useState<YamlViewerTarget | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ limitranges: LimitRangeInfo[] }>(`${clusterId}/limitranges?namespace=${namespace}`)
      .then(d => { setLrs(d.limitranges); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 60_000)

  const lower = filterText.toLowerCase()
  const visible = filterText ? lrs.filter(l => l.name.toLowerCase().includes(lower) || l.namespace.toLowerCase().includes(lower)) : lrs

  function toggleExpand(key: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  function fmtResource(val: string | undefined) {
    if (!val) return '—'
    return val
  }

  function LimitRow({ label, entry }: { label: string; entry: LimitEntry }) {
    const hasCpu = entry.default['cpu'] || entry.max['cpu'] || entry.min['cpu'] || entry.default_request['cpu']
    const hasMem = entry.default['memory'] || entry.max['memory'] || entry.min['memory'] || entry.default_request['memory']
    return (
      <div className="mt-2 first:mt-0">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-blue-400 mb-1">{label}</div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[10px] text-slate-500">
              <th className="text-left pb-1 pr-4 font-normal">Resource</th>
              <th className="text-right pb-1 pr-4 font-normal">Default Req</th>
              <th className="text-right pb-1 pr-4 font-normal">Default Lim</th>
              <th className="text-right pb-1 pr-4 font-normal">Min</th>
              <th className="text-right pb-1 font-normal">Max</th>
            </tr>
          </thead>
          <tbody>
            {hasCpu && (
              <tr>
                <td className="pr-4 text-slate-400 font-mono">cpu</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{fmtResource(entry.default_request['cpu'])}</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{fmtResource(entry.default['cpu'])}</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{fmtResource(entry.min['cpu'])}</td>
                <td className="text-right text-slate-300 font-mono">{fmtResource(entry.max['cpu'])}</td>
              </tr>
            )}
            {hasMem && (
              <tr>
                <td className="pr-4 text-slate-400 font-mono">memory</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{fmtResource(entry.default_request['memory'])}</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{fmtResource(entry.default['memory'])}</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{fmtResource(entry.min['memory'])}</td>
                <td className="text-right text-slate-300 font-mono">{fmtResource(entry.max['memory'])}</td>
              </tr>
            )}
            {!hasCpu && !hasMem && Object.entries(entry.default).map(([k, v]) => (
              <tr key={k}>
                <td className="pr-4 text-slate-400 font-mono">{k}</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{fmtResource(entry.default_request[k])}</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{v}</td>
                <td className="text-right pr-4 text-slate-300 font-mono">{fmtResource(entry.min[k])}</td>
                <td className="text-right text-slate-300 font-mono">{fmtResource(entry.max[k])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {yamlTarget && <YamlViewerModal target={yamlTarget} onClose={() => setYamlTarget(null)} />}
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      <div className="flex items-center gap-2">
        <input
          value={filterText} onChange={e => setFilterText(e.target.value)} placeholder="Filter limit ranges…"
          className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loading ? <SkeletonRows rows={3} /> : !loadErr && lrs.length === 0 ? (
        <EmptyState icon={Cpu} title="No Limit Ranges" hint="No LimitRanges found in this namespace." />
      ) : !loadErr ? (
        <div className="space-y-2">
          {visible.map(lr => {
            const key = `${lr.namespace}/${lr.name}`
            const open = expanded.has(key)
            return (
              <div key={key} className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
                <div
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-800/30 select-none"
                  onClick={() => toggleExpand(key)}
                >
                  {open ? <ChevronDown size={14} className="text-slate-400 flex-shrink-0" /> : <ChevronRight size={14} className="text-slate-400 flex-shrink-0" />}
                  <span className="font-mono text-white text-sm flex-1">{lr.name}</span>
                  {!namespace && <span className="text-xs text-slate-400 mr-3">{lr.namespace}</span>}
                  <span className="text-xs text-slate-500">{lr.limits.length} limit{lr.limits.length !== 1 ? 's' : ''}</span>
                  <div className="flex gap-1 ml-2">
                    {lr.limits.map(l => (
                      <span key={l.type} className="px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400 text-[10px]">{l.type}</span>
                    ))}
                  </div>
                  <span className="text-xs text-slate-500 ml-3">{_relTime(lr.created_at)}</span>
                  <div className="flex gap-1 ml-2" onClick={e => e.stopPropagation()}>
                    <ActionBtn icon={FileText} title="View YAML" onClick={() => setYamlTarget({ clusterId, kind: 'limitranges', name: lr.name, namespace: lr.namespace })} />
                  </div>
                </div>
                {open && (
                  <div className="px-6 py-4 border-t border-slate-800 bg-slate-900/40 space-y-4">
                    {lr.limits.map((entry, i) => (
                      <LimitRow key={i} label={entry.type} entry={entry} />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

// ── Image Catalog Section ─────────────────────────────────────────────────────

function ImagesSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [images, setImages] = useState<ImageInfo[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filterText, setFilterText] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [showLatestOnly, setShowLatestOnly] = useState(false)
  const { sortKey, sortDir, onSort } = useSort('pod_count')

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ images: ImageInfo[]; total: number }>(`${clusterId}/images?namespace=${namespace}`)
      .then(d => { setImages(d.images); setTotal(d.total); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 60_000)

  function toggleExpand(key: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  const lower = filterText.toLowerCase()
  let visible = images.filter(img => {
    if (showLatestOnly && !img.is_latest) return false
    if (lower && !img.image.toLowerCase().includes(lower)) return false
    return true
  })
  if (sortKey) {
    visible = [...visible].sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[sortKey]
      const bv = (b as unknown as Record<string, unknown>)[sortKey]
      const cmp = String(av ?? '').localeCompare(String(bv ?? ''), undefined, { numeric: true })
      return sortDir === 'asc' ? cmp : -cmp
    })
  }

  const latestCount = images.filter(i => i.is_latest).length

  return (
    <div className="space-y-4">
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={filterText} onChange={e => setFilterText(e.target.value)} placeholder="Filter images…"
          className="flex-1 min-w-[140px] bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <button
          onClick={() => setShowLatestOnly(v => !v)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors ${showLatestOnly ? 'bg-yellow-700/50 text-yellow-200' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
        >
          <AlertTriangle size={11} />
          :latest ({latestCount})
        </button>
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {latestCount > 0 && (
        <div className="flex items-center gap-2 px-3 py-2 bg-yellow-900/20 border border-yellow-700/30 rounded-lg text-xs text-yellow-300">
          <AlertTriangle size={12} />
          <span>{latestCount} image{latestCount > 1 ? 's' : ''} use <code className="font-mono">:latest</code> tag — pin to a specific version for reproducible deployments.</span>
        </div>
      )}
      {loading ? <SkeletonRows rows={5} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Upload} title="No Images Found" hint="No container images found in this namespace." />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <div className="px-4 py-2 border-b border-slate-800 flex items-center justify-between">
            <span className="text-[10px] text-slate-500">{visible.length} of {total} images</span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="w-6 px-2 py-2" />
                <SortableHeader col="short" label="Image" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <SortableHeader col="tag" label="Tag" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <SortableHeader col="pod_count" label="Pods" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Namespaces</th>
                <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-slate-400">Risk</th>
                <th className="px-4 py-2">
                  <CopyButton text={visible.map(i => i.image).join('\n')} />
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map(img => {
                const key = img.image
                const open = expanded.has(key)
                return (
                  <>
                    <tr key={key} className={`border-b border-slate-800/40 hover:bg-slate-800/20 cursor-pointer ${open ? 'bg-slate-800/10' : ''}`} onClick={() => toggleExpand(key)}>
                      <td className="px-2 py-3 text-slate-500">
                        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      </td>
                      <td className="px-4 py-3 font-mono text-white text-xs max-w-[260px] truncate" title={img.image}>
                        {img.short || img.image}
                      </td>
                      <td className="px-4 py-3 text-xs">
                        <span className={`font-mono ${img.is_latest ? 'text-yellow-400' : img.is_pinned ? 'text-emerald-400' : 'text-slate-300'}`}>
                          {img.tag}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-white font-medium">{img.pod_count}</td>
                      <td className="px-4 py-3 text-xs text-slate-400 max-w-[200px] truncate">
                        {(img.namespaces ?? []).join(', ')}
                      </td>
                      <td className="px-4 py-3 text-xs">
                        {img.is_latest ? (
                          <span className="px-1.5 py-0.5 rounded bg-yellow-900/40 text-yellow-400 text-[10px]">:latest</span>
                        ) : img.is_pinned ? (
                          <span className="px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-400 text-[10px]">pinned</span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400 text-[10px]">tagged</span>
                        )}
                      </td>
                      <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                        <CopyButton text={img.image} />
                      </td>
                    </tr>
                    {open && (
                      <tr key={`${key}-detail`} className="border-b border-slate-800/40 bg-slate-900/40">
                        <td colSpan={7} className="px-8 py-3">
                          <div className="font-mono text-[10px] text-slate-500 mb-2 break-all">{img.image}</div>
                          <div className="flex flex-wrap gap-2">
                            {img.pods.map((p, i) => (
                              <span key={i} className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] ${
                                p.phase === 'Running' ? 'bg-emerald-900/30 text-emerald-400' :
                                p.phase === 'Pending' ? 'bg-yellow-900/30 text-yellow-400' :
                                'bg-red-900/30 text-red-400'
                              }`}>
                                {p.namespace}/{p.name}
                              </span>
                            ))}
                            {img.pod_count > 20 && <span className="text-slate-500 text-[10px]">+{img.pod_count - 20} more</span>}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Events Section ────────────────────────────────────────────────────────────

function EventsSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  type EventItem = { type: string; reason: string; message: string; object: string; count: number; last_time: string }
  const [events, setEvents] = useState<EventItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filter, setFilter] = useState<'all' | 'warning'>('all')
  const [search, setSearch] = useState('')
  const [reasonFilter, setReasonFilter] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState<'time' | 'count'>('time')

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ events: EventItem[] }>(`${clusterId}/events?namespace=${namespace}`)
      .then(d => { setEvents(d.events); setLoadErr(null) })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 15_000)

  const warningEvents = events.filter(e => e.type === 'Warning')
  const topReasons = Object.entries(
    warningEvents.reduce<Record<string, number>>((acc, e) => { acc[e.reason] = (acc[e.reason] || 0) + 1; return acc }, {})
  ).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([r]) => r)

  const q = search.toLowerCase()
  let visible = events
    .filter(e => filter === 'all' || e.type === 'Warning')
    .filter(e => !reasonFilter || e.reason === reasonFilter)
    .filter(e => !q || e.object.toLowerCase().includes(q) || e.reason.toLowerCase().includes(q) || e.message.toLowerCase().includes(q))

  if (sortBy === 'count') visible = [...visible].sort((a, b) => b.count - a.count)

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex bg-slate-800 rounded-lg p-0.5">
          {(['all', 'warning'] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-3 py-1.5 text-xs rounded-md capitalize transition-colors ${filter === f ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}>
              {f === 'warning' ? `Warnings (${warningEvents.length})` : 'All'}
            </button>
          ))}
        </div>
        <div className="flex bg-slate-800 rounded-lg p-0.5">
          {(['time', 'count'] as const).map(s => (
            <button key={s} onClick={() => setSortBy(s)}
              className={`px-2.5 py-1.5 text-xs rounded-md transition-colors ${sortBy === s ? 'bg-slate-600 text-white' : 'text-slate-400 hover:text-white'}`}>
              {s === 'time' ? 'Recent' : 'Count'}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Search object, reason, message…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="flex-1 min-w-[150px] px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {topReasons.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {topReasons.map(r => (
            <button key={r}
              onClick={() => setReasonFilter(reasonFilter === r ? null : r)}
              className={`px-2 py-0.5 rounded-full text-[10px] transition-colors ${reasonFilter === r ? 'bg-yellow-700/60 text-yellow-200' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
            >{r}</button>
          ))}
          {reasonFilter && (
            <button onClick={() => setReasonFilter(null)} className="px-2 py-0.5 rounded-full text-[10px] bg-slate-700 text-slate-400 hover:text-slate-200">
              ✕ clear
            </button>
          )}
        </div>
      )}

      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={8} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Activity} title="No events" hint="Events appear here as the cluster runs." />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Type</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Object</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Reason</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Message</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Count</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Last</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((ev, i) => (
                <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                  <td className="px-4 py-2.5">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${ev.type === 'Warning' ? 'bg-yellow-900/40 text-yellow-400' : 'bg-slate-800 text-slate-400'}`}>
                      {ev.type}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-slate-300">{ev.object}</td>
                  <td className="px-4 py-2.5 text-slate-400">{ev.reason}</td>
                  <td className="px-4 py-2.5 text-slate-300 max-w-sm truncate">{ev.message}</td>
                  <td className="px-4 py-2.5 text-slate-400">{ev.count}</td>
                  <td className="px-4 py-2.5 text-slate-500 whitespace-nowrap">{_relTime(ev.last_time)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Audit Log Section ─────────────────────────────────────────────────────────

interface AuditEvent {
  timestamp: number; user: string; verb: string; cluster: string
  namespace: string; kind: string; name: string; status: string
}

function AuditSection({ clusterId }: { clusterId: string }) {
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const [filter, setFilter] = useState('')
  const [limit, setLimit] = useState(100)

  const load = useCallback(() => {
    setLoading(true)
    vksGet<{ events: AuditEvent[] }>(`audit?limit=${limit}`)
      .then(d => {
        const filtered = d.events.filter(e => !clusterId || e.cluster === clusterId || e.cluster.endsWith('/' + clusterId.split('/').pop()))
        setEvents(filtered); setLoadErr(null)
      })
      .catch(e => setLoadErr(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setLoading(false))
  }, [clusterId, limit])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 15_000)

  const q = filter.toLowerCase()
  const visible = q
    ? events.filter(e => e.verb.includes(q) || e.name.includes(q) || e.user.includes(q) || e.kind.toLowerCase().includes(q))
    : events

  function verbColor(verb: string) {
    if (['delete', 'delete-secret', 'delete-pvc'].some(v => verb.startsWith(v))) return 'bg-red-900/40 text-red-400'
    if (['scale', 'restart', 'cordon', 'drain'].includes(verb)) return 'bg-yellow-900/40 text-yellow-400'
    if (['reveal-secret', 'yaml_edit', 'configmap_update'].includes(verb)) return 'bg-orange-900/40 text-orange-400'
    return 'bg-blue-900/40 text-blue-400'
  }

  function handleCsvExport() {
    const header = 'Time,User,Action,Kind,Name,Namespace,Cluster'
    const rows = visible.map(ev => [
      new Date(ev.timestamp * 1000).toISOString(),
      ev.user || '',
      ev.verb,
      ev.kind,
      ev.name,
      ev.namespace || '',
      ev.cluster,
    ].map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))
    const csv = [header, ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `audit-${clusterId.split('/').pop()}-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <input
          type="text"
          placeholder="Filter by verb, resource, or user…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 min-w-[150px] px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
        />
        <select
          value={limit}
          onChange={e => setLimit(Number(e.target.value))}
          className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-300 focus:outline-none"
        >
          {[100, 250, 500].map(n => <option key={n} value={n}>{n} events</option>)}
        </select>
        <button onClick={handleCsvExport} title="Export CSV"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 text-xs">
          <Download size={13} /> CSV
        </button>
        <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {loadErr && !loading && <SectionError error={loadErr} onRetry={load} />}
      {loading ? <SkeletonRows rows={8} /> : !loadErr && visible.length === 0 ? (
        <EmptyState icon={Shield} title="No audit events" hint="Actions taken in this cluster will appear here." />
      ) : !loadErr ? (
        <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Time</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">User</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Action</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Resource</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Name</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Namespace</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((ev, i) => (
                <tr key={i} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                  <td className="px-4 py-2.5 text-slate-500 whitespace-nowrap">{_relTime(new Date(ev.timestamp * 1000).toISOString())}</td>
                  <td className="px-4 py-2.5 text-slate-300 font-mono">{ev.user || '—'}</td>
                  <td className="px-4 py-2.5">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${verbColor(ev.verb)}`}>{ev.verb}</span>
                  </td>
                  <td className="px-4 py-2.5 text-slate-400">{ev.kind}</td>
                  <td className="px-4 py-2.5 font-mono text-white">{ev.name}</td>
                  <td className="px-4 py-2.5 text-slate-400">{ev.namespace || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ── Rollout History Modal ─────────────────────────────────────────────────────

interface RevisionInfo {
  revision: number; name: string; created_at: string
  replicas: number; ready_replicas: number
  images: string[]; change_cause: string
}

function RolloutHistoryModal({ clusterId, deploymentName, namespace, onClose }: {
  clusterId: string; deploymentName: string; namespace: string; onClose: () => void
}) {
  const [revisions, setRevisions] = useState<RevisionInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    vksGet<{ revisions: RevisionInfo[] }>(`${clusterId}/deployments/${encodeURIComponent(deploymentName)}/history?namespace=${namespace}`)
      .then(d => { setRevisions(d.revisions); setErr(null) })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false))
  }, [clusterId, deploymentName, namespace])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog" aria-modal="true" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl shadow-2xl max-h-[80vh] flex flex-col"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
          <RotateCcw size={14} className="text-blue-400" />
          <span className="text-sm font-medium text-white">Rollout History</span>
          <span className="text-slate-500 text-xs font-mono ml-1">{deploymentName}</span>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div className="overflow-y-auto flex-1 p-4">
          {loading && <div className="flex items-center gap-2 text-slate-400 text-sm"><Loader2 size={14} className="animate-spin" />Loading…</div>}
          {err && <div className="text-red-400 text-sm">{err}</div>}
          {!loading && !err && revisions.length === 0 && (
            <div className="text-slate-500 text-sm text-center py-4">No revision history found.</div>
          )}
          {revisions.map(r => (
            <div key={r.revision} className={`border border-slate-700/50 rounded-lg p-4 mb-3 ${r.revision === revisions[0].revision ? 'border-blue-600/40 bg-blue-900/10' : 'bg-slate-900/40'}`}>
              <div className="flex items-center gap-3 mb-2">
                <span className={`px-2 py-0.5 rounded text-xs font-bold ${r.revision === revisions[0].revision ? 'bg-blue-600/30 text-blue-400' : 'bg-slate-800 text-slate-400'}`}>
                  rev {r.revision}
                </span>
                <span className="text-slate-500 text-xs">{_relTime(r.created_at)}</span>
                <span className="text-xs text-slate-400 font-mono ml-auto">{r.ready_replicas}/{r.replicas} ready</span>
              </div>
              {r.change_cause && (
                <div className="text-xs text-slate-400 mb-2 italic">{r.change_cause}</div>
              )}
              <div className="space-y-1">
                {r.images.map((img, i) => (
                  <div key={i} className={`font-mono text-xs ${img.endsWith(':latest') ? 'text-yellow-400' : 'text-slate-300'}`}>
                    {img}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Create Workload Modal ─────────────────────────────────────────────────────

function CreateWorkloadModal({ clusterId, namespace, onClose }: {
  clusterId: string; namespace: string; onClose: () => void
}) {
  const [tab, setTab] = useState<'form' | 'yaml'>('form')
  const [form, setForm] = useState({
    name: '', namespace: namespace || 'default', replicas: 1,
    image: '', tag: 'latest', port: '',
    cpuReq: '100m', memReq: '128Mi', cpuLim: '500m', memLim: '512Mi',
    envVars: [] as { key: string; value: string }[],
  })
  const [yamlText, setYamlText] = useState('')
  const [nlPrompt, setNlPrompt] = useState('')
  const [generating, setGenerating] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const toast = useToast()

  function buildManifest() {
    const manifest: Record<string, unknown> = {
      apiVersion: 'apps/v1',
      kind: 'Deployment',
      metadata: { name: form.name, namespace: form.namespace, labels: { app: form.name } },
      spec: {
        replicas: form.replicas,
        selector: { matchLabels: { app: form.name } },
        template: {
          metadata: { labels: { app: form.name } },
          spec: {
            containers: [{
              name: form.name,
              image: `${form.image}:${form.tag}`,
              ports: form.port ? [{ containerPort: parseInt(form.port) }] : [],
              resources: {
                requests: { cpu: form.cpuReq, memory: form.memReq },
                limits: { cpu: form.cpuLim, memory: form.memLim },
              },
              env: form.envVars.map(e => ({ name: e.key, value: e.value })),
            }],
          },
        },
      },
    }
    // If port set, also generate Service
    if (form.port) {
      return [manifest, {
        apiVersion: 'v1', kind: 'Service',
        metadata: { name: form.name, namespace: form.namespace },
        spec: {
          selector: { app: form.name },
          ports: [{ port: parseInt(form.port), targetPort: parseInt(form.port) }],
        },
      }]
    }
    return [manifest]
  }

  useEffect(() => {
    if (tab === 'yaml' && form.name && form.image) {
      const manifests = buildManifest()
      setYamlText(manifests.map(m => '---\n' + simpleYaml(m)).join('\n'))
    }
  }, [tab, form])

  async function generateFromNL() {
    if (!nlPrompt.trim()) return
    setGenerating(true)
    const es = new EventSource(`${API}/api/v1/vks/generate/manifest`)
    // Actually POST — use fetch+SSE pattern
    es.close()
    try {
      const resp = await fetch(`${API}/api/v1/vks/generate/manifest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: nlPrompt, context: { namespace: form.namespace } }),
      })
      const reader = resp.body?.getReader()
      let full = ''
      if (reader) {
        const decoder = new TextDecoder()
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const chunk = decoder.decode(value)
          for (const line of chunk.split('\n')) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))
                if (data.manifest) { full = data.manifest; setYamlText(full); setTab('yaml') }
              } catch { /* */ }
            }
          }
        }
      }
    } catch (e) { toast.error(`Generate failed: ${e}`) }
    finally { setGenerating(false) }
  }

  async function submit() {
    setSubmitting(true)
    try {
      const yaml = tab === 'yaml' ? yamlText : buildManifest().map(m => simpleYaml(m)).join('\n---\n')
      const resp = await vksPost<{ results: { ok: boolean; error?: string }[] }>(`${clusterId}/apply`, { yaml })
      const errors = resp.results.filter(r => !r.ok)
      if (errors.length > 0) {
        toast.error(`Apply error: ${errors[0].error}`)
      } else {
        toast.success('Workload created')
        onClose()
      }
    } catch (e) { toast.error(`Submit failed: ${e}`) }
    finally { setSubmitting(false) }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center pt-16 overflow-y-auto bg-black/60 backdrop-blur-sm"
      role="dialog" aria-modal="true" onKeyDown={e => e.key === 'Escape' && onClose()}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl mb-8 shadow-2xl">
        <div className="flex items-center gap-2 px-6 py-4 border-b border-slate-800">
          <Plus size={16} className="text-blue-400" />
          <h2 className="text-white font-semibold">Create Workload</h2>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={16} /></button>
        </div>

        {/* NL generator */}
        <div className="px-6 py-4 border-b border-slate-800 bg-blue-950/20">
          <div className="flex gap-2">
            <input
              value={nlPrompt}
              onChange={e => setNlPrompt(e.target.value)}
              placeholder='Describe in plain English, e.g. "nginx with 3 replicas, 256Mi limit, port 80"'
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            <button onClick={generateFromNL} disabled={generating || !nlPrompt.trim()}
              className="px-3 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg flex items-center gap-1.5">
              {generating ? <Loader2 size={14} className="animate-spin" /> : <BotMessageSquare size={14} />}
              Generate
            </button>
          </div>
        </div>

        {/* Form / YAML tabs */}
        <div className="flex border-b border-slate-800">
          {(['form', 'yaml'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-5 py-3 text-sm capitalize ${tab === t ? 'border-b-2 border-blue-500 text-white' : 'text-slate-400 hover:text-white'}`}>
              {t === 'yaml' ? 'Edit as YAML' : 'Form'}
            </button>
          ))}
        </div>

        <div className="px-6 py-5">
          {tab === 'form' ? (
            <div className="grid gap-4">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Name" value={form.name} onChange={v => setForm(f => ({ ...f, name: v }))} placeholder="my-app" />
                <Field label="Namespace" value={form.namespace} onChange={v => setForm(f => ({ ...f, namespace: v }))} placeholder="default" />
              </div>
              <div className="grid grid-cols-3 gap-4">
                <Field label="Image" value={form.image} onChange={v => setForm(f => ({ ...f, image: v }))} placeholder="nginx" className="col-span-2" />
                <Field label="Tag" value={form.tag} onChange={v => setForm(f => ({ ...f, tag: v }))} placeholder="latest" />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Replicas" value={String(form.replicas)} onChange={v => setForm(f => ({ ...f, replicas: parseInt(v) || 1 }))} placeholder="1" type="number" />
                <Field label="Port (optional)" value={form.port} onChange={v => setForm(f => ({ ...f, port: v }))} placeholder="8080" />
              </div>
              <div className="grid grid-cols-4 gap-3">
                <Field label="CPU Request" value={form.cpuReq} onChange={v => setForm(f => ({ ...f, cpuReq: v }))} placeholder="100m" />
                <Field label="Mem Request" value={form.memReq} onChange={v => setForm(f => ({ ...f, memReq: v }))} placeholder="128Mi" />
                <Field label="CPU Limit" value={form.cpuLim} onChange={v => setForm(f => ({ ...f, cpuLim: v }))} placeholder="500m" />
                <Field label="Mem Limit" value={form.memLim} onChange={v => setForm(f => ({ ...f, memLim: v }))} placeholder="512Mi" />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-2">Environment Variables</label>
                {form.envVars.map((ev, i) => (
                  <div key={i} className="flex gap-2 mb-2">
                    <input value={ev.key} onChange={e => setForm(f => ({ ...f, envVars: f.envVars.map((x, j) => j === i ? { ...x, key: e.target.value } : x) }))}
                      placeholder="KEY" className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-white" />
                    <input value={ev.value} onChange={e => setForm(f => ({ ...f, envVars: f.envVars.map((x, j) => j === i ? { ...x, value: e.target.value } : x) }))}
                      placeholder="value" className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-white" />
                    <button onClick={() => setForm(f => ({ ...f, envVars: f.envVars.filter((_, j) => j !== i) }))}
                      className="text-slate-500 hover:text-red-400"><X size={14} /></button>
                  </div>
                ))}
                <button onClick={() => setForm(f => ({ ...f, envVars: [...f.envVars, { key: '', value: '' }] }))}
                  className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1">
                  <Plus size={12} /> Add variable
                </button>
              </div>
            </div>
          ) : (
            <textarea
              value={yamlText}
              onChange={e => setYamlText(e.target.value)}
              className="w-full h-72 font-mono text-xs bg-slate-800 border border-slate-700 rounded-lg p-3 text-emerald-300 placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
              placeholder="# Paste or generate YAML here"
            />
          )}
        </div>

        <div className="flex justify-end gap-2 px-6 pb-5">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm rounded-lg">Cancel</button>
          <button onClick={submit} disabled={submitting}
            className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg">
            {submitting ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle size={14} />}
            Apply
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Import YAML Modal ─────────────────────────────────────────────────────────

function ImportYamlModal({ clusterId, onClose }: { clusterId: string; onClose: () => void }) {
  const [yaml, setYaml] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [results, setResults] = useState<{ kind: string; name: string; ok: boolean; error?: string }[]>([])
  const toast = useToast()

  async function apply() {
    if (!yaml.trim()) return
    setSubmitting(true)
    try {
      const resp = await vksPost<{ results: typeof results }>(`${clusterId}/apply`, { yaml })
      setResults(resp.results)
      const ok = resp.results.filter(r => r.ok).length
      const fail = resp.results.filter(r => !r.ok).length
      if (fail === 0) toast.success(`Applied ${ok} resource(s)`)
      else toast.error(`${ok} applied, ${fail} failed`)
    } catch (e) { toast.error(`Apply failed: ${e}`) }
    finally { setSubmitting(false) }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog" aria-modal="true" onKeyDown={e => e.key === 'Escape' && onClose()}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl shadow-2xl">
        <div className="flex items-center gap-2 px-6 py-4 border-b border-slate-800">
          <Upload size={16} className="text-blue-400" />
          <h2 className="text-white font-semibold">Import YAML</h2>
          <button onClick={onClose} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={16} /></button>
        </div>
        <div className="p-6 space-y-4">
          <textarea
            value={yaml}
            onChange={e => setYaml(e.target.value)}
            className="w-full h-64 font-mono text-xs bg-slate-800 border border-slate-700 rounded-lg p-3 text-emerald-300 placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
            placeholder="# Paste YAML here — supports multiple documents (---)"
          />
          {results.length > 0 && (
            <div className="space-y-1">
              {results.map((r, i) => (
                <div key={i} className={`flex items-center gap-2 text-xs ${r.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                  {r.ok ? <CheckCircle size={12} /> : <XCircle size={12} />}
                  <span>{r.kind}/{r.name}</span>
                  {r.error && <span className="text-slate-500">{r.error}</span>}
                </div>
              ))}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="px-4 py-2 bg-slate-700 text-slate-300 text-sm rounded-lg hover:bg-slate-600">Cancel</button>
            <button onClick={apply} disabled={submitting || !yaml.trim()}
              className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg">
              {submitting ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              Apply
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function ActionBtn({ icon: Icon, title, onClick, danger = false }: {
  icon: React.ElementType; title: string; onClick: () => void; danger?: boolean
}) {
  return (
    <button
      title={title}
      onClick={e => { e.stopPropagation(); onClick() }}
      aria-label={title}
      className={`p-1.5 rounded transition-colors ${danger ? 'text-slate-500 hover:text-red-400 hover:bg-red-900/20' : 'text-slate-500 hover:text-white hover:bg-slate-700'}`}
    >
      <Icon size={13} />
    </button>
  )
}

function Field({ label, value, onChange, placeholder, type = 'text', className = '' }: {
  label: string; value: string; onChange: (v: string) => void
  placeholder?: string; type?: string; className?: string
}) {
  return (
    <div className={className}>
      <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
      />
    </div>
  )
}

function _relTime(ts: string): string {
  if (!ts) return ''
  const diff = Date.now() - new Date(ts).getTime()
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

function _cronNextRun(schedule: string): string {
  try {
    const shortcuts: Record<string, string> = {
      '@hourly': '0 * * * *', '@daily': '0 0 * * *', '@midnight': '0 0 * * *',
      '@weekly': '0 0 * * 0', '@monthly': '0 0 1 * *', '@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *',
    }
    const expr = shortcuts[schedule.trim()] ?? schedule.trim()
    const parts = expr.split(/\s+/)
    if (parts.length !== 5) return '?'
    const [minP, hourP, domP, monP, dowP] = parts
    const now = new Date()
    for (let i = 1; i <= 525600; i++) {
      const t = new Date(now.getTime() + i * 60000)
      const matches = (p: string, v: number, max: number): boolean => {
        if (p === '*') return true
        return p.split(',').some(seg => {
          if (seg.includes('/')) {
            const [range, step] = seg.split('/')
            const start = range === '*' ? 0 : parseInt(range)
            return (v - start) % parseInt(step) === 0 && v >= start
          }
          if (seg.includes('-')) { const [a, b] = seg.split('-').map(Number); return v >= a && v <= b }
          return parseInt(seg) === v
        })
      }
      if (
        matches(minP, t.getMinutes(), 59) &&
        matches(hourP, t.getHours(), 23) &&
        matches(domP, t.getDate(), 31) &&
        matches(monP, t.getMonth() + 1, 12) &&
        matches(dowP, t.getDay(), 6)
      ) {
        const diffMs = t.getTime() - now.getTime()
        const m = Math.round(diffMs / 60000)
        if (m < 60) return `in ${m}m`
        if (m < 1440) return `in ${Math.round(m / 60)}h`
        return `in ${Math.round(m / 1440)}d`
      }
    }
    return '?'
  } catch { return '?' }
}

function simpleYaml(obj: unknown, indent = 0): string {
  if (obj === null || obj === undefined) return 'null'
  if (typeof obj === 'boolean') return String(obj)
  if (typeof obj === 'number') return String(obj)
  if (typeof obj === 'string') {
    if (obj.includes('\n') || obj.includes(':') || obj.startsWith('"')) return `"${obj.replace(/"/g, '\\"')}"`
    return obj
  }
  if (Array.isArray(obj)) {
    if (obj.length === 0) return '[]'
    return obj.map(item => `${' '.repeat(indent)}- ${simpleYaml(item, indent + 2)}`).join('\n')
  }
  if (typeof obj === 'object') {
    const entries = Object.entries(obj as Record<string, unknown>).filter(([, v]) => v !== undefined && v !== null)
    if (entries.length === 0) return '{}'
    return entries.map(([k, v]) => {
      const valStr = simpleYaml(v, indent + 2)
      if (typeof v === 'object' && v !== null && !Array.isArray(v)) {
        return `${' '.repeat(indent)}${k}:\n${valStr}`
      }
      if (Array.isArray(v) && (v as unknown[]).length > 0) {
        return `${' '.repeat(indent)}${k}:\n${valStr}`
      }
      return `${' '.repeat(indent)}${k}: ${valStr}`
    }).join('\n')
  }
  return String(obj)
}

// ── ImportKubeconfigModal ─────────────────────────────────────────────────────

function ImportKubeconfigModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit() {
    if (!name.trim()) { toast.error('Name is required'); return }
    if (!yaml.trim()) { toast.error('Kubeconfig YAML is required'); return }
    setLoading(true)
    try {
      await vksPost('clusters/import', { name: name.trim(), kubeconfig_yaml: yaml })
      toast.success(`Cluster "${name}" imported`)
      onImported()
      onClose()
    } catch (e: unknown) {
      toast.error(`Import failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-[#0f1629] border border-slate-700 rounded-xl w-full max-w-lg shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
          <h2 className="text-sm font-semibold text-white">Import Kubeconfig</h2>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300"><X size={16} /></button>
        </div>
        <div className="p-5 space-y-4">
          <p className="text-xs text-slate-400">Paste a kubeconfig for any accessible cluster. It will be stored server-side and remain available even if the supervisor is unreachable.</p>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Cluster name</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="my-cluster"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Kubeconfig YAML</label>
            <textarea
              value={yaml}
              onChange={e => setYaml(e.target.value)}
              rows={10}
              placeholder="apiVersion: v1&#10;kind: Config&#10;..."
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-300 placeholder-slate-600 font-mono outline-none focus:border-blue-500 resize-none"
            />
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-slate-700">
          <button onClick={onClose} className="px-4 py-2 text-xs text-slate-400 hover:text-white rounded-lg">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg flex items-center gap-2"
          >
            {loading && <Loader2 size={12} className="animate-spin" />}
            Import
          </button>
        </div>
      </div>
    </div>
  )
}


// ── Topology Section ─────────────────────────────────────────────────────────

interface TopoWorkload { id: string; kind: string; name: string; namespace: string; selector: Record<string, string> }
interface TopoService { id: string; name: string; namespace: string; selector: Record<string, string>; targets: string[]; type: string; ports: string[]; has_selector: boolean }
interface TopoIngress { id: string; name: string; namespace: string; rules: Array<{ host: string; path: string; service: string; port: string | number }> }

function TopologySection({ clusterId, namespace, onNavigate }: { clusterId: string; namespace: string; onNavigate?: (s: PlatformSection) => void }) {
  const [workloads, setWorkloads] = useState<TopoWorkload[]>([])
  const [services, setServices] = useState<TopoService[]>([])
  const [ingresses, setIngresses] = useState<TopoIngress[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  function toggleExpanded(id: string) {
    setExpanded(prev => {
      const s = new Set(prev)
      s.has(id) ? s.delete(id) : s.add(id)
      return s
    })
  }

  useEffect(() => {
    setLoading(true)
    setError(null)
    const q = namespace ? `?namespace=${namespace}` : ''
    vksGet<{ workloads: TopoWorkload[]; services: TopoService[]; ingresses: TopoIngress[] }>(`${clusterId}/topology${q}`)
      .then(d => { setWorkloads(d.workloads); setServices(d.services); setIngresses(d.ingresses) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  if (loading) return <div className="flex items-center justify-center h-48"><Loader2 className="animate-spin text-slate-500" size={24} /></div>
  if (error) return <div className="p-6 text-red-400 text-sm">{error}</div>

  const wMap = new Map(workloads.map(w => [w.id, w]))
  const sMap = new Map(services.map(s => [s.id, s]))

  // Build ingress → service → workload chains
  const chains: Array<{ ingress: TopoIngress | null; service: TopoService; workloadIds: string[] }> = []
  const servicesCoveredByIngress = new Set<string>()

  for (const ing of ingresses) {
    const serviceNames = [...new Set(ing.rules.map(r => r.service))]
    for (const svcName of serviceNames) {
      const svc = services.find(s => s.name === svcName && s.namespace === ing.namespace)
      if (svc) {
        servicesCoveredByIngress.add(svc.id)
        chains.push({ ingress: ing, service: svc, workloadIds: svc.targets })
      }
    }
  }
  // Services not reached by ingress
  for (const svc of services) {
    if (!servicesCoveredByIngress.has(svc.id)) {
      chains.push({ ingress: null, service: svc, workloadIds: svc.targets })
    }
  }

  const kindColor = (k: string) => k === 'Deployment' ? 'bg-blue-900/60 text-blue-300 border-blue-700' : 'bg-purple-900/60 text-purple-300 border-purple-700'

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-white font-semibold text-lg flex items-center gap-2">
          <Network size={18} className="text-blue-400" /> Service Topology
        </h2>
        <span className="text-xs text-slate-500">{ingresses.length} ingresses · {services.length} services · {workloads.length} workloads</span>
      </div>
      {chains.length === 0 && (
        <div className="text-center text-slate-500 py-16 text-sm">No services found in this namespace.</div>
      )}
      {chains.map((chain, ci) => (
        <div key={ci} className="bg-slate-800/40 border border-slate-700/50 rounded-xl overflow-hidden">
          {/* Ingress row */}
          {chain.ingress && (
            <div className="flex items-start gap-3 px-4 py-3 bg-slate-700/20 border-b border-slate-700/40">
              <Globe size={14} className="text-green-400 mt-0.5 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <span className="text-xs font-semibold text-green-300">{chain.ingress.name}</span>
                <span className="text-[10px] text-slate-500 ml-2">Ingress · {chain.ingress.namespace}</span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {chain.ingress.rules.map((r, ri) => (
                    <span key={ri} className="text-[10px] bg-slate-700 text-slate-400 rounded px-2 py-0.5 font-mono">
                      {r.host || '*'}{r.path || '/'} → :{r.port}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}
          {/* Service row */}
          <div
            className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-700/20 transition-colors"
            onClick={() => toggleExpanded(chain.service.id)}
          >
            {chain.ingress && <div className="w-4 flex-shrink-0 border-l-2 border-slate-600 h-4 ml-3" />}
            <Globe size={14} className="text-cyan-400 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <span className="text-sm font-medium text-white">{chain.service.name}</span>
              <span className="text-[10px] text-slate-500 ml-2">Service · {chain.service.type}</span>
              {chain.service.ports?.length > 0 && <span className="text-[10px] text-slate-600 ml-2">{chain.service.ports.join(', ')}</span>}
            </div>
            <span className="text-xs text-slate-500">{chain.workloadIds.length} workloads</span>
            {onNavigate && (
              <button
                onClick={e => { e.stopPropagation(); onNavigate('services') }}
                title="Go to Services section"
                className="px-1.5 py-0.5 rounded text-[10px] bg-cyan-900/30 text-cyan-400 hover:bg-cyan-900/60 border border-cyan-800/40 flex-shrink-0"
              >→ Services</button>
            )}
            <ChevronDown size={14} className={`text-slate-500 transition-transform ${expanded.has(chain.service.id) ? 'rotate-180' : ''}`} />
          </div>
          {/* Workloads */}
          {expanded.has(chain.service.id) && chain.workloadIds.length > 0 && (
            <div className="border-t border-slate-700/40 divide-y divide-slate-700/30">
              {chain.workloadIds.map(wid => {
                const w = wMap.get(wid)
                if (!w) return null
                return (
                  <div key={wid} className="flex items-center gap-3 px-6 py-2.5 bg-slate-800/20">
                    <div className="w-6 flex-shrink-0 flex justify-end">
                      <div className="w-0.5 h-full bg-slate-600" />
                    </div>
                    <Box size={13} className="text-blue-400 flex-shrink-0" />
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${kindColor(w.kind)}`}>{w.kind}</span>
                    <span className="text-sm text-slate-200">{w.name}</span>
                    <span className="text-[10px] text-slate-500">{w.namespace}</span>
                    <div className="ml-auto flex items-center gap-2">
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(w.selector).map(([k, v]) => (
                          <span key={k} className="text-[10px] bg-slate-700/60 text-slate-400 rounded px-1.5 py-0.5 font-mono">{k}={v}</span>
                        ))}
                      </div>
                      {onNavigate && (
                        <button
                          onClick={() => onNavigate('workloads')}
                          title="Go to Workloads section"
                          className="px-1.5 py-0.5 rounded text-[10px] bg-blue-900/30 text-blue-400 hover:bg-blue-900/60 border border-blue-800/40 flex-shrink-0"
                        >→ Workloads</button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
          {expanded.has(chain.service.id) && chain.workloadIds.length === 0 && (
            <div className="px-8 py-2 text-xs text-slate-500 italic border-t border-slate-700/40">No matching workloads found for this service selector.</div>
          )}
        </div>
      ))}
    </div>
  )
}


// ── Pod Traffic / Connection Map ─────────────────────────────────────────────────

interface ConnPod {
  name: string; namespace: string; labels: Record<string, string>
  ip: string; node: string; phase: string
  has_netpol: boolean; netpol_name: string; env_svc_refs: string[]
  rx_bytes: number; tx_bytes: number
}
interface ConnEndpoint { name: string; namespace: string; port: number }
interface ConnService {
  service_name: string; service_namespace: string; service_type: string
  cluster_ip: string; ports: { port: number; protocol: string; target_port: string }[]
  endpoints: ConnEndpoint[]; callers: { name: string; namespace: string }[]
}
interface ConnRec { severity: 'high' | 'medium' | 'low'; type: string; message: string; targets: string[]; fix_yaml?: string; fix_namespace?: string }
interface ConnNetPol { pod_name: string; namespace: string; selector_labels: Record<string, string>; yaml: string }
interface ConnData {
  pods: ConnPod[]
  connections: ConnService[]
  recommendations: ConnRec[]
  generated_netpols: ConnNetPol[]
  summary: { total_pods: number; running_pods: number; protected_pods: number; total_services: number; total_recommendations: number }
}

// Explanation output panel — auto-fetches when `active` flips to true.
// Render it below the YAML; put the trigger button in the toolbar separately.
function NetpolExplainOutput({ clusterId, yaml, podName, namespace, active, onReexplain }: {
  clusterId: string; yaml: string; podName?: string; namespace?: string
  active: boolean; onReexplain?: () => void
}) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const fetch_ = useCallback(async () => {
    setText(''); setErr(''); setLoading(true)
    try {
      const res = await fetch(`/api/v1/vks/${encodeURIComponent(clusterId)}/netpol/explain`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml, pod_name: podName ?? '', namespace: namespace ?? '' }),
      })
      if (!res.ok || !res.body) { setErr(`Error ${res.status}`); setLoading(false); return }
      const reader = res.body.getReader(); const dec = new TextDecoder()
      while (true) {
        const { done: d, value } = await reader.read(); if (d) break
        for (const line of dec.decode(value).split('\n')) {
          if (!line.startsWith('data: ')) continue
          try {
            const evt = JSON.parse(line.slice(6))
            if (evt.text) setText(prev => prev + evt.text)
            if (evt.error) setErr(evt.error)
          } catch { /* skip malformed */ }
        }
      }
    } catch (e: unknown) { setErr(String(e instanceof Error ? e.message : e)) }
    finally { setLoading(false) }
  }, [clusterId, yaml, podName, namespace])

  useEffect(() => { if (active) fetch_() }, [active]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!active) return null
  return (
    <div className="border-t border-violet-900/40 bg-violet-950/20">
      <div className="flex items-center gap-2 px-4 py-2 border-b border-violet-900/30">
        <Sparkles size={11} className="text-violet-400" />
        <span className="text-[10px] font-medium text-violet-300">LLM Policy Explanation</span>
        {loading && <Loader2 size={10} className="animate-spin text-slate-500" />}
        {!loading && (text || err) && (
          <button onClick={() => { onReexplain?.(); fetch_() }}
            className="ml-auto text-[10px] text-slate-500 hover:text-violet-300 transition-colors">Re-explain</button>
        )}
      </div>
      {err && <div className="px-4 py-2 text-[11px] text-red-400">{err}</div>}
      {text && (
        <div className="px-4 py-3 text-[12px] text-slate-300 leading-relaxed prose prose-invert prose-sm max-w-none max-h-72 overflow-y-auto">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}

function _sevColor(s: string) {
  return s === 'high' ? 'text-red-400 bg-red-900/30 border-red-700/40'
    : s === 'medium' ? 'text-amber-400 bg-amber-900/30 border-amber-700/40'
    : 'text-slate-400 bg-slate-800/40 border-slate-700/40'
}

function RecCardList({ recs, clusterId, onApplied }: { recs: ConnRec[]; clusterId: string; onApplied?: () => void }) {
  const [expandedFix, setExpandedFix] = useState<Set<number>>(new Set())
  const [copiedFix, setCopiedFix] = useState<number | null>(null)
  const [applyingFix, setApplyingFix] = useState<number | null>(null)
  const [appliedFix, setAppliedFix] = useState<Set<number>>(new Set())
  const [fixErr, setFixErr] = useState<Record<number, string>>({})
  const [explainOpen, setExplainOpen] = useState<Set<number>>(new Set())

  async function applyFix(i: number, yaml: string, ns: string) {
    setApplyingFix(i); setFixErr(prev => { const n = { ...prev }; delete n[i]; return n })
    try {
      await _applyNetpol(clusterId, yaml, ns)
      setAppliedFix(prev => new Set(prev).add(i))
      onApplied?.()
    } catch (e: unknown) {
      setFixErr(prev => ({ ...prev, [i]: String(e instanceof Error ? e.message : e) }))
    } finally { setApplyingFix(null) }
  }

  const recHints: Record<string, string> = {
    no_netpol: 'The generated policies below each restrict this pod to only known callers. Scroll down to create them.',
    wide_open_ingress: 'The fix policy below replaces the open ingress rule with a scoped selector. Edit the port and podSelector before applying.',
    wide_open_egress: 'The fix policy restricts egress to specific destinations. Adjust the podSelector and ports to match your workload.',
    exposed_no_netpol: 'This pod is reachable from outside the cluster with no network controls. Create a policy below to restrict ingress to only the ingress controller.',
    cross_namespace_traffic: 'Add namespaceSelector rules to NetworkPolicies on both ends of each cross-namespace connection to make the intent explicit.',
  }

  return (
    <div className="space-y-3">
      {recs.map((rec, i) => {
        const isOpen = expandedFix.has(i)
        return (
          <div key={i} className={`border rounded-xl overflow-hidden ${_sevColor(rec.severity)}`}>
            <div className="p-4">
              <div className="flex items-start gap-3">
                <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border ${_sevColor(rec.severity)} flex-shrink-0 mt-0.5`}>{rec.severity}</span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200">{rec.message}</p>
                  {rec.targets.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {rec.targets.map((t, j) => (
                        <span key={j} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">{t}</span>
                      ))}
                    </div>
                  )}
                  <p className="text-[11px] text-slate-500 mt-2">{recHints[rec.type] ?? ''}</p>
                </div>
                {rec.fix_yaml && rec.fix_namespace && (
                  appliedFix.has(i)
                    ? <span className="flex items-center gap-1 px-2.5 py-1 rounded text-[11px] text-emerald-400 flex-shrink-0"><CheckCircle size={11} /> Applied</span>
                    : <button onClick={() => setExpandedFix(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s })}
                        className="flex items-center gap-1 px-2.5 py-1 rounded text-[11px] bg-blue-900/50 hover:bg-blue-800/60 text-blue-300 border border-blue-800 flex-shrink-0">
                        <Shield size={11} /> Fix this
                        <ChevronDown size={11} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                      </button>
                )}
              </div>
            </div>
            {isOpen && rec.fix_yaml && rec.fix_namespace && (
              <div className="border-t border-slate-800/60 bg-slate-950/40">
                <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-800/40">
                  <span className="text-[10px] text-slate-500 flex-1">Review and apply — edit podSelector/ports to match your workload before creating</span>
                  <button onClick={() => { navigator.clipboard.writeText(rec.fix_yaml!); setCopiedFix(i); setTimeout(() => setCopiedFix(null), 1500) }}
                    className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors ${copiedFix === i ? 'text-emerald-400' : 'text-slate-400 hover:text-white'}`}>
                    {copiedFix === i ? <CheckCircle size={11} /> : <Copy size={11} />}
                    {copiedFix === i ? 'Copied' : 'Copy'}
                  </button>
                  <button onClick={() => setExplainOpen(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s })}
                    className={`flex items-center gap-1 px-2.5 py-1 rounded text-[11px] border transition-colors ${explainOpen.has(i) ? 'bg-violet-800/60 text-violet-200 border-violet-700' : 'bg-violet-900/50 hover:bg-violet-800/60 text-violet-300 border-violet-800'}`}>
                    <Sparkles size={11} /> Explain
                  </button>
                  <button onClick={() => applyFix(i, rec.fix_yaml!, rec.fix_namespace!)} disabled={applyingFix === i}
                    className="flex items-center gap-1 px-2.5 py-1 rounded text-[11px] bg-blue-700 hover:bg-blue-600 text-white disabled:opacity-50 transition-colors">
                    {applyingFix === i ? <Loader2 size={11} className="animate-spin" /> : <Shield size={11} />}
                    {applyingFix === i ? 'Applying…' : 'Create Policy'}
                  </button>
                </div>
                {fixErr[i] && (
                  <div className="px-4 py-2 text-[11px] text-red-400 bg-red-950/30 border-b border-red-900/40">{fixErr[i]}</div>
                )}
                <pre className="px-4 py-3 text-[11px] font-mono text-emerald-300 leading-relaxed overflow-x-auto whitespace-pre max-h-72 overflow-y-auto">
                  {rec.fix_yaml}
                </pre>
                <NetpolExplainOutput clusterId={clusterId} yaml={rec.fix_yaml} namespace={rec.fix_namespace} active={explainOpen.has(i)} />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

async function _applyNetpol(clusterId: string, yaml: string, namespace: string) {
  const res = await fetch(`/api/v1/vks/${encodeURIComponent(clusterId)}/netpol/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml, namespace }),
  })
  if (!res.ok) { const t = await res.text(); throw new Error(t) }
  return res.json() as Promise<{ action: string; name: string; namespace: string }>
}

function GeneratedNetPolPanel({ netpols, podData, fmtBytes, clusterId, onApplied }: {
  netpols: ConnNetPol[]
  podData: ConnPod[]
  fmtBytes: (b: number) => string
  clusterId: string
  onApplied?: () => void
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [copied, setCopied] = useState<string | null>(null)
  const [applying, setApplying] = useState<string | null>(null)
  const [applied, setApplied] = useState<Set<string>>(new Set())
  const [applyErr, setApplyErr] = useState<Record<string, string>>({})
  const [explainOpen, setExplainOpen] = useState<Set<string>>(new Set())
  const [downloadAll, setDownloadAll] = useState(false)

  function toggle(key: string) {
    setExpanded(prev => { const s = new Set(prev); s.has(key) ? s.delete(key) : s.add(key); return s })
  }

  function copyYaml(key: string, yaml: string) {
    navigator.clipboard.writeText(yaml).then(() => { setCopied(key); setTimeout(() => setCopied(null), 1500) })
  }

  function downloadYaml(yaml: string, name: string) {
    const blob = new Blob([yaml], { type: 'text/yaml' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
    a.download = `netpol-${name}.yaml`; a.click(); URL.revokeObjectURL(a.href)
  }

  function downloadAllYamls() {
    const combined = netpols.map(n => `# NetworkPolicy for ${n.namespace}/${n.pod_name}\n${n.yaml}`).join('\n---\n')
    const blob = new Blob([combined], { type: 'text/yaml' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
    a.download = 'netpols-generated.yaml'; a.click(); URL.revokeObjectURL(a.href)
  }

  async function applyPolicy(key: string, yaml: string, namespace: string) {
    setApplying(key); setApplyErr(prev => { const n = { ...prev }; delete n[key]; return n })
    try {
      await _applyNetpol(clusterId, yaml, namespace)
      setApplied(prev => new Set(prev).add(key))
      onApplied?.()
    } catch (e: unknown) {
      setApplyErr(prev => ({ ...prev, [key]: String(e instanceof Error ? e.message : e) }))
    } finally {
      setApplying(null)
    }
  }

  return (
    <div className="bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-2 flex-wrap">
        <AlertTriangle size={14} className="text-orange-400" />
        <span className="text-sm font-medium text-slate-300">Generated NetworkPolicies ({netpols.length} unprotected pods)</span>
        <span className="text-xs text-slate-500 flex-1">— ready-to-apply YAML, derived from live service topology and endpoint data</span>
        <button onClick={downloadAllYamls}
          className="flex items-center gap-1 px-2.5 py-1 rounded text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700">
          <Download size={11} /> Download all ({netpols.length})
        </button>
      </div>
      <div className="divide-y divide-slate-800/60">
        {netpols.map(np => {
          const pod = podData.find(p => p.name === np.pod_name && p.namespace === np.namespace)
          const key = `${np.namespace}/${np.pod_name}`
          const isOpen = expanded.has(key)
          const totalBytes = (pod?.rx_bytes ?? 0) + (pod?.tx_bytes ?? 0)
          const selStr = Object.entries(np.selector_labels).map(([k, v]) => `${k}=${v}`).join(', ')
          return (
            <div key={key}>
              <button
                className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-800/30 transition-colors text-left"
                onClick={() => toggle(key)}
              >
                <span className="w-2 h-2 rounded-full bg-orange-500 flex-shrink-0" />
                <span className="font-mono text-sm text-orange-200 flex-1 truncate">{np.pod_name}</span>
                <span className="text-xs text-slate-500">{np.namespace}</span>
                {selStr && <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">{selStr}</span>}
                {totalBytes > 0 && <span className="text-[10px] text-slate-500">↓{fmtBytes(pod?.rx_bytes ?? 0)} ↑{fmtBytes(pod?.tx_bytes ?? 0)}</span>}
                <ChevronDown size={14} className={`text-slate-500 transition-transform flex-shrink-0 ${isOpen ? 'rotate-180' : ''}`} />
              </button>
              {isOpen && (
                <div className="border-t border-slate-800/60 bg-slate-950/40">
                  <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-800/40">
                    <span className="text-[10px] text-slate-500 flex-1">
                      Default-deny ingress+egress · allows known callers · allows DNS · derived from live endpoint graph
                    </span>
                    <button onClick={() => copyYaml(key, np.yaml)}
                      className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors ${copied === key ? 'text-emerald-400' : 'text-slate-400 hover:text-white'}`}>
                      {copied === key ? <CheckCircle size={11} /> : <Copy size={11} />}
                      {copied === key ? 'Copied' : 'Copy'}
                    </button>
                    <button onClick={() => downloadYaml(np.yaml, np.pod_name)}
                      className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-slate-400 hover:text-white">
                      <Download size={11} /> Save
                    </button>
                    <button onClick={() => setExplainOpen(prev => { const s = new Set(prev); s.has(key) ? s.delete(key) : s.add(key); return s })}
                      className={`flex items-center gap-1 px-2.5 py-1 rounded text-[11px] border transition-colors ${explainOpen.has(key) ? 'bg-violet-800/60 text-violet-200 border-violet-700' : 'bg-violet-900/50 hover:bg-violet-800/60 text-violet-300 border-violet-800'}`}>
                      <Sparkles size={11} /> Explain
                    </button>
                    {applied.has(key)
                      ? <span className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-emerald-400"><CheckCircle size={11} /> Applied</span>
                      : <button onClick={() => applyPolicy(key, np.yaml, np.namespace)} disabled={applying === key}
                          className="flex items-center gap-1 px-2.5 py-1 rounded text-[11px] bg-blue-700 hover:bg-blue-600 text-white disabled:opacity-50 transition-colors">
                          {applying === key ? <Loader2 size={11} className="animate-spin" /> : <Shield size={11} />}
                          {applying === key ? 'Applying…' : 'Create Policy'}
                        </button>
                    }
                  </div>
                  {applyErr[key] && (
                    <div className="px-4 py-2 text-[11px] text-red-400 bg-red-950/30 border-b border-red-900/40">{applyErr[key]}</div>
                  )}
                  <pre className="px-4 py-3 text-[11px] font-mono text-emerald-300 leading-relaxed overflow-x-auto whitespace-pre max-h-80 overflow-y-auto">
                    {np.yaml}
                  </pre>
                  <NetpolExplainOutput clusterId={clusterId} yaml={np.yaml} podName={np.pod_name} namespace={np.namespace} active={explainOpen.has(key)} />
                  <div className="px-4 py-2 border-t border-slate-800/40 text-[10px] text-slate-600 font-mono">
                    kubectl apply -f - &lt;&lt;'EOF'<br />{np.yaml}<br />EOF
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Force layout (runs synchronously in useMemo) ───────────────────────────────
interface FNode { id: string; x: number; y: number; type: 'pod' | 'service' }
interface FEdge { source: string; target: string; kind: 'endpoint' | 'caller' }

function runForceLayout(nodes: FNode[], edges: FEdge[], W: number, H: number, iters = 160): Map<string, { x: number; y: number }> {
  if (nodes.length === 0) return new Map()
  const pos = new Map(nodes.map(n => [n.id, { x: n.x, y: n.y }]))
  const vel = new Map(nodes.map(n => [n.id, { vx: 0, vy: 0 }]))
  const ids = nodes.map(n => n.id)

  for (let t = 0; t < iters; t++) {
    const cool = Math.max(0.1, 1 - t / iters)

    // Repulsion
    for (let i = 0; i < ids.length; i++) {
      const a = pos.get(ids[i])!
      for (let j = i + 1; j < ids.length; j++) {
        const b = pos.get(ids[j])!
        const dx = a.x - b.x || 0.01
        const dy = a.y - b.y || 0.01
        const d2 = dx * dx + dy * dy + 1
        const f = 5000 / d2
        const va = vel.get(ids[i])!; const vb = vel.get(ids[j])!
        va.vx += dx * f; va.vy += dy * f
        vb.vx -= dx * f; vb.vy -= dy * f
      }
    }

    // Spring attraction for edges
    const IDEAL = 140
    for (const e of edges) {
      const a = pos.get(e.source); const b = pos.get(e.target)
      if (!a || !b) continue
      const dx = b.x - a.x; const dy = b.y - a.y
      const d = Math.sqrt(dx * dx + dy * dy) + 0.01
      const f = (d - IDEAL) * 0.025
      const va = vel.get(e.source)!; const vb = vel.get(e.target)!
      va.vx += (dx / d) * f; va.vy += (dy / d) * f
      vb.vx -= (dx / d) * f; vb.vy -= (dy / d) * f
    }

    // Gravity + integrate
    for (const id of ids) {
      const p = pos.get(id)!; const v = vel.get(id)!
      v.vx += (W / 2 - p.x) * 0.002; v.vy += (H / 2 - p.y) * 0.002
      v.vx *= 0.82; v.vy *= 0.82
      pos.set(id, {
        x: Math.max(44, Math.min(W - 44, p.x + v.vx * cool)),
        y: Math.max(44, Math.min(H - 44, p.y + v.vy * cool)),
      })
    }
  }
  return pos
}

function PodConnectionMapSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [data, setData] = useState<ConnData | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const [view, setView] = useState<'graph' | 'security'>('graph')
  const svgRef = useRef<SVGSVGElement>(null)
  const W = 980; const H = 620

  const load = useCallback(() => {
    setLoading(true); setErr(null)
    vksGet<ConnData>(`${clusterId}/pod-connections${namespace ? `?namespace=${namespace}` : ''}`)
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setErr(String(e)); setLoading(false) })
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 60_000)

  // Build graph nodes + edges from data
  const { nodes, edges, positions } = useMemo(() => {
    if (!data) return { nodes: [] as FNode[], edges: [] as FEdge[], positions: new Map<string, { x: number; y: number }>() }

    const fnodes: FNode[] = []
    const fedges: FEdge[] = []

    // Seed positions: pods in namespace clusters, services in center ring
    const nsList = [...new Set(data.pods.map(p => p.namespace))]
    const nsAngle = (ns: string) => (nsList.indexOf(ns) / Math.max(nsList.length, 1)) * 2 * Math.PI

    data.pods.forEach((pod, i) => {
      const angle = nsAngle(pod.namespace)
      const nsIdx = nsList.indexOf(pod.namespace)
      const podsInNs = data.pods.filter(p => p.namespace === pod.namespace)
      const podIdx = podsInNs.findIndex(p => p.name === pod.name)
      const spread = Math.min(podsInNs.length * 30, 180)
      const baseAngle = angle + (podIdx - (podsInNs.length - 1) / 2) * (spread / Math.max(podsInNs.length - 1, 1)) * (Math.PI / 180)
      const r = 200 + (nsIdx % 2) * 40
      fnodes.push({
        id: `pod:${pod.namespace}/${pod.name}`,
        type: 'pod',
        x: W / 2 + r * Math.cos(baseAngle) + (Math.random() - 0.5) * 40,
        y: H / 2 + r * Math.sin(baseAngle) + (Math.random() - 0.5) * 40,
      })
    })

    data.connections.forEach((svc, i) => {
      const angle = (i / Math.max(data.connections.length, 1)) * 2 * Math.PI
      fnodes.push({
        id: `svc:${svc.service_namespace}/${svc.service_name}`,
        type: 'service',
        x: W / 2 + 80 * Math.cos(angle) + (Math.random() - 0.5) * 60,
        y: H / 2 + 80 * Math.sin(angle) + (Math.random() - 0.5) * 60,
      })
      // Endpoint edges
      for (const ep of svc.endpoints) {
        const epId = `pod:${ep.namespace}/${ep.name}`
        if (fnodes.some(n => n.id === epId)) {
          fedges.push({ source: `svc:${svc.service_namespace}/${svc.service_name}`, target: epId, kind: 'endpoint' })
        }
      }
      // Caller edges
      for (const caller of svc.callers) {
        const callerId = `pod:${caller.namespace}/${caller.name}`
        if (fnodes.some(n => n.id === callerId)) {
          fedges.push({ source: callerId, target: `svc:${svc.service_namespace}/${svc.service_name}`, kind: 'caller' })
        }
      }
    })

    const positions = runForceLayout(fnodes, fedges, W, H)
    return { nodes: fnodes, edges: fedges, positions }
  }, [data])

  const podMap = useMemo(() => new Map((data?.pods ?? []).map(p => [`pod:${p.namespace}/${p.name}`, p])), [data])
  const svcMap = useMemo(() => new Map((data?.connections ?? []).map(s => [`svc:${s.service_namespace}/${s.service_name}`, s])), [data])

  function _fmtBytes(b: number) {
    if (b === 0) return ''
    if (b < 1024) return `${b}B`
    if (b < 1048576) return `${(b / 1024).toFixed(0)}K`
    if (b < 1073741824) return `${(b / 1048576).toFixed(1)}M`
    return `${(b / 1073741824).toFixed(1)}G`
  }

  function isConnected(id: string, sel: string) {
    if (!sel) return false
    return edges.some(e => (e.source === sel && e.target === id) || (e.target === sel && e.source === id))
  }

  const sevColor = (s: string) => s === 'high' ? 'text-red-400 bg-red-900/30 border-red-700/40'
    : s === 'medium' ? 'text-amber-400 bg-amber-900/30 border-amber-700/40'
    : 'text-slate-400 bg-slate-800/40 border-slate-700/40'

  const activeId = hovered ?? selected

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Workflow size={20} className="text-cyan-400" /> Pod Connection Map
          </h2>
          <p className="text-slate-400 text-sm mt-1">
            Service-mediated connections, inferred callers, network activity, and NetworkPolicy coverage
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex bg-slate-800 rounded-lg p-0.5">
            {(['graph', 'security'] as const).map(v => (
              <button key={v} onClick={() => setView(v)}
                className={`px-3 py-1.5 text-xs rounded-md capitalize transition-colors ${view === v ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}>
                {v === 'security' ? `Security (${data?.summary.total_recommendations ?? 0})` : 'Graph'}
              </button>
            ))}
          </div>
          <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Summary chips */}
      {data && (
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="px-2.5 py-1 rounded-lg bg-slate-800 border border-slate-700 text-slate-300">{data.summary.running_pods} running pods</span>
          <span className={`px-2.5 py-1 rounded-lg border ${data.summary.protected_pods === data.summary.running_pods ? 'bg-emerald-900/30 border-emerald-700/40 text-emerald-300' : 'bg-amber-900/30 border-amber-700/40 text-amber-300'}`}>
            {data.summary.protected_pods}/{data.summary.running_pods} NetworkPolicy protected
          </span>
          <span className="px-2.5 py-1 rounded-lg bg-slate-800 border border-slate-700 text-slate-300">{data.summary.total_services} services</span>
          {data.summary.total_recommendations > 0 && (
            <span className="px-2.5 py-1 rounded-lg bg-red-900/30 border border-red-700/40 text-red-300 font-medium cursor-pointer" onClick={() => setView('security')}>
              {data.summary.total_recommendations} security rec{data.summary.total_recommendations !== 1 ? 's' : ''}
            </span>
          )}
          <span className="text-slate-600 text-[10px] ml-2">Connections inferred from service endpoints + K8s env vars · Network bytes from kubelet stats</span>
        </div>
      )}

      {err && <SectionError error={err} onRetry={load} />}
      {loading && <div className="flex items-center justify-center h-64"><Loader2 className="animate-spin text-slate-500" size={28} /></div>}

      {!loading && data && view === 'graph' && (
        <div className="flex gap-4">
          {/* SVG Canvas */}
          <div className="flex-1 min-w-0 bg-slate-900/70 border border-slate-700/50 rounded-xl overflow-hidden relative">
            {/* Legend */}
            <div className="absolute top-3 left-3 flex items-center gap-3 text-[10px] text-slate-500 z-10">
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-full bg-emerald-500/80 inline-block" />protected pod</span>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-full bg-orange-500/80 inline-block" />unprotected pod</span>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rotate-45 bg-blue-400/80 inline-block" />service</span>
              <span className="flex items-center gap-1"><span className="w-8 border-t border-emerald-400 inline-block" />endpoint</span>
              <span className="flex items-center gap-1"><span className="w-8 border-t border-dashed border-amber-400 inline-block" />inferred caller</span>
            </div>
            <svg ref={svgRef} width={W} height={H} className="w-full" viewBox={`0 0 ${W} ${H}`}>
              <defs>
                <marker id="arrow-ep" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
                  <path d="M0,0 L0,6 L6,3 Z" fill="#34d399" opacity="0.8" />
                </marker>
                <marker id="arrow-caller" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
                  <path d="M0,0 L0,6 L6,3 Z" fill="#fbbf24" opacity="0.8" />
                </marker>
              </defs>

              {/* Edges */}
              {edges.map((e, i) => {
                const src = positions.get(e.source); const tgt = positions.get(e.target)
                if (!src || !tgt) return null
                const isActive = activeId && (e.source === activeId || e.target === activeId)
                const mx = (src.x + tgt.x) / 2; const my = (src.y + tgt.y) / 2
                const bend = e.kind === 'caller' ? -40 : 40
                const dx = tgt.y - src.y; const dy = src.x - tgt.x
                const len = Math.sqrt(dx * dx + dy * dy) + 0.01
                const cx = mx + (dx / len) * bend; const cy = my + (dy / len) * bend
                const portLabel = e.kind === 'endpoint' ? (() => {
                  const svcId = e.source
                  const svc = svcMap.get(svcId)
                  const ep = svc?.endpoints.find(ep2 => `pod:${ep2.namespace}/${ep2.name}` === e.target)
                  return ep?.port ? String(ep.port) : ''
                })() : ''
                return (
                  <g key={i} opacity={activeId && !isActive ? 0.15 : 1}>
                    <path
                      d={`M${src.x},${src.y} Q${cx},${cy} ${tgt.x},${tgt.y}`}
                      fill="none"
                      stroke={e.kind === 'endpoint' ? '#34d399' : '#fbbf24'}
                      strokeWidth={isActive ? 2 : 1}
                      strokeDasharray={e.kind === 'caller' ? '5,3' : undefined}
                      strokeOpacity={0.7}
                      markerEnd={e.kind === 'endpoint' ? 'url(#arrow-ep)' : 'url(#arrow-caller)'}
                    />
                    {portLabel && (
                      <text x={cx} y={cy - 4} textAnchor="middle" className="text-[9px]" fill="#94a3b8" fontSize={9}>{portLabel}</text>
                    )}
                  </g>
                )
              })}

              {/* Nodes */}
              {nodes.map(node => {
                const p = positions.get(node.id)
                if (!p) return null
                const isActive = activeId === node.id
                const isConnConn = !!activeId && isConnected(node.id, activeId)
                const fade = activeId && !isActive && !isConnConn

                if (node.type === 'pod') {
                  const pod = podMap.get(node.id)
                  if (!pod) return null
                  const totalBytes = pod.rx_bytes + pod.tx_bytes
                  const r = Math.max(14, Math.min(24, 14 + Math.log1p(totalBytes / 1e6) * 2))
                  const fill = pod.phase !== 'Running' ? '#475569' : pod.has_netpol ? '#065f46' : '#7c2d12'
                  const stroke = pod.phase !== 'Running' ? '#64748b' : pod.has_netpol ? '#34d399' : '#f97316'
                  const label = pod.name.length > 18 ? pod.name.slice(0, 15) + '…' : pod.name
                  const byteLabel = _fmtBytes(totalBytes)
                  return (
                    <g key={node.id} opacity={fade ? 0.2 : 1}
                      onClick={() => setSelected(sel => sel === node.id ? null : node.id)}
                      onMouseEnter={() => setHovered(node.id)}
                      onMouseLeave={() => setHovered(null)}
                      style={{ cursor: 'pointer' }}>
                      <circle cx={p.x} cy={p.y} r={r + 4} fill="transparent" />
                      <circle cx={p.x} cy={p.y} r={r}
                        fill={fill}
                        stroke={isActive ? '#93c5fd' : stroke}
                        strokeWidth={isActive ? 3 : 1.5}
                      />
                      {byteLabel && <text x={p.x} y={p.y + 1} textAnchor="middle" dominantBaseline="middle" fontSize={7} fill="#cbd5e1">{byteLabel}</text>}
                      <text x={p.x} y={p.y + r + 10} textAnchor="middle" fontSize={9} fill={isActive ? '#e2e8f0' : '#94a3b8'}>{label}</text>
                      <text x={p.x} y={p.y + r + 20} textAnchor="middle" fontSize={8} fill="#475569">{pod.namespace}</text>
                    </g>
                  )
                }

                // Service node (diamond)
                const svc = svcMap.get(node.id)
                if (!svc) return null
                const ds = 18
                const svcLabel = svc.service_name.length > 16 ? svc.service_name.slice(0, 13) + '…' : svc.service_name
                const portStr = svc.ports.slice(0, 2).map(pt => pt.port).join(',')
                const svcFill = svc.service_type === 'LoadBalancer' ? '#1e3a5f' : svc.service_type === 'NodePort' ? '#3b1d6b' : '#1e3a5f'
                const svcStroke = svc.service_type === 'LoadBalancer' ? '#f59e0b' : svc.service_type === 'NodePort' ? '#a78bfa' : '#3b82f6'
                return (
                  <g key={node.id} opacity={fade ? 0.2 : 1}
                    onClick={() => setSelected(sel => sel === node.id ? null : node.id)}
                    onMouseEnter={() => setHovered(node.id)}
                    onMouseLeave={() => setHovered(null)}
                    style={{ cursor: 'pointer' }}>
                    <polygon
                      points={`${p.x},${p.y - ds} ${p.x + ds},${p.y} ${p.x},${p.y + ds} ${p.x - ds},${p.y}`}
                      fill={svcFill}
                      stroke={isActive ? '#93c5fd' : svcStroke}
                      strokeWidth={isActive ? 3 : 1.5}
                    />
                    <text x={p.x} y={p.y} textAnchor="middle" dominantBaseline="middle" fontSize={8} fill="#7dd3fc">{portStr}</text>
                    <text x={p.x} y={p.y + ds + 10} textAnchor="middle" fontSize={9} fill={isActive ? '#e2e8f0' : '#94a3b8'}>{svcLabel}</text>
                    <text x={p.x} y={p.y + ds + 20} textAnchor="middle" fontSize={8} fill="#475569">{svc.service_type}</text>
                  </g>
                )
              })}
            </svg>

            {/* Detail tooltip for selected node */}
            {selected && (() => {
              const pod = podMap.get(selected)
              const svc = svcMap.get(selected)
              if (pod) return (
                <div className="absolute bottom-3 left-3 bg-slate-800/95 border border-slate-700 rounded-xl p-3 text-xs max-w-xs shadow-xl">
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${pod.phase === 'Running' ? 'bg-emerald-900/40 text-emerald-400' : 'bg-slate-700 text-slate-400'}`}>{pod.phase}</span>
                    <span className="font-mono text-white font-medium">{pod.name}</span>
                    <button onClick={() => setSelected(null)} className="ml-auto text-slate-500 hover:text-white"><X size={12} /></button>
                  </div>
                  <div className="space-y-1 text-slate-400">
                    <div><span className="text-slate-500">Namespace:</span> {pod.namespace}</div>
                    <div><span className="text-slate-500">Node:</span> {pod.node || '—'}</div>
                    <div><span className="text-slate-500">IP:</span> <span className="font-mono">{pod.ip || '—'}</span></div>
                    <div className="flex items-center gap-1">
                      <span className="text-slate-500">NetPol:</span>
                      {pod.has_netpol
                        ? <span className="text-emerald-400">✓ {pod.netpol_name}</span>
                        : <span className="text-orange-400">⚠ none — all traffic allowed</span>}
                    </div>
                    {(pod.rx_bytes > 0 || pod.tx_bytes > 0) && (
                      <div><span className="text-slate-500">Net (total):</span> ↓{_fmtBytes(pod.rx_bytes)} ↑{_fmtBytes(pod.tx_bytes)}</div>
                    )}
                    {pod.env_svc_refs.length > 0 && (
                      <div><span className="text-slate-500">Env service refs:</span> {pod.env_svc_refs.join(', ')}</div>
                    )}
                  </div>
                </div>
              )
              if (svc) return (
                <div className="absolute bottom-3 left-3 bg-slate-800/95 border border-slate-700 rounded-xl p-3 text-xs max-w-xs shadow-xl">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-900/40 text-blue-400">{svc.service_type}</span>
                    <span className="font-mono text-white font-medium">{svc.service_name}</span>
                    <button onClick={() => setSelected(null)} className="ml-auto text-slate-500 hover:text-white"><X size={12} /></button>
                  </div>
                  <div className="space-y-1 text-slate-400">
                    <div><span className="text-slate-500">Namespace:</span> {svc.service_namespace}</div>
                    <div><span className="text-slate-500">ClusterIP:</span> <span className="font-mono">{svc.cluster_ip}</span></div>
                    <div><span className="text-slate-500">Ports:</span> <span className="font-mono">{svc.ports.map(pt => `${pt.port}/${pt.protocol}`).join(', ') || '—'}</span></div>
                    <div><span className="text-slate-500">Endpoints:</span> {svc.endpoints.length} pod(s)</div>
                    {svc.callers.length > 0 && <div><span className="text-slate-500">Inferred callers:</span> {svc.callers.map(c => c.name).join(', ')}</div>}
                  </div>
                </div>
              )
              return null
            })()}
          </div>
        </div>
      )}

      {/* Security recommendations view */}
      {!loading && data && view === 'security' && (
        <div className="space-y-3">
          {data.recommendations.length === 0 ? (
            <div className="bg-emerald-900/20 border border-emerald-700/40 rounded-xl p-6 text-center">
              <CheckCircle size={24} className="text-emerald-400 mx-auto mb-2" />
              <p className="text-emerald-300 font-medium">No security issues detected</p>
              <p className="text-slate-500 text-xs mt-1">All running pods have NetworkPolicies and no wide-open rules were found</p>
            </div>
          ) : (
            <RecCardList recs={data.recommendations} clusterId={clusterId} onApplied={load} />
          )}

          {/* Generated NetworkPolicy YAMLs */}
          {(data.generated_netpols?.length ?? 0) > 0 && (
            <GeneratedNetPolPanel netpols={data.generated_netpols} podData={data.pods} fmtBytes={_fmtBytes} clusterId={clusterId} onApplied={load} />
          )}
        </div>
      )}
    </div>
  )
}

// ── Resource YAML Modal ─────────────────────────────────────────────────────────

function ResourceYamlModal({ title, apiPath, clusterId, onClose }: {
  title: string; apiPath: string; clusterId: string; onClose: () => void
}) {
  const [yaml, setYaml] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    vksGet<{ yaml: string }>(`${clusterId}/resource-yaml?api_path=${encodeURIComponent(apiPath)}`)
      .then(d => setYaml(d.yaml))
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [clusterId, apiPath])

  function handleCopy() {
    if (!yaml) return
    navigator.clipboard.writeText(yaml).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })
  }

  function lineClass(l: string) {
    if (/^\s*(kind|apiVersion|metadata):\s/.test(l)) return 'text-blue-300'
    if (/^\s+#/.test(l)) return 'text-slate-500'
    if (/^\s+- /.test(l)) return 'text-cyan-300'
    if (/^\w+:/.test(l)) return 'text-emerald-300'
    if (/:\s*$/.test(l)) return 'text-slate-300 font-semibold'
    if (/:\s+/.test(l)) return 'text-slate-300'
    return 'text-slate-400'
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl mx-4">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700">
          <FileText size={15} className="text-blue-400" />
          <span className="text-white text-sm font-mono flex-1 truncate">{title}</span>
          <button onClick={handleCopy} className={`flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors ${copied ? 'text-emerald-400' : 'text-slate-400 hover:text-white'}`}>
            {copied ? <CheckCircle size={12} /> : <Copy size={12} />}
            {copied ? 'Copied' : 'Copy'}
          </button>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed">
          {loading && <div className="flex items-center justify-center h-32"><Loader2 className="animate-spin text-slate-500" size={20} /></div>}
          {error && <div className="text-red-400">{error}</div>}
          {yaml && yaml.split('\n').map((l, i) => (
            <div key={i} className={lineClass(l)}>{l || '\u00a0'}</div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── CRDs Section ──────────────────────────────────────────────────────────────

interface CRDInfo {
  name: string; group: string; scope: string; kind: string; plural: string
  versions: string[]; established: string; created_at: string
}

interface CRDInstance { name: string; namespace: string; created_at: string; labels: Record<string, string> }

function CRDsSection({ clusterId }: { clusterId: string }) {
  const [crds, setCrds] = useState<CRDInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<CRDInfo | null>(null)
  const [instances, setInstances] = useState<CRDInstance[] | null>(null)
  const [instLoading, setInstLoading] = useState(false)

  useEffect(() => {
    vksGet<{ crds: CRDInfo[] }>(`${clusterId}/crds`)
      .then(d => setCrds(d.crds))
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [clusterId])

  function openInstances(crd: CRDInfo) {
    setSelected(crd)
    setInstances(null)
    setInstLoading(true)
    vksGet<{ instances: CRDInstance[] }>(`${clusterId}/crds/${crd.name}/instances`)
      .then(d => setInstances(d.instances))
      .catch(() => setInstances([]))
      .finally(() => setInstLoading(false))
  }

  const filtered = crds.filter(c =>
    !search || c.name.toLowerCase().includes(search.toLowerCase()) ||
    c.group.toLowerCase().includes(search.toLowerCase()) ||
    c.kind.toLowerCase().includes(search.toLowerCase())
  )

  const scopeColor = (s: string) => s === 'Namespaced' ? 'bg-blue-900/50 text-blue-300' : 'bg-purple-900/50 text-purple-300'

  if (loading) return <div className="flex items-center justify-center h-48"><Loader2 className="animate-spin text-slate-500" size={24} /></div>
  if (error) return <div className="p-6 text-red-400 text-sm">{error}</div>

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-semibold text-lg flex items-center gap-2">
          <Box size={18} className="text-orange-400" /> Custom Resource Definitions
        </h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500">{crds.length} CRDs</span>
          <input type="text" placeholder="Search CRDs…" value={search} onChange={e => setSearch(e.target.value)}
            className="px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 w-48" />
        </div>
      </div>
      <div className="bg-slate-800/30 border border-slate-700/50 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700/50">
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">Name</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">Group</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">Kind</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">Scope</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">Versions</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">Status</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/30">
            {filtered.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500 text-xs">No CRDs found.</td></tr>
            )}
            {filtered.map(crd => (
              <tr key={crd.name} className="hover:bg-slate-700/20 transition-colors">
                <td className="px-4 py-2.5">
                  <span className="text-white font-mono text-xs">{crd.name}</span>
                </td>
                <td className="px-4 py-2.5 text-xs text-slate-400 font-mono">{crd.group}</td>
                <td className="px-4 py-2.5 text-xs text-cyan-300">{crd.kind}</td>
                <td className="px-4 py-2.5">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full ${scopeColor(crd.scope)}`}>{crd.scope}</span>
                </td>
                <td className="px-4 py-2.5 text-xs text-slate-400">{crd.versions.join(', ')}</td>
                <td className="px-4 py-2.5">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full ${crd.established === 'True' ? 'bg-emerald-900/50 text-emerald-300' : 'bg-yellow-900/50 text-yellow-300'}`}>
                    {crd.established === 'True' ? 'Established' : 'Pending'}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right">
                  <button onClick={() => openInstances(crd)}
                    className="text-[10px] px-2.5 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors">
                    Instances
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Instances modal */}
      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={e => e.target === e.currentTarget && setSelected(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl max-h-[70vh] flex flex-col mx-4">
            <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700">
              <Box size={14} className="text-orange-400" />
              <span className="text-white text-sm font-mono flex-1">{selected.kind} instances</span>
              <span className="text-xs text-slate-500">{selected.name}</span>
              <button onClick={() => setSelected(null)} className="p-1.5 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {instLoading && <div className="flex items-center justify-center h-24"><Loader2 className="animate-spin text-slate-500" size={18} /></div>}
              {!instLoading && instances?.length === 0 && (
                <div className="text-center text-slate-500 text-sm py-8">No instances found.</div>
              )}
              {!instLoading && instances && instances.length > 0 && (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-700/50">
                      <th className="text-left pb-2 text-slate-400 font-medium">Name</th>
                      <th className="text-left pb-2 text-slate-400 font-medium">Namespace</th>
                      <th className="text-left pb-2 text-slate-400 font-medium">Created</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/30">
                    {instances.map((inst, i) => (
                      <tr key={i} className="hover:bg-slate-700/20">
                        <td className="py-2 font-mono text-slate-200">{inst.name}</td>
                        <td className="py-2 text-slate-400">{inst.namespace || '—'}</td>
                        <td className="py-2 text-slate-500">{inst.created_at ? new Date(inst.created_at).toLocaleDateString() : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── Cluster Compare Modal ─────────────────────────────────────────────────────

interface ClusterSummary {
  cluster_id: string; version: string
  nodes: { total: number; ready: number }
  pods: { total: number; Running?: number; Failed?: number }
  workloads: { total: number; healthy: number }
  namespaces: number
  capacity: { cpu_cores: number; memory_gib: number }
}

function ClusterCompareModal({ clusters, onClose }: { clusters: Cluster[]; onClose: () => void }) {
  const [clusterA, setClusterA] = useState(clusters[0]?.id ?? '')
  const [clusterB, setClusterB] = useState(clusters[1]?.id ?? '')
  const [summaryA, setSummaryA] = useState<ClusterSummary | null>(null)
  const [summaryB, setSummaryB] = useState<ClusterSummary | null>(null)
  const [loadingA, setLoadingA] = useState(false)
  const [loadingB, setLoadingB] = useState(false)

  useEffect(() => {
    if (!clusterA) return
    setLoadingA(true); setSummaryA(null)
    vksGet<ClusterSummary>(`${clusterA}/summary`)
      .then(setSummaryA).catch(() => setSummaryA(null)).finally(() => setLoadingA(false))
  }, [clusterA])

  useEffect(() => {
    if (!clusterB) return
    setLoadingB(true); setSummaryB(null)
    vksGet<ClusterSummary>(`${clusterB}/summary`)
      .then(setSummaryB).catch(() => setSummaryB(null)).finally(() => setLoadingB(false))
  }, [clusterB])

  function metricRow(label: string, a: string | number | undefined, b: string | number | undefined, higherBetter = true) {
    const aNum = typeof a === 'number' ? a : parseFloat(String(a ?? 0))
    const bNum = typeof b === 'number' ? b : parseFloat(String(b ?? 0))
    const aWin = higherBetter ? aNum > bNum : aNum < bNum
    const bWin = higherBetter ? bNum > aNum : bNum < aNum
    const same = aNum === bNum
    return (
      <tr key={label} className="border-b border-slate-700/30 hover:bg-slate-700/10">
        <td className="px-4 py-2.5 text-xs text-slate-400">{label}</td>
        <td className={`px-4 py-2.5 text-sm text-right font-mono ${!same && aWin ? 'text-emerald-300 font-semibold' : 'text-slate-300'}`}>
          {a ?? '—'}
          {!same && aWin && <span className="ml-1 text-emerald-500 text-[10px]">▲</span>}
        </td>
        <td className={`px-4 py-2.5 text-sm text-right font-mono ${!same && bWin ? 'text-emerald-300 font-semibold' : 'text-slate-300'}`}>
          {b ?? '—'}
          {!same && bWin && <span className="ml-1 text-emerald-500 text-[10px]">▲</span>}
        </td>
      </tr>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl max-h-[85vh] flex flex-col mx-4 shadow-2xl">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700">
          <ArrowUpDown size={14} className="text-blue-400" />
          <span className="text-white font-medium text-sm flex-1">Compare Clusters</span>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        {/* Cluster pickers */}
        <div className="flex gap-4 px-4 py-3 border-b border-slate-700/50">
          <div className="flex-1">
            <label className="text-[10px] text-slate-500 block mb-1">Cluster A</label>
            <select value={clusterA} onChange={e => setClusterA(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500">
              {clusters.map(c => <option key={c.id} value={c.id}>{c.name || c.id}</option>)}
            </select>
          </div>
          <div className="flex-1">
            <label className="text-[10px] text-slate-500 block mb-1">Cluster B</label>
            <select value={clusterB} onChange={e => setClusterB(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500">
              {clusters.map(c => <option key={c.id} value={c.id}>{c.name || c.id}</option>)}
            </select>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-700/50">
                <th className="px-4 py-2 text-xs text-slate-500 text-left font-medium w-40">Metric</th>
                <th className="px-4 py-2 text-xs text-slate-300 text-right">
                  {loadingA ? <Loader2 size={12} className="animate-spin inline" /> : (summaryA?.cluster_id?.split('/').pop() || clusterA.split('/').pop() || 'A')}
                </th>
                <th className="px-4 py-2 text-xs text-slate-300 text-right">
                  {loadingB ? <Loader2 size={12} className="animate-spin inline" /> : (summaryB?.cluster_id?.split('/').pop() || clusterB.split('/').pop() || 'B')}
                </th>
              </tr>
            </thead>
            <tbody>
              {metricRow('K8s Version', summaryA?.version, summaryB?.version, false)}
              {metricRow('Nodes (total)', summaryA?.nodes.total, summaryB?.nodes.total)}
              {metricRow('Nodes (ready)', summaryA?.nodes.ready, summaryB?.nodes.ready)}
              {metricRow('Pods (total)', summaryA?.pods.total, summaryB?.pods.total)}
              {metricRow('Pods (Running)', summaryA?.pods.Running, summaryB?.pods.Running)}
              {metricRow('Pods (Failed)', summaryA?.pods.Failed, summaryB?.pods.Failed, false)}
              {metricRow('Workloads', summaryA?.workloads.total, summaryB?.workloads.total)}
              {metricRow('Healthy Workloads', summaryA?.workloads.healthy, summaryB?.workloads.healthy)}
              {metricRow('Namespaces', summaryA?.namespaces, summaryB?.namespaces)}
              {metricRow('CPU (cores)', summaryA?.capacity.cpu_cores, summaryB?.capacity.cpu_cores)}
              {metricRow('Memory (GiB)', summaryA?.capacity.memory_gib, summaryB?.capacity.memory_gib)}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}


// ── Pod Resources Section ─────────────────────────────────────────────────────

interface PodResourceInfo {
  name: string; namespace: string; phase: string
  req_cpu_m: number; req_mem_mib: number
  lim_cpu_m: number; lim_mem_mib: number
  live_cpu_m: number | null; live_mem_mib: number | null
  cpu_pct: number | null; mem_pct: number | null
}

function ResourceBar({ pct, warn = 70, crit = 90 }: { pct: number | null; warn?: number; crit?: number }) {
  if (pct === null) return <span className="text-slate-600 text-xs">—</span>
  const color = pct >= crit ? 'bg-red-500' : pct >= warn ? 'bg-yellow-500' : 'bg-emerald-500'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden min-w-[60px]">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className={`text-xs tabular-nums ${pct >= crit ? 'text-red-400' : pct >= warn ? 'text-yellow-400' : 'text-emerald-400'}`}>{pct}%</span>
    </div>
  )
}

function PodResourcesSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [pods, setPods] = useState<PodResourceInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [metricsAvail, setMetricsAvail] = useState(true)
  const [sortBy, setSortBy] = useState<'live_cpu_m' | 'live_mem_mib' | 'cpu_pct' | 'mem_pct' | 'req_cpu_m'>('live_cpu_m')
  const [search, setSearch] = useState('')

  useEffect(() => {
    setLoading(true)
    const q = namespace ? `?namespace=${namespace}` : ''
    vksGet<{ pods: PodResourceInfo[]; metrics_available: boolean }>(`${clusterId}/pod-resources${q}`)
      .then(d => { setPods(d.pods); setMetricsAvail(d.metrics_available) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useAutoRefresh(() => {
    const q = namespace ? `?namespace=${namespace}` : ''
    vksGet<{ pods: PodResourceInfo[]; metrics_available: boolean }>(`${clusterId}/pod-resources${q}`)
      .then(d => { setPods(d.pods); setMetricsAvail(d.metrics_available) })
      .catch(() => {})
  }, 20_000)

  const lowerSearch = search.toLowerCase()
  const sorted = [...pods]
    .filter(p => !lowerSearch || p.name.toLowerCase().includes(lowerSearch) || p.namespace.toLowerCase().includes(lowerSearch))
    .sort((a, b) => {
      const av = a[sortBy] ?? -1
      const bv = b[sortBy] ?? -1
      return (bv as number) - (av as number)
    })

  if (loading) return <div className="flex items-center justify-center h-48"><Loader2 className="animate-spin text-slate-500" size={24} /></div>
  if (error) return <div className="p-6 text-red-400 text-sm">{error}</div>

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <h2 className="text-white font-semibold text-lg flex items-center gap-2">
          <Activity size={18} className="text-emerald-400" /> Pod Resource Usage
        </h2>
        {!metricsAvail && (
          <span className="text-xs bg-yellow-900/40 text-yellow-400 border border-yellow-700/40 rounded px-2 py-1">
            Metrics server unavailable — showing requests/limits only
          </span>
        )}
        <div className="flex items-center gap-2 ml-auto">
          <input type="text" placeholder="Search pods…" value={search} onChange={e => setSearch(e.target.value)}
            className="px-2 py-1.5 bg-slate-800 border border-slate-700 rounded text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 w-40" />
          <select value={sortBy} onChange={e => setSortBy(e.target.value as typeof sortBy)}
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-300 focus:outline-none">
            <option value="live_cpu_m">Live CPU</option>
            <option value="live_mem_mib">Live Memory</option>
            <option value="cpu_pct">CPU %</option>
            <option value="mem_pct">Memory %</option>
            <option value="req_cpu_m">CPU Request</option>
          </select>
        </div>
      </div>
      <div className="bg-slate-800/30 border border-slate-700/50 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700/50">
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">Pod</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400 hidden md:table-cell">NS</th>
              <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-400">Live CPU</th>
              <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-400">Req/Lim CPU</th>
              <th className="px-4 py-2.5 text-xs font-medium text-slate-400 w-32">CPU %</th>
              <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-400">Live Mem</th>
              <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-400">Req/Lim Mem</th>
              <th className="px-4 py-2.5 text-xs font-medium text-slate-400 w-32">Mem %</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/30">
            {sorted.length === 0 && (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-slate-500 text-xs">No pods found.</td></tr>
            )}
            {sorted.map(p => (
              <tr key={`${p.namespace}/${p.name}`} className="hover:bg-slate-700/20 transition-colors">
                <td className="px-4 py-2.5">
                  <span className="font-mono text-xs text-white truncate max-w-[180px] block">{p.name}</span>
                  <span className={`text-[10px] ${p.phase === 'Running' ? 'text-emerald-400' : p.phase === 'Pending' ? 'text-yellow-400' : 'text-red-400'}`}>{p.phase}</span>
                </td>
                <td className="px-4 py-2.5 text-xs text-slate-400 font-mono hidden md:table-cell">{p.namespace}</td>
                <td className="px-4 py-2.5 text-xs text-right font-mono text-slate-300">
                  {p.live_cpu_m !== null ? `${p.live_cpu_m}m` : '—'}
                </td>
                <td className="px-4 py-2.5 text-xs text-right font-mono text-slate-500">
                  {p.req_cpu_m}m / {p.lim_cpu_m > 0 ? `${p.lim_cpu_m}m` : '∞'}
                </td>
                <td className="px-4 py-2.5"><ResourceBar pct={p.cpu_pct} /></td>
                <td className="px-4 py-2.5 text-xs text-right font-mono text-slate-300">
                  {p.live_mem_mib !== null ? `${p.live_mem_mib}Mi` : '—'}
                </td>
                <td className="px-4 py-2.5 text-xs text-right font-mono text-slate-500">
                  {p.req_mem_mib}Mi / {p.lim_mem_mib > 0 ? `${p.lim_mem_mib}Mi` : '∞'}
                </td>
                <td className="px-4 py-2.5"><ResourceBar pct={p.mem_pct} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}


// ── Multi-Cluster Fleet Health (Loop 49) ─────────────────────────────────────

interface FleetClusterHealth {
  cluster_id: string; name: string; status: 'healthy' | 'degraded' | 'unreachable' | 'unknown'
  version: string; provider: string
  nodes: { total: number; ready: number }
  pods: { total: number }
  workloads: { total: number; healthy: number }
  namespaces: number
  capacity: { cpu_cores: number; memory_gib: number }
}

function FleetHealthCard({ cluster, onSelect }: { cluster: FleetClusterHealth; onSelect?: () => void }) {
  const statusConfig = {
    healthy: { dot: 'bg-emerald-400', border: 'border-emerald-700/40', badge: 'text-emerald-400' },
    degraded: { dot: 'bg-yellow-400', border: 'border-yellow-700/40', badge: 'text-yellow-400' },
    unreachable: { dot: 'bg-red-400', border: 'border-red-700/40', badge: 'text-red-400' },
    unknown: { dot: 'bg-slate-500', border: 'border-slate-600', badge: 'text-slate-500' },
  }
  const cfg = statusConfig[cluster.status] || statusConfig.unknown
  const workloadHealth = cluster.workloads.total > 0
    ? Math.round((cluster.workloads.healthy / cluster.workloads.total) * 100)
    : 100
  const nodeHealth = cluster.nodes.total > 0
    ? Math.round((cluster.nodes.ready / cluster.nodes.total) * 100)
    : 100

  return (
    <button onClick={onSelect} className={`w-full text-left bg-slate-800/60 border ${cfg.border} rounded-xl p-4 transition-colors space-y-3 ${onSelect ? 'cursor-pointer hover:bg-slate-800/90 hover:border-blue-500/50 hover:shadow-blue-900/30 hover:shadow-lg' : 'cursor-default hover:bg-slate-800'}`}>
      <div className="flex items-center gap-2">
        <div className={`w-2.5 h-2.5 rounded-full ${cfg.dot} shrink-0`} />
        <span className="font-semibold text-white text-sm truncate flex-1">{cluster.name}</span>
        <span className={`text-xs font-medium ${cfg.badge}`}>{cluster.status}</span>
      </div>
      {cluster.status === 'unreachable' ? (
        <div className="text-xs text-slate-500 italic">Cluster unreachable</div>
      ) : (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <div className="text-slate-500">Version</div><div className="text-slate-200 font-mono">{cluster.version || '—'}</div>
          <div className="text-slate-500">Nodes</div><div className={`font-medium ${nodeHealth < 100 ? 'text-yellow-400' : 'text-emerald-400'}`}>{cluster.nodes.ready}/{cluster.nodes.total} ready</div>
          <div className="text-slate-500">Pods</div><div className="text-slate-200">{cluster.pods.total}</div>
          <div className="text-slate-500">Workloads</div><div className={`font-medium ${workloadHealth < 100 ? 'text-yellow-400' : 'text-emerald-400'}`}>{cluster.workloads.healthy}/{cluster.workloads.total} healthy</div>
          <div className="text-slate-500">Namespaces</div><div className="text-slate-200">{cluster.namespaces}</div>
          <div className="text-slate-500">CPU/Mem</div><div className="text-slate-400 text-xs">{cluster.capacity.cpu_cores} cores / {cluster.capacity.memory_gib} GiB</div>
        </div>
      )}
    </button>
  )
}

function FleetHealthSection({ onSelectCluster }: { onSelectCluster?: (id: string) => void }) {
  const [data, setData] = useState<{ clusters: FleetClusterHealth[]; total: number; healthy: number; degraded: number; unreachable: number; last_updated?: string } | null>(null)
  const [secsSince, setSecsSince] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    vksGet<typeof data>('fleet/k8s-health')
      .then(d => { setData(d); setError(''); setSecsSince(0) })
      .catch(() => setError('Failed to load fleet health'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 30_000)

  useEffect(() => {
    const t = setInterval(() => setSecsSince(s => s + 1), 1000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2"><LayoutGrid size={20} className="text-emerald-400" />Fleet Health</h2>
          <p className="text-slate-400 text-sm mt-1">Real-time health across all registered Kubernetes clusters</p>
        </div>
        <div className="flex items-center gap-2">
          {data && (
            <span className="text-xs text-slate-500">
              Updated {secsSince < 60 ? `${secsSince}s` : `${Math.floor(secsSince / 60)}m`} ago
            </span>
          )}
          <button onClick={load} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300">
            <RefreshCw size={14} />Refresh
          </button>
        </div>
      </div>

      {data && (
        <div className="flex flex-wrap gap-3">
          <span className={`text-xs px-3 py-1.5 rounded-lg border ${data.healthy === data.total ? 'bg-emerald-900/30 border-emerald-700/50 text-emerald-300' : 'bg-slate-800 border-slate-700 text-slate-300'}`}>{data.total} clusters total</span>
          {data.healthy > 0 && <span className="text-xs bg-emerald-900/30 border border-emerald-700/50 rounded-lg px-3 py-1.5 text-emerald-300">{data.healthy} healthy</span>}
          {data.degraded > 0 && <span className="text-xs bg-yellow-900/30 border border-yellow-700/50 rounded-lg px-3 py-1.5 text-yellow-300">{data.degraded} degraded</span>}
          {data.unreachable > 0 && <span className="text-xs bg-red-900/30 border border-red-700/50 rounded-lg px-3 py-1.5 text-red-300">{data.unreachable} unreachable</span>}
        </div>
      )}

      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}
      {loading && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"><Skeleton className="h-48 rounded-xl" /><Skeleton className="h-48 rounded-xl" /><Skeleton className="h-48 rounded-xl" /></div>}

      {!loading && data && (data.clusters?.length ?? 0) === 0 && (
        <EmptyState icon={LayoutGrid} title="No clusters found" hint="Register VKS clusters to see fleet health here." />
      )}

      {!loading && data && (data.clusters?.length ?? 0) > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(data.clusters ?? []).map(c => (
            <FleetHealthCard key={c.cluster_id} cluster={c} onSelect={onSelectCluster ? () => onSelectCluster(c.cluster_id) : undefined} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Fleet Diff (Loop 55) ─────────────────────────────────────────────────────

interface DiffRow { metric: string; key: string; a: number | null; b: number | null; delta: number | null }
interface ClusterSnapshot {
  cluster_id: string; reachable: boolean; error?: string
  node_count: number; ready_nodes: number; alloc_cpu_cores: number; alloc_mem_gib: number
  pod_total: number; pod_running: number; pod_pending: number; pod_failed: number
  deployment_count: number; deployment_ready: number; deployment_degraded: number
  pvc_count: number; storage_gib: number; namespace_count: number; secret_count: number
}

function FleetDiffSection() {
  const [clusters, setClusters] = useState<{ cluster_id: string; name: string }[]>([])
  const [clusterA, setClusterA] = useState('')
  const [clusterB, setClusterB] = useState('')
  const [diff, setDiff] = useState<{ cluster_a: ClusterSnapshot; cluster_b: ClusterSnapshot; diff: DiffRow[] } | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    vksGet<{ clusters: { cluster_id: string; name: string }[] }>('clusters')
      .then(d => setClusters(d.clusters ?? []))
      .catch(() => {})
  }, [])

  const compare = () => {
    if (!clusterA || !clusterB) return
    setLoading(true); setErr(null); setDiff(null)
    vksGet<typeof diff>(`fleet/diff?a=${encodeURIComponent(clusterA)}&b=${encodeURIComponent(clusterB)}`)
      .then(d => { setDiff(d); setLoading(false) })
      .catch(e => { setErr(String(e)); setLoading(false) })
  }

  const deltaColor = (delta: number | null, key: string) => {
    if (delta === null || delta === 0) return 'text-slate-400'
    const higherIsBetter = !['pod_pending', 'pod_failed', 'deployment_degraded'].includes(key)
    return (higherIsBetter ? delta > 0 : delta < 0) ? 'text-emerald-400' : 'text-red-400'
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <ArrowUpDown size={16} className="text-blue-400" />
        <span className="text-base font-semibold text-white">Fleet Diff — Compare Two Clusters</span>
      </div>

      <div className="flex flex-wrap gap-4 items-end">
        <div className="flex-1 min-w-48">
          <label className="block text-xs text-slate-500 mb-1">Cluster A</label>
          <select value={clusterA} onChange={e => setClusterA(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-blue-500">
            <option value="">— select cluster —</option>
            {clusters.map(c => <option key={c.cluster_id} value={c.cluster_id}>{c.name || c.cluster_id}</option>)}
          </select>
        </div>
        <div className="flex-1 min-w-48">
          <label className="block text-xs text-slate-500 mb-1">Cluster B</label>
          <select value={clusterB} onChange={e => setClusterB(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-blue-500">
            <option value="">— select cluster —</option>
            {clusters.map(c => <option key={c.cluster_id} value={c.cluster_id}>{c.name || c.cluster_id}</option>)}
          </select>
        </div>
        <button onClick={compare} disabled={!clusterA || !clusterB || loading}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm font-medium rounded-lg flex items-center gap-1.5">
          {loading ? <Loader2 size={14} className="animate-spin" /> : <ArrowUpDown size={14} />}
          Compare
        </button>
      </div>

      {err && <SectionError error={err} onRetry={compare} />}

      {diff && (
        <div className="space-y-4">
          {(!diff.cluster_a.reachable || !diff.cluster_b.reachable) && (
            <div className="bg-amber-900/20 border border-amber-700/40 rounded-xl p-3 text-sm text-amber-300 flex items-center gap-2">
              <AlertTriangle size={14} />
              {!diff.cluster_a.reachable && <span>Cluster A unreachable: {diff.cluster_a.error}</span>}
              {!diff.cluster_b.reachable && <span>Cluster B unreachable: {diff.cluster_b.error}</span>}
            </div>
          )}
          <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-800/50 border-b border-slate-800">
                <tr className="text-[10px] text-slate-400 uppercase tracking-wider">
                  <th className="px-4 py-2.5 text-left w-1/3">Metric</th>
                  <th className="px-4 py-2.5 text-right">Cluster A</th>
                  <th className="px-4 py-2.5 text-right">Cluster B</th>
                  <th className="px-4 py-2.5 text-right">Delta (B−A)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/40">
                {diff.diff.map(row => (
                  <tr key={row.key} className="hover:bg-slate-800/20 transition-colors">
                    <td className="px-4 py-2.5 text-slate-400">{row.metric}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-slate-300">
                      {row.a ?? <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-slate-300">
                      {row.b ?? <span className="text-slate-600">—</span>}
                    </td>
                    <td className={`px-4 py-2.5 text-right font-mono font-semibold ${deltaColor(row.delta, row.key)}`}>
                      {row.delta === null ? '—' : row.delta === 0 ? '=' : row.delta > 0 ? `+${row.delta}` : row.delta}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!diff && !loading && !err && (
        <div className="bg-slate-900/40 rounded-xl border-2 border-dashed border-slate-700/50 p-10 text-center text-slate-500">
          <ArrowUpDown size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Select two clusters and click Compare to see side-by-side differences</p>
        </div>
      )}
    </div>
  )
}

// ── Orphan Resource Detector (Loop 48) ───────────────────────────────────────

interface OrphanSummary {
  orphaned_services: number; unbound_pvcs: number
  orphaned_ingresses: number; zero_replica_deployments: number; total: number
}

interface OrphanItem {
  name: string; namespace: string; reason: string; created_at: string
  [key: string]: unknown
}

interface OrphanData {
  orphaned_services: (OrphanItem & { selector: Record<string, string>; type: string })[]
  unbound_pvcs: (OrphanItem & { phase: string; storage: string; storage_class: string })[]
  orphaned_ingresses: (OrphanItem & { missing_services: string[] })[]
  zero_replica_deployments: (OrphanItem & { desired_replicas: number })[]
  summary: OrphanSummary
}

function OrphansSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [data, setData] = useState<OrphanData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState<'services' | 'pvcs' | 'ingresses' | 'deployments'>('services')

  const load = useCallback(() => {
    setLoading(true)
    const ns = namespace ? `?namespace=${namespace}` : ''
    vksGet<any>(`${clusterId}/orphans${ns}`)
      .then(d => { setData(d); setError('') })
      .catch(() => setError('Failed to scan for orphaned resources'))
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])

  const tabDef = [
    { id: 'services' as const, label: 'Services', count: data?.summary.orphaned_services ?? 0 },
    { id: 'pvcs' as const, label: 'PVCs', count: data?.summary.unbound_pvcs ?? 0 },
    { id: 'ingresses' as const, label: 'Ingresses', count: data?.summary.orphaned_ingresses ?? 0 },
    { id: 'deployments' as const, label: 'Zero-Replicas', count: data?.summary.zero_replica_deployments ?? 0 },
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2"><Ghost size={20} className="text-purple-400" />Orphan Detector</h2>
          <p className="text-slate-400 text-sm mt-1">Detect unused or misconfigured resources: orphaned services, stale PVCs, broken ingresses, idle deployments</p>
        </div>
        <button onClick={load} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300">
          <RefreshCw size={14} />Scan
        </button>
      </div>

      {/* Summary row */}
      {data && (
        <div className="flex flex-wrap gap-2">
          <span className={`text-xs px-3 py-1.5 rounded-lg border ${data.summary.total === 0 ? 'bg-emerald-900/30 border-emerald-700/50 text-emerald-300' : 'bg-orange-900/30 border-orange-700/50 text-orange-300'}`}>
            {data.summary.total} orphaned resource{data.summary.total !== 1 ? 's' : ''} found
          </span>
        </div>
      )}

      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}
      {loading && <SkeletonRows rows={5} />}

      {!loading && data && (
        <>
          {data.summary.total === 0 && (
            <EmptyState icon={Ghost} title="No orphaned resources found" hint="All services, PVCs, and ingresses appear to be in use." />
          )}

          {data.summary.total > 0 && (
            <>
              {/* Tabs */}
              <div className="flex items-center gap-1 border-b border-slate-700">
                {tabDef.map(t => (
                  <button key={t.id} onClick={() => setActiveTab(t.id)}
                    className={`px-4 py-2.5 text-sm border-b-2 -mb-px transition-colors ${activeTab === t.id ? 'border-emerald-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
                    {t.label}
                    {t.count > 0 && <span className="ml-2 text-xs bg-orange-900/40 border border-orange-700/40 rounded-full px-1.5 py-0.5 text-orange-300">{t.count}</span>}
                  </button>
                ))}
              </div>

              {/* Tab content */}
              {activeTab === 'services' && (
                (data.orphaned_services?.length ?? 0) === 0
                  ? <EmptyState icon={Ghost} title="No orphaned services" hint="All services with selectors have matching Running pods." />
                  : <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-800/80"><tr className="text-slate-400 text-xs">
                        <th className="text-left px-4 py-3">Service</th><th className="text-left px-4 py-3">Namespace</th>
                        <th className="text-left px-4 py-3">Type</th><th className="text-left px-4 py-3">Selector</th>
                        <th className="text-left px-4 py-3">Reason</th>
                      </tr></thead>
                      <tbody>{(data.orphaned_services ?? []).map((s: any, i: number) => (
                        <tr key={i} className="border-t border-slate-800 hover:bg-slate-800/30">
                          <td className="px-4 py-3 text-white font-mono text-xs">{s.name}</td>
                          <td className="px-4 py-3 text-slate-400 font-mono text-xs">{s.namespace}</td>
                          <td className="px-4 py-3 text-slate-300 text-xs">{s.type}</td>
                          <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{Object.entries(s.selector as Record<string, string>).map(([k,v]) => <span key={k} className="text-xs bg-slate-800 border border-slate-700 rounded px-1 py-0.5 font-mono text-slate-400">{k}={v}</span>)}</div></td>
                          <td className="px-4 py-3 text-orange-400 text-xs">{s.reason}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
              )}

              {activeTab === 'pvcs' && (
                (data.unbound_pvcs?.length ?? 0) === 0
                  ? <EmptyState icon={Ghost} title="No unbound PVCs" hint="All PVCs are bound and mounted." />
                  : <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-800/80"><tr className="text-slate-400 text-xs">
                        <th className="text-left px-4 py-3">PVC</th><th className="text-left px-4 py-3">Namespace</th>
                        <th className="text-left px-4 py-3">Phase</th><th className="text-left px-4 py-3">Size</th>
                        <th className="text-left px-4 py-3">Storage Class</th><th className="text-left px-4 py-3">Reason</th>
                      </tr></thead>
                      <tbody>{(data.unbound_pvcs ?? []).map((p: any, i: number) => (
                        <tr key={i} className="border-t border-slate-800 hover:bg-slate-800/30">
                          <td className="px-4 py-3 text-white font-mono text-xs">{p.name}</td>
                          <td className="px-4 py-3 text-slate-400 font-mono text-xs">{p.namespace}</td>
                          <td className="px-4 py-3"><span className={`text-xs px-2 py-0.5 rounded border ${p.phase === 'Bound' ? 'bg-emerald-900/40 text-emerald-300 border-emerald-700/40' : 'bg-red-900/40 text-red-300 border-red-700/40'}`}>{p.phase}</span></td>
                          <td className="px-4 py-3 text-slate-300 text-xs">{p.storage}</td>
                          <td className="px-4 py-3 text-slate-400 text-xs">{p.storage_class || '—'}</td>
                          <td className="px-4 py-3 text-orange-400 text-xs">{p.reason}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
              )}

              {activeTab === 'ingresses' && (
                (data.orphaned_ingresses?.length ?? 0) === 0
                  ? <EmptyState icon={Ghost} title="No broken ingresses" hint="All ingress backends point to existing services." />
                  : <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-800/80"><tr className="text-slate-400 text-xs">
                        <th className="text-left px-4 py-3">Ingress</th><th className="text-left px-4 py-3">Namespace</th>
                        <th className="text-left px-4 py-3">Missing Services</th><th className="text-left px-4 py-3">Reason</th>
                      </tr></thead>
                      <tbody>{(data.orphaned_ingresses ?? []).map((ing: any, i: number) => (
                        <tr key={i} className="border-t border-slate-800 hover:bg-slate-800/30">
                          <td className="px-4 py-3 text-white font-mono text-xs">{ing.name}</td>
                          <td className="px-4 py-3 text-slate-400 font-mono text-xs">{ing.namespace}</td>
                          <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{(ing.missing_services ?? []).map((s: string) => <span key={s} className="text-xs bg-red-900/30 border border-red-700/40 rounded px-1.5 py-0.5 text-red-300 font-mono">{s}</span>)}</div></td>
                          <td className="px-4 py-3 text-orange-400 text-xs">{ing.reason}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
              )}

              {activeTab === 'deployments' && (
                (data.zero_replica_deployments?.length ?? 0) === 0
                  ? <EmptyState icon={Ghost} title="No zero-replica deployments" hint="All non-HPA-managed deployments have at least one desired replica." />
                  : <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-800/80"><tr className="text-slate-400 text-xs">
                        <th className="text-left px-4 py-3">Deployment</th><th className="text-left px-4 py-3">Namespace</th>
                        <th className="text-left px-4 py-3">Replicas</th><th className="text-left px-4 py-3">Created</th>
                      </tr></thead>
                      <tbody>{(data.zero_replica_deployments ?? []).map((d: any, i: number) => (
                        <tr key={i} className="border-t border-slate-800 hover:bg-slate-800/30">
                          <td className="px-4 py-3 text-white font-mono text-xs">{d.name}</td>
                          <td className="px-4 py-3 text-slate-400 font-mono text-xs">{d.namespace}</td>
                          <td className="px-4 py-3 text-orange-400 font-bold text-xs">0</td>
                          <td className="px-4 py-3 text-slate-500 text-xs">{d.created_at ? new Date(d.created_at).toLocaleDateString() : '—'}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}

// ── Loop 56: Workload Restart Timeline ───────────────────────────────────────

interface RestartWorkload {
  kind: string; name: string; namespace: string; total_restarts: number
  pod_count: number; last_restart: string
  top_pods: { pod: string; namespace: string; restarts: number; last_restart: string }[]
}

function RestartTimelineSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [data, setData] = useState<{ workloads: RestartWorkload[]; summary: { total_workloads: number; workloads_with_restarts: number; total_restarts: number } } | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [minRestarts, setMinRestarts] = useState(1)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const load = () => {
    setLoading(true); setErr(null)
    vksGet<NonNullable<typeof data>>(
      `${clusterId}/workloads/restart-timeline?min_restarts=${minRestarts}${namespace ? `&namespace=${namespace}` : ''}`
    ).then(d => { setData(d); setLoading(false) })
     .catch(e => { setErr(String(e)); setLoading(false) })
  }

  useEffect(load, [clusterId, namespace, minRestarts])

  const toggleExpand = (key: string) => setExpanded(prev => {
    const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n
  })

  if (loading) return <SkeletonRows rows={5} />
  if (err) return <SectionError error={err} onRetry={load} />
  if (!data) return null

  const { workloads, summary } = data

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <RotateCcw size={16} className="text-amber-400" />
          <span className="text-base font-semibold text-white">Workload Restart Timeline</span>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-xs text-slate-400 flex items-center gap-2">
            Min restarts:
            <select value={minRestarts} onChange={e => setMinRestarts(Number(e.target.value))}
              className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-blue-500">
              {[0,1,3,5,10].map(v => <option key={v} value={v}>{v === 0 ? 'All' : `≥${v}`}</option>)}
            </select>
          </label>
          <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
          <div className="text-2xl font-bold text-white">{summary.total_workloads}</div>
          <div className="text-xs text-slate-500 mt-0.5">Workloads Shown</div>
        </div>
        <div className={`rounded-xl border p-3 text-center ${summary.workloads_with_restarts > 0 ? 'bg-amber-900/20 border-amber-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
          <div className={`text-2xl font-bold ${summary.workloads_with_restarts > 0 ? 'text-amber-400' : 'text-slate-400'}`}>{summary.workloads_with_restarts}</div>
          <div className="text-xs text-slate-500 mt-0.5">With Restarts</div>
        </div>
        <div className={`rounded-xl border p-3 text-center ${summary.total_restarts > 10 ? 'bg-red-900/20 border-red-700/50' : summary.total_restarts > 0 ? 'bg-amber-900/20 border-amber-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
          <div className={`text-2xl font-bold ${summary.total_restarts > 10 ? 'text-red-400' : summary.total_restarts > 0 ? 'text-amber-400' : 'text-slate-400'}`}>{summary.total_restarts}</div>
          <div className="text-xs text-slate-500 mt-0.5">Total Restarts</div>
        </div>
      </div>

      {workloads.length === 0 ? (
        <EmptyState icon={RotateCcw} title="No restarts" hint={`No workloads with ≥${minRestarts} restart${minRestarts !== 1 ? 's' : ''}`} />
      ) : (
        <div className="space-y-1.5">
          {workloads.map(w => {
            const key = `${w.kind}/${w.namespace}/${w.name}`
            const isExpanded = expanded.has(key)
            const severity = w.total_restarts >= 20 ? 'text-red-400 bg-red-900/40 border-red-700/40' : w.total_restarts >= 5 ? 'text-amber-400 bg-amber-900/40 border-amber-700/40' : 'text-slate-400 bg-slate-800/60 border-slate-700/40'
            return (
              <div key={key} className="bg-slate-900/60 rounded-xl border border-slate-700/50 overflow-hidden">
                <button className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-800/30 text-left transition-colors"
                  onClick={() => toggleExpand(key)}>
                  <div className="flex items-center gap-3 min-w-0">
                    {isExpanded ? <ChevronDown size={14} className="text-slate-400 shrink-0" /> : <ChevronRight size={14} className="text-slate-400 shrink-0" />}
                    <span className="text-[10px] bg-slate-700/60 text-slate-400 border border-slate-600/40 px-1.5 py-0.5 rounded font-medium">{w.kind}</span>
                    <span className="font-mono font-medium text-white truncate">{w.name}</span>
                    <span className="text-slate-500 text-xs">{w.namespace}</span>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    {w.last_restart && <span className="text-xs text-slate-500">{_relTime(w.last_restart)}</span>}
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${severity}`}>
                      {w.total_restarts} restart{w.total_restarts !== 1 ? 's' : ''}
                    </span>
                  </div>
                </button>
                {isExpanded && (
                  <div className="border-t border-slate-800 bg-slate-800/20 px-4 py-3">
                    <table className="min-w-full text-xs">
                      <thead>
                        <tr className="text-slate-500 uppercase tracking-wide text-[10px]">
                          <th className="text-left pb-1.5">Pod</th>
                          <th className="text-right pb-1.5">Restarts</th>
                          <th className="text-right pb-1.5">Last Restart</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60">
                        {w.top_pods.map(p => (
                          <tr key={p.pod}>
                            <td className="py-1.5 font-mono text-slate-300">{p.pod}</td>
                            <td className={`py-1.5 text-right font-bold ${p.restarts >= 10 ? 'text-red-400' : p.restarts >= 3 ? 'text-amber-400' : 'text-slate-400'}`}>{p.restarts}</td>
                            <td className="py-1.5 text-right text-slate-500">{p.last_restart ? _relTime(p.last_restart) : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── PDB Coverage Analyzer (Loop 57) ─────────────────────────────────────────

interface PdbWorkload {
  kind: string; name: string; namespace: string
  replicas: number; ready: number
  covered: boolean; pdb_name: string | null
  pdb_min_available: number | string | null; pdb_max_unavailable: number | string | null
  pdb_quality: 'ok' | 'misconfigured' | 'too_strict' | null
  issues: string[]
}

interface PdbSummary {
  total_workloads: number; covered: number; uncovered: number
  misconfigured_pdbs: number; total_pdbs: number
}

function PdbCoverageSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [data, setData] = useState<{ workloads: PdbWorkload[]; pdbs: unknown[]; summary: PdbSummary } | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [minReplicas, setMinReplicas] = useState(1)
  const [showUncoveredOnly, setShowUncoveredOnly] = useState(false)

  const load = () => {
    setLoading(true); setErr(null)
    vksGet<NonNullable<typeof data>>(
      `${clusterId}/pdb-coverage?min_replicas=${minReplicas}${namespace ? `&namespace=${namespace}` : ''}`
    ).then(d => { setData(d); setLoading(false) })
     .catch(e => { setErr(String(e)); setLoading(false) })
  }

  useEffect(load, [clusterId, namespace, minReplicas])

  if (loading) return <SkeletonRows rows={5} />
  if (err) return <SectionError error={err} onRetry={load} />
  if (!data) return null

  const { workloads, summary } = data
  const displayed = showUncoveredOnly ? workloads.filter(w => w.issues.length > 0) : workloads

  const qualityBadge = (q: PdbWorkload['pdb_quality']) => {
    if (!q) return null
    if (q === 'misconfigured') return <span className="text-[10px] bg-red-900/40 text-red-300 border border-red-700/40 px-1.5 py-0.5 rounded">misconfigured</span>
    if (q === 'too_strict')    return <span className="text-[10px] bg-yellow-900/40 text-yellow-300 border border-yellow-700/40 px-1.5 py-0.5 rounded">too strict</span>
    return <span className="text-[10px] bg-emerald-900/40 text-emerald-300 border border-emerald-700/40 px-1.5 py-0.5 rounded">ok</span>
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck size={16} className="text-violet-400" />
          <span className="text-base font-semibold text-white">PDB Coverage</span>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-xs text-slate-400 flex items-center gap-2">
            Min replicas:
            <select value={minReplicas} onChange={e => setMinReplicas(Number(e.target.value))}
              className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-blue-500">
              {[1,2,3,5].map(v => <option key={v} value={v}>{`≥${v}`}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400 flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={showUncoveredOnly} onChange={e => setShowUncoveredOnly(e.target.checked)} className="w-3 h-3 accent-blue-500" />
            Issues only
          </label>
          <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
          <div className="text-2xl font-bold text-white">{summary.total_workloads}</div>
          <div className="text-xs text-slate-500 mt-0.5">Workloads</div>
        </div>
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
          <div className="text-2xl font-bold text-emerald-400">{summary.covered}</div>
          <div className="text-xs text-slate-500 mt-0.5">Covered</div>
        </div>
        <div className={`rounded-xl border p-3 text-center ${summary.uncovered > 0 ? 'bg-red-900/20 border-red-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
          <div className={`text-2xl font-bold ${summary.uncovered > 0 ? 'text-red-400' : 'text-slate-400'}`}>{summary.uncovered}</div>
          <div className="text-xs text-slate-500 mt-0.5">Uncovered</div>
        </div>
        <div className={`rounded-xl border p-3 text-center ${summary.misconfigured_pdbs > 0 ? 'bg-yellow-900/20 border-yellow-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
          <div className={`text-2xl font-bold ${summary.misconfigured_pdbs > 0 ? 'text-yellow-400' : 'text-slate-400'}`}>{summary.misconfigured_pdbs}</div>
          <div className="text-xs text-slate-500 mt-0.5">Misconfigured PDBs</div>
        </div>
      </div>

      {displayed.length === 0 ? (
        <EmptyState icon={ShieldCheck} title={showUncoveredOnly ? 'All workloads covered' : 'No workloads found'} hint={showUncoveredOnly ? 'No PDB issues detected' : 'No workloads match filters'} />
      ) : (
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 overflow-hidden">
          <table className="min-w-full text-xs">
            <thead className="bg-slate-800/50 border-b border-slate-800">
              <tr className="text-[10px] text-slate-400 uppercase tracking-wider">
                <th className="text-left px-4 py-2.5">Workload</th>
                <th className="text-left px-4 py-2.5">Kind</th>
                <th className="text-left px-4 py-2.5">Namespace</th>
                <th className="text-center px-4 py-2.5">Replicas</th>
                <th className="text-left px-4 py-2.5">PDB</th>
                <th className="text-center px-4 py-2.5">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40">
              {displayed.map(w => (
                <tr key={`${w.kind}/${w.namespace}/${w.name}`} className={`transition-colors ${w.issues.length > 0 ? 'bg-red-900/10 hover:bg-red-900/20' : 'hover:bg-slate-800/20'}`}>
                  <td className="px-4 py-3 font-mono font-medium text-white">{w.name}</td>
                  <td className="px-4 py-3">
                    <span className="text-[10px] bg-slate-700/60 text-slate-400 border border-slate-600/40 px-1.5 py-0.5 rounded">{w.kind}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{w.namespace}</td>
                  <td className="px-4 py-3 text-center">
                    <span className={`font-medium ${w.ready < w.replicas ? 'text-amber-400' : 'text-slate-400'}`}>
                      {w.ready}/{w.replicas}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {w.pdb_name ? (
                      <span className="font-mono">{w.pdb_name}
                        {w.pdb_min_available != null && <span className="ml-1 text-slate-500">(min={w.pdb_min_available})</span>}
                        {w.pdb_max_unavailable != null && <span className="ml-1 text-slate-500">(maxUn={w.pdb_max_unavailable})</span>}
                      </span>
                    ) : <span className="text-slate-600 italic">none</span>}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {!w.covered ? (
                      <span className="text-[10px] bg-red-900/40 text-red-300 border border-red-700/40 px-2 py-0.5 rounded-full font-medium">no PDB</span>
                    ) : qualityBadge(w.pdb_quality)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Pod Anti-Affinity Coverage (Loop 58) ─────────────────────────────────────

interface AffinityWorkload {
  kind: string; name: string; namespace: string
  replicas: number; ready: number
  protection: 'required' | 'preferred' | 'none'
  has_anti_affinity: boolean; has_tsc: boolean; tsc_count: number
  required_anti_affinity: boolean; preferred_anti_affinity: boolean
  issues: string[]
}

interface AffinitySummary {
  total_workloads: number; unprotected: number; preferred_only: number; fully_protected: number
}

function AffinityCoverageSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [data, setData] = useState<{ workloads: AffinityWorkload[]; summary: AffinitySummary } | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [minReplicas, setMinReplicas] = useState(2)
  const [showIssuesOnly, setShowIssuesOnly] = useState(false)

  const load = () => {
    setLoading(true); setErr(null)
    vksGet<NonNullable<typeof data>>(
      `${clusterId}/affinity-coverage?min_replicas=${minReplicas}${namespace ? `&namespace=${namespace}` : ''}`
    ).then(d => { setData(d); setLoading(false) })
     .catch(e => { setErr(String(e)); setLoading(false) })
  }

  useEffect(load, [clusterId, namespace, minReplicas])

  if (loading) return <SkeletonRows rows={5} />
  if (err) return <SectionError error={err} onRetry={load} />
  if (!data) return null

  const { workloads, summary } = data
  const displayed = showIssuesOnly ? workloads.filter(w => w.protection === 'none') : workloads

  const protectionBadge = (p: AffinityWorkload['protection']) => {
    if (p === 'required') return <span className="text-[10px] bg-emerald-900/40 text-emerald-300 border border-emerald-700/40 px-1.5 py-0.5 rounded-full font-medium">required</span>
    if (p === 'preferred') return <span className="text-[10px] bg-yellow-900/40 text-yellow-300 border border-yellow-700/40 px-1.5 py-0.5 rounded-full font-medium">preferred</span>
    return <span className="text-[10px] bg-red-900/40 text-red-300 border border-red-700/40 px-1.5 py-0.5 rounded-full font-medium">none</span>
  }

  const riskPct = summary.total_workloads > 0
    ? Math.round((summary.unprotected / summary.total_workloads) * 100)
    : 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Layers size={16} className="text-sky-400" />
          <span className="text-base font-semibold text-white">Anti-Affinity Coverage</span>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-xs text-slate-400 flex items-center gap-2">
            Min replicas:
            <select value={minReplicas} onChange={e => setMinReplicas(Number(e.target.value))}
              className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-blue-500">
              {[1,2,3,5].map(v => <option key={v} value={v}>{`≥${v}`}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400 flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={showIssuesOnly} onChange={e => setShowIssuesOnly(e.target.checked)} className="w-3 h-3 accent-blue-500" />
            Unprotected only
          </label>
          <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
          <div className="text-2xl font-bold text-white">{summary.total_workloads}</div>
          <div className="text-xs text-slate-500 mt-0.5">Workloads</div>
        </div>
        <div className={`rounded-xl border p-3 text-center ${summary.unprotected > 0 ? 'bg-red-900/20 border-red-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
          <div className={`text-2xl font-bold ${summary.unprotected > 0 ? 'text-red-400' : 'text-slate-400'}`}>{summary.unprotected}</div>
          <div className="text-xs text-slate-500 mt-0.5">Unprotected</div>
        </div>
        <div className={`rounded-xl border p-3 text-center ${summary.preferred_only > 0 ? 'bg-yellow-900/20 border-yellow-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
          <div className={`text-2xl font-bold ${summary.preferred_only > 0 ? 'text-yellow-400' : 'text-slate-400'}`}>{summary.preferred_only}</div>
          <div className="text-xs text-slate-500 mt-0.5">Preferred Only</div>
        </div>
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
          <div className="text-2xl font-bold text-emerald-400">{summary.fully_protected}</div>
          <div className="text-xs text-slate-500 mt-0.5">Fully Protected</div>
        </div>
      </div>

      {summary.unprotected > 0 && (
        <div className="flex items-start gap-2 p-3 bg-red-900/20 border border-red-700/40 rounded-xl text-xs text-red-300">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <div>
            <span className="font-semibold">{riskPct}% of workloads are unprotected</span> — if a node fails, multiple replicas may be lost simultaneously.
            Add <code className="font-mono bg-red-900/40 px-1 rounded">podAntiAffinity</code> or <code className="font-mono bg-red-900/40 px-1 rounded">topologySpreadConstraints</code> to spread replicas across nodes.
          </div>
        </div>
      )}

      {displayed.length === 0 ? (
        <EmptyState icon={Layers} title={showIssuesOnly ? 'All workloads protected' : 'No workloads found'} hint={showIssuesOnly ? 'No anti-affinity gaps detected' : 'No workloads match filters'} />
      ) : (
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 overflow-hidden">
          <table className="min-w-full text-xs">
            <thead className="bg-slate-800/50 border-b border-slate-800">
              <tr className="text-[10px] text-slate-400 uppercase tracking-wider">
                <th className="text-left px-4 py-2.5">Workload</th>
                <th className="text-left px-4 py-2.5">Kind</th>
                <th className="text-left px-4 py-2.5">Namespace</th>
                <th className="text-center px-4 py-2.5">Replicas</th>
                <th className="text-center px-4 py-2.5">Spread Mechanism</th>
                <th className="text-center px-4 py-2.5">Protection</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40">
              {displayed.map(w => (
                <tr key={`${w.kind}/${w.namespace}/${w.name}`} className={`transition-colors ${w.protection === 'none' ? 'bg-red-900/10 hover:bg-red-900/20' : 'hover:bg-slate-800/20'}`}>
                  <td className="px-4 py-3 font-mono font-medium text-white">{w.name}</td>
                  <td className="px-4 py-3">
                    <span className="text-[10px] bg-slate-700/60 text-slate-400 border border-slate-600/40 px-1.5 py-0.5 rounded">{w.kind}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{w.namespace}</td>
                  <td className="px-4 py-3 text-center">
                    <span className={`font-medium ${w.ready < w.replicas ? 'text-amber-400' : 'text-slate-400'}`}>
                      {w.ready}/{w.replicas}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center text-slate-400">
                    {w.has_tsc ? (
                      <span className="text-sky-400">{w.tsc_count} TSC</span>
                    ) : w.has_anti_affinity ? (
                      <span className="text-blue-400">podAntiAffinity</span>
                    ) : (
                      <span className="text-slate-600 italic">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">{protectionBadge(w.protection)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Pod Security Audit (Loop 59) ─────────────────────────────────────────────

interface SecurityContainer {
  container: string; image: string
  privileged: boolean; allow_privilege_escalation: boolean | null
  run_as_user: number | null; run_as_non_root: boolean | null
  read_only_root_fs: boolean; risks: string[]
}

interface SecurityFinding {
  name: string; namespace: string; phase: string
  host_network: boolean; host_pid: boolean; host_ipc: boolean
  containers: SecurityContainer[]; risks: string[]; risk_score: number
}

interface SecuritySummary {
  total_pods: number; flagged_pods: number
  privileged: number; run_as_root: number; allow_escalation: number
  host_network: number; host_pid: number; host_ipc: number; no_read_only_root: number
}

function SecurityAuditSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [findings, setFindings] = useState<SecurityFinding[]>([])
  const [summary, setSummary] = useState<SecuritySummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState<string>('')

  const load = useCallback(() => {
    setLoading(true); setErr(null)
    vksGet<{ findings: SecurityFinding[]; summary: SecuritySummary }>(
      `${clusterId}/security-audit${namespace ? `?namespace=${namespace}` : ''}`
    ).then(d => { setFindings(d.findings); setSummary(d.summary); setLoading(false) })
     .catch(e => { setErr(String(e)); setLoading(false) })
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])
  useAutoRefresh(load, 60_000)

  const toggleExpand = (key: string) => setExpanded(prev => {
    const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n
  })

  if (loading) return <SkeletonRows rows={5} />
  if (err) return <SectionError error={err} onRetry={load} />

  const RISK_LABELS: Record<string, { label: string; color: string }> = {
    privileged:           { label: 'Privileged', color: 'bg-red-900/50 text-red-300 border border-red-700/50' },
    run_as_root:          { label: 'Root UID',   color: 'bg-orange-900/40 text-orange-300 border border-orange-700/40' },
    allow_escalation:     { label: 'PrivEsc',    color: 'bg-amber-900/40 text-amber-300 border border-amber-700/40' },
    host_network:         { label: 'HostNet',    color: 'bg-rose-900/40 text-rose-300 border border-rose-700/40' },
    host_pid:             { label: 'HostPID',    color: 'bg-rose-900/40 text-rose-300 border border-rose-700/40' },
    host_ipc:             { label: 'HostIPC',    color: 'bg-rose-900/40 text-rose-300 border border-rose-700/40' },
    no_read_only_root_fs: { label: 'RW Root',    color: 'bg-slate-700/60 text-slate-400 border border-slate-600/40' },
  }

  const riskBadge = (risk: string) => {
    const r = RISK_LABELS[risk] || { label: risk, color: 'bg-slate-100 text-slate-600' }
    return <span key={risk} className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${r.color}`}>{r.label}</span>
  }

  const displayed = filter
    ? findings.filter(f => f.risks.includes(filter))
    : findings

  const flagPct = summary && summary.total_pods > 0
    ? Math.round((summary.flagged_pods / summary.total_pods) * 100)
    : 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <ShieldAlert size={16} className="text-red-400" />
          <span className="text-base font-semibold text-white">Security Audit</span>
          {summary && <span className="text-xs text-slate-500">{summary.total_pods} pods scanned</span>}
        </div>
        <div className="flex items-center gap-2">
          <select value={filter} onChange={e => setFilter(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-blue-500">
            <option value="">All risks</option>
            {Object.entries(RISK_LABELS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
            <div className="text-2xl font-bold text-white">{summary.total_pods}</div>
            <div className="text-xs text-slate-500 mt-0.5">Total Pods</div>
          </div>
          <div className={`rounded-xl border p-3 text-center ${summary.flagged_pods > 0 ? 'bg-red-900/20 border-red-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
            <div className={`text-2xl font-bold ${summary.flagged_pods > 0 ? 'text-red-400' : 'text-slate-400'}`}>{summary.flagged_pods}</div>
            <div className="text-xs text-slate-500 mt-0.5">Flagged ({flagPct}%)</div>
          </div>
          <div className={`rounded-xl border p-3 text-center ${summary.privileged > 0 ? 'bg-red-900/20 border-red-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
            <div className={`text-2xl font-bold ${summary.privileged > 0 ? 'text-red-400' : 'text-slate-400'}`}>{summary.privileged}</div>
            <div className="text-xs text-slate-500 mt-0.5">Privileged</div>
          </div>
          <div className={`rounded-xl border p-3 text-center ${summary.host_network > 0 ? 'bg-rose-900/20 border-rose-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
            <div className={`text-2xl font-bold ${summary.host_network > 0 ? 'text-rose-400' : 'text-slate-400'}`}>{summary.host_network}</div>
            <div className="text-xs text-slate-500 mt-0.5">Host Network</div>
          </div>
        </div>
      )}

      {displayed.length === 0 ? (
        <EmptyState icon={ShieldAlert} title="No security issues found" hint="All scanned pods pass the security policy checks" />
      ) : (
        <div className="space-y-1.5">
          {displayed.map(f => {
            const key = `${f.namespace}/${f.name}`
            const isExpanded = expanded.has(key)
            const isCritical = f.risks.some(r => ['privileged', 'host_network', 'host_pid'].includes(r))
            return (
              <div key={key} className={`bg-slate-900/60 rounded-xl border overflow-hidden ${isCritical ? 'border-red-700/50' : 'border-slate-700/50'}`}>
                <button className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-800/30 text-left transition-colors"
                  onClick={() => toggleExpand(key)}>
                  <div className="flex items-center gap-3 min-w-0">
                    {isExpanded ? <ChevronDown size={14} className="text-slate-400 shrink-0" /> : <ChevronRight size={14} className="text-slate-400 shrink-0" />}
                    <span className="font-mono font-medium text-white truncate">{f.name}</span>
                    <span className="text-slate-500 text-xs">{f.namespace}</span>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
                    {f.risks.filter(r => r !== 'no_read_only_root_fs').slice(0, 4).map(riskBadge)}
                    {f.risk_score > 4 && <span className="text-[10px] text-slate-500">+{f.risk_score - 4}</span>}
                  </div>
                </button>
                {isExpanded && (
                  <div className="border-t border-slate-800 bg-slate-800/20 px-4 py-3">
                    {(f.host_network || f.host_pid || f.host_ipc) && (
                      <div className="flex gap-2 mb-3">
                        {f.host_network && <span className="text-xs bg-rose-900/40 text-rose-300 border border-rose-700/40 px-2 py-1 rounded">hostNetwork</span>}
                        {f.host_pid    && <span className="text-xs bg-rose-900/40 text-rose-300 border border-rose-700/40 px-2 py-1 rounded">hostPID</span>}
                        {f.host_ipc    && <span className="text-xs bg-rose-900/40 text-rose-300 border border-rose-700/40 px-2 py-1 rounded">hostIPC</span>}
                      </div>
                    )}
                    {f.containers.length > 0 && (
                      <table className="min-w-full text-xs">
                        <thead>
                          <tr className="text-slate-500 uppercase tracking-wide text-[10px]">
                            <th className="text-left pb-1.5">Container</th>
                            <th className="text-left pb-1.5">Image</th>
                            <th className="text-left pb-1.5">Risks</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60">
                          {f.containers.map(c => (
                            <tr key={c.container}>
                              <td className="py-1.5 font-mono text-slate-300">{c.container}</td>
                              <td className="py-1.5 text-slate-500 font-mono max-w-[220px] truncate">{c.image}</td>
                              <td className="py-1.5">
                                <div className="flex gap-1 flex-wrap">{c.risks.map(riskBadge)}</div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Namespace Label Compliance (Loop 60) ─────────────────────────────────────

interface NsLabelResult {
  name: string; is_system: boolean; labels: Record<string, string>
  label_count: number; psa_mode: string | null
  has_team_label: boolean; has_env_label: boolean
  missing_custom_labels: string[]; issues: string[]
}

interface NsLabelSummary {
  total: number; system_namespaces: number; no_psa_label: number
  no_team_label: number; no_env_label: number; missing_custom: number; fully_labeled: number
}

function NamespaceLabelsSection({ clusterId }: { clusterId: string }) {
  const [data, setData] = useState<{ namespaces: NsLabelResult[]; summary: NsLabelSummary } | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [includeSystem, setIncludeSystem] = useState(false)
  const [customRequired, setCustomRequired] = useState('')
  const [showIssuesOnly, setShowIssuesOnly] = useState(false)
  const [labelTarget, setLabelTarget] = useState<NsLabelResult | null>(null)
  const [labelPatch, setLabelPatch] = useState<Record<string, string>>({})
  const [patching, setPatching] = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true); setErr(null)
    const params = new URLSearchParams()
    params.set('include_system', String(includeSystem))
    if (customRequired.trim()) params.set('required', customRequired.trim())
    vksGet<NonNullable<typeof data>>(`${clusterId}/namespace-labels?${params}`)
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setErr(String(e)); setLoading(false) })
  }

  useEffect(load, [clusterId, includeSystem])

  async function applyLabels() {
    if (!labelTarget) return
    setPatching(true)
    try {
      const r1 = await vksPatch<{ requires_confirm: boolean; token: string }>(
        `${clusterId}/namespaces/${labelTarget.name}/labels`, { labels: labelPatch }
      )
      if (r1.requires_confirm) {
        const r2 = await vksPatch<{ ok: boolean }>(
          `${clusterId}/namespaces/${labelTarget.name}/labels?token=${r1.token}`, { labels: labelPatch }
        )
        if (r2.ok) { toast.success(`Labels applied to ${labelTarget.name}`); setLabelTarget(null); load() }
      }
    } catch (e) { toast.error(`Label patch failed: ${e}`) }
    finally { setPatching(false) }
  }

  if (loading) return <SkeletonRows rows={5} />
  if (err) return <SectionError error={err} onRetry={load} />
  if (!data) return null

  const { namespaces, summary } = data
  const displayed = showIssuesOnly ? namespaces.filter(n => n.issues.length > 0) : namespaces

  const PSA_COLORS: Record<string, string> = {
    restricted: 'bg-emerald-900/40 text-emerald-300 border border-emerald-700/40',
    baseline:   'bg-yellow-900/40 text-yellow-300 border border-yellow-700/40',
    privileged: 'bg-red-900/40 text-red-300 border border-red-700/40',
  }

  return (
    <div className="space-y-4">
      {labelTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setLabelTarget(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800">
              <Tag size={14} className="text-violet-400" />
              <span className="text-sm font-medium text-white">Apply Labels — <span className="font-mono text-blue-400">{labelTarget.name}</span></span>
              <button onClick={() => setLabelTarget(null)} className="ml-auto p-1 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
            </div>
            <div className="p-5 space-y-3">
              {Object.entries(labelPatch).map(([k, v]) => (
                <div key={k} className="grid grid-cols-2 gap-2">
                  <input value={k} readOnly className="bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono text-slate-400" />
                  <input value={v} onChange={e => setLabelPatch(p => ({ ...p, [k]: e.target.value }))}
                    className="bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-blue-500" />
                </div>
              ))}
              <button onClick={() => setLabelPatch(p => ({ ...p, '': '' }))}
                className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1">
                <Plus size={11} /> Add label
              </button>
            </div>
            <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-800">
              <button onClick={() => setLabelTarget(null)} className="px-4 py-2 text-xs text-slate-400 hover:text-white">Cancel</button>
              <button onClick={applyLabels} disabled={patching}
                className="flex items-center gap-1.5 px-4 py-2 text-xs bg-violet-600 hover:bg-violet-500 text-white rounded-lg disabled:opacity-50">
                {patching ? <Loader2 size={11} className="animate-spin" /> : <Tag size={11} />} Apply Labels
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Tag size={16} className="text-violet-400" />
          <span className="text-base font-semibold text-white">Namespace Label Compliance</span>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-xs text-slate-400 flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={includeSystem} onChange={e => setIncludeSystem(e.target.checked)} className="w-3 h-3 accent-blue-500" />
            Include system ns
          </label>
          <label className="text-xs text-slate-400 flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={showIssuesOnly} onChange={e => setShowIssuesOnly(e.target.checked)} className="w-3 h-3 accent-blue-500" />
            Issues only
          </label>
          <input
            type="text"
            placeholder="Required labels (comma-sep)"
            value={customRequired}
            onChange={e => setCustomRequired(e.target.value)}
            onBlur={load}
            onKeyDown={e => e.key === 'Enter' && load()}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-300 placeholder-slate-500 w-48 focus:outline-none focus:border-blue-500"
          />
          <button onClick={load} className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
          <div className="text-2xl font-bold text-white">{summary.total}</div>
          <div className="text-xs text-slate-500 mt-0.5">Namespaces</div>
        </div>
        <div className={`rounded-xl border p-3 text-center ${summary.no_psa_label > 0 ? 'bg-red-900/20 border-red-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
          <div className={`text-2xl font-bold ${summary.no_psa_label > 0 ? 'text-red-400' : 'text-slate-400'}`}>{summary.no_psa_label}</div>
          <div className="text-xs text-slate-500 mt-0.5">No PSA Label</div>
        </div>
        <div className={`rounded-xl border p-3 text-center ${summary.no_team_label > 0 ? 'bg-amber-900/20 border-amber-700/50' : 'bg-slate-900/60 border-slate-700/50'}`}>
          <div className={`text-2xl font-bold ${summary.no_team_label > 0 ? 'text-amber-400' : 'text-slate-400'}`}>{summary.no_team_label}</div>
          <div className="text-xs text-slate-500 mt-0.5">No Team Label</div>
        </div>
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-center">
          <div className="text-2xl font-bold text-emerald-400">{summary.fully_labeled}</div>
          <div className="text-xs text-slate-500 mt-0.5">Fully Labeled</div>
        </div>
      </div>

      {displayed.length === 0 ? (
        <EmptyState icon={Tag} title="No namespaces found" hint="No namespaces match the current filters" />
      ) : (
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/50 overflow-hidden">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-800/50 border-b border-slate-800">
              <tr className="text-[10px] text-slate-400 uppercase tracking-wider">
                <th className="text-left px-4 py-2.5">Namespace</th>
                <th className="text-center px-4 py-2.5">Labels</th>
                <th className="text-center px-4 py-2.5">PSA Mode</th>
                <th className="text-center px-4 py-2.5">Team</th>
                <th className="text-center px-4 py-2.5">Env</th>
                <th className="text-left px-4 py-2.5">Issues</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40">
              {displayed.map(n => (
                <tr key={n.name} className={`hover:bg-slate-800/20 transition-colors ${n.issues.length > 0 ? 'bg-amber-900/5' : ''}`}>
                  <td className="px-4 py-3 font-mono font-medium text-white">
                    {n.name}
                    {n.is_system && <span className="ml-1.5 text-[10px] text-slate-500 bg-slate-700/60 px-1 py-0.5 rounded">system</span>}
                  </td>
                  <td className="px-4 py-3 text-center text-xs text-slate-400">{n.label_count}</td>
                  <td className="px-4 py-3 text-center">
                    {n.psa_mode ? (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${PSA_COLORS[n.psa_mode] || 'bg-slate-700/60 text-slate-400 border border-slate-600/40'}`}>{n.psa_mode}</span>
                    ) : (
                      <span className="text-[10px] text-slate-600 italic">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {n.has_team_label ? <span className="text-emerald-400 text-sm">✓</span> : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {n.has_env_label ? <span className="text-emerald-400 text-sm">✓</span> : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    {n.issues.length === 0
                      ? <span className="text-[10px] text-emerald-400">Compliant</span>
                      : <span className="text-[10px] text-amber-400">{n.issues.length} issue{n.issues.length !== 1 ? 's' : ''}</span>}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {!n.is_system && (
                      <button
                        onClick={() => {
                          const defaults: Record<string, string> = {}
                          if (!n.has_team_label) defaults['team'] = ''
                          if (!n.has_env_label) defaults['env'] = ''
                          if (!n.psa_mode) defaults['pod-security.kubernetes.io/enforce'] = 'baseline'
                          setLabelPatch(defaults)
                          setLabelTarget(n)
                        }}
                        className="text-[10px] px-2 py-1 rounded-md bg-violet-900/40 hover:bg-violet-800/60 text-violet-300 border border-violet-700/40 font-medium transition-colors"
                      >
                        {n.issues.length > 0 ? 'Fix Labels' : 'Edit Labels'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── OOM Kill Detector (Loop 47) ──────────────────────────────────────────────

interface OOMContainer {
  name: string; restart_count: number; is_oom: boolean
  last_reason: string; last_exit_code: number | null; last_finished: string
  req_mem_mib: number; lim_mem_mib: number; live_mem_mib: number | null
  suggested_limit_mib: number | null
}

interface OOMPod {
  name: string; namespace: string; phase: string
  containers: OOMContainer[]; total_restarts: number; has_oom: boolean
}

function OOMDetectorSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [pods, setPods] = useState<OOMPod[]>([])
  const [summary, setSummary] = useState({ total_flagged: 0, oom_pods: 0, metrics_available: false })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [threshold, setThreshold] = useState('5')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const load = useCallback(() => {
    setLoading(true)
    const ns = namespace ? `&namespace=${namespace}` : ''
    const t = parseInt(threshold) || 5
    vksGet<any>(`${clusterId}/oom-detector?restart_threshold=${t}${ns}`)
      .then(d => {
        setPods(d.pods || [])
        setSummary({ total_flagged: d.total_flagged, oom_pods: d.oom_pods, metrics_available: d.metrics_available })
        setError('')
      })
      .catch(() => setError('Failed to scan for OOM kills'))
      .finally(() => setLoading(false))
  }, [clusterId, namespace, threshold])

  useEffect(() => { load() }, [load])

  const toggle = (key: string) => setExpanded(prev => {
    const n = new Set(prev); if (n.has(key)) n.delete(key); else n.add(key); return n
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2"><Flame size={20} className="text-red-400" />OOM Kill Detector</h2>
          <p className="text-slate-400 text-sm mt-1">Identify containers killed by out-of-memory events or with high restart counts</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-slate-400">
            Flag if restarts ≥
            <input value={threshold} onChange={e => setThreshold(e.target.value)}
              className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono focus:outline-none focus:border-emerald-500" />
          </label>
          <button onClick={load} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300">
            <RefreshCw size={14} />Scan
          </button>
        </div>
      </div>

      {/* Summary */}
      {(summary.total_flagged > 0 || !loading) && (
        <div className="flex items-center gap-3">
          {summary.oom_pods > 0 && <span className="text-xs bg-red-900/30 border border-red-700/50 rounded-lg px-3 py-1.5 text-red-300 font-semibold">{summary.oom_pods} OOMKilled</span>}
          {summary.total_flagged > summary.oom_pods && <span className="text-xs bg-yellow-900/30 border border-yellow-700/50 rounded-lg px-3 py-1.5 text-yellow-300">{summary.total_flagged - summary.oom_pods} high restarts</span>}
          {summary.total_flagged === 0 && !loading && <span className="text-xs bg-emerald-900/30 border border-emerald-700/50 rounded-lg px-3 py-1.5 text-emerald-300">No issues found</span>}
          {!summary.metrics_available && <span className="text-xs text-slate-500 italic">Memory metrics unavailable — showing requested limits only</span>}
        </div>
      )}

      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}
      {loading && <SkeletonRows rows={5} />}

      {!loading && pods.length === 0 && !error && (
        <EmptyState icon={Flame} title="No OOM kills or excessive restarts detected" hint="All containers are within the configured restart threshold." />
      )}

      {!loading && pods.map(pod => {
        const key = `${pod.namespace}/${pod.name}`
        const isOpen = expanded.has(key)
        return (
          <div key={key} className={`bg-slate-900 border rounded-xl overflow-hidden ${pod.has_oom ? 'border-red-700/50' : 'border-yellow-700/40'}`}>
            <button onClick={() => toggle(key)} className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-800/40 transition-colors">
              {pod.has_oom
                ? <Flame size={16} className="text-red-400 shrink-0" />
                : <AlertTriangle size={16} className="text-yellow-400 shrink-0" />}
              <span className="font-medium text-white">{pod.name}</span>
              <span className="text-slate-500 font-mono text-xs">{pod.namespace}</span>
              <span className={`ml-2 text-xs px-2 py-0.5 rounded border ${pod.has_oom ? 'bg-red-900/40 text-red-300 border-red-700/50' : 'bg-yellow-900/40 text-yellow-300 border-yellow-700/50'}`}>
                {pod.has_oom ? 'OOMKilled' : `${pod.total_restarts} restarts`}
              </span>
              <span className={`text-xs px-2 py-0.5 rounded border ml-1 ${pod.phase === 'Running' ? 'bg-emerald-900/30 text-emerald-400 border-emerald-700/40' : 'bg-slate-700/40 text-slate-400 border-slate-600/40'}`}>{pod.phase}</span>
              <ChevronRight size={16} className={`ml-auto text-slate-500 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
            </button>

            {isOpen && (
              <div className="border-t border-slate-800">
                <table className="w-full text-xs">
                  <thead className="bg-slate-800/60">
                    <tr className="text-slate-400">
                      <th className="text-left px-4 py-2">Container</th>
                      <th className="text-left px-4 py-2">Restarts</th>
                      <th className="text-left px-4 py-2">Last Reason</th>
                      <th className="text-left px-4 py-2">Req (MiB)</th>
                      <th className="text-left px-4 py-2">Lim (MiB)</th>
                      <th className="text-left px-4 py-2">Live (MiB)</th>
                      <th className="text-left px-4 py-2">Suggested Lim</th>
                      <th className="text-left px-4 py-2">Last Finished</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pod.containers.map(c => (
                      <tr key={c.name} className={`border-t border-slate-800 ${c.is_oom ? 'bg-red-950/10' : ''}`}>
                        <td className="px-4 py-2.5 text-white font-mono">{c.name}</td>
                        <td className={`px-4 py-2.5 font-bold ${c.restart_count >= 10 ? 'text-red-400' : 'text-yellow-400'}`}>{c.restart_count}</td>
                        <td className={`px-4 py-2.5 ${c.is_oom ? 'text-red-300 font-semibold' : 'text-slate-400'}`}>{c.last_reason || '—'}</td>
                        <td className="px-4 py-2.5 text-slate-300">{c.req_mem_mib || '—'}</td>
                        <td className="px-4 py-2.5 text-slate-300">{c.lim_mem_mib || '—'}</td>
                        <td className="px-4 py-2.5 text-slate-300">{c.live_mem_mib ?? '—'}</td>
                        <td className={`px-4 py-2.5 ${c.suggested_limit_mib ? 'text-emerald-400 font-semibold' : 'text-slate-600'}`}>
                          {c.suggested_limit_mib ? `${c.suggested_limit_mib} MiB` : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-slate-500 whitespace-nowrap">{c.last_finished ? new Date(c.last_finished).toLocaleString() : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── TLS Certificate Expiry Scanner (Loop 46) ─────────────────────────────────

interface TLSCert {
  secret_name: string; namespace: string; cn: string; sans: string[]
  expiry: string; days_remaining: number; status: 'ok' | 'warning' | 'expired'
}

function TLSCertsSection({ clusterId }: { clusterId: string }) {
  const [certs, setCerts] = useState<TLSCert[]>([])
  const [summary, setSummary] = useState({ total: 0, expired: 0, warning: 0, ok: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [daysWarning, setDaysWarning] = useState('30')
  const [namespace, setNamespace] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    const ns = namespace ? `&namespace=${namespace}` : ''
    const dw = parseInt(daysWarning) || 30
    vksGet<any>(`${clusterId}/tls-certs?days_warning=${dw}${ns}`)
      .then(d => {
        setCerts(d.certs || [])
        setSummary({ total: d.total, expired: d.expired, warning: d.warning, ok: d.ok })
        setError('')
      })
      .catch(() => setError('Failed to load TLS certificates'))
      .finally(() => setLoading(false))
  }, [clusterId, daysWarning, namespace])

  useEffect(() => { load() }, [load])

  const statusBadge = (status: string, days: number) => {
    if (status === 'expired') return <span className="px-2 py-0.5 rounded text-xs border bg-red-900/40 text-red-300 border-red-700/50 font-medium">Expired</span>
    if (status === 'warning') return <span className="px-2 py-0.5 rounded text-xs border bg-yellow-900/40 text-yellow-300 border-yellow-700/50 font-medium">{days}d left</span>
    return <span className="px-2 py-0.5 rounded text-xs border bg-emerald-900/40 text-emerald-300 border-emerald-700/50 font-medium">{days}d</span>
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2"><ShieldAlert size={20} className="text-emerald-400" />TLS Certificate Scanner</h2>
          <p className="text-slate-400 text-sm mt-1">Inspect kubernetes.io/tls secrets and track certificate expiry</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-slate-400">
            Warn if under
            <input value={daysWarning} onChange={e => setDaysWarning(e.target.value)}
              className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono focus:outline-none focus:border-emerald-500" />
            days
          </label>
          <input value={namespace} onChange={e => setNamespace(e.target.value)} placeholder="All namespaces"
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500 w-36" />
          <button onClick={load} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300">
            <RefreshCw size={14} />Scan
          </button>
        </div>
      </div>

      {/* Summary chips */}
      {summary.total > 0 && (
        <div className="flex items-center gap-3">
          <span className="text-xs bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-slate-300">{summary.total} certs total</span>
          {summary.expired > 0 && <span className="text-xs bg-red-900/30 border border-red-700/50 rounded-lg px-3 py-1.5 text-red-300 font-semibold">{summary.expired} expired</span>}
          {summary.warning > 0 && <span className="text-xs bg-yellow-900/30 border border-yellow-700/50 rounded-lg px-3 py-1.5 text-yellow-300">{summary.warning} expiring soon</span>}
          {summary.ok > 0 && <span className="text-xs bg-emerald-900/30 border border-emerald-700/50 rounded-lg px-3 py-1.5 text-emerald-300">{summary.ok} healthy</span>}
        </div>
      )}

      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}
      {loading && <SkeletonRows rows={5} />}

      {!loading && certs.length === 0 && !error && (
        <EmptyState icon={ShieldAlert} title="No TLS certificates found" hint="Create secrets of type kubernetes.io/tls to see them here." />
      )}

      {!loading && certs.length > 0 && (
        <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-800/80">
              <tr className="text-slate-400 text-xs">
                <th className="text-left px-4 py-3">Status</th>
                <th className="text-left px-4 py-3">Secret</th>
                <th className="text-left px-4 py-3">Namespace</th>
                <th className="text-left px-4 py-3">CN</th>
                <th className="text-left px-4 py-3">SANs</th>
                <th className="text-left px-4 py-3">Expires</th>
              </tr>
            </thead>
            <tbody>
              {certs.map((c, i) => (
                <tr key={i} className={`border-t border-slate-800 hover:bg-slate-800/30 transition-colors ${c.status === 'expired' ? 'bg-red-950/10' : ''}`}>
                  <td className="px-4 py-3">{statusBadge(c.status, c.days_remaining)}</td>
                  <td className="px-4 py-3 font-mono text-xs text-white">{c.secret_name}</td>
                  <td className="px-4 py-3 text-slate-400 font-mono text-xs">{c.namespace}</td>
                  <td className="px-4 py-3 text-slate-200 text-xs">{c.cn || '—'}</td>
                  <td className="px-4 py-3 max-w-xs">
                    <div className="flex flex-wrap gap-1">
                      {c.sans.slice(0, 3).map((s, j) => (
                        <span key={j} className="text-xs bg-slate-800 border border-slate-700 rounded px-1.5 py-0.5 text-slate-400 font-mono truncate max-w-[140px]">{s}</span>
                      ))}
                      {c.sans.length > 3 && <span className="text-xs text-slate-600">+{c.sans.length - 3}</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs whitespace-nowrap">{c.expiry ? new Date(c.expiry).toLocaleDateString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Network Policy Traffic Analyzer (Loop 45) ─────────────────────────────────

interface NetPolVerdict {
  verdict: string; allowed: boolean
  src: { namespace: string; labels: Record<string, string> }
  dst: { namespace: string; labels: Record<string, string>; port: number; protocol: string }
  ingress: { allowed: boolean; allowed_by: string[]; blocked_by: string[]; policy_count: number }
  egress: { allowed: boolean; allowed_by: string[]; blocked_by: string[]; policy_count: number }
}

function LabelEditor({ labels, onChange }: { labels: Record<string, string>; onChange: (l: Record<string, string>) => void }) {
  const [key, setKey] = useState('')
  const [val, setVal] = useState('')
  const add = () => {
    if (!key.trim()) return
    onChange({ ...labels, [key.trim()]: val.trim() })
    setKey(''); setVal('')
  }
  const remove = (k: string) => { const n = { ...labels }; delete n[k]; onChange(n) }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5 min-h-[28px]">
        {Object.entries(labels).map(([k, v]) => (
          <span key={k} className="flex items-center gap-1 text-xs bg-slate-700 border border-slate-600 rounded px-2 py-0.5 text-slate-200 font-mono">
            {k}={v}
            <button onClick={() => remove(k)} className="text-slate-500 hover:text-red-400 ml-1"><X size={10} /></button>
          </span>
        ))}
        {Object.keys(labels).length === 0 && <span className="text-xs text-slate-600 italic">any pod</span>}
      </div>
      <div className="flex items-center gap-1.5">
        <input value={key} onChange={e => setKey(e.target.value)} placeholder="key"
          className="w-28 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono focus:outline-none focus:border-emerald-500" />
        <span className="text-slate-600">=</span>
        <input value={val} onChange={e => setVal(e.target.value)} placeholder="value" onKeyDown={e => e.key === 'Enter' && add()}
          className="w-28 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono focus:outline-none focus:border-emerald-500" />
        <button onClick={add} className="text-xs px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-300">+</button>
      </div>
    </div>
  )
}

function NetPolAnalyzerSection({ clusterId }: { clusterId: string }) {
  const [srcNs, setSrcNs] = useState('default')
  const [srcLabels, setSrcLabels] = useState<Record<string, string>>({ app: 'frontend' })
  const [dstNs, setDstNs] = useState('default')
  const [dstLabels, setDstLabels] = useState<Record<string, string>>({ app: 'backend' })
  const [port, setPort] = useState('8080')
  const [protocol, setProtocol] = useState('TCP')
  const [result, setResult] = useState<NetPolVerdict | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [namespaces, setNamespaces] = useState<string[]>([])

  useEffect(() => {
    vksGet<any>(`${clusterId}/namespaces`)
      .then(d => setNamespaces((d.items || []).map((n: { name: string }) => n.name).sort()))
      .catch(() => {})
  }, [clusterId])

  const analyze = () => {
    setLoading(true)
    const srcStr = Object.entries(srcLabels).map(([k, v]) => `${k}=${v}`).join(',')
    const dstStr = Object.entries(dstLabels).map(([k, v]) => `${k}=${v}`).join(',')
    const p = parseInt(port) || 80
    vksGet<any>(`${clusterId}/netpol/analyze?src_ns=${srcNs}&src_labels=${encodeURIComponent(srcStr)}&dst_ns=${dstNs}&dst_labels=${encodeURIComponent(dstStr)}&port=${p}&protocol=${protocol}`)
      .then(d => { setResult(d); setError('') })
      .catch(() => setError('Analysis failed'))
      .finally(() => setLoading(false))
  }

  const nsInput = (val: string, setter: (s: string) => void) => (
    namespaces.length > 0 ? (
      <select value={val} onChange={e => setter(e.target.value)}
        className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500 w-40">
        {namespaces.map(ns => <option key={ns} value={ns}>{ns}</option>)}
      </select>
    ) : (
      <input value={val} onChange={e => setter(e.target.value)} placeholder="namespace"
        className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500 w-40" />
    )
  )

  const verdictCfg: Record<string, { color: string; icon: string; label: string }> = {
    allowed: { color: 'bg-emerald-900/40 border-emerald-600 text-emerald-300', icon: '✓', label: 'ALLOWED' },
    blocked_by_ingress: { color: 'bg-red-900/40 border-red-600 text-red-300', icon: '✗', label: 'BLOCKED (Ingress Policy)' },
    blocked_by_egress: { color: 'bg-orange-900/40 border-orange-600 text-orange-300', icon: '✗', label: 'BLOCKED (Egress Policy)' },
    blocked_by_both: { color: 'bg-red-900/40 border-red-600 text-red-300', icon: '✗', label: 'BLOCKED (Ingress + Egress)' },
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-bold text-white flex items-center gap-2"><Network size={20} className="text-emerald-400" />Network Policy Analyzer</h2>
        <p className="text-slate-400 text-sm mt-1">Evaluate whether traffic from source pod to destination pod is permitted by NetworkPolicies</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Source pod */}
        <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 space-y-3">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Source Pod</div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500 w-20 shrink-0">Namespace</span>
            {nsInput(srcNs, setSrcNs)}
          </div>
          <div>
            <span className="text-xs text-slate-500 block mb-1.5">Labels</span>
            <LabelEditor labels={srcLabels} onChange={setSrcLabels} />
          </div>
        </div>

        {/* Destination pod */}
        <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 space-y-3">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Destination Pod</div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500 w-20 shrink-0">Namespace</span>
            {nsInput(dstNs, setDstNs)}
          </div>
          <div>
            <span className="text-xs text-slate-500 block mb-1.5">Labels</span>
            <LabelEditor labels={dstLabels} onChange={setDstLabels} />
          </div>
          <div className="flex items-center gap-3 pt-1">
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500 w-8">Port</span>
              <input value={port} onChange={e => setPort(e.target.value)}
                className="w-20 bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-slate-200 font-mono focus:outline-none focus:border-emerald-500" />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Proto</span>
              <select value={protocol} onChange={e => setProtocol(e.target.value)}
                className="bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-emerald-500">
                <option>TCP</option><option>UDP</option><option>SCTP</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      <button onClick={analyze} disabled={loading}
        className="flex items-center gap-2 px-5 py-2.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 rounded-xl text-white font-medium">
        {loading ? <Loader2 size={16} className="animate-spin" /> : <Network size={16} />}
        Analyze Traffic
      </button>

      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}

      {result && (
        <div className="space-y-4">
          {/* Verdict banner */}
          {(() => {
            const cfg = verdictCfg[result.verdict] || verdictCfg['blocked_by_both']
            return (
              <div className={`border-2 rounded-xl p-5 flex items-center gap-4 ${cfg.color}`}>
                <div className="text-4xl font-bold">{cfg.icon}</div>
                <div>
                  <div className="text-2xl font-bold">{cfg.label}</div>
                  <div className="text-sm opacity-80 mt-0.5">
                    {result.src.namespace}/{Object.entries(result.src.labels).map(([k,v]) => `${k}=${v}`).join(',') || '*'}
                    {' → '}
                    {result.dst.namespace}/{Object.entries(result.dst.labels).map(([k,v]) => `${k}=${v}`).join(',') || '*'}
                    {' on '}port {result.dst.port}/{result.dst.protocol}
                  </div>
                </div>
              </div>
            )
          })()}

          {/* Ingress / Egress detail */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {[
              { label: 'Ingress (destination side)', data: result.ingress },
              { label: 'Egress (source side)', data: result.egress },
            ].map(({ label, data }) => (
              <div key={label} className={`rounded-xl border p-4 space-y-2 ${data.allowed ? 'border-emerald-700/50 bg-emerald-900/10' : 'border-red-700/50 bg-red-900/10'}`}>
                <div className="flex items-center gap-2">
                  {data.allowed ? <CheckCircle size={16} className="text-emerald-400" /> : <XCircle size={16} className="text-red-400" />}
                  <span className="text-sm font-semibold text-slate-200">{label}</span>
                  <span className="text-xs text-slate-500 ml-auto">{data.policy_count} polic{data.policy_count !== 1 ? 'ies' : 'y'}</span>
                </div>
                {(data.allowed_by?.length ?? 0) > 0 && (
                  <div>
                    <div className="text-xs text-slate-500 mb-1">Allowed by:</div>
                    {(data.allowed_by ?? []).map((p: any) => <div key={p} className="text-xs text-emerald-400 font-mono pl-2">• {p}</div>)}
                  </div>
                )}
                {(data.blocked_by?.length ?? 0) > 0 && (
                  <div>
                    <div className="text-xs text-slate-500 mb-1">Blocked by:</div>
                    {(data.blocked_by ?? []).map((p: any) => <div key={p} className="text-xs text-red-400 font-mono pl-2">• {p}</div>)}
                  </div>
                )}
                {(data.allowed_by?.length ?? 0) === 0 && (data.blocked_by?.length ?? 0) === 0 && (
                  <div className="text-xs text-slate-500 italic">No matching policies — implicit allow</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Cost Estimator (Loop 44) ─────────────────────────────────────────────────

interface CostNamespace {
  namespace: string; cpu_cores: number; mem_gib: number
  hourly: number; monthly: number; pod_count: number
}

interface CostPod {
  name: string; namespace: string; cpu_cores: number
  mem_gib: number; hourly: number; monthly: number
}

interface CostData {
  total: { hourly: number; monthly: number; cpu_cores: number; mem_gib: number }
  namespaces: CostNamespace[]
  top_pods: CostPod[]
  pricing: { cpu_hour: number; mem_hour: number }
}

function CostBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  const color = pct > 70 ? 'bg-red-500' : pct > 40 ? 'bg-yellow-500' : 'bg-emerald-500'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-slate-800 rounded-full h-1.5 min-w-[60px]">
        <div className={`h-1.5 rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-400 w-16 text-right">${value.toFixed(2)}/mo</span>
    </div>
  )
}

function CostEstimatorSection({ clusterId }: { clusterId: string }) {
  const [data, setData] = useState<CostData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [cpuHour, setCpuHour] = useState('0.048')
  const [memHour, setMemHour] = useState('0.006')

  const load = useCallback(() => {
    setLoading(true)
    const cpu = parseFloat(cpuHour) || 0.048
    const mem = parseFloat(memHour) || 0.006
    vksGet<any>(`${clusterId}/cost-estimate?cpu_hour=${cpu}&mem_hour=${mem}`)
      .then(d => { setData(d); setError('') })
      .catch(() => setError('Failed to load cost data'))
      .finally(() => setLoading(false))
  }, [clusterId, cpuHour, memHour])

  useEffect(() => { load() }, [load])

  const maxNsCost = data ? Math.max(...(data.namespaces ?? []).map((n: any) => n.monthly), 1) : 1

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2"><DollarSign size={20} className="text-emerald-400" />Cost Estimator</h2>
          <p className="text-slate-400 text-sm mt-1">Monthly estimate based on pod CPU/memory requests (Running pods only)</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-slate-400">
            CPU $/vCPU-hr
            <input value={cpuHour} onChange={e => setCpuHour(e.target.value)}
              className="w-20 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-200 text-xs font-mono focus:outline-none focus:border-emerald-500" />
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-400">
            Mem $/GiB-hr
            <input value={memHour} onChange={e => setMemHour(e.target.value)}
              className="w-20 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-200 text-xs font-mono focus:outline-none focus:border-emerald-500" />
          </label>
          <button onClick={load} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 rounded-lg text-white">
            <RefreshCw size={14} />Recalculate
          </button>
        </div>
      </div>

      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}
      {loading && <SkeletonRows rows={6} />}

      {data && !loading && (
        <>
          {/* Total summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: 'Monthly Estimate', value: `$${data.total.monthly.toFixed(2)}`, sub: `$${data.total.hourly.toFixed(4)}/hr`, color: 'text-emerald-400' },
              { label: 'Total CPU', value: `${data.total.cpu_cores.toFixed(2)} cores`, sub: 'requested', color: 'text-blue-400' },
              { label: 'Total Memory', value: `${data.total.mem_gib.toFixed(1)} GiB`, sub: 'requested', color: 'text-violet-400' },
              { label: 'Namespaces', value: (data.namespaces?.length ?? 0), sub: `${data.top_pods?.length ?? 0} pods costed`, color: 'text-cyan-400' },
            ].map(c => (
              <div key={c.label} className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4">
                <div className={`text-2xl font-bold ${c.color}`}>{c.value}</div>
                <div className="text-slate-400 text-xs mt-1">{c.label}</div>
                <div className="text-slate-600 text-xs">{c.sub}</div>
              </div>
            ))}
          </div>

          {/* Namespace breakdown */}
          {(data.namespaces?.length ?? 0) > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-slate-300 mb-2">Cost by Namespace</h3>
              <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-slate-800/80">
                    <tr className="text-slate-400 text-xs">
                      <th className="text-left px-4 py-3">Namespace</th>
                      <th className="text-left px-4 py-3">Pods</th>
                      <th className="text-left px-4 py-3">CPU (cores)</th>
                      <th className="text-left px-4 py-3">Mem (GiB)</th>
                      <th className="text-left px-4 py-3 w-48">Monthly Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.namespaces ?? []).map((ns: any) => (
                      <tr key={ns.namespace} className="border-t border-slate-800 hover:bg-slate-800/30">
                        <td className="px-4 py-3 font-mono text-sm text-white">{ns.namespace}</td>
                        <td className="px-4 py-3 text-slate-400">{ns.pod_count}</td>
                        <td className="px-4 py-3 text-slate-300 font-mono text-xs">{ns.cpu_cores.toFixed(3)}</td>
                        <td className="px-4 py-3 text-slate-300 font-mono text-xs">{ns.mem_gib.toFixed(2)}</td>
                        <td className="px-4 py-3"><CostBar value={ns.monthly} max={maxNsCost} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Top pods */}
          {(data.top_pods?.length ?? 0) > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-slate-300 mb-2">Most Expensive Pods (top 20)</h3>
              <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-slate-800/80">
                    <tr className="text-slate-400 text-xs">
                      <th className="text-left px-4 py-3">Pod</th>
                      <th className="text-left px-4 py-3">Namespace</th>
                      <th className="text-left px-4 py-3">CPU (cores)</th>
                      <th className="text-left px-4 py-3">Mem (GiB)</th>
                      <th className="text-left px-4 py-3">$/month</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.top_pods ?? []).map((p: any, i: number) => (
                      <tr key={i} className="border-t border-slate-800 hover:bg-slate-800/30">
                        <td className="px-4 py-2.5 font-mono text-xs text-white">{p.name}</td>
                        <td className="px-4 py-2.5 text-slate-400 font-mono text-xs">{p.namespace}</td>
                        <td className="px-4 py-2.5 text-slate-300 text-xs">{p.cpu_cores.toFixed(3)}</td>
                        <td className="px-4 py-2.5 text-slate-300 text-xs">{p.mem_gib.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-emerald-400 font-mono text-xs font-semibold">${p.monthly.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(data.namespaces?.length ?? 0) === 0 && (
            <EmptyState icon={DollarSign} title="No running pods found" hint="Cost estimation requires Running pods with resource requests." />
          )}
        </>
      )}
    </div>
  )
}

// ── Global Cross-Namespace Search (Loop 43) ───────────────────────────────────

interface SearchResult {
  kind: string; name: string; namespace: string
  labels: Record<string, string>; created_at: string; status: string
}

const ALL_SEARCH_KINDS = ['pods', 'deployments', 'statefulsets', 'daemonsets', 'services',
  'configmaps', 'secrets', 'ingresses', 'jobs', 'cronjobs', 'pvcs', 'serviceaccounts']

const KIND_COLOR: Record<string, string> = {
  Pod: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/40',
  Deployment: 'bg-blue-900/40 text-blue-300 border-blue-700/40',
  StatefulSet: 'bg-violet-900/40 text-violet-300 border-violet-700/40',
  DaemonSet: 'bg-indigo-900/40 text-indigo-300 border-indigo-700/40',
  Service: 'bg-cyan-900/40 text-cyan-300 border-cyan-700/40',
  ConfigMap: 'bg-yellow-900/40 text-yellow-300 border-yellow-700/40',
  Secret: 'bg-red-900/40 text-red-300 border-red-700/40',
  Ingress: 'bg-orange-900/40 text-orange-300 border-orange-700/40',
  Job: 'bg-slate-700/40 text-slate-300 border-slate-600/40',
  CronJob: 'bg-slate-700/40 text-slate-300 border-slate-600/40',
  PVC: 'bg-pink-900/40 text-pink-300 border-pink-700/40',
  ServiceAccount: 'bg-teal-900/40 text-teal-300 border-teal-700/40',
}

function KindBadge({ kind }: { kind: string }) {
  const cls = KIND_COLOR[kind] || 'bg-slate-700/40 text-slate-300 border-slate-600/40'
  return <span className={`px-1.5 py-0.5 rounded text-xs border font-mono ${cls}`}>{kind}</span>
}

function GlobalSearchSection({ clusterId }: { clusterId: string }) {
  const [query, setQuery] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedKinds, setSelectedKinds] = useState<Set<string>>(new Set(
    ['pods', 'deployments', 'statefulsets', 'services', 'configmaps', 'ingresses']
  ))
  const [searched, setSearched] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  // Debounce query
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(query), 400)
    return () => clearTimeout(t)
  }, [query])

  useEffect(() => {
    if (debouncedQ.length < 2) { setResults([]); setSearched(false); return }
    setLoading(true)
    const kinds = [...selectedKinds].join(',')
    vksGet<any>(`${clusterId}/search?q=${encodeURIComponent(debouncedQ)}&kinds=${kinds}&limit=200`)
      .then(d => { setResults(d.results || []); setSearched(true); setError('') })
      .catch(() => setError('Search failed'))
      .finally(() => setLoading(false))
  }, [debouncedQ, selectedKinds, clusterId])

  const toggleKind = (k: string) => setSelectedKinds(prev => {
    const next = new Set(prev)
    if (next.has(k)) next.delete(k); else next.add(k)
    return next
  })

  const kindCounts = results.reduce((acc, r) => { acc[r.kind] = (acc[r.kind] || 0) + 1; return acc }, {} as Record<string, number>)

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-bold text-white flex items-center gap-2"><Search size={20} className="text-emerald-400" />Cross-Namespace Search</h2>
        <p className="text-slate-400 text-sm mt-1">Search resources by name, namespace, or label across the cluster</p>
      </div>

      {/* Search input */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
        <input
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search by name, namespace, or label (min 2 chars)…"
          className="w-full bg-slate-800 border border-slate-600 rounded-xl pl-9 pr-4 py-3 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500 text-sm"
        />
        {loading && <Loader2 size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 animate-spin" />}
      </div>

      {/* Kind toggles */}
      <div className="flex flex-wrap gap-1.5">
        {ALL_SEARCH_KINDS.map(k => {
          const display = k.charAt(0).toUpperCase() + k.slice(1).replace(/s$/, '')
          const active = selectedKinds.has(k)
          return (
            <button key={k} onClick={() => toggleKind(k)}
              className={`text-xs px-2.5 py-1 rounded-lg border transition-colors ${active ? 'bg-emerald-900/40 border-emerald-700/60 text-emerald-300' : 'bg-slate-800 border-slate-700 text-slate-500 hover:text-slate-300'}`}>
              {display}{kindCounts[display] ? ` (${kindCounts[display]})` : ''}
            </button>
          )
        })}
      </div>

      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}

      {searched && !loading && results.length === 0 && (
        <EmptyState icon={Search} title="No results found" hint={`No resources match "${debouncedQ}" in the selected kinds`} />
      )}

      {results.length > 0 && (
        <div>
          <div className="text-sm text-slate-400 mb-2">{results.length} result{results.length !== 1 ? 's' : ''} for <span className="text-emerald-400 font-mono">"{debouncedQ}"</span></div>
          <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-800/80">
                <tr className="text-slate-400 text-xs">
                  <th className="text-left px-4 py-3">Kind</th>
                  <th className="text-left px-4 py-3">Name</th>
                  <th className="text-left px-4 py-3">Namespace</th>
                  <th className="text-left px-4 py-3">Status</th>
                  <th className="text-left px-4 py-3">Labels</th>
                  <th className="text-left px-4 py-3">Created</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i} className="border-t border-slate-800 hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-2.5"><KindBadge kind={r.kind} /></td>
                    <td className="px-4 py-2.5 font-medium text-white font-mono text-xs">{r.name}</td>
                    <td className="px-4 py-2.5 text-slate-400 font-mono text-xs">{r.namespace}</td>
                    <td className="px-4 py-2.5 text-slate-400 text-xs">{r.status || '—'}</td>
                    <td className="px-4 py-2.5 max-w-xs">
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(r.labels).slice(0, 3).map(([k, v]) => (
                          <span key={k} className="text-xs bg-slate-800 border border-slate-700 rounded px-1.5 py-0.5 text-slate-400 font-mono truncate max-w-[160px]">{k}={v}</span>
                        ))}
                        {Object.keys(r.labels).length > 3 && <span className="text-xs text-slate-600">+{Object.keys(r.labels).length - 3}</span>}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-slate-500 text-xs whitespace-nowrap">{r.created_at ? new Date(r.created_at).toLocaleDateString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Helm Release Browser (Loop 42) ────────────────────────────────────────────

interface HelmRelease {
  name: string; namespace: string; revision: number; status: string
  chart_name: string; chart_version: string; app_version: string
  description: string; first_deployed: string; last_deployed: string
}

interface HelmHistoryEntry {
  revision: number; status: string; chart_version: string
  app_version: string; description: string; deployed_at: string
}

function HelmStatusBadge({ status }: { status: string }) {
  const cfg: Record<string, string> = {
    deployed: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/50',
    failed: 'bg-red-900/40 text-red-300 border-red-700/50',
    superseded: 'bg-slate-700/40 text-slate-400 border-slate-600/50',
    uninstalling: 'bg-orange-900/40 text-orange-300 border-orange-700/50',
    pending_install: 'bg-blue-900/40 text-blue-300 border-blue-700/50',
    pending_upgrade: 'bg-blue-900/40 text-blue-300 border-blue-700/50',
    pending_rollback: 'bg-yellow-900/40 text-yellow-300 border-yellow-700/50',
  }
  const cls = cfg[status] || 'bg-slate-700/40 text-slate-400 border-slate-600/50'
  return <span className={`px-2 py-0.5 rounded text-xs border font-medium ${cls}`}>{status.replace(/_/g, ' ')}</span>
}

function HelmValuesModal({ clusterId, namespace, name, onClose }: { clusterId: string; namespace: string; name: string; onClose: () => void }) {
  const [data, setData] = useState<{ values_yaml: string; revision: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    vksGet<any>(`${clusterId}/helm/releases/${namespace}/${name}/values`)
      .then(setData).finally(() => setLoading(false))
  }, [clusterId, namespace, name])
  const copy = () => { navigator.clipboard.writeText(data?.values_yaml || ''); setCopied(true); setTimeout(() => setCopied(false), 2000) }
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-slate-700 shrink-0">
          <div>
            <div className="font-semibold text-white">{name} — User Values</div>
            {data && <div className="text-xs text-slate-400 mt-0.5">Revision {data.revision}</div>}
          </div>
          <div className="flex items-center gap-2">
            <button onClick={copy} className="flex items-center gap-1 text-xs px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-300">
              <Copy size={12} />{copied ? 'Copied!' : 'Copy'}
            </button>
            <button onClick={onClose} className="text-slate-400 hover:text-white p-1"><X size={16} /></button>
          </div>
        </div>
        <div className="flex-1 overflow-auto p-4">
          {loading ? <div className="text-slate-400 text-sm">Loading…</div> : (
            <pre className="text-xs font-mono text-emerald-300 whitespace-pre-wrap">{data?.values_yaml}</pre>
          )}
        </div>
      </div>
    </div>
  )
}

function HelmHistoryModal({ clusterId, namespace, name, onClose }: { clusterId: string; namespace: string; name: string; onClose: () => void }) {
  const [history, setHistory] = useState<HelmHistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    vksGet<any>(`${clusterId}/helm/releases/${namespace}/${name}/history`)
      .then(d => setHistory((d as any).history || [])).finally(() => setLoading(false))
  }, [clusterId, namespace, name])
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl max-h-[70vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-slate-700 shrink-0">
          <div className="font-semibold text-white">{name} — Revision History</div>
          <button onClick={onClose} className="text-slate-400 hover:text-white p-1"><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-auto">
          {loading ? <div className="p-4 text-slate-400 text-sm">Loading…</div> : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-800/90">
                <tr className="text-slate-400 text-xs">
                  <th className="text-left px-4 py-2">Rev</th>
                  <th className="text-left px-4 py-2">Status</th>
                  <th className="text-left px-4 py-2">Chart</th>
                  <th className="text-left px-4 py-2">App</th>
                  <th className="text-left px-4 py-2">Deployed</th>
                  <th className="text-left px-4 py-2 max-w-xs">Note</th>
                </tr>
              </thead>
              <tbody>
                {history.map(h => (
                  <tr key={h.revision} className="border-t border-slate-800 hover:bg-slate-800/40">
                    <td className="px-4 py-2 text-white font-mono">#{h.revision}</td>
                    <td className="px-4 py-2"><HelmStatusBadge status={h.status} /></td>
                    <td className="px-4 py-2 text-slate-300 font-mono text-xs">{h.chart_version}</td>
                    <td className="px-4 py-2 text-slate-300 text-xs">{h.app_version}</td>
                    <td className="px-4 py-2 text-slate-400 text-xs whitespace-nowrap">{h.deployed_at ? new Date(h.deployed_at).toLocaleString() : '—'}</td>
                    <td className="px-4 py-2 text-slate-400 text-xs max-w-xs truncate">{h.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

function HelmManifestModal({ clusterId, namespace, name, onClose }: { clusterId: string; namespace: string; name: string; onClose: () => void }) {
  const [data, setData] = useState<{ manifest: string; resource_count: number; resource_kinds: string[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    vksGet<any>(`${clusterId}/helm/releases/${namespace}/${name}/manifest`)
      .then(setData).finally(() => setLoading(false))
  }, [clusterId, namespace, name])
  const copy = () => { navigator.clipboard.writeText(data?.manifest || ''); setCopied(true); setTimeout(() => setCopied(false), 2000) }
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-slate-700 shrink-0">
          <div>
            <div className="font-semibold text-white">{name} — Rendered Manifest</div>
            {data && <div className="text-xs text-slate-400 mt-0.5">{data.resource_count} resources: {(data.resource_kinds ?? []).join(', ')}</div>}
          </div>
          <div className="flex items-center gap-2">
            <button onClick={copy} className="flex items-center gap-1 text-xs px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-300">
              <Copy size={12} />{copied ? 'Copied!' : 'Copy'}
            </button>
            <button onClick={onClose} className="text-slate-400 hover:text-white p-1"><X size={16} /></button>
          </div>
        </div>
        <div className="flex-1 overflow-auto p-4">
          {loading ? <div className="text-slate-400 text-sm">Loading…</div> : (
            <pre className="text-xs font-mono text-emerald-300 whitespace-pre-wrap">{data?.manifest}</pre>
          )}
        </div>
      </div>
    </div>
  )
}

function HelmSection({ clusterId }: { clusterId: string }) {
  const [releases, setReleases] = useState<HelmRelease[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [nsFilter, setNsFilter] = useState('')
  const [valuesModal, setValuesModal] = useState<{ namespace: string; name: string } | null>(null)
  const [historyModal, setHistoryModal] = useState<{ namespace: string; name: string } | null>(null)
  const [manifestModal, setManifestModal] = useState<{ namespace: string; name: string } | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    vksGet<any>(`${clusterId}/helm/releases`)
      .then(d => { setReleases((d as any).releases || []); setError('') })
      .catch(() => setError('Failed to load Helm releases'))
      .finally(() => setLoading(false))
  }, [clusterId])

  useEffect(() => { load() }, [load])

  const namespaces = [...new Set(releases.map(r => r.namespace))].sort()
  const filtered = releases.filter(r => {
    const q = search.toLowerCase()
    const matchQ = !q || r.name.includes(q) || r.chart_name.includes(q) || r.namespace.includes(q)
    const matchNs = !nsFilter || r.namespace === nsFilter
    return matchQ && matchNs
  })

  const statusCounts = releases.reduce((acc, r) => { acc[r.status] = (acc[r.status] || 0) + 1; return acc }, {} as Record<string, number>)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2"><Package size={20} className="text-emerald-400" />Helm Releases</h2>
          <p className="text-slate-400 text-sm mt-1">{releases.length} release{releases.length !== 1 ? 's' : ''} across {namespaces.length} namespace{namespaces.length !== 1 ? 's' : ''}</p>
        </div>
        <button onClick={load} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300">
          <RefreshCw size={14} />Refresh
        </button>
      </div>

      {/* Status summary chips */}
      {Object.keys(statusCounts).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(statusCounts).map(([s, n]) => (
            <span key={s} className="flex items-center gap-1.5 text-xs px-2 py-1 bg-slate-800 rounded-lg border border-slate-700">
              <HelmStatusBadge status={s} /><span className="text-slate-400">×{n}</span>
            </span>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search releases…"
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500 w-56"
        />
        {namespaces.length > 1 && (
          <select value={nsFilter} onChange={e => setNsFilter(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-emerald-500">
            <option value="">All namespaces</option>
            {namespaces.map(ns => <option key={ns} value={ns}>{ns}</option>)}
          </select>
        )}
      </div>

      {loading && <SkeletonRows rows={5} />}
      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}

      {!loading && !error && filtered.length === 0 && (
        <EmptyState icon={Package} title={releases.length === 0 ? 'No Helm v3 releases found' : 'No releases match the filter'} hint={releases.length === 0 ? 'Helm v3 stores release state as labeled Secrets in each namespace.' : undefined} />
      )}

      {!loading && filtered.length > 0 && (
        <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-800/80">
              <tr className="text-slate-400 text-xs">
                <th className="text-left px-4 py-3">Release</th>
                <th className="text-left px-4 py-3">Namespace</th>
                <th className="text-left px-4 py-3">Chart</th>
                <th className="text-left px-4 py-3">Version</th>
                <th className="text-left px-4 py-3">App Ver</th>
                <th className="text-left px-4 py-3">Rev</th>
                <th className="text-left px-4 py-3">Status</th>
                <th className="text-left px-4 py-3">Last Deployed</th>
                <th className="text-left px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(r => (
                <tr key={`${r.namespace}/${r.name}`} className="border-t border-slate-800 hover:bg-slate-800/30 transition-colors">
                  <td className="px-4 py-3 font-medium text-white">{r.name}</td>
                  <td className="px-4 py-3 text-slate-400 font-mono text-xs">{r.namespace}</td>
                  <td className="px-4 py-3 text-slate-300">{r.chart_name}</td>
                  <td className="px-4 py-3 text-slate-400 font-mono text-xs">{r.chart_version}</td>
                  <td className="px-4 py-3 text-slate-400 text-xs">{r.app_version || '—'}</td>
                  <td className="px-4 py-3 text-slate-400 font-mono text-xs">#{r.revision}</td>
                  <td className="px-4 py-3"><HelmStatusBadge status={r.status} /></td>
                  <td className="px-4 py-3 text-slate-400 text-xs whitespace-nowrap">{r.last_deployed ? new Date(r.last_deployed).toLocaleString() : '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      <button onClick={() => setValuesModal({ namespace: r.namespace, name: r.name })}
                        title="View values" className="text-xs px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-300">Values</button>
                      <button onClick={() => setHistoryModal({ namespace: r.namespace, name: r.name })}
                        title="View history" className="text-xs px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-300">History</button>
                      <button onClick={() => setManifestModal({ namespace: r.namespace, name: r.name })}
                        title="View manifest" className="text-xs px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-300">Manifest</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {valuesModal && <HelmValuesModal clusterId={clusterId} namespace={valuesModal.namespace} name={valuesModal.name} onClose={() => setValuesModal(null)} />}
      {historyModal && <HelmHistoryModal clusterId={clusterId} namespace={historyModal.namespace} name={historyModal.name} onClose={() => setHistoryModal(null)} />}
      {manifestModal && <HelmManifestModal clusterId={clusterId} namespace={manifestModal.namespace} name={manifestModal.name} onClose={() => setManifestModal(null)} />}
    </div>
  )
}

// ── Event Stream Section ──────────────────────────────────────────────────────

interface LiveEvent {
  id: number; event_type: string; reason: string; message: string
  type: string; involved_kind: string; involved_name: string
  namespace: string; count: number; last_time: string
}

function EventStreamSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [events, setEvents] = useState<LiveEvent[]>([])
  const [paused, setPaused] = useState(false)
  const [kindFilter, setKindFilter] = useState('')
  const [reasonFilter, setReasonFilter] = useState('')
  const [connected, setConnected] = useState(false)
  const counterRef = useRef(0)
  const bottomRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    function connect() {
      if (esRef.current) esRef.current.close()
      const params = new URLSearchParams()
      if (namespace) params.set('namespace', namespace)
      if (reasonFilter) params.set('reason', reasonFilter)
      if (kindFilter) params.set('kind', kindFilter)
      const url = `${API}/api/v1/vks/${clusterId}/events/stream?${params}`
      const es = new EventSource(url)
      esRef.current = es
      es.onopen = () => setConnected(true)
      es.onmessage = ev => {
        try {
          const d = JSON.parse(ev.data)
          if (d.error || d.done) { setConnected(false); return }
          if (paused) return
          const id = ++counterRef.current
          setEvents(prev => [...prev.slice(-499), { ...d, id }])
        } catch { /* ignore */ }
      }
      es.onerror = () => { setConnected(false); setTimeout(connect, 3000) }
    }
    connect()
    return () => { esRef.current?.close(); setConnected(false) }
  }, [clusterId, namespace, kindFilter, reasonFilter])

  useEffect(() => {
    if (!paused) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events, paused])

  const visibleEvents = events.filter(ev => {
    if (kindFilter && ev.involved_kind.toLowerCase() !== kindFilter.toLowerCase()) return false
    if (reasonFilter && !ev.reason.toLowerCase().includes(reasonFilter.toLowerCase())) return false
    return true
  })

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-6 py-3 border-b border-slate-800 flex-wrap">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${connected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
          <span className="text-white font-medium text-sm">Live Event Stream</span>
          <span className="text-xs text-slate-500">{events.length} events</span>
        </div>
        <input type="text" placeholder="Kind filter…" value={kindFilter} onChange={e => setKindFilter(e.target.value)}
          className="px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-white placeholder-slate-500 focus:outline-none w-28" />
        <input type="text" placeholder="Reason filter…" value={reasonFilter} onChange={e => setReasonFilter(e.target.value)}
          className="px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-white placeholder-slate-500 focus:outline-none w-28" />
        <div className="ml-auto flex gap-2">
          <button onClick={() => setEvents([])} className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 rounded">
            Clear
          </button>
          <button onClick={() => setPaused(p => !p)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${paused ? 'bg-yellow-600 text-white' : 'bg-emerald-700 text-white'}`}>
            {paused ? <Play size={11} /> : <Square size={11} />}
            {paused ? 'Resume' : 'Pause'}
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto font-mono text-xs">
        {visibleEvents.length === 0 && (
          <div className="flex items-center justify-center h-32 text-slate-500 gap-2">
            <Loader2 size={14} className={connected ? 'animate-spin' : ''} />
            {connected ? 'Waiting for events…' : 'Connecting…'}
          </div>
        )}
        <table className="w-full">
          <tbody>
            {visibleEvents.map(ev => (
              <tr key={ev.id} className={`border-b border-slate-800/30 hover:bg-slate-800/20 ${ev.type === 'Warning' ? 'bg-red-950/20' : ''}`}>
                <td className="px-3 py-1.5 w-16 text-slate-600">{ev.last_time ? new Date(ev.last_time).toLocaleTimeString() : ''}</td>
                <td className="px-2 py-1.5 w-20">
                  <span className={`px-1.5 py-0.5 rounded text-[9px] ${ev.type === 'Warning' ? 'bg-red-900/50 text-red-300' : 'bg-slate-700 text-slate-400'}`}>
                    {ev.type || 'Normal'}
                  </span>
                </td>
                <td className="px-2 py-1.5 w-28 text-blue-300">{ev.involved_kind}</td>
                <td className="px-2 py-1.5 w-40 text-cyan-300 truncate max-w-[140px]">{ev.involved_name}</td>
                <td className="px-2 py-1.5 w-28 text-yellow-300">{ev.reason}</td>
                <td className="px-2 py-1.5 text-slate-300 truncate max-w-[400px]">{ev.message}</td>
                {ev.count > 1 && <td className="px-2 py-1.5 w-10 text-slate-500">×{ev.count}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        <div ref={bottomRef} />
      </div>
    </div>
  )
}


// ── Annotations Editor Modal ──────────────────────────────────────────────────

function AnnotationsEditorModal({ clusterId, workload, onClose, onSaved }: {
  clusterId: string; workload: WorkloadItem; onClose: () => void; onSaved: () => void
}) {
  const [annotations, setAnnotations] = useState<Record<string, string>>(workload.annotations ?? {})
  const [newKey, setNewKey] = useState('')
  const [newVal, setNewVal] = useState('')
  const [saving, setSaving] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const toast = useToast()

  const kindPath = workload.kind?.toLowerCase().replace('deployment', 'deployment')
    .replace('statefulset', 'statefulset').replace('daemonset', 'daemonset') ?? 'deployment'

  async function handleAddAnnotation() {
    if (!newKey.trim()) return
    setAnnotations(prev => ({ ...prev, [newKey.trim()]: newVal }))
    setNewKey(''); setNewVal('')
  }

  function handleRemoveAnnotation(key: string) {
    setAnnotations(prev => {
      const n = { ...prev }
      delete n[key]
      return n
    })
  }

  async function handleSave() {
    const original = workload.annotations ?? {}
    const add: Record<string, string> = {}
    const remove: string[] = []

    for (const [k, v] of Object.entries(annotations)) {
      if (original[k] !== v) add[k] = v
    }
    for (const k of Object.keys(original)) {
      if (!(k in annotations)) remove.push(k)
    }

    if (Object.keys(add).length === 0 && remove.length === 0) {
      toast.error('No changes to save'); return
    }

    setSaving(true)
    try {
      const resp = await vksPost<ConfirmState & { requires_confirm?: boolean }>(
        `${clusterId}/workloads/${kindPath}/${workload.name}/annotations`,
        { namespace: workload.namespace || '', add, remove }
      )
      if (resp.requires_confirm) {
        setConfirm({ ...resp, onConfirm: async (token) => {
          await vksPost(`${clusterId}/workloads/${kindPath}/${workload.name}/annotations?token=${token}`,
            { namespace: workload.namespace || '', add, remove })
          toast.success('Annotations saved')
          onSaved()
        }})
      }
    } catch (e) { toast.error(`Save failed: ${e}`) } finally { setSaving(false) }
  }

  const SYSTEM_PREFIXES = ['kubectl.kubernetes.io', 'deployment.kubernetes.io', 'meta.helm.sh']

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-lg mx-4 flex flex-col max-h-[80vh]">
        {confirm && <ConfirmDialog state={confirm} onDone={() => setConfirm(null)} />}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700">
          <Info size={14} className="text-blue-400" />
          <div className="flex-1 min-w-0">
            <span className="text-white text-sm font-medium">{workload.name}</span>
            <span className="text-slate-500 text-xs ml-2">annotations</span>
          </div>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-slate-700 text-slate-400"><X size={14} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {Object.entries(annotations).length === 0 && (
            <p className="text-slate-500 text-xs text-center py-4">No annotations.</p>
          )}
          {Object.entries(annotations).map(([k, v]) => {
            const isSystem = SYSTEM_PREFIXES.some(p => k.startsWith(p))
            return (
              <div key={k} className={`flex items-start gap-2 p-2 rounded-lg ${isSystem ? 'bg-slate-800/30 opacity-60' : 'bg-slate-800/50'}`}>
                <div className="flex-1 min-w-0 font-mono text-xs">
                  <div className="text-cyan-300 truncate">{k}</div>
                  <div className="text-slate-300 truncate mt-0.5">{v}</div>
                </div>
                {!isSystem && (
                  <button onClick={() => handleRemoveAnnotation(k)} className="flex-shrink-0 p-1 rounded hover:bg-slate-600 text-slate-500 hover:text-red-400">
                    <X size={11} />
                  </button>
                )}
              </div>
            )
          })}
        </div>
        <div className="border-t border-slate-700 p-4 space-y-3">
          <div className="flex gap-2">
            <input value={newKey} onChange={e => setNewKey(e.target.value)} placeholder="annotation.key"
              className="flex-1 px-2 py-1.5 bg-slate-800 border border-slate-700 rounded text-xs text-white placeholder-slate-500 font-mono focus:outline-none focus:border-blue-500" />
            <input value={newVal} onChange={e => setNewVal(e.target.value)} placeholder="value"
              onKeyDown={e => e.key === 'Enter' && handleAddAnnotation()}
              className="flex-1 px-2 py-1.5 bg-slate-800 border border-slate-700 rounded text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500" />
            <button onClick={handleAddAnnotation} className="px-2 py-1.5 bg-slate-700 hover:bg-slate-600 text-white rounded text-xs">
              <Plus size={12} />
            </button>
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="px-4 py-2 text-xs text-slate-400 hover:text-white rounded-lg">Cancel</button>
            <button onClick={handleSave} disabled={saving}
              className="px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg flex items-center gap-2">
              {saving && <Loader2 size={11} className="animate-spin" />}
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


// ── Loop 50: Scheduling Issues / Pending Pod Analyzer ────────────────────────

const SCHED_CATEGORY_META: Record<string, { label: string; color: string; bg: string }> = {
  insufficient_cpu:    { label: 'Insufficient CPU',   color: 'text-orange-400', bg: 'bg-orange-500/10 border-orange-500/30' },
  insufficient_memory: { label: 'Insufficient Memory',color: 'text-orange-300', bg: 'bg-orange-500/10 border-orange-500/30' },
  no_matching_node:    { label: 'No Matching Node',   color: 'text-yellow-400', bg: 'bg-yellow-500/10 border-yellow-500/30' },
  taint_toleration:    { label: 'Taint Mismatch',     color: 'text-purple-400', bg: 'bg-purple-500/10 border-purple-500/30' },
  affinity_mismatch:   { label: 'Affinity Mismatch',  color: 'text-blue-400',   bg: 'bg-blue-500/10 border-blue-500/30' },
  pvc_pending:         { label: 'PVC Pending',         color: 'text-cyan-400',   bg: 'bg-cyan-500/10 border-cyan-500/30' },
  no_nodes_available:  { label: 'No Nodes Available', color: 'text-red-400',    bg: 'bg-red-500/10 border-red-500/30' },
  image_pull:          { label: 'Image Pull Error',   color: 'text-red-400',    bg: 'bg-red-500/10 border-red-500/30' },
  other:               { label: 'Other',              color: 'text-slate-400',  bg: 'bg-slate-500/10 border-slate-500/30' },
  unknown:             { label: 'Unknown',            color: 'text-slate-500',  bg: 'bg-slate-500/10 border-slate-500/30' },
}

function SchedulingIssuesSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [filterCat, setFilterCat] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (namespace) params.set('namespace', namespace)
      setData(await vksGet<any>(`${clusterId}/scheduling-issues?${params}`))
    } catch { setData(null) } finally { setLoading(false) }
  }

  useEffect(() => { if (clusterId) load() }, [clusterId, namespace])

  const pods: any[] = (data?.pending_pods ?? []).filter((p: any) => !filterCat || p.category === filterCat)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Clock size={20} className="text-yellow-400" />Scheduling Issues
          </h2>
          <p className="text-slate-400 text-sm mt-1">
            Pending pods with categorised scheduling failure reasons
            {data && <span className="ml-2 text-slate-500">· {data.total} pending</span>}
          </p>
        </div>
        <button onClick={load} disabled={loading} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded-lg text-slate-300">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />Scan
        </button>
      </div>

      {data && Object.keys(data.categories).length > 0 && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setFilterCat('')}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              !filterCat
                ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-300'
                : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
            }`}
          >
            All ({data.total})
          </button>
          {Object.entries(data.categories as Record<string, number>).map(([cat, count]) => {
            const meta = SCHED_CATEGORY_META[cat] ?? SCHED_CATEGORY_META.other
            return (
              <button
                key={cat}
                onClick={() => setFilterCat(filterCat === cat ? '' : cat)}
                className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                  filterCat === cat
                    ? `${meta.bg} ${meta.color}`
                    : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                }`}
              >
                {meta.label} ({count})
              </button>
            )
          })}
        </div>
      )}

      {loading && <SkeletonRows rows={5} />}

      {!loading && data?.total === 0 && (
        <EmptyState icon={CheckCircle} title="No pending pods" hint="All pods scheduled and running." />
      )}

      {!loading && pods.length > 0 && (
        <div className="rounded-lg border border-slate-700/60 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-800/60">
              <tr>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400 w-6" />
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Pod</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Namespace</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Category</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Since</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Images</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/40">
              {pods.map((pod: any) => {
                const key = `${pod.namespace}/${pod.name}`
                const isExp = expanded.has(key)
                const meta = SCHED_CATEGORY_META[pod.category] ?? SCHED_CATEGORY_META.other
                return (
                  <>
                    <tr
                      key={key}
                      className="hover:bg-slate-800/40 cursor-pointer"
                      onClick={() =>
                        setExpanded(prev => {
                          const n = new Set(prev)
                          isExp ? n.delete(key) : n.add(key)
                          return n
                        })
                      }
                    >
                      <td className="px-4 py-2 text-slate-500">
                        {isExp
                          ? <ChevronDown className="w-3.5 h-3.5" />
                          : <ChevronRight className="w-3.5 h-3.5" />}
                      </td>
                      <td className="px-4 py-2 font-mono text-slate-200">{pod.name}</td>
                      <td className="px-4 py-2 text-slate-400">{pod.namespace}</td>
                      <td className="px-4 py-2">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border ${meta.bg} ${meta.color}`}>
                          {meta.label}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-slate-400 text-xs">
                        {pod.created_at ? new Date(pod.created_at).toLocaleString() : '—'}
                      </td>
                      <td className="px-4 py-2 text-slate-400 text-xs font-mono truncate max-w-[200px]">
                        {(pod.images as string[]).join(', ')}
                      </td>
                    </tr>
                    {isExp && (
                      <tr key={`${key}-exp`} className="bg-slate-900/50">
                        <td colSpan={6} className="px-6 py-4">
                          <div className="space-y-3">
                            {pod.message && (
                              <div>
                                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1">Scheduler Message</div>
                                <div className="bg-slate-900 rounded p-3 text-xs font-mono text-slate-300 whitespace-pre-wrap break-all">
                                  {pod.message}
                                </div>
                              </div>
                            )}
                            {(pod.image_issues as any[]).length > 0 && (
                              <div>
                                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1">Image Issues</div>
                                <div className="space-y-1">
                                  {(pod.image_issues as any[]).map((ii: any, i: number) => (
                                    <div key={i} className="bg-red-950/30 border border-red-800/30 rounded p-2 text-xs">
                                      <span className="text-red-300 font-medium">{ii.container}</span>
                                      <span className="text-slate-500 mx-1">—</span>
                                      <span className="text-red-400">{ii.reason}</span>
                                      {ii.message && <span className="text-slate-400 ml-2">{ii.message}</span>}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                            {Object.keys(pod.node_selector as Record<string, string>).length > 0 && (
                              <div>
                                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1">Node Selector</div>
                                <div className="flex flex-wrap gap-1">
                                  {Object.entries(pod.node_selector as Record<string, string>).map(([k, v]) => (
                                    <span key={k} className="px-2 py-0.5 bg-slate-700/50 text-slate-300 rounded text-xs font-mono">
                                      {k}={v}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                            {(pod.tolerations as any[]).length > 0 && (
                              <div>
                                <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1">Tolerations</div>
                                <div className="flex flex-wrap gap-1">
                                  {(pod.tolerations as any[]).map((t: any, i: number) => (
                                    <span key={i} className="px-2 py-0.5 bg-slate-700/50 text-slate-300 rounded text-xs font-mono">
                                      {t.key}{t.effect ? `:${t.effect}` : ''}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}


// ── Loop 51: RBAC Risk Auditor ────────────────────────────────────────────────

const RBAC_SEV_META: Record<string, { color: string; bg: string; dot: string }> = {
  critical: { color: 'text-red-400',    bg: 'bg-red-500/10 border-red-500/30',    dot: 'bg-red-500' },
  high:     { color: 'text-orange-400', bg: 'bg-orange-500/10 border-orange-500/30', dot: 'bg-orange-500' },
  medium:   { color: 'text-yellow-400', bg: 'bg-yellow-500/10 border-yellow-500/30', dot: 'bg-yellow-500' },
  low:      { color: 'text-slate-400',  bg: 'bg-slate-500/10 border-slate-500/30',  dot: 'bg-slate-500' },
}

const RBAC_TYPE_LABEL: Record<string, string> = {
  cluster_admin_grant:   'Cluster-Admin Grant',
  system_masters_grant:  'system:masters Group',
  risky_clusterrole:     'Risky ClusterRole',
  risky_role:            'Risky Role',
}

function RBACRisksSection({ clusterId }: { clusterId: string }) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [filterSev, setFilterSev] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      setData(await vksGet<any>(`${clusterId}/rbac/risks`))
    } catch { setData(null) } finally { setLoading(false) }
  }

  useEffect(() => { if (clusterId) load() }, [clusterId])

  const risks: any[] = (data?.risks ?? []).filter((r: any) => !filterSev || r.severity === filterSev)
  const summary = data?.summary ?? {}

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <ShieldCheck size={20} className="text-green-400" />RBAC Risk Auditor
          </h2>
          <p className="text-slate-400 text-sm mt-1">
            Detect over-privileged bindings: cluster-admin grants, wildcard rules, sensitive resource access
          </p>
        </div>
        <button onClick={load} disabled={loading} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded-lg text-slate-300">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />Audit
        </button>
      </div>

      {/* Summary */}
      {data && (
        <div className="flex flex-wrap gap-2">
          {(['critical', 'high', 'medium'] as const).map(sev => {
            const count = summary[sev] ?? 0
            if (count === 0) return null
            const meta = RBAC_SEV_META[sev]
            return (
              <button
                key={sev}
                onClick={() => setFilterSev(filterSev === sev ? '' : sev)}
                className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                  filterSev === sev ? `${meta.bg} ${meta.color}` : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                }`}
              >
                {sev.charAt(0).toUpperCase() + sev.slice(1)} ({count})
              </button>
            )
          })}
          {filterSev && (
            <button onClick={() => setFilterSev('')} className="px-3 py-1 rounded-full text-xs border border-slate-600 text-slate-400 hover:border-slate-500">
              Clear filter
            </button>
          )}
        </div>
      )}

      {loading && <SkeletonRows rows={5} />}

      {!loading && data?.summary?.total === 0 && (
        <EmptyState icon={CheckCircle} title="No RBAC risks detected" hint="No cluster-admin grants, wildcards, or sensitive resource access found." />
      )}

      {!loading && risks.length > 0 && (
        <div className="rounded-lg border border-slate-700/60 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-800/60">
              <tr>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400 w-6" />
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Severity</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Type</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Binding</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Role</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Subject</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/40">
              {risks.map((risk: any, idx: number) => {
                const key = `${risk.binding}-${risk.subject_name}-${idx}`
                const isExp = expanded.has(key)
                const meta = RBAC_SEV_META[risk.severity] ?? RBAC_SEV_META.low
                return (
                  <>
                    <tr
                      key={key}
                      className="hover:bg-slate-800/40 cursor-pointer"
                      onClick={() => setExpanded(prev => { const n = new Set(prev); isExp ? n.delete(key) : n.add(key); return n })}
                    >
                      <td className="px-4 py-2 text-slate-500">
                        {isExp ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                      </td>
                      <td className="px-4 py-2">
                        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-semibold border ${meta.bg} ${meta.color}`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
                          {risk.severity}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-slate-300 text-xs">{RBAC_TYPE_LABEL[risk.type] ?? risk.type}</td>
                      <td className="px-4 py-2 font-mono text-slate-200 text-xs">
                        {risk.binding}
                        <span className="ml-1.5 text-slate-500 text-[10px]">({risk.binding_kind})</span>
                        {risk.namespace && <span className="ml-1.5 text-slate-600 text-[10px]">ns:{risk.namespace}</span>}
                      </td>
                      <td className="px-4 py-2 text-indigo-300 font-mono text-xs">{risk.role}</td>
                      <td className="px-4 py-2 text-slate-300 text-xs">
                        <span className="text-slate-500">{risk.subject_kind}/</span>{risk.subject_name}
                        {risk.subject_namespace && <span className="text-slate-600 ml-1">@{risk.subject_namespace}</span>}
                      </td>
                    </tr>
                    {isExp && (
                      <tr key={`${key}-exp`} className="bg-slate-900/50">
                        <td colSpan={6} className="px-6 py-4">
                          <div className="space-y-2">
                            <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1">Risk Findings</div>
                            {(risk.findings as any[]).map((f: any, i: number) => {
                              const fmeta = RBAC_SEV_META[f.severity] ?? RBAC_SEV_META.low
                              return (
                                <div key={i} className={`flex items-start gap-2 rounded p-2 border text-xs ${fmeta.bg}`}>
                                  <span className={`mt-0.5 w-1.5 h-1.5 rounded-full shrink-0 ${fmeta.dot}`} />
                                  <span className={fmeta.color}>{f.detail}</span>
                                </div>
                              )
                            })}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}


// ── Loop 52: Node Resource Pressure Dashboard ────────────────────────────────

const NODE_PRESSURE_META: Record<string, { label: string; color: string; bg: string; bar: string }> = {
  over_committed: { label: 'Over-committed', color: 'text-red-400',    bg: 'bg-red-500/10 border-red-500/30',       bar: 'bg-red-500' },
  not_ready:      { label: 'Not Ready',      color: 'text-slate-400',  bg: 'bg-slate-500/10 border-slate-500/30',   bar: 'bg-slate-500' },
  high:           { label: 'High',           color: 'text-orange-400', bg: 'bg-orange-500/10 border-orange-500/30', bar: 'bg-orange-500' },
  medium:         { label: 'Medium',         color: 'text-yellow-400', bg: 'bg-yellow-500/10 border-yellow-500/30', bar: 'bg-yellow-500' },
  low:            { label: 'Low',            color: 'text-green-400',  bg: 'bg-green-500/10 border-green-500/30',   bar: 'bg-green-500' },
}

function PressureBar({ pct, barClass }: { pct: number; barClass: string }) {
  const w = Math.min(pct, 100)
  return (
    <div className="relative h-1.5 bg-slate-700 rounded-full overflow-hidden w-24">
      <div className={`absolute inset-y-0 left-0 rounded-full ${barClass}`} style={{ width: `${w}%` }} />
    </div>
  )
}

function NodePressureSection({ clusterId }: { clusterId: string }) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [filterPres, setFilterPres] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      setData(await vksGet<any>(`${clusterId}/nodes/pressure`))
    } catch { setData(null) } finally { setLoading(false) }
  }

  useEffect(() => { if (clusterId) load() }, [clusterId])

  const nodes: any[] = (data?.nodes ?? []).filter((n: any) => !filterPres || n.pressure === filterPres)
  const cluster = data?.cluster ?? {}

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Cpu size={20} className="text-blue-400" />Node Pressure
          </h2>
          <p className="text-slate-400 text-sm mt-1">
            Per-node CPU & memory allocation pressure — requested vs allocatable
          </p>
        </div>
        <button onClick={load} disabled={loading} className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded-lg text-slate-300">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />Refresh
        </button>
      </div>

      {/* Cluster summary */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'Total Nodes', value: cluster.total_nodes ?? 0, sub: `${cluster.ready_nodes ?? 0} ready` },
            { label: 'Over-committed', value: cluster.over_committed ?? 0, sub: `${cluster.high_pressure ?? 0} high`, highlight: (cluster.over_committed ?? 0) > 0 },
            { label: 'Cluster CPU', value: `${cluster.cluster_cpu_req_pct ?? 0}%`, sub: 'requested', highlight: (cluster.cluster_cpu_req_pct ?? 0) > 80 },
            { label: 'Cluster Mem', value: `${cluster.cluster_mem_req_pct ?? 0}%`, sub: 'requested', highlight: (cluster.cluster_mem_req_pct ?? 0) > 80 },
          ].map(card => (
            <div key={card.label} className={`rounded-xl border p-3 ${card.highlight ? 'bg-red-950/20 border-red-700/30' : 'bg-slate-800/50 border-slate-700/40'}`}>
              <div className="text-[10px] uppercase tracking-wider text-slate-400">{card.label}</div>
              <div className={`text-2xl font-bold mt-1 ${card.highlight ? 'text-red-400' : 'text-white'}`}>{card.value}</div>
              <div className="text-[10px] text-slate-500 mt-0.5">{card.sub}</div>
            </div>
          ))}
        </div>
      )}

      {/* Pressure filter */}
      {data && (
        <div className="flex flex-wrap gap-2">
          {(Object.entries(NODE_PRESSURE_META) as [string, any][])
            .filter(([pres]) => (data.nodes as any[]).some((n: any) => n.pressure === pres))
            .map(([pres, meta]) => {
              const count = (data.nodes as any[]).filter((n: any) => n.pressure === pres).length
              return (
                <button
                  key={pres}
                  onClick={() => setFilterPres(filterPres === pres ? '' : pres)}
                  className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                    filterPres === pres ? `${meta.bg} ${meta.color}` : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                  }`}
                >
                  {meta.label} ({count})
                </button>
              )
            })}
          {filterPres && (
            <button onClick={() => setFilterPres('')} className="px-3 py-1 rounded-full text-xs border border-slate-600 text-slate-400 hover:border-slate-500">
              Clear
            </button>
          )}
        </div>
      )}

      {loading && <SkeletonRows rows={5} />}

      {!loading && data?.nodes?.length === 0 && (
        <EmptyState icon={Server} title="No nodes found" hint="The cluster has no nodes." />
      )}

      {!loading && nodes.length > 0 && (
        <div className="rounded-lg border border-slate-700/60 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-800/60">
              <tr>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400 w-6" />
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Node</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Pressure</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">CPU Req</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Mem Req</th>
                <th className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-slate-400">Pods</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/40">
              {nodes.map((node: any) => {
                const isExp = expanded.has(node.name)
                const meta = NODE_PRESSURE_META[node.pressure] ?? NODE_PRESSURE_META.low
                return (
                  <>
                    <tr
                      key={node.name}
                      className="hover:bg-slate-800/40 cursor-pointer"
                      onClick={() => setExpanded(prev => { const n = new Set(prev); isExp ? n.delete(node.name) : n.add(node.name); return n })}
                    >
                      <td className="px-4 py-2 text-slate-500">
                        {isExp ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                      </td>
                      <td className="px-4 py-2">
                        <div className="font-mono text-slate-200 text-sm">{node.name}</div>
                        <div className="text-[10px] text-slate-500 mt-0.5">{(node.roles as string[]).join(', ') || 'worker'}</div>
                      </td>
                      <td className="px-4 py-2">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border ${meta.bg} ${meta.color}`}>
                          {meta.label}
                        </span>
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          <PressureBar pct={node.cpu_req_pct} barClass={meta.bar} />
                          <span className={`text-xs font-mono ${node.cpu_req_pct > 100 ? 'text-red-400' : 'text-slate-300'}`}>
                            {node.cpu_req_pct}%
                          </span>
                        </div>
                        <div className="text-[10px] text-slate-500 mt-0.5">{Math.round(node.req_cpu_m)}m / {Math.round(node.alloc_cpu_m)}m</div>
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          <PressureBar pct={node.mem_req_pct} barClass={meta.bar} />
                          <span className={`text-xs font-mono ${node.mem_req_pct > 100 ? 'text-red-400' : 'text-slate-300'}`}>
                            {node.mem_req_pct}%
                          </span>
                        </div>
                        <div className="text-[10px] text-slate-500 mt-0.5">{Math.round(node.req_mem_mib / 1024 * 10) / 10}Gi / {Math.round(node.alloc_mem_mib / 1024 * 10) / 10}Gi</div>
                      </td>
                      <td className="px-4 py-2 text-slate-300 text-sm font-mono">{node.pod_count}</td>
                    </tr>
                    {isExp && (
                      <tr key={`${node.name}-exp`} className="bg-slate-900/50">
                        <td colSpan={6} className="px-6 py-4">
                          <div className="space-y-3">
                            {node.top_pods.length === 0 && (
                              <div className="text-slate-500 text-xs">No running pods on this node.</div>
                            )}
                            {node.top_pods.length > 0 && (
                              <>
                                <div className="text-[10px] uppercase tracking-wider text-slate-400">Top Pods by CPU Request</div>
                                <div className="overflow-hidden rounded-lg border border-slate-700/40">
                                  <table className="w-full text-xs">
                                    <thead className="bg-slate-800/60">
                                      <tr>
                                        <th className="px-3 py-1.5 text-left text-slate-500">Pod</th>
                                        <th className="px-3 py-1.5 text-left text-slate-500">Namespace</th>
                                        <th className="px-3 py-1.5 text-left text-slate-500">CPU Req</th>
                                        <th className="px-3 py-1.5 text-left text-slate-500">Mem Req</th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-800">
                                      {(node.top_pods as any[]).map((p: any) => (
                                        <tr key={`${p.namespace}/${p.name}`} className="hover:bg-slate-800/20">
                                          <td className="px-3 py-1.5 font-mono text-slate-200">{p.name}</td>
                                          <td className="px-3 py-1.5 text-slate-400">{p.namespace}</td>
                                          <td className="px-3 py-1.5 text-slate-300 font-mono">{p.req_cpu_m}m</td>
                                          <td className="px-3 py-1.5 text-slate-300 font-mono">{p.req_mem_mib}Mi</td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}


// ── Loop 53: Cross-Pod Log Search ────────────────────────────────────────────

function LogSearchSection({ clusterId, namespace }: { clusterId: string; namespace: string }) {
  const [query, setQuery] = useState('')
  const [ns, setNs] = useState(namespace)
  const [tail, setTail] = useState('500')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { setNs(namespace) }, [namespace])

  const search = async () => {
    const q = query.trim()
    if (q.length < 2) { setError('Query must be at least 2 characters'); return }
    if (!ns) { setError('Select a namespace first'); return }
    setError('')
    setLoading(true)
    try {
      const params = new URLSearchParams({ q, namespace: ns, tail })
      setData(await vksGet<any>(`${clusterId}/log-search?${params}`))
    } catch (e: any) { setError(e.message || 'Search failed') } finally { setLoading(false) }
  }

  const handleKey = (e: React.KeyboardEvent) => { if (e.key === 'Enter') search() }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-bold text-white flex items-center gap-2">
          <FileText size={20} className="text-cyan-400" />Log Search
        </h2>
        <p className="text-slate-400 text-sm mt-1">
          Search across all pod logs in a namespace — returns matching lines with pod and line number
        </p>
      </div>

      {/* Search bar */}
      <div className="flex flex-wrap gap-2 items-end">
        <div className="flex-1 min-w-[200px]">
          <label className="text-[10px] uppercase tracking-wider text-slate-400 mb-1 block">Search query</label>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="error, exception, timeout…"
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-cyan-500"
          />
        </div>
        <div className="w-32">
          <label className="text-[10px] uppercase tracking-wider text-slate-400 mb-1 block">Namespace</label>
          <input
            value={ns}
            onChange={e => setNs(e.target.value)}
            placeholder="default"
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-cyan-500"
          />
        </div>
        <div className="w-24">
          <label className="text-[10px] uppercase tracking-wider text-slate-400 mb-1 block">Last N lines</label>
          <input
            value={tail}
            onChange={e => setTail(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
          />
        </div>
        <button
          onClick={search}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-50 rounded-lg text-white text-sm font-medium"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
          Search
        </button>
      </div>

      {error && <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg p-3">{error}</div>}

      {/* Summary */}
      {data && !loading && (
        <div className="flex flex-wrap gap-3 text-xs text-slate-400">
          <span className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5">
            <span className="text-white font-medium">{data.pods_searched}</span> pods searched
          </span>
          <span className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5">
            <span className={`font-medium ${data.pods_with_matches > 0 ? 'text-cyan-300' : 'text-slate-400'}`}>{data.pods_with_matches}</span> pods with matches
          </span>
          <span className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5">
            <span className={`font-medium ${data.total_matches > 0 ? 'text-cyan-300' : 'text-slate-400'}`}>{data.total_matches}</span> total matches
          </span>
        </div>
      )}

      {!loading && data && data.pods_with_matches === 0 && (
        <EmptyState icon={Search} title="No matches found" hint={`"${data.query}" not found in any pod logs in namespace "${data.namespace}".`} />
      )}

      {!loading && data && (data.results as any[]).length > 0 && (
        <div className="space-y-3">
          {(data.results as any[]).map((result: any) => (
            <div key={result.pod} className="rounded-xl border border-slate-700/60 overflow-hidden">
              <div className="bg-slate-800/80 px-4 py-2.5 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Terminal size={14} className="text-cyan-400" />
                  <span className="font-mono text-slate-200 text-sm">{result.pod}</span>
                  <span className="text-slate-500 text-xs">@{result.namespace}</span>
                </div>
                <span className="text-xs bg-cyan-900/30 border border-cyan-700/40 rounded px-2 py-0.5 text-cyan-300">
                  {result.match_count} {result.match_count === 1 ? 'match' : 'matches'}
                </span>
              </div>
              <div className="bg-slate-950/50">
                {(result.matches as any[]).map((m: any, i: number) => {
                  const line: string = m.line
                  const q = data.query.toLowerCase()
                  const idx = line.toLowerCase().indexOf(q)
                  const before = idx >= 0 ? line.slice(0, idx) : line
                  const match  = idx >= 0 ? line.slice(idx, idx + q.length) : ''
                  const after  = idx >= 0 ? line.slice(idx + q.length) : ''
                  return (
                    <div key={i} className="flex items-start gap-3 px-4 py-1.5 border-b border-slate-800/60 last:border-b-0 hover:bg-slate-800/20">
                      <span className="shrink-0 text-[10px] text-slate-600 font-mono w-10 text-right pt-0.5">{m.line_no}</span>
                      <pre className="text-xs font-mono text-slate-300 whitespace-pre-wrap break-all flex-1">
                        {before}<mark className="bg-yellow-400/25 text-yellow-200 rounded">{match}</mark>{after}
                      </pre>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Nav items ─────────────────────────────────────────────────────────────────

interface NavItem { id: PlatformSection; label: string; icon: React.ElementType; description: string }
interface NavGroup { id: string; label: string; items: NavItem[] }

const NAV_GROUPS: NavGroup[] = [
  { id: 'core', label: 'Core', items: [
    { id: 'overview',      label: 'Overview',     icon: Activity,    description: 'High-level cluster snapshot — node health, resource usage, and workload status at a glance.' },
    { id: 'namespaces',    label: 'Namespaces',   icon: Layers,      description: 'List and manage namespaces; apply labels, Pod Security Standards, and quota policies.' },
    { id: 'workloads',     label: 'Workloads',    icon: Box,         description: 'Deployments, StatefulSets, DaemonSets — scale, restart, view YAML, and manage replicas.' },
    { id: 'pods',          label: 'Pods',         icon: Box,         description: 'Live pod list with status, restart count, resource usage, and direct log access.' },
    { id: 'nodes',         label: 'Nodes',        icon: Server,      description: 'Node inventory showing allocatable CPU/memory, taints, labels, and Ready conditions.' },
  ]},
  { id: 'networking', label: 'Networking', items: [
    { id: 'services',        label: 'Services',      icon: Globe,       description: 'ClusterIP, NodePort, and LoadBalancer services with endpoints and port mappings.' },
    { id: 'ingresses',       label: 'Ingresses',     icon: Network,     description: 'HTTP/HTTPS routing rules — host/path mappings, TLS termination, and backend targets.' },
    { id: 'networkpolicies', label: 'Net Policies',  icon: MinusCircle, description: 'Ingress/egress firewall rules controlling traffic flow between pods and namespaces.' },
    { id: 'netpol-analyzer', label: 'NetPol Verify', icon: Network,     description: 'Simulate and validate whether specific pod-to-pod traffic is allowed or blocked.' },
    { id: 'pod-traffic',     label: 'Connection Map', icon: Workflow,    description: 'Visual pod connection graph — service edges, inferred callers, network bytes, and security recommendations.' },
    { id: 'tls-certs',       label: 'TLS Certs',     icon: ShieldAlert, description: 'TLS certificates stored as Secrets — expiry dates, issuer, and domain coverage.' },
  ]},
  { id: 'storage', label: 'Storage', items: [
    { id: 'storage',      label: 'Storage',      icon: HardDrive, description: 'PersistentVolumeClaims and StorageClasses with capacity, access mode, and binding status.' },
    { id: 'pvc-analysis', label: 'PVC Analysis', icon: HardDrive, description: 'Identify oversized, underused, or unbound PVCs to reclaim wasted storage costs.' },
  ]},
  { id: 'config', label: 'Config & Access', items: [
    { id: 'config',          label: 'ConfigMaps',   icon: Settings2,  description: 'ConfigMap inventory — view data keys, track changes, and compare across namespaces.' },
    { id: 'secrets',         label: 'Secrets',      icon: Lock,       description: 'Secrets inventory with type, age, and how many workloads reference each secret.' },
    { id: 'serviceaccounts', label: 'Svc Accounts', icon: Info,       description: 'Service accounts with image pull secrets, labels, and associated RBAC bindings.' },
    { id: 'rbac',            label: 'RBAC',         icon: Lock,       description: 'Roles, ClusterRoles, and their bindings — who can do what in which namespace.' },
    { id: 'limitranges',     label: 'Limit Ranges', icon: Cpu,        description: 'Default container resource limits and maximums enforced per namespace.' },
    { id: 'quotas',          label: 'Quotas',       icon: MemoryStick, description: 'ResourceQuota usage — current vs hard limits for CPU, memory, and object counts.' },
  ]},
  { id: 'scaling', label: 'Scaling & Resilience', items: [
    { id: 'hpa',              label: 'Autoscalers',       icon: Zap,        description: 'HorizontalPodAutoscalers — current replicas, min/max bounds, and CPU/memory targets.' },
    { id: 'pdbs',             label: 'Disruption Budgets',icon: MinusCircle,description: 'PodDisruptionBudgets ensuring minimum availability during node drains and updates.' },
    { id: 'pdb-coverage',     label: 'PDB Coverage',      icon: ShieldCheck,description: 'Deployments missing PodDisruptionBudgets — identifies blast radius on node drain.' },
    { id: 'affinity-coverage',label: 'Anti-Affinity',     icon: Layers,     description: 'Deployments missing anti-affinity rules that risk all replicas on one node.' },
    { id: 'scheduling',       label: 'Scheduling',        icon: Clock,      description: 'Pending pods, unschedulable reasons, node selector mismatches, and resource gaps.' },
  ]},
  { id: 'observability', label: 'Observability', items: [
    { id: 'events',           label: 'Events',       icon: Radio,     description: 'Kubernetes events filtered by type and reason — warnings, errors, object lifecycle.' },
    { id: 'event-stream',     label: 'Live Events',  icon: Radio,     description: 'Real-time streaming Kubernetes event watch — see objects change as they happen.' },
    { id: 'log-search',       label: 'Log Search',   icon: FileText,  description: 'Full-text search across pod logs — find errors, stack traces, and patterns instantly.' },
    { id: 'node-pressure',    label: 'Node Pressure',icon: Cpu,       description: 'Nodes under MemoryPressure, DiskPressure, or PIDPressure conditions.' },
    { id: 'oom-detector',     label: 'OOM Kills',    icon: Flame,     description: 'Containers killed by the OOMKiller — history, frequency, and memory usage trends.' },
    { id: 'restart-timeline', label: 'Restarts',     icon: RotateCcw, description: 'Visual timeline of pod restarts to spot crash loops and recurring failure patterns.' },
  ]},
  { id: 'security', label: 'Security', items: [
    { id: 'rbac-risks',      label: 'RBAC Risks',     icon: ShieldCheck, description: 'Overprivileged bindings, wildcard verbs, and privilege escalation paths in RBAC.' },
    { id: 'security-audit',  label: 'Security Audit', icon: ShieldAlert, description: 'CIS Benchmark checks — privileged containers, host network access, and capabilities.' },
    { id: 'namespace-labels',label: 'NS Labels',      icon: Tag,         description: 'Namespace labels for Pod Security Standards enforcement and team ownership tagging.' },
    { id: 'images',          label: 'Images',         icon: Upload,      description: 'Container images in use with registry source, tag, and image pull policy per workload.' },
  ]},
  { id: 'fleet', label: 'Fleet', items: [
    { id: 'fleet-health', label: 'Fleet Health', icon: LayoutGrid,  description: 'Aggregate health summary across all registered clusters — nodes, workloads, and status.' },
    { id: 'fleet-diff',   label: 'Fleet Diff',   icon: ArrowUpDown, description: 'Side-by-side resource comparison between two clusters to spot configuration drift.' },
  ]},
  { id: 'advanced', label: 'Advanced', items: [
    { id: 'crds',          label: 'CRDs',         icon: Box,         description: 'Custom Resource Definitions — schema, API versions, scope, and instance counts.' },
    { id: 'pod-resources', label: 'Pod Resources',icon: Activity,    description: 'Actual vs requested CPU/memory per pod — identify over-provisioned or starved containers.' },
    { id: 'topology',      label: 'Topology',     icon: Network,     description: 'Visualize pod-to-node placement and topology spread across zones and nodes.' },
    { id: 'cost',          label: 'Cost',         icon: DollarSign,  description: 'Estimated resource cost breakdown by namespace, workload, and node.' },
    { id: 'search',        label: 'Search',       icon: Search,      description: 'Global cross-resource search — find any object by name across all namespaces instantly.' },
    { id: 'helm',          label: 'Helm',         icon: Package,     description: 'Helm releases — status, chart version, deployed values, and revision history.' },
    { id: 'orphans',       label: 'Orphans',      icon: Ghost,       description: 'Orphaned ConfigMaps, Secrets, and PVCs with no active consumers — safe to clean up.' },
    { id: 'audit',         label: 'Audit Log',    icon: Shield,      description: 'Audit trail of all write actions performed through this console, with user and timestamp.' },
  ]},
]

// ── Floating AI Panel ─────────────────────────────────────────────────────────

interface AIMessage { role: 'user' | 'assistant'; text: string }

function FloatingAIPanel({ section, namespace, clusterId }: {
  section: string; namespace: string; clusterId: string
}) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<AIMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function ask() {
    const q = input.trim()
    if (!q || loading) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: q }])
    setLoading(true)
    const params = new URLSearchParams({ question: q, section, namespace, cluster_id: clusterId })
    const es = new EventSource(`${API}/api/v1/vks/ask?${params}`)
    let buf = ''
    es.onmessage = e => {
      try {
        const d = JSON.parse(e.data)
        if (d.done) {
          es.close()
          setLoading(false)
        } else if (d.error) {
          es.close()
          setLoading(false)
          setMessages(prev => [...prev, { role: 'assistant', text: `Error: ${d.error}` }])
        } else if (d.text) {
          buf += d.text
          setMessages(prev => {
            const last = prev[prev.length - 1]
            if (last?.role === 'assistant') return [...prev.slice(0, -1), { role: 'assistant', text: buf }]
            return [...prev, { role: 'assistant', text: buf }]
          })
        }
      } catch { /* ignore parse error */ }
    }
    es.onerror = () => { es.close(); setLoading(false) }
  }

  return (
    <>
      {/* Floating toggle button */}
      <button
        onClick={() => setOpen(o => !o)}
        className="fixed bottom-6 right-6 z-50 w-12 h-12 rounded-full bg-blue-600 hover:bg-blue-500 shadow-lg flex items-center justify-center text-white transition-all"
        title="AI Assistant"
      >
        {open ? <X size={20} /> : <BotMessageSquare size={20} />}
      </button>

      {/* Side drawer */}
      {open && (
        <div className="fixed bottom-20 right-6 z-50 w-96 h-[520px] bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col overflow-hidden">
          {/* Header */}
          <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700 bg-slate-800/60">
            <BotMessageSquare size={16} className="text-blue-400" />
            <span className="text-sm font-medium text-slate-200">K8s / MCO Assistant</span>
            {(section || namespace) && (
              <span className="ml-auto text-[10px] text-slate-500 truncate max-w-[160px]">
                {[section, namespace].filter(Boolean).join(' · ')}
              </span>
            )}
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3 text-sm">
            {messages.length === 0 && !loading && (
              <div className="text-slate-500 text-xs text-center mt-8 space-y-1">
                <BotMessageSquare size={28} className="mx-auto text-slate-600" />
                <p className="mt-2">Ask anything about K8s or MCO.</p>
                <p>I know about OpenShift → K8s migrations.</p>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                <div className={`max-w-[85%] rounded-lg px-3 py-2 text-xs leading-relaxed ${
                  m.role === 'user'
                    ? 'bg-blue-600/30 text-blue-100'
                    : 'bg-slate-800 text-slate-200 prose prose-invert prose-xs max-w-none'
                }`}>
                  {m.role === 'assistant'
                    ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                    : m.text}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="bg-slate-800 rounded-lg px-3 py-2">
                  <Loader2 size={14} className="animate-spin text-blue-400" />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="flex items-center gap-2 px-3 py-3 border-t border-slate-700 bg-slate-800/40">
            <input
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500"
              placeholder="Ask about workloads, RBAC, quotas…"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }}
              disabled={loading}
            />
            <button
              onClick={ask}
              disabled={loading || !input.trim()}
              className="p-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors"
            >
              <SendHorizontal size={14} />
            </button>
          </div>
        </div>
      )}
    </>
  )
}

// ── Main PlatformPage ─────────────────────────────────────────────────────────

export function PlatformPage() {
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clustersLoading, setClustersLoading] = useState(true)
  const [supervisorErr, setSupervisorErr] = useState<string | null>(null)
  const [selectedCluster, setSelectedCluster] = useState<Cluster | null>(null)
  const [section, setSection] = useState<PlatformSection>('overview')
  const [namespace, setNamespace] = useState('')
  const [namespaces, setNamespaces] = useState<string[]>([])
  const nsSelectRef = useRef<HTMLSelectElement>(null)
  const [showNL, setShowNL] = useState(false)
  const [showImportCluster, setShowImportCluster] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [showCompare, setShowCompare] = useState(false)
  const [warningCount, setWarningCount] = useState(0)
  const [podFilter, setPodFilter] = useState('')
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set(['advanced']))
  const toggleGroup = useCallback((gid: string) => setCollapsedGroups(prev => {
    const next = new Set(prev); next.has(gid) ? next.delete(gid) : next.add(gid); return next
  }), [])
  const [navTooltip, setNavTooltip] = useState<{ label: string; description: string; x: number; y: number } | null>(null)
  const [showKubeconfigConfirm, setShowKubeconfigConfirm] = useState(false)
  const [navFilter, setNavFilter] = useState('')
  const toast = useToast()

  const loadClusters = useCallback(() => {
    setClustersLoading(true)
    vksGet<{ clusters: Cluster[]; supervisor_error: string | null }>('clusters')
      .then(d => {
        setClusters(d.clusters)
        setSupervisorErr(d.supervisor_error || null)
      })
      .catch(e => setSupervisorErr(`Service unavailable: ${e}`))
      .finally(() => setClustersLoading(false))
  }, [])

  const [reconnecting, setReconnecting] = useState(false)
  const reconnectSupervisor = useCallback(() => {
    setReconnecting(true)
    vksPost('supervisor/reconnect')
      .then(() => { toast.success('Supervisor reconnected'); loadClusters() })
      .catch(e => toast.error(`Reconnect failed: ${e?.message ?? e}`))
      .finally(() => setReconnecting(false))
  }, [loadClusters, toast])

  // Load clusters
  useEffect(() => { loadClusters() }, [loadClusters])

  // Global keyboard shortcuts
  useEffect(() => {
    const SECTION_KEYS: Record<string, PlatformSection> = { o: 'overview', p: 'pods', w: 'workloads', n: 'nodes', e: 'events', s: 'services', i: 'ingresses', c: 'config', k: 'secrets', q: 'quotas', a: 'audit', x: 'serviceaccounts' }
    let pendingG = false
    const handler = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (ev.key === '?') { setShowShortcuts(s => !s); return }
      // n key: focus namespace picker if visible, otherwise fall through to section nav
      if (ev.key === 'n' && !pendingG && nsSelectRef.current) {
        ev.preventDefault()
        nsSelectRef.current.focus()
        return
      }
      if (ev.key === 'g') { pendingG = true; setTimeout(() => { pendingG = false }, 1000); return }
      if (pendingG && SECTION_KEYS[ev.key]) {
        setSection(SECTION_KEYS[ev.key] as PlatformSection)
        pendingG = false
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // Load namespace list for picker
  useEffect(() => {
    if (!selectedCluster) return
    vksGet<{ namespaces: { name: string; is_system: boolean }[] }>(`${selectedCluster.id}/namespaces`)
      .then(d => {
        const userNs = (d.namespaces ?? []).filter((n: any) => !n.is_system).map((n: any) => n.name)
        setNamespaces(userNs)
      })
      .catch(() => { /* silent */ })
  }, [selectedCluster])

  // Poll for warning event count (badge on Events nav item)
  useEffect(() => {
    if (!selectedCluster) { setWarningCount(0); return }
    const poll = () => {
      vksGet<{ events: { type: string }[] }>(`${selectedCluster.id}/events?namespace=`)
        .then(d => setWarningCount((d.events ?? []).filter((e: any) => e.type === 'Warning').length))
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 60_000)
    return () => clearInterval(id)
  }, [selectedCluster])

  function selectCluster(c: Cluster) {
    setSelectedCluster(c)
    setSection('overview')
    setNamespace('')
  }

  const clusterId = selectedCluster?.id || ''

  const sectionContent = () => {
    if (!selectedCluster) return null
    switch (section) {
      case 'overview': return <OverviewSection clusterId={clusterId} onNavigate={setSection} />
      case 'namespaces': return <NamespacesSection clusterId={clusterId} onSelectNamespace={ns => { setNamespace(ns); setSection('pods') }} />
      case 'workloads': return <WorkloadsSection clusterId={clusterId} namespace={namespace} onViewPods={(name) => { setPodFilter(name); setSection('pods') }} />
      case 'pods': return <PodsSection clusterId={clusterId} namespace={namespace} externalFilter={podFilter} />
      case 'nodes': return <NodesSection clusterId={clusterId} />
      case 'services': return <ServicesSection clusterId={clusterId} namespace={namespace} />
      case 'ingresses': return <IngressSection clusterId={clusterId} namespace={namespace} />
      case 'networkpolicies': return <NetworkPoliciesSection clusterId={clusterId} namespace={namespace} />
      case 'storage': return <StorageSection clusterId={clusterId} namespace={namespace} />
      case 'pvc-analysis': return <PvcAnalysisSection clusterId={clusterId} namespace={namespace} />
      case 'quotas': return <QuotaSection clusterId={clusterId} namespace={namespace} />
      case 'config': return <ConfigMapSection clusterId={clusterId} namespace={namespace} />
      case 'secrets': return <SecretsSection clusterId={clusterId} namespace={namespace} />
      case 'serviceaccounts': return <ServiceAccountsSection clusterId={clusterId} namespace={namespace} />
      case 'rbac': return <RBACSection clusterId={clusterId} namespace={namespace} />
      case 'hpa': return <HPASection clusterId={clusterId} namespace={namespace} />
      case 'pdbs': return <PDBSection clusterId={clusterId} namespace={namespace} />
      case 'limitranges': return <LimitRangesSection clusterId={clusterId} namespace={namespace} />
      case 'images': return <ImagesSection clusterId={clusterId} namespace={namespace} />
      case 'topology': return <TopologySection clusterId={clusterId} namespace={namespace} onNavigate={setSection} />
      case 'pod-traffic': return <PodConnectionMapSection clusterId={clusterId} namespace={namespace} />
      case 'crds': return <CRDsSection clusterId={clusterId} />
      case 'pod-resources': return <PodResourcesSection clusterId={clusterId} namespace={namespace} />
      case 'events': return <EventsSection clusterId={clusterId} namespace={namespace} />
      case 'fleet-health': return <FleetHealthSection />
      case 'fleet-diff': return <FleetDiffSection />
      case 'log-search': return <LogSearchSection clusterId={clusterId} namespace={namespace} />
      case 'node-pressure': return <NodePressureSection clusterId={clusterId} />
      case 'rbac-risks': return <RBACRisksSection clusterId={clusterId} />
      case 'scheduling': return <SchedulingIssuesSection clusterId={clusterId} namespace={namespace} />
      case 'orphans': return <OrphansSection clusterId={clusterId} namespace={namespace} />
      case 'oom-detector': return <OOMDetectorSection clusterId={clusterId} namespace={namespace} />
      case 'restart-timeline': return <RestartTimelineSection clusterId={clusterId} namespace={namespace} />
      case 'pdb-coverage': return <PdbCoverageSection clusterId={clusterId} namespace={namespace} />
      case 'affinity-coverage': return <AffinityCoverageSection clusterId={clusterId} namespace={namespace} />
      case 'security-audit': return <SecurityAuditSection clusterId={clusterId} namespace={namespace} />
      case 'namespace-labels': return <NamespaceLabelsSection clusterId={clusterId} />
      case 'tls-certs': return <TLSCertsSection clusterId={clusterId} />
      case 'netpol-analyzer': return <NetPolAnalyzerSection clusterId={clusterId} />
      case 'cost': return <CostEstimatorSection clusterId={clusterId} />
      case 'search': return <GlobalSearchSection clusterId={clusterId} />
      case 'helm': return <HelmSection clusterId={clusterId} />
      case 'event-stream': return <EventStreamSection clusterId={clusterId} namespace={namespace} />
      case 'audit': return <AuditSection clusterId={clusterId} />
      default: return null
    }
  }

  const hasNamespaceFilter = !['overview', 'namespaces', 'nodes'].includes(section)

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {navTooltip && (
        <div
          className="fixed z-[9999] pointer-events-none"
          style={{
            left: navTooltip.x + 8,
            top: Math.min(navTooltip.y - 4, window.innerHeight - 170),
          }}
        >
          <div className="flex items-start gap-0">
            <div className="mt-3 w-0 h-0 border-t-4 border-b-4 border-r-[6px] border-t-transparent border-b-transparent border-r-slate-600 shrink-0" />
            <div className="w-60 p-3 bg-slate-900 border border-slate-700 rounded-lg shadow-2xl">
              <div className="text-[12px] font-semibold text-white mb-1">{navTooltip.label}</div>
              <div className="text-[11px] text-slate-400 leading-relaxed">{navTooltip.description}</div>
            </div>
          </div>
        </div>
      )}
      {showShortcuts && <KeyboardShortcutsOverlay onClose={() => setShowShortcuts(false)} />}
      {showCompare && <ClusterCompareModal clusters={clusters} onClose={() => setShowCompare(false)} />}
      <PageHeader
        icon={Server}
        title="Platform Console"
        subtitle={selectedCluster ? (
          <span className="flex items-center gap-1 text-xs flex-wrap">
            <button onClick={() => setSelectedCluster(null)} className="text-blue-400 hover:text-blue-300 transition-colors">{selectedCluster.name}</button>
            <span className="text-slate-600">/</span>
            <button onClick={() => setNamespace('')} className={namespace ? 'text-blue-400 hover:text-blue-300 transition-colors' : 'text-slate-500 italic'}>
              {namespace || 'all namespaces'}
            </button>
            <span className="text-slate-600">/</span>
            <span className="text-slate-300">{NAV_GROUPS.flatMap(g => g.items).find(i => i.id === section)?.label ?? section}</span>
          </span>
        ) : 'Manage VKS clusters without kubectl'}
      >
        <button
          onClick={() => setShowShortcuts(true)}
          className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400"
          title="Keyboard shortcuts (?)"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M6 14h.01M18 14h.01M10 14h4"/></svg>
        </button>
        {clusters.length >= 2 && (
          <button onClick={() => setShowCompare(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg border border-slate-700"
            title="Compare clusters">
            <ArrowUpDown size={12} /> Compare
          </button>
        )}
        {selectedCluster && (
          <button
            onClick={() => setShowNL(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 text-sm rounded-lg border border-blue-600/30"
          >
            <BotMessageSquare size={14} />
            NL Actions
          </button>
        )}
      </PageHeader>

      {showNL && selectedCluster && (
        <NLCommandBar clusterId={clusterId} onClose={() => setShowNL(false)} />
      )}

      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left rail */}
        <div className="w-52 flex-shrink-0 border-r border-slate-800 flex flex-col overflow-hidden">
          {/* Cluster picker */}
          <div className="px-3 py-3 border-b border-slate-800">
            <div className="flex items-center justify-between mb-2 px-1">
              <span className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">Clusters</span>
              <button
                onClick={() => setShowImportCluster(true)}
                title="Import kubeconfig"
                className="text-slate-500 hover:text-blue-400 transition-colors"
              >
                <Plus size={13} />
              </button>
            </div>
            {supervisorErr && (
              <div className="mb-2 flex items-start gap-1.5 px-2 py-1.5 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
                <AlertTriangle size={11} className="text-yellow-400 flex-shrink-0 mt-0.5" />
                <div className="flex flex-col gap-1 min-w-0">
                  <span className="text-[10px] text-yellow-400 leading-tight">Supervisor unreachable — imported clusters still available</span>
                  <button
                    onClick={reconnectSupervisor}
                    disabled={reconnecting}
                    className="text-[10px] text-yellow-300 hover:text-yellow-100 underline text-left disabled:opacity-50"
                  >{reconnecting ? 'Reconnecting…' : 'Reconnect'}</button>
                </div>
              </div>
            )}
            {clustersLoading ? (
              <Skeleton className="h-8 rounded-lg" />
            ) : (
              <div className="space-y-1">
                <button
                  onClick={() => setSelectedCluster(null)}
                  className={`w-full text-left px-2 py-1.5 rounded-lg text-xs transition-colors ${!selectedCluster ? 'bg-blue-600/20 text-blue-400' : 'text-slate-400 hover:bg-slate-800'}`}
                >
                  All clusters ({clusters.length})
                </button>
                {clusters.map(c => (
                  <div key={c.id} className="group relative">
                    <button
                      onClick={() => selectCluster(c)}
                      className={`w-full text-left px-2 py-1.5 rounded-lg text-xs transition-colors flex items-center gap-2 pr-6 ${selectedCluster?.id === c.id ? 'bg-blue-600/20 text-blue-400' : 'text-slate-400 hover:bg-slate-800'}`}
                    >
                      <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${c.ready ? 'bg-emerald-400' : 'bg-red-400'}`} />
                      <span className="truncate flex-1">{c.name}</span>
                      {c.source === 'imported' && (
                        <span className="text-[9px] bg-purple-600/30 text-purple-400 px-1 rounded flex-shrink-0">imp</span>
                      )}
                    </button>
                    {c.source === 'imported' && (
                      <button
                        onClick={async e => {
                          e.stopPropagation()
                          try {
                            await vksDelete(`clusters/import/${c.name}`)
                            if (selectedCluster?.id === c.id) setSelectedCluster(null)
                            loadClusters()
                          } catch (err) { /* ignore */ }
                        }}
                        title="Remove imported cluster"
                        className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 text-slate-600 hover:text-red-400 transition-all"
                      >
                        <X size={11} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Nav items — only visible when cluster selected */}
          {selectedCluster && (
            <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2">
              {/* Nav filter */}
              <div className="relative mb-2">
                <Search size={10} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
                <input
                  value={navFilter}
                  onChange={e => setNavFilter(e.target.value)}
                  placeholder="Filter sections…"
                  className="w-full bg-slate-800 border border-slate-700 rounded-md pl-6 pr-6 py-1 text-[11px] text-slate-300 placeholder-slate-600 focus:outline-none focus:border-slate-500"
                />
                {navFilter && (
                  <button onClick={() => setNavFilter('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300">
                    <X size={10} />
                  </button>
                )}
              </div>
              {NAV_GROUPS.map(group => {
                const filteredItems = navFilter
                  ? group.items.filter(i => i.label.toLowerCase().includes(navFilter.toLowerCase()) || i.description.toLowerCase().includes(navFilter.toLowerCase()))
                  : group.items
                if (navFilter && filteredItems.length === 0) return null
                const isCollapsed = !navFilter && collapsedGroups.has(group.id)
                return (
                <div key={group.id} className="mb-1">
                  <button
                    onClick={() => !navFilter && toggleGroup(group.id)}
                    className="w-full flex items-center justify-between px-1 py-1 text-[10px] uppercase tracking-wider text-slate-400 hover:text-slate-400 font-medium transition-colors"
                  >
                    <span>{group.label}</span>
                    {!navFilter && (isCollapsed ? <ChevronRight size={10} /> : <ChevronDown size={10} />)}
                  </button>
                  {!isCollapsed && filteredItems.map(item => (
                    <div key={item.id} className="group/nav flex items-center">
                      <button
                        onClick={() => setSection(item.id)}
                        className={`flex-1 text-left px-2 py-1.5 rounded-lg text-xs transition-colors flex items-center gap-2 min-w-0 ${section === item.id ? 'bg-blue-600/20 text-blue-400' : 'text-slate-400 hover:bg-slate-800 hover:text-white'}`}
                      >
                        <item.icon size={13} className="shrink-0" />
                        <span className="flex-1 truncate">{item.label}</span>
                        {item.id === 'events' && warningCount > 0 && (
                          <span className="px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 shrink-0">
                            {warningCount}
                          </span>
                        )}
                      </button>
                      <button
                        className="shrink-0 p-1 text-slate-600 opacity-40 group-hover/nav:opacity-100 hover:text-blue-400 transition-all"
                        onMouseEnter={e => {
                          const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
                          setNavTooltip({ label: item.label, description: item.description, x: r.right, y: r.top })
                        }}
                        onMouseLeave={() => setNavTooltip(null)}
                        tabIndex={-1}
                      >
                        <Info size={10} />
                      </button>
                    </div>
                  ))}
                </div>
                )
              })}
              {navFilter && NAV_GROUPS.every(g => !g.items.some(i => i.label.toLowerCase().includes(navFilter.toLowerCase()) || i.description.toLowerCase().includes(navFilter.toLowerCase()))) && (
                <div className="text-[11px] text-slate-500 text-center py-4">No sections match</div>
              )}
            </div>
          )}

          {/* Cluster metadata */}
          {selectedCluster && (
            <div className="px-3 py-3 border-t border-slate-800">
              <div className="text-[10px] text-slate-500 space-y-1">
                <div>K8s: <span className="text-slate-400 font-mono">{selectedCluster.k8s_version || '—'}</span></div>
                <div>Phase: <span className={selectedCluster.phase === 'Provisioned' ? 'text-emerald-400' : 'text-yellow-400'}>{selectedCluster.phase}</span></div>
              </div>
              <button
                onClick={() => setShowKubeconfigConfirm(true)}
                className="mt-2 w-full flex items-center gap-1.5 px-2 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-400 text-[10px] rounded"
              >
                <Download size={11} /> Kubeconfig
              </button>
              {showKubeconfigConfirm && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowKubeconfigConfirm(false)}>
                  <div className="bg-slate-900 border border-red-800/50 rounded-xl p-5 w-80 shadow-2xl" onClick={e => e.stopPropagation()}>
                    <div className="flex items-center gap-2 mb-3">
                      <ShieldAlert size={16} className="text-red-400 shrink-0" />
                      <span className="text-sm font-semibold text-white">Download Kubeconfig</span>
                    </div>
                    <div className="text-xs text-slate-300 mb-1">Cluster: <span className="text-white font-medium">{selectedCluster.name}</span></div>
                    <p className="text-xs text-slate-400 mt-2 mb-4 leading-relaxed">
                      This kubeconfig grants <span className="text-yellow-300 font-medium">full admin access</span> to the cluster. Keep it secure and never commit it to source control. This download will be recorded in the audit log.
                    </p>
                    <div className="flex gap-2">
                      <button onClick={() => setShowKubeconfigConfirm(false)} className="flex-1 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg">Cancel</button>
                      <button
                        onClick={() => { window.open(`${API}/api/v1/vks/${clusterId}/kubeconfig`, '_blank'); setShowKubeconfigConfirm(false); toast.success('Kubeconfig downloaded — access logged') }}
                        className="flex-1 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded-lg flex items-center justify-center gap-1.5"
                      >
                        <Download size={11} /> Download & Audit
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Main content */}
        <div className="flex-1 overflow-y-auto p-6">
          {!selectedCluster ? (
            <div className="space-y-6">
              <p className="text-slate-400 text-sm">Select a cluster to manage, or view all cluster cards below.</p>
              {clustersLoading ? (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  <Skeleton className="h-32 rounded-xl" /><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-32 rounded-xl" />
                </div>
              ) : (
                <>
                  {supervisorErr && (
                    <div className="flex items-start gap-2 p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-xs text-yellow-400 mb-4">
                      <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
                      <div className="flex-1">
                        <span className="font-semibold">Supervisor unreachable:</span> {supervisorErr}<br />
                        <span className="text-yellow-500/70">Imported clusters are still available. Use the + button to add clusters via kubeconfig.</span>
                      </div>
                      <button
                        onClick={reconnectSupervisor}
                        disabled={reconnecting}
                        className="flex-shrink-0 px-2.5 py-1 bg-yellow-500/20 hover:bg-yellow-500/30 border border-yellow-500/40 rounded text-[11px] text-yellow-300 font-medium transition-colors disabled:opacity-50"
                      >{reconnecting ? 'Reconnecting…' : 'Reconnect'}</button>
                    </div>
                  )}
                  <ClusterPicker clusters={clusters} selected={selectedCluster} onSelect={selectCluster} />
              <FleetHealthSection onSelectCluster={(id) => { const c = clusters.find(cl => cl.id === id); if (c) selectCluster(c) }} />
                </>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {/* Section header + namespace filter */}
              <div className="flex items-center gap-3 flex-wrap">
                <h2 className="text-white font-semibold capitalize">{section === 'events' ? 'Live Events' : section}</h2>
                <span className="text-slate-500 text-sm">in</span>
                <span className="text-blue-400 text-sm">{selectedCluster.name}</span>

                {hasNamespaceFilter && (
                  <div className="ml-auto flex items-center gap-1.5">
                    <select
                      ref={nsSelectRef}
                      value={namespace}
                      onChange={e => setNamespace(e.target.value)}
                      className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-blue-500"
                    >
                      <option value="">All namespaces</option>
                      {namespaces.map(ns => <option key={ns} value={ns}>{ns}</option>)}
                    </select>
                    <kbd className="hidden sm:inline text-[9px] px-1 py-0.5 bg-slate-800 border border-slate-700 rounded text-slate-600 select-none">n</kbd>
                  </div>
                )}
              </div>

              <SectionErrorBoundary title={section}>
                {sectionContent()}
              </SectionErrorBoundary>
            </div>
          )}
        </div>
      </div>

      {showImportCluster && (
        <ImportKubeconfigModal
          onClose={() => setShowImportCluster(false)}
          onImported={loadClusters}
        />
      )}

      <FloatingAIPanel
        section={section}
        namespace={namespace}
        clusterId={clusterId}
      />
    </div>
  )
}
