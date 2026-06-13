import { useState, useEffect, useRef } from 'react'
import {
  Terminal, Play, Copy, Trash2, Loader2, CheckCircle,
  AlertTriangle, ChevronDown, History, Sparkles, HelpCircle, Square,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''
const HISTORY_KEY = 'kubectl-history'
const MAX_HISTORY = 30

type Safety = 'read' | 'mutate' | 'destructive'
type Cluster = 'supervisor' | 'workload'

interface OutputLine {
  text: string
  type: 'stdout' | 'error' | 'meta'
}

function classifyCommand(cmd: string): Safety {
  const c = cmd.toLowerCase().trim()
  if (/kubectl\s+(delete|drain|cordon|uncordon|taint)\b/.test(c)) return 'destructive'
  if (/kubectl\s+(apply|create|patch|scale|rollout|set\s|replace|expose|annotate|label|edit|run\s)\b/.test(c)) return 'mutate'
  return 'read'
}

function SafetyBadge({ safety }: { safety: Safety }) {
  if (safety === 'read') return (
    <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full bg-green-900/40 text-green-400 border border-green-800/50 uppercase tracking-wider">
      <CheckCircle size={9} /> Read-only
    </span>
  )
  if (safety === 'mutate') return (
    <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-900/40 text-amber-400 border border-amber-800/50 uppercase tracking-wider">
      <AlertTriangle size={9} /> Mutating
    </span>
  )
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-900/40 text-red-400 border border-red-800/50 uppercase tracking-wider">
      <AlertTriangle size={9} /> Destructive
    </span>
  )
}

function ConfirmDialog({ command, onConfirm, onCancel }: { command: string; onConfirm: () => void; onCancel: () => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    cancelRef.current?.focus()
  }, [])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Tab') {
      e.preventDefault()
      if (document.activeElement === cancelRef.current) {
        confirmRef.current?.focus()
      } else {
        cancelRef.current?.focus()
      }
    }
    if (e.key === 'Escape') onCancel()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      onKeyDown={handleKeyDown}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
    >
      <div className="bg-[#0d1117] border border-red-800/60 rounded-xl p-6 max-w-md w-full mx-4 shadow-2xl">
        <div className="flex items-center gap-2.5 mb-4">
          <div className="w-8 h-8 rounded-lg bg-red-900/40 flex items-center justify-center">
            <AlertTriangle size={16} className="text-red-400" />
          </div>
          <h3 id="confirm-dialog-title" className="text-sm font-bold text-white">Destructive Command</h3>
        </div>
        <p className="text-xs text-slate-300 mb-3">This command may permanently modify or delete Kubernetes resources:</p>
        <pre className="text-xs font-mono text-red-300 bg-red-900/10 border border-red-900/30 rounded-lg p-3 mb-5 break-all whitespace-pre-wrap">{command}</pre>
        <div className="flex gap-2 justify-end">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="px-4 py-2 text-xs text-slate-400 hover:text-white border border-vmware-border rounded-lg hover:border-slate-500 transition-colors"
          >
            Cancel
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className="px-4 py-2 text-xs font-semibold text-white bg-red-700 hover:bg-red-600 rounded-lg transition-colors flex items-center gap-1.5"
          >
            <Play size={10} /> Confirm & Run
          </button>
        </div>
      </div>
    </div>
  )
}

type QuickAction = { label: string; icon: string; cmd: (ns: string) => string; section: string }

