import { useState, useEffect, useRef } from 'react'
import { Archive, Code2, Trash2, Eye, ChevronRight, Loader2, RefreshCw, Zap, Server, Activity, Settings, FileText, BotMessageSquare, Upload, Laptop, Terminal, Radar, TrendingUp } from 'lucide-react'
import { useToast } from './Toast'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface ScanSummary {
  id: string
  timestamp: string
  target: string
  query: string
  result: {
    readiness_score: number
    status: string
    risk_factors: { severity: string; message: string; component: string }[]
    recommendations: string[]
    explanation: string
    evidence: unknown[]
    raw_metrics: unknown
  }
}

const TARGET_LABELS: Record<string, string> = {
  vcf_readiness: 'VCF Readiness',
  capacity: 'Capacity',
  anomaly_detection: 'Anomaly Detection',
  network: 'Network',
}

const STATUS_COLORS: Record<string, string> = {
  READY: 'text-green-400 bg-green-900/20 border-green-800',
  WARNING: 'text-yellow-400 bg-yellow-900/20 border-yellow-800',
  NOT_READY: 'text-red-400 bg-red-900/20 border-red-800',
  UNKNOWN: 'text-slate-400 bg-slate-800/20 border-slate-700',
}

function fmt(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function ScoreRing({ score }: { score: number }) {
  const color = score >= 80 ? '#22c55e' : score >= 50 ? '#eab308' : '#ef4444'
  return (
    <div className="relative w-10 h-10 flex-shrink-0">
      <svg viewBox="0 0 36 36" className="rotate-[-90deg]">
        <circle cx="18" cy="18" r="15" fill="none" stroke="#1e293b" strokeWidth="3" />
        <circle cx="18" cy="18" r="15" fill="none" stroke={color} strokeWidth="3"
          strokeDasharray={`${(score / 100) * 94.2} 94.2`} strokeLinecap="round" />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-[9px] font-bold text-white">{score}</span>
      </div>
    </div>
  )
}

export function ArchivePage({
  onViewScan,
}: {
  onViewScan: (scan: ScanSummary) => void
}) {
  const toast = useToast()
  const [scans, setScans] = useState<ScanSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  const pendingDeletes = useRef(new Map<string, ReturnType<typeof setTimeout>>())

  async function load() {
    setLoading(true)
    try {
      const r = await fetch(`${API_BASE}/api/v1/scans`)
      const data = await r.json()
      setScans(data.scans || [])
    } catch {
      setScans([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // Optimistic delete with 5s Undo: remove from the list immediately, only
  // send the DELETE after the undo window closes.
  function handleDelete(id: string) {
    const scan = scans.find(s => s.id === id)
    setScans(s => s.filter(x => x.id !== id))
    const timer = setTimeout(async () => {
      pendingDeletes.current.delete(id)
      try {
        const r = await fetch(`${API_BASE}/api/v1/scans/${id}`, { method: 'DELETE' })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
      } catch {
        toast.error('Failed to delete scan — restored')
        if (scan) setScans(s => [...s, scan].sort((a, b) => b.timestamp.localeCompare(a.timestamp)))
      }
    }, 5000)
    pendingDeletes.current.set(id, timer)
    toast.info('Scan deleted', {
      duration: 5000,
      action: {
        label: 'Undo',
        onClick: () => {
          const t = pendingDeletes.current.get(id)
          if (t) { clearTimeout(t); pendingDeletes.current.delete(id) }
          if (scan) setScans(s => [...s, scan].sort((a, b) => b.timestamp.localeCompare(a.timestamp)))
        },
      },
    })
  }

  return (
    <div className="min-h-screen bg-vmware-dark">
      <main className="max-w-7xl mx-auto px-6 py-6">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
            <Archive size={14} className="text-blue-400" />
            Scan History
            <span className="text-slate-400 font-normal">· {scans.length} scan{scans.length !== 1 ? 's' : ''}</span>
          </h2>
          <button onClick={load} disabled={loading}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors disabled:opacity-40">
            {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh
          </button>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-blue-400" />
          </div>
        )}

        {!loading && scans.length === 0 && (
          <div className="rounded-xl border border-vmware-border bg-vmware-card p-12 flex flex-col items-center gap-3 text-center">
            <FileText size={32} className="text-slate-500" />
            <p className="text-slate-300 text-sm">No saved scans yet</p>
            <p className="text-slate-400 text-xs">Run an analysis and click "Save Scan" to archive it here</p>
          </div>
        )}

        <div className="space-y-3">
          {scans.map(scan => (
            <div key={scan.id} className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
              <div className="flex items-center gap-4 px-4 py-3">
                <ScoreRing score={scan.result.readiness_score} />

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-semibold text-white">
                      {TARGET_LABELS[scan.target] ?? scan.target}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS[scan.result.status] ?? STATUS_COLORS.UNKNOWN}`}>
                      {scan.result.status}
                    </span>
                    <span className="text-xs text-slate-400">{fmt(scan.timestamp)}</span>
                  </div>
                  <p className="text-xs text-slate-300 mt-0.5 truncate">{scan.query}</p>
                </div>

                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <button
                    onClick={() => onViewScan(scan)}
                    className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 border border-blue-800/50 hover:border-blue-600 px-2.5 py-1.5 rounded-lg transition-colors"
                  >
                    <Eye size={11} /> View
                  </button>
                  <button
                    onClick={() => setExpanded(expanded === scan.id ? null : scan.id)}
                    className="flex items-center gap-1 text-xs text-slate-400 hover:text-white border border-vmware-border px-2 py-1.5 rounded-lg transition-colors"
                  >
                    <ChevronRight size={11} className={`transition-transform ${expanded === scan.id ? 'rotate-90' : ''}`} />
                  </button>
                  <button
                    onClick={() => handleDelete(scan.id)}
                    aria-label="Delete scan"
                    className="flex items-center gap-1 text-xs text-slate-500 hover:text-red-400 border border-vmware-border hover:border-red-800/50 px-2 py-1.5 rounded-lg transition-colors"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>

              {expanded === scan.id && (
                <div className="border-t border-vmware-border px-4 py-3 space-y-2">
                  {scan.result.risk_factors.slice(0, 5).map((rf, i) => (
                    <div key={i} className="flex items-start gap-2 text-xs">
                      <span className={`flex-shrink-0 w-1.5 h-1.5 rounded-full mt-1 ${rf.severity === 'critical' ? 'bg-red-500' : rf.severity === 'warning' ? 'bg-yellow-500' : 'bg-blue-400'}`} />
                      <span className="text-slate-400">{rf.message}</span>
                    </div>
                  ))}
                  {scan.result.risk_factors.length === 0 && (
                    <p className="text-xs text-green-400">No risk factors identified</p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
