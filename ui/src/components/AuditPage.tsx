import { useState, useEffect, useCallback } from 'react'
import { Search, RefreshCw, ShieldCheck, ChevronLeft, ChevronRight, Activity } from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface AuditEntry {
  id: number
  ts: string
  user_id: string
  source_ip: string
  action: string
  resource: string
  status_code: number
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

interface HeatmapCell { weekday: number; hour: number; count: number }

function AuditHeatmap() {
  const [cells, setCells] = useState<HeatmapCell[]>([])
  const [maxCount, setMaxCount] = useState(0)
  const [tooltip, setTooltip] = useState<{ text: string; x: number; y: number } | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/audit/heatmap`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) { setCells(d.cells); setMaxCount(d.max_count) } })
      .catch(() => {})
  }, [])

  if (!cells.length) return null

  function cellColor(count: number): string {
    if (!count || !maxCount) return 'bg-slate-800/60'
    const ratio = count / maxCount
    if (ratio > 0.75) return 'bg-red-500'
    if (ratio > 0.5)  return 'bg-orange-500'
    if (ratio > 0.25) return 'bg-yellow-500'
    if (ratio > 0.1)  return 'bg-blue-500/70'
    return 'bg-blue-900/60'
  }

  const byKey = Object.fromEntries(cells.map(c => [`${c.weekday}-${c.hour}`, c.count]))

  return (
    <div className="rounded-xl border border-slate-700/50 bg-slate-800/20 p-4">
      <div className="flex items-center gap-2 mb-4">
        <Activity size={13} className="text-slate-400" />
        <span className="text-xs font-semibold text-slate-300">Activity Heatmap — last 90 days (UTC)</span>
        <span className="text-[10px] text-slate-500 ml-auto">darker = more events</span>
      </div>
      <div className="flex gap-1.5">
        {/* Day labels */}
        <div className="flex flex-col gap-0.5 mr-1">
          <div className="h-4" />
          {DAYS.map(d => (
            <div key={d} className="h-3 flex items-center">
              <span className="text-[9px] text-slate-600 w-6 text-right">{d}</span>
            </div>
          ))}
        </div>
        {/* Grid: columns = hours 0–23, rows = weekdays 0–6 */}
        {Array.from({ length: 24 }, (_, h) => (
          <div key={h} className="flex flex-col gap-0.5">
            <div className="h-4 flex items-end justify-center">
              {h % 3 === 0 && <span className="text-[9px] text-slate-600">{String(h).padStart(2, '0')}</span>}
            </div>
            {Array.from({ length: 7 }, (_, d) => {
              const count = byKey[`${d}-${h}`] ?? 0
              return (
                <div
                  key={d}
                  className={`w-3 h-3 rounded-sm cursor-default transition-opacity hover:opacity-80 ${cellColor(count)}`}
                  onMouseEnter={e => setTooltip({ text: `${DAYS[d]} ${String(h).padStart(2,'0')}:00 — ${count} event${count !== 1 ? 's' : ''}`, x: e.clientX, y: e.clientY })}
                  onMouseLeave={() => setTooltip(null)}
                />
              )
            })}
          </div>
        ))}
      </div>
      {tooltip && (
        <div
          className="fixed z-50 bg-slate-900 border border-slate-700 text-[10px] text-slate-300 px-2 py-1 rounded pointer-events-none shadow-lg"
          style={{ left: tooltip.x + 12, top: tooltip.y - 8 }}
        >
          {tooltip.text}
        </div>
      )}
      <div className="flex items-center gap-2 mt-3">
        <span className="text-[9px] text-slate-600">Low</span>
        {['bg-slate-800/60', 'bg-blue-900/60', 'bg-blue-500/70', 'bg-yellow-500', 'bg-orange-500', 'bg-red-500'].map(c => (
          <div key={c} className={`w-3 h-3 rounded-sm ${c}`} />
        ))}
        <span className="text-[9px] text-slate-600">High</span>
      </div>
    </div>
  )
}

function statusColor(code: number) {
  if (code >= 500) return 'text-red-400'
  if (code >= 400) return 'text-amber-400'
  return 'text-green-400'
}

function methodColor(action: string) {
  const m = action.split(' ')[0]
  if (m === 'DELETE') return 'bg-red-900/40 text-red-300'
  if (m === 'POST' || m === 'PATCH' || m === 'PUT') return 'bg-amber-900/40 text-amber-300'
  return 'bg-slate-700/60 text-slate-300'
}

function formatTs(ts: string) {
  const d = new Date(ts)
  return d.toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'medium', hour12: false })
}

const PAGE_SIZE = 50

export function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [filterUser, setFilterUser] = useState('')
  const [filterAction, setFilterAction] = useState('')
  const [page, setPage] = useState(0)
  const [draftUser, setDraftUser] = useState('')
  const [draftAction, setDraftAction] = useState('')

  const load = useCallback(async (userF: string, actionF: string, p: number) => {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(p * PAGE_SIZE),
      })
      if (userF)   params.set('user', userF)
      if (actionF) params.set('action', actionF)
      const r = await fetch(`${API_BASE}/api/v1/audit?${params}`)
      const d = await r.json()
      if (d.error) { setError(d.error); setEntries([]); setTotal(0) }
      else { setEntries(d.entries || []); setTotal(d.total || 0) }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(filterUser, filterAction, page) }, [load, filterUser, filterAction, page])

  function applyFilters() {
    setFilterUser(draftUser)
    setFilterAction(draftAction)
    setPage(0)
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="min-h-screen bg-[#0a0e17] text-white p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <ShieldCheck size={20} className="text-blue-400" />
        <div>
          <h1 className="text-lg font-semibold">Audit Log</h1>
          <p className="text-xs text-slate-400">Every config change, analysis run, kubectl command, and workspace execution</p>
        </div>
        <button
          onClick={() => load(filterUser, filterAction, page)}
          className="ml-auto p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Heatmap */}
      <AuditHeatmap />

      {/* Filters */}
      <div className="flex gap-3">
        <div className="relative flex-1">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={draftUser}
            onChange={e => setDraftUser(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && applyFilters()}
            placeholder="Filter by user…"
            className="w-full pl-8 pr-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div className="relative flex-1">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={draftAction}
            onChange={e => setDraftAction(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && applyFilters()}
            placeholder="Filter by action (e.g. POST /api/v1/config)…"
            className="w-full pl-8 pr-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
          />
        </div>
        <button
          onClick={applyFilters}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg font-medium"
        >
          Search
        </button>
      </div>

      {/* Stats bar */}
      <div className="text-xs text-slate-400">
        {total.toLocaleString()} event{total !== 1 ? 's' : ''} total
        {(filterUser || filterAction) && ' (filtered)'}
        {total > 0 && ` · showing ${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, total)}`}
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg bg-red-900/30 border border-red-700/40 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="rounded-xl border border-slate-700/50 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700/50 bg-slate-800/60">
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider w-40">Time</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider w-32">User</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider">Action</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider w-32">Source IP</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider w-16">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/30">
            {loading && entries.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">Loading…</td></tr>
            )}
            {!loading && entries.length === 0 && !error && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">No events yet — activity will appear here as you use the platform.</td></tr>
            )}
            {entries.map(e => (
              <tr key={e.id} className="hover:bg-slate-800/40 transition-colors">
                <td className="px-4 py-2.5 font-mono text-xs text-slate-400 whitespace-nowrap">{formatTs(e.ts)}</td>
                <td className="px-4 py-2.5">
                  <span className="text-slate-200 font-medium truncate block max-w-[120px]" title={e.user_id}>{e.user_id}</span>
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-2">
                    <span className={`px-1.5 py-0.5 rounded text-xs font-mono font-semibold ${methodColor(e.action)}`}>
                      {e.action.split(' ')[0]}
                    </span>
                    <span className="font-mono text-xs text-slate-300 truncate">{e.action.split(' ').slice(1).join(' ')}</span>
                  </div>
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-slate-400">{e.source_ip || '—'}</td>
                <td className="px-4 py-2.5">
                  <span className={`font-mono text-xs font-semibold ${statusColor(e.status_code)}`}>{e.status_code}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            className="p-1.5 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-30"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-sm text-slate-400">Page {page + 1} of {totalPages}</span>
          <button
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="p-1.5 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-30"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}
    </div>
  )
}