const QUICK_ACTIONS: Record<Cluster, QuickAction[]> = {
  supervisor: [
    // ── Cluster health ──────────────────────────────────────────────────────
    { section: 'Cluster', label: 'Nodes',         icon: '🖧',  cmd: () => `kubectl get nodes -o wide` },
    { section: 'Cluster', label: 'Node Capacity',  icon: '📊', cmd: () => `kubectl get nodes -o custom-columns='NAME:.metadata.name,CPU:.status.capacity.cpu,MEMORY:.status.capacity.memory,PODS:.status.capacity.pods'` },
    { section: 'Cluster', label: 'Top Nodes',      icon: '📈', cmd: () => `kubectl top nodes` },
    { section: 'Cluster', label: 'Cordoned',       icon: '🚫', cmd: () => `kubectl get nodes --field-selector=spec.unschedulable=true` },
    { section: 'Cluster', label: 'Namespaces',     icon: '📁', cmd: () => `kubectl get namespaces` },
    // ── VKS ─────────────────────────────────────────────────────────────────
    { section: 'VKS',     label: 'VK Clusters',    icon: '☸️', cmd: () => `kubectl get vspherekubernetesclusters -A -o wide` },
    { section: 'VKS',     label: 'CPI Pods',       icon: '⚡', cmd: () => `kubectl get pods -n vmware-system-cpi -o wide` },
    { section: 'VKS',     label: 'CSI Pods',       icon: '💾', cmd: () => `kubectl get pods -n vmware-system-csi -o wide` },
    // ── Events & storage ────────────────────────────────────────────────────
    { section: 'Events',  label: 'All Warnings',   icon: '⚠️', cmd: () => `kubectl get events -A --field-selector=type=Warning --sort-by=.lastTimestamp` },
    { section: 'Events',  label: 'Failed Pods',    icon: '🔴', cmd: () => `kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded` },
    { section: 'Storage', label: 'PVs',            icon: '🗄️', cmd: () => `kubectl get pv -o wide` },
    { section: 'Storage', label: 'StorageClasses', icon: '📦', cmd: () => `kubectl get storageclasses` },
  ],
  workload: [
    // ── Pods ─────────────────────────────────────────────────────────────────
    { section: 'Pods',    label: 'All Pods',       icon: '⚡', cmd: (ns) => `kubectl get pods -n ${ns} -o wide` },
    { section: 'Pods',    label: 'By Restarts',    icon: '🔁', cmd: (ns) => `kubectl get pods -n ${ns} --sort-by='.status.containerStatuses[0].restartCount'` },
    { section: 'Pods',    label: 'Pending',        icon: '🕐', cmd: (ns) => `kubectl get pods -n ${ns} --field-selector=status.phase=Pending -o wide` },
    { section: 'Pods',    label: 'Failed',         icon: '🔴', cmd: (ns) => `kubectl get pods -n ${ns} --field-selector=status.phase!=Running,status.phase!=Succeeded` },
    // ── Events ───────────────────────────────────────────────────────────────
    { section: 'Events',  label: 'Warnings',       icon: '⚠️', cmd: (ns) => `kubectl get events -n ${ns} --field-selector=type=Warning --sort-by=.lastTimestamp` },
    { section: 'Events',  label: 'All Events',     icon: '📋', cmd: (ns) => `kubectl get events -n ${ns} --sort-by=.lastTimestamp` },
    // ── Resources ────────────────────────────────────────────────────────────
    { section: 'Resources', label: 'CPU Hogs',     icon: '🔥', cmd: (ns) => `kubectl top pods -n ${ns} --sort-by=cpu` },
    { section: 'Resources', label: 'Mem Hogs',     icon: '🧠', cmd: (ns) => `kubectl top pods -n ${ns} --sort-by=memory` },
    // ── Workloads ────────────────────────────────────────────────────────────
    { section: 'Workloads', label: 'All Workloads',icon: '🚀', cmd: (ns) => `kubectl get deployments,statefulsets,daemonsets -n ${ns}` },
    { section: 'Workloads', label: 'Rollout Status',icon: '🔄', cmd: (ns) => `kubectl rollout status deployment -n ${ns}` },
    { section: 'Workloads', label: 'HPA',          icon: '📐', cmd: (ns) => `kubectl get hpa -n ${ns}` },
    { section: 'Workloads', label: 'Jobs',         icon: '⚙️', cmd: (ns) => `kubectl get jobs,cronjobs -n ${ns}` },
    // ── Networking ───────────────────────────────────────────────────────────
    { section: 'Network', label: 'Svc+Endpoints',  icon: '🌐', cmd: (ns) => `kubectl get svc,endpoints -n ${ns}` },
    { section: 'Network', label: 'Ingress',        icon: '🔀', cmd: (ns) => `kubectl get ingress -n ${ns} -o wide` },
    { section: 'Network', label: 'NetPolicies',    icon: '🛡️', cmd: (ns) => `kubectl get networkpolicies -n ${ns}` },
    // ── Storage ──────────────────────────────────────────────────────────────
    { section: 'Storage', label: 'PVCs',           icon: '🗄️', cmd: (ns) => `kubectl get pvc -n ${ns} -o wide` },
    { section: 'Storage', label: 'ConfigMaps',     icon: '📝', cmd: (ns) => `kubectl get configmaps -n ${ns}` },
    { section: 'Storage', label: 'Secrets',        icon: '🔑', cmd: (ns) => `kubectl get secrets -n ${ns}` },
  ],
}

export function KubectlPage() {
  const [cluster, setCluster] = useState<Cluster>('supervisor')
  const [nl, setNl] = useState('')
  const [command, setCommand] = useState('')
  const [safety, setSafety] = useState<Safety>('read')
  const [namespace, setNamespace] = useState('vcf-ai-ops')
  const [namespaces, setNamespaces] = useState<string[]>(['vcf-ai-ops'])
  const [nsOpen, setNsOpen] = useState(false)
  const [lines, setLines] = useState<OutputLine[]>([])
  const [runOnAll, setRunOnAll] = useState(false)
  const [supervisorLines, setSupervisorLines] = useState<OutputLine[]>([])
  const [workloadLines, setWorkloadLines] = useState<OutputLine[]>([])
  const [running, setRunning] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [explanation, setExplanation] = useState('')
  const [explaining, setExplaining] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [history, setHistory] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') } catch { return [] }
  })
  const [showHistory, setShowHistory] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copiedOutput, setCopiedOutput] = useState(false)
  const terminalRef = useRef<HTMLDivElement>(null)
  const supervisorTermRef = useRef<HTMLDivElement>(null)
  const workloadTermRef = useRef<HTMLDivElement>(null)
  const runAbortRef = useRef<AbortController | null>(null)
  const explainAbortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/kubectl/namespaces?cluster=${cluster}`)
      .then(r => r.json())
      .then(d => {
        if (d.namespaces?.length) {
          setNamespaces(d.namespaces)
          if (!d.namespaces.includes(namespace)) setNamespace(d.namespaces[0])
        }
      })
      .catch(() => {})
  }, [cluster]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight
  }, [lines])

  useEffect(() => {
    if (supervisorTermRef.current) supervisorTermRef.current.scrollTop = supervisorTermRef.current.scrollHeight
  }, [supervisorLines])

  useEffect(() => {
    if (workloadTermRef.current) workloadTermRef.current.scrollTop = workloadTermRef.current.scrollHeight
  }, [workloadLines])

  function updateCommand(cmd: string) {
    setCommand(cmd)
    setSafety(classifyCommand(cmd))
    setExplanation('')
  }

  async function handleGenerate() {
    if (!nl.trim()) return
    setGenerating(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/kubectl/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: nl.trim(), cluster, namespace }),
      })
      const data = await resp.json()
      if (data.command) updateCommand(data.command)
    } catch (e) {
      setLines(prev => [...prev, { text: `Generate failed: ${e}`, type: 'error' }])
    } finally {
      setGenerating(false)
    }
  }

  function handleRunClick() {
    if (!command.trim()) return
    if (safety === 'destructive') {
      setShowConfirm(true)
    } else {
      executeCommand()
    }
  }

  async function executeCommand() {
    setShowConfirm(false)
    if (!command.trim()) return
    runAbortRef.current?.abort()
    const ctrl = new AbortController()
    runAbortRef.current = ctrl

    setRunning(true)
    setExplanation('')

    const newHistory = [command, ...history.filter(h => h !== command)].slice(0, MAX_HISTORY)
    setHistory(newHistory)
    localStorage.setItem(HISTORY_KEY, JSON.stringify(newHistory))

    if (runOnAll) {
      setSupervisorLines([{ text: `$ ${command}`, type: 'meta' }])
      setWorkloadLines([{ text: `$ ${command}`, type: 'meta' }])
      const exitCodes: Record<string, number> = {}
      try {
        const resp = await fetch(`${API_BASE}/api/v1/kubectl/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command, cluster: 'supervisor', run_on_all: true }),
          signal: ctrl.signal,
        })
        if (!resp.body) throw new Error('No response body')
        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          const parts = buf.split('\n\n')
          buf = parts.pop() ?? ''
          for (const part of parts) {
            const raw = part.trim()
            if (!raw.startsWith('data:')) continue
            try {
              const ev = JSON.parse(raw.slice(5).trim())
              const setter = ev.cluster === 'workload' ? setWorkloadLines : setSupervisorLines
              if (ev.type === 'line') {
                setter(prev => [...prev, { text: ev.text, type: 'stdout' }])
              } else if (ev.type === 'error') {
                setter(prev => [...prev, { text: ev.text, type: 'error' }])
              } else if (ev.type === 'done') {
                exitCodes[ev.cluster] = ev.exit_code
                const code = ev.exit_code
                setter(prev => [...prev, { text: code === 0 ? '─── exit 0 ───' : `─── exit ${code} ───`, type: code === 0 ? 'meta' : 'error' }])
              }
            } catch { /* skip malformed */ }
          }
        }
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          const msg = `Error: ${err}`
          setSupervisorLines(prev => [...prev, { text: msg, type: 'error' }])
          setWorkloadLines(prev => [...prev, { text: msg, type: 'error' }])
        }
      } finally {
        setRunning(false)
      }
      return
    }

    setLines([{ text: `$ ${command}`, type: 'meta' }])
    try {
      const resp = await fetch(`${API_BASE}/api/v1/kubectl/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command, cluster }),
        signal: ctrl.signal,
      })
      if (!resp.body) throw new Error('No response body')
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let exitCode = 0
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() ?? ''
        for (const part of parts) {
          const line = part.trim()
          if (!line.startsWith('data:')) continue
          try {
            const ev = JSON.parse(line.slice(5).trim())
            if (ev.type === 'line') {
              setLines(prev => [...prev, { text: ev.text, type: 'stdout' }])
            } else if (ev.type === 'done') {
              exitCode = ev.exit_code
            }
          } catch { /* skip malformed */ }
        }
      }
      setLines(prev => [
        ...prev,
        { text: exitCode === 0 ? '─── exit 0 ───' : `─── exit ${exitCode} ───`, type: exitCode === 0 ? 'meta' : 'error' },
      ])
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setLines(prev => [...prev, { text: `Error: ${err}`, type: 'error' }])
      }
    } finally {
      setRunning(false)
    }
  }

  async function handleExplain() {
    if (!lines.length) return
    explainAbortRef.current?.abort()
    const ctrl = new AbortController()
    explainAbortRef.current = ctrl

    const output = lines
      .filter(l => l.type === 'stdout')
      .map(l => l.text)
      .join('\n')
      .slice(0, 3000)

    setExplaining(true)
    setExplanation('')

    try {
      const resp = await fetch(`${API_BASE}/api/v1/kubectl/explain`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command, output }),
        signal: explainAbortRef.current.signal,
      })
      if (!resp.body) throw new Error('No body')
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() ?? ''
        for (const part of parts) {
          const line = part.trim()
          if (!line.startsWith('data:')) continue
          try {
            const ev = JSON.parse(line.slice(5).trim())
            if (ev.type === 'token') setExplanation(prev => prev + ev.text)
          } catch { /* skip */ }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setExplanation(`Explain failed: ${err}`)
      }
    } finally {
      setExplaining(false)
    }
  }

  function handleCopyCommand() {
    navigator.clipboard.writeText(command)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  function handleCopyOutput() {
    const text = lines.map(l => l.text).join('\n')
    navigator.clipboard.writeText(text)
    setCopiedOutput(true)
    setTimeout(() => setCopiedOutput(false), 1500)
  }

  function handleStop() {
    runAbortRef.current?.abort()
    setRunning(false)
    const interrupted = { text: '─── interrupted ───', type: 'meta' as const }
    if (runOnAll) {
      setSupervisorLines(prev => [...prev, interrupted])
      setWorkloadLines(prev => [...prev, interrupted])
    } else {
      setLines(prev => [...prev, interrupted])
    }
  }

  return (
    <div className="h-full bg-[#0a0e17] flex flex-col overflow-hidden">
      {showConfirm && (
        <ConfirmDialog
          command={command}
          onConfirm={executeCommand}
          onCancel={() => setShowConfirm(false)}
        />
      )}

      {/* 3-column body */}
      <div className="flex flex-1 overflow-hidden max-w-[1900px] mx-auto w-full px-4 py-4 gap-4">

        {/* ── LEFT: controls ── */}
        <div className="w-72 flex-shrink-0 flex flex-col gap-3 overflow-y-auto pr-1">

          {/* 1. Cluster toggle */}
          <div className="rounded-xl border border-vmware-border bg-vmware-card p-1 flex gap-1 flex-shrink-0">
            {(['supervisor', 'workload'] as Cluster[]).map(c => (
              <button
                key={c}
                onClick={() => { setCluster(c); setRunOnAll(false); setLines([]); setExplanation('') }}
                className={`flex-1 py-2 text-xs font-semibold rounded-lg transition-colors capitalize ${
                  !runOnAll && cluster === c
                    ? c === 'supervisor'
                      ? 'bg-blue-700/60 text-blue-200 border border-blue-600/50'
                      : 'bg-green-700/60 text-green-200 border border-green-600/50'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                }`}
              >
                {c === 'supervisor' ? '☁ Supervisor' : '⚙ Workload'}
              </button>
            ))}
          </div>

          {/* Broadcast toggle */}
          <button
            onClick={() => { setRunOnAll(v => !v); setSupervisorLines([]); setWorkloadLines([]) }}
            className={`flex items-center justify-center gap-2 w-full py-2 text-xs font-semibold rounded-xl border transition-colors ${
              runOnAll
                ? 'bg-purple-900/40 text-purple-200 border-purple-700/60 hover:bg-purple-900/60'
                : 'text-slate-400 border-vmware-border hover:text-slate-200 hover:bg-slate-800/40'
            }`}
            aria-label={runOnAll ? 'Disable broadcast mode' : 'Enable broadcast — run on all clusters'}
          >
            <span className="text-[11px]">⬡</span>
            {runOnAll ? 'Broadcast ON — all clusters' : 'Broadcast (all clusters)'}
          </button>

          {/* 2. Namespace picker — workload only */}
          {cluster === 'workload' && (
            <div className="rounded-xl border border-vmware-border bg-vmware-card p-3 flex-shrink-0">
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Namespace</p>
              <div className="relative">
                <button
                  onClick={() => setNsOpen(o => !o)}
                  className="w-full flex items-center justify-between text-xs text-slate-200 bg-slate-800/60 border border-vmware-border hover:border-slate-500 px-3 py-2 rounded-lg transition-colors"
                >
                  <span className="font-mono">{namespace}</span>
                  <ChevronDown size={12} className="text-slate-400" />
                </button>
                {nsOpen && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setNsOpen(false)} />
                    <div className="absolute top-full left-0 right-0 mt-1 bg-[#0d1117] border border-vmware-border rounded-lg shadow-xl z-20 max-h-64 overflow-y-auto">
                      {namespaces.map(ns => (
                        <button
                          key={ns}
                          onClick={() => { setNamespace(ns); setNsOpen(false) }}
                          className={`w-full text-left text-xs px-3 py-2 hover:bg-slate-800/60 font-mono transition-colors ${ns === namespace ? 'text-green-400' : 'text-slate-300'}`}
                        >
                          {ns}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {/* 3. Command editor */}
          <div className="rounded-xl border border-vmware-border bg-vmware-card p-3 flex flex-col gap-2 flex-shrink-0">
            <div className="flex items-center justify-between">
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Command</p>
              {command && <SafetyBadge safety={safety} />}
            </div>
            <textarea
              value={command}
              onChange={e => updateCommand(e.target.value)}
              rows={4}
              spellCheck={false}
              placeholder={cluster === 'supervisor' ? 'kubectl get nodes -o wide' : `kubectl get pods -n ${namespace}`}
              className="w-full bg-[#0d1117] border border-green-900/40 rounded-lg px-3 py-2 text-xs font-mono text-green-300 placeholder-green-900/60 resize-none focus:outline-none focus:border-green-700 transition-colors"
            />
            <div className="flex gap-2">
              <button
                onClick={handleRunClick}
                disabled={running || !command.trim()}
                className={`flex-1 flex items-center justify-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  safety === 'destructive'
                    ? 'text-white bg-red-700/80 hover:bg-red-600/80'
                    : safety === 'mutate'
                    ? 'text-white bg-amber-700/70 hover:bg-amber-600/70'
                    : 'text-white bg-green-700/80 hover:bg-green-600/80'
                }`}
              >
                {running ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
                {running ? 'Running…' : 'Run'}
              </button>
              <button
                onClick={handleCopyCommand}
                disabled={!command.trim()}
                className="px-3 py-2 text-xs text-slate-400 hover:text-white border border-vmware-border rounded-lg hover:border-slate-500 transition-colors disabled:opacity-40"
                title="Copy command"
              >
                {copied ? <CheckCircle size={12} className="text-green-400" /> : <Copy size={12} />}
              </button>
            </div>
          </div>

          {/* 4. AI command generator */}
          <div className="rounded-xl border border-vmware-border bg-vmware-card p-3 flex-shrink-0">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
              <Sparkles size={9} /> AI Generator
            </p>
            <textarea
              value={nl}
              onChange={e => setNl(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleGenerate() }}
              placeholder="e.g. show pods that keep restarting…"
              rows={3}
              className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-600 resize-none focus:outline-none focus:border-green-700 transition-colors"
            />
            <button
              onClick={handleGenerate}
              disabled={generating || !nl.trim()}
              className="mt-2 w-full flex items-center justify-center gap-1.5 text-xs font-semibold text-white bg-green-700/80 hover:bg-green-600/80 disabled:opacity-50 disabled:cursor-not-allowed px-3 py-2 rounded-lg transition-colors"
            >
              {generating ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
              {generating ? 'Generating…' : 'Generate Command'}
            </button>
          </div>

          {/* 5. History — bottom of left panel */}
          {history.length > 0 && (
            <div className="rounded-xl border border-vmware-border bg-vmware-card p-3">
              <button
                onClick={() => setShowHistory(h => !h)}
                className="w-full flex items-center justify-between text-[10px] font-bold text-slate-500 uppercase tracking-widest hover:text-slate-400 transition-colors"
              >
                <span className="flex items-center gap-1.5"><History size={9} /> History ({history.length})</span>
                <ChevronDown size={10} className={`transition-transform ${showHistory ? 'rotate-180' : ''}`} />
              </button>
              {showHistory && (
                <div className="mt-2 space-y-1 max-h-44 overflow-y-auto">
                  {history.map((h, i) => (
                    <button
                      key={i}
                      onClick={() => updateCommand(h)}
                      className="w-full text-left text-[10px] font-mono text-slate-400 hover:text-green-300 truncate px-2 py-1 rounded hover:bg-slate-800/40 transition-colors"
                      title={h}
                    >
                      {h}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── CENTER: terminal + always-visible AI explanation ── */}
        <div className="flex-1 min-w-0 flex flex-col gap-3 overflow-hidden">

          {/* Terminal — single or broadcast two-column */}
          {runOnAll ? (
            <div className="flex-1 flex gap-3 min-h-0">
              {([
                { label: '☁ Supervisor', linesState: supervisorLines, ref: supervisorTermRef, color: 'blue' },
                { label: '⚙ Workload',   linesState: workloadLines,   ref: workloadTermRef,   color: 'green' },
              ] as const).map(({ label, linesState, ref, color }) => (
                <div key={label} className={`flex-1 rounded-xl border ${color === 'blue' ? 'border-blue-900/40' : 'border-green-900/40'} bg-[#0d1117] flex flex-col overflow-hidden min-h-0`}>
                  <div className={`flex items-center justify-between px-3 py-2 border-b ${color === 'blue' ? 'border-blue-900/30' : 'border-green-900/30'} bg-[#0a0e17] flex-shrink-0`}>
                    <div className="flex items-center gap-2">
                      <div className="flex gap-1">
                        <div className="w-2 h-2 rounded-full bg-red-500/70" />
                        <div className="w-2 h-2 rounded-full bg-yellow-500/70" />
                        <div className="w-2 h-2 rounded-full bg-green-500/70" />
                      </div>
                      <span className={`text-[10px] font-mono ${color === 'blue' ? 'text-blue-900/80' : 'text-green-900/80'} ml-1`}>{label}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      {running && <button onClick={handleStop} aria-label="Stop" className="flex items-center gap-1 text-[10px] text-red-400 hover:text-red-300 px-1.5 py-0.5 rounded border border-red-900/40 transition-colors"><Square size={8} /></button>}
                      <button
                        onClick={() => color === 'blue' ? setSupervisorLines([]) : setWorkloadLines([])}
                        aria-label="Clear terminal"
                        className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-red-400 px-1.5 py-0.5 rounded border border-vmware-border transition-colors"
                      >
                        <Trash2 size={8} />
                      </button>
                    </div>
                  </div>
                  <div
                    ref={ref}
                    className="flex-1 font-mono text-xs p-3 overflow-y-auto"
                    style={{ fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace" }}
                  >
                    {linesState.length === 0 ? (
                      <div className="flex items-center justify-center h-full">
                        <p className={`text-xs ${color === 'blue' ? 'text-blue-900/60' : 'text-green-900/60'}`}>waiting…</p>
                      </div>
                    ) : (
                      linesState.map((line, i) => (
                        <div key={i} className={line.type === 'meta' ? `${color === 'blue' ? 'text-blue-600/80' : 'text-green-600/80'} select-none mb-1` : line.type === 'error' ? 'text-red-400' : `${color === 'blue' ? 'text-blue-200' : 'text-green-300'}`}>
                          {line.text || ' '}
                        </div>
                      ))
                    )}
                    {running && <div className={`inline-block w-1.5 h-3 ${color === 'blue' ? 'bg-blue-500' : 'bg-green-500'} animate-pulse mt-1`} />}
                  </div>
                </div>
              ))}
            </div>
          ) : (
          <div className="flex-1 rounded-xl border border-green-900/40 bg-[#0d1117] flex flex-col overflow-hidden min-h-0">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-green-900/30 bg-[#0a0e17] flex-shrink-0">
              <div className="flex items-center gap-2">
                <div className="flex gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-red-500/70" />
                  <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/70" />
                  <div className="w-2.5 h-2.5 rounded-full bg-green-500/70" />
                </div>
                <span className="text-[10px] font-mono text-green-900/80 ml-2">
                  {cluster === 'supervisor' ? 'supervisor-cluster' : `supervisor-cluster / ${namespace}`} ~ kubectl
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                {running && (
                  <button
                    onClick={handleStop}
                    aria-label="Stop command"
                    className="flex items-center gap-1 text-[10px] text-red-400 hover:text-red-300 px-2 py-1 rounded border border-red-900/40 hover:border-red-700 transition-colors"
                  >
                    <Square size={9} /> Stop
                  </button>
                )}
                {lines.length > 0 && (
                  <>
                    <button
                      onClick={handleExplain}
                      disabled={explaining || running}
                      className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-yellow-300 px-2 py-1 rounded border border-vmware-border hover:border-yellow-800/60 transition-colors disabled:opacity-40"
                      aria-label="Explain output with AI"
                    >
                      {explaining ? <Loader2 size={9} className="animate-spin" /> : <HelpCircle size={9} />}
                      {explaining ? 'Explaining…' : 'Explain'}
                    </button>
                    <button
                      onClick={handleCopyOutput}
                      aria-label="Copy output"
                      className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 px-2 py-1 rounded border border-vmware-border hover:border-slate-600 transition-colors"
                    >
                      {copiedOutput ? <CheckCircle size={9} className="text-green-400" /> : <Copy size={9} />}
                      {copiedOutput ? 'Copied' : 'Copy'}
                    </button>
                  </>
                )}
                <button
                  onClick={() => { setLines([]); setExplanation('') }}
                  aria-label="Clear terminal"
                  className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-red-400 px-2 py-1 rounded border border-vmware-border hover:border-red-900/50 transition-colors"
                >
                  <Trash2 size={9} /> Clear
                </button>
              </div>
            </div>

            <div
              ref={terminalRef}
              className="flex-1 font-mono text-xs p-4 overflow-y-auto"
              style={{ fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace" }}
            >
              {lines.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center gap-3 py-12">
                  <Terminal size={32} className="text-green-900/60" />
                  <p className="text-green-900/70 text-xs">Ready — pick a quick action or describe a command</p>
                </div>
              ) : (
                lines.map((line, i) => (
                  <div
                    key={i}
                    className={
                      line.type === 'meta'
                        ? 'text-green-600/80 select-none mb-1'
                        : line.type === 'error'
                        ? 'text-red-400'
                        : 'text-green-300'
                    }
                  >
                    {line.text || ' '}
                  </div>
                ))
              )}
              {running && (
                <div className="flex items-center gap-1.5 text-green-600/70 mt-1">
                  <span className="inline-block w-1.5 h-3 bg-green-500 animate-pulse" />
                </div>
              )}
            </div>
          </div>
          )}

          {/* AI Explanation — always visible, fixed height */}
          <div className="h-44 flex-shrink-0 rounded-xl border border-yellow-900/30 bg-[#0d0f0a] flex flex-col overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-yellow-900/20 flex-shrink-0">
              <HelpCircle size={11} className="text-yellow-500/70" />
              <span className="text-[10px] font-bold text-yellow-500/60 uppercase tracking-widest">AI Explanation</span>
              {explaining && <span className="ml-auto text-[10px] text-yellow-400 animate-pulse">● thinking…</span>}
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-3">
              {explanation ? (
                <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">
                  {explanation}
                  {explaining && <span className="inline-block w-1.5 h-3.5 bg-yellow-400 animate-pulse ml-0.5 align-middle" />}
                </p>
              ) : (
                <p className="text-xs text-slate-600 italic">
                  {explaining
                    ? <span className="text-yellow-500/60">Analyzing output…</span>
                    : 'Run a command, then click Explain to get an AI analysis of the output.'}
                </p>
              )}
            </div>
          </div>
        </div>

        {/* ── RIGHT: quick actions ── */}
        <div className="w-56 flex-shrink-0 overflow-y-auto">
          <div className="rounded-xl border border-vmware-border bg-vmware-card p-3">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-3">Quick Actions</p>
            {(() => {
              const actions = QUICK_ACTIONS[cluster]
              const sections = [...new Set(actions.map(a => a.section))]
              return sections.map(sec => (
                <div key={sec} className="mb-3">
                  <p className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-1.5">{sec}</p>
                  <div className="flex flex-col gap-1">
                    {actions.filter(a => a.section === sec).map(qa => (
                      <button
                        key={qa.label}
                        onClick={() => updateCommand(qa.cmd(namespace))}
                        className="text-[11px] text-slate-300 hover:text-white bg-slate-800/60 hover:bg-slate-700/60 border border-vmware-border hover:border-slate-600 px-2 py-1.5 rounded-lg transition-colors text-left flex items-center gap-1.5 leading-tight"
                      >
                        <span className="text-[10px] flex-shrink-0">{qa.icon}</span>
                        <span className="truncate">{qa.label}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))
            })()}
          </div>
        </div>

      </div>
    </div>
  )
}
