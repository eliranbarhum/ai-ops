import { useState, useEffect, useRef, useCallback } from 'react'
import {
  ShieldAlert, Play, Square, Trash2, RefreshCw, ChevronDown, ChevronUp,
  Clock, AlertTriangle, Info, Zap, Target, FileSearch, X, Copy, Check,
  Calendar,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface ScopeProfile {
  id?: string
  label: string
  description: string
  risk_note: string
  resources?: string
  est_seconds_per_host: number
  tags?: string[]
  severity?: string[] | null
}

interface VulnScan {
  id: string
  label: string
  scope: string
  targets: string[]
  source_scan_id: string | null
  status: 'running' | 'done' | 'error' | 'stopped'
  started_at: string
  completed_at: string | null
  total_findings: number
  critical_count: number
  high_count: number
  medium_count: number
  low_count: number
  command?: string
}

interface Finding {
  id: string
  vuln_scan_id: string
  host: string
  template_id: string
  template_name: string
  severity: string
  tags: string[]
  matched_at: string
  description: string
  reference: string[]
  extracted_results: string[]
  found_at: string
}

interface EstimateResult {
  scope: string
  host_count: number
  estimated_seconds: number
  estimated_human: string
  description: string
  risk_note: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const SEVERITY_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  critical: { bg: 'bg-red-900/30',    text: 'text-red-400',    border: 'border-red-800/60' },
  high:     { bg: 'bg-orange-900/30', text: 'text-orange-400', border: 'border-orange-800/60' },
  medium:   { bg: 'bg-yellow-900/30', text: 'text-yellow-400', border: 'border-yellow-800/60' },
  low:      { bg: 'bg-blue-900/20',   text: 'text-blue-400',   border: 'border-blue-800/40' },
  info:     { bg: 'bg-slate-800/40',  text: 'text-slate-400',  border: 'border-slate-700/60' },
}

function SeverityBadge({ severity }: { severity: string }) {
  const s = SEVERITY_STYLES[severity.toLowerCase()] ?? SEVERITY_STYLES.info
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border ${s.bg} ${s.text} ${s.border}`}>
      {severity}
    </span>
  )
}

function CountPill({ count, severity }: { count: number; severity: string }) {
  if (!count) return null
  const s = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.info
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold ${s.bg} ${s.text} border ${s.border}`}>
      {count} {severity}
    </span>
  )
}

function formatTime(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function ElapsedTimer({ startedAt }: { startedAt: string }) {
  const [elapsed, setElapsed] = useState('')
  useEffect(() => {
    function update() {
      const secs = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)
      if (secs < 60) setElapsed(` · ${secs}s elapsed`)
      else if (secs < 3600) setElapsed(` · ${Math.floor(secs / 60)}m ${secs % 60}s elapsed`)
      else setElapsed(` · ${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m elapsed`)
    }
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [startedAt])
  return <span className="text-blue-400">{elapsed}</span>
}

function CommandBlock({ command }: { command: string }) {
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(command).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <div className="rounded-lg border border-slate-700/60 bg-[#0d1117] overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-slate-700/40">
        <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Command</span>
        <button onClick={copy} className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
          {copied ? <Check size={10} className="text-green-400" /> : <Copy size={10} />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="px-3 py-2.5 text-[11px] font-mono text-green-300 whitespace-pre-wrap break-all leading-relaxed">
        {command}
      </pre>
    </div>
  )
}

function StatusDot({ status }: { status: VulnScan['status'] }) {
  const map = {
    running: 'bg-blue-400 animate-pulse',
    done:    'bg-green-400',
    error:   'bg-red-500',
    stopped: 'bg-slate-500',
  }
  return <span className={`inline-block w-2 h-2 rounded-full ${map[status] ?? 'bg-slate-500'}`} />
}

// ---------------------------------------------------------------------------
// Scope selector card
// ---------------------------------------------------------------------------
const SCOPE_COLORS: Record<string, { ring: string; glow: string; accent: string }> = {
  safe:     { ring: 'border-green-700/60',  glow: 'hover:border-green-500/80',  accent: 'text-green-400' },
  standard: { ring: 'border-blue-700/60',   glow: 'hover:border-blue-500/80',   accent: 'text-blue-400' },
  full:     { ring: 'border-orange-700/60', glow: 'hover:border-orange-500/80', accent: 'text-orange-400' },
}

function ScopeCard({
  id, profile, selected, onSelect,
}: {
  id: string; profile: ScopeProfile; selected: boolean; onSelect: () => void
}) {
  const c = SCOPE_COLORS[id] ?? SCOPE_COLORS.standard
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left rounded-xl border p-4 transition-all duration-150 ${
        selected
          ? `${c.ring} bg-slate-800/60 ring-1 ring-inset ${c.ring}`
          : `border-slate-700/50 bg-slate-900/40 ${c.glow}`
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className={`text-sm font-bold ${selected ? c.accent : 'text-slate-300'}`}>
          {profile.label}
        </span>
        {selected && <span className={`w-2 h-2 rounded-full ${c.accent.replace('text-', 'bg-')}`} />}
      </div>
      <p className="text-xs text-slate-400 leading-relaxed mb-3">{profile.description}</p>
      <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
        <Clock size={10} />
        ~{profile.est_seconds_per_host >= 3600
          ? `${profile.est_seconds_per_host / 3600}h`
          : profile.est_seconds_per_host >= 60
            ? `${Math.round(profile.est_seconds_per_host / 60)}m`
            : `${profile.est_seconds_per_host}s`} / host
      </div>
      {profile.resources && (
        <div className="mt-1.5 flex items-start gap-1 text-[10px] text-slate-500">
          <Info size={9} className="mt-0.5 flex-shrink-0" />
          <span>{profile.resources}</span>
        </div>
      )}
      <div className={`mt-2 text-[10px] font-medium ${c.accent} flex items-start gap-1`}>
        <AlertTriangle size={10} className="mt-0.5 flex-shrink-0" />
        <span>{profile.risk_note}</span>
      </div>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Findings table
// ---------------------------------------------------------------------------
function FindingsTable({ scanId }: { scanId: string }) {
  const [findings, setFindings] = useState<Finding[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [severityFilter, setSeverityFilter] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    setLoading(true)
    const params = new URLSearchParams()
    if (severityFilter) params.set('severity', severityFilter)
    fetch(`${API_BASE}/api/v1/discovery/vuln-scans/${scanId}/findings?${params}`)
      .then(r => r.ok ? r.json() : { findings: [] })
      .then(d => setFindings(d.findings ?? []))
      .finally(() => setLoading(false))
  }, [scanId, severityFilter])

  const visible = findings.filter(f =>
    !filter || f.template_name.toLowerCase().includes(filter.toLowerCase()) ||
    f.host.toLowerCase().includes(filter.toLowerCase()) ||
    f.template_id.toLowerCase().includes(filter.toLowerCase())
  )

  function toggleExpand(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info']

  return (
    <div className="space-y-3">
      {/* Filter bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative flex-1 min-w-48">
          <FileSearch size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder="Filter by template or host…"
            className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-slate-500"
          />
        </div>
        <div className="flex gap-1">
          {['', ...SEVERITY_ORDER].map(s => (
            <button
              key={s || 'all'}
              onClick={() => setSeverityFilter(s)}
              className={`px-2.5 py-1 rounded text-[10px] font-medium transition-colors ${
                severityFilter === s
                  ? 'bg-slate-700 text-white'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {s || 'All'}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="text-center py-8 text-slate-500 text-sm">Loading findings…</div>
      )}

      {!loading && visible.length === 0 && (
        <div className="text-center py-8 text-slate-500 text-sm">No findings match your filter</div>
      )}

      {!loading && visible.length > 0 && (
        <div className="space-y-1">
          {visible.map(f => {
            const isOpen = expanded.has(f.id)
            const s = SEVERITY_STYLES[f.severity.toLowerCase()] ?? SEVERITY_STYLES.info
            return (
              <div key={f.id} className={`rounded-lg border ${s.border} ${s.bg} overflow-hidden`}>
                <button
                  onClick={() => toggleExpand(f.id)}
                  className="w-full flex items-center gap-3 px-3 py-2.5 text-left"
                >
                  <SeverityBadge severity={f.severity} />
                  <span className="flex-1 text-xs text-slate-200 font-medium truncate">{f.template_name}</span>
                  <span className="text-[10px] text-slate-500 font-mono truncate max-w-[120px]">{f.host}</span>
                  {isOpen ? <ChevronUp size={12} className="text-slate-500 flex-shrink-0" /> : <ChevronDown size={12} className="text-slate-500 flex-shrink-0" />}
                </button>
                {isOpen && (
                  <div className="border-t border-white/5 px-3 py-3 space-y-2">
                    <div className="flex gap-4 text-[10px]">
                      <div>
                        <span className="text-slate-500">Template: </span>
                        <span className="font-mono text-slate-300">{f.template_id}</span>
                      </div>
                      <div>
                        <span className="text-slate-500">Matched: </span>
                        <span className="font-mono text-slate-300 break-all">{f.matched_at}</span>
                      </div>
                    </div>
                    {f.description && (
                      <p className="text-xs text-slate-300">{f.description}</p>
                    )}
                    {f.extracted_results?.length > 0 && (
                      <div>
                        <p className="text-[10px] text-slate-500 mb-1">Extracted:</p>
                        <pre className="text-[10px] font-mono text-green-300 bg-black/30 rounded p-2 overflow-auto max-h-24">
                          {f.extracted_results.join('\n')}
                        </pre>
                      </div>
                    )}
                    {f.tags?.length > 0 && (
                      <div className="flex gap-1 flex-wrap">
                        {f.tags.map(t => (
                          <span key={t} className="text-[9px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/50">{t}</span>
                        ))}
                      </div>
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

// ---------------------------------------------------------------------------
// Scan row in history list
// ---------------------------------------------------------------------------
function ScanRow({
  scan,
  onSelect,
  onDelete,
  onStop,
  selected,
}: {
  scan: VulnScan
  onSelect: () => void
  onDelete: () => void
  onStop: () => void
  selected: boolean
}) {
  return (
    <div
      onClick={onSelect}
      className={`rounded-lg border px-3 py-2.5 cursor-pointer transition-colors ${
        selected
          ? 'border-blue-700/60 bg-blue-900/20'
          : 'border-slate-700/50 bg-slate-900/40 hover:border-slate-600/60'
      }`}
    >
      <div className="flex items-center gap-2">
        <StatusDot status={scan.status} />
        <span className="flex-1 text-xs text-slate-200 font-medium truncate">
          {scan.label || scan.scope}
        </span>
        <div className="flex items-center gap-1">
          {scan.status === 'running' && (
            <button
              onClick={e => { e.stopPropagation(); onStop() }}
              className="p-1 text-slate-500 hover:text-red-400 transition-colors"
              title="Stop scan"
            >
              <Square size={11} />
            </button>
          )}
          <button
            onClick={e => { e.stopPropagation(); onDelete() }}
            className="p-1 text-slate-600 hover:text-red-400 transition-colors"
            title="Delete"
          >
            <Trash2 size={11} />
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 mt-1.5 flex-wrap">
        <span className="text-[10px] text-slate-500">
          {scan.scope} · {scan.targets.length} target{scan.targets.length !== 1 ? 's' : ''}
        </span>
        {scan.total_findings > 0 && (
          <div className="flex gap-1">
            <CountPill count={scan.critical_count} severity="critical" />
            <CountPill count={scan.high_count} severity="high" />
            <CountPill count={scan.medium_count} severity="medium" />
          </div>
        )}
        {scan.status === 'done' && scan.total_findings === 0 && (
          <span className="text-[10px] text-green-500">Clean</span>
        )}
      </div>
      <p className="text-[10px] text-slate-600 mt-1">{formatTime(scan.started_at)}</p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Live progress stream
// ---------------------------------------------------------------------------
interface LiveEvent {
  type: string
  message?: string
  command?: string
  template_id?: string
  severity?: string
  host?: string
  name?: string
  total_findings?: number
  counts?: Record<string, number>
}

function LiveProgress({ scanId, onDone, onCommand }: { scanId: string; onDone: () => void; onCommand?: (cmd: string) => void }) {
  const [events, setEvents] = useState<{ text: string; color: string }[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const doneRef = useRef(false)

  useEffect(() => {
    if (doneRef.current) return
    const es = new EventSource(`${API_BASE}/api/v1/discovery/vuln-scans/${scanId}/events`)
    es.onmessage = (e) => {
      try {
        const ev: LiveEvent = JSON.parse(e.data)
        if (ev.type === 'command') {
          if (ev.command) onCommand?.(ev.command)
        } else if (ev.type === 'progress') {
          setEvents(prev => [...prev, { text: ev.message ?? '', color: 'text-slate-400' }])
        } else if (ev.type === 'finding') {
          const sev = ev.severity?.toLowerCase() ?? 'info'
          const colorMap: Record<string, string> = {
            critical: 'text-red-400', high: 'text-orange-400',
            medium: 'text-yellow-400', low: 'text-blue-400', info: 'text-slate-400',
          }
          setEvents(prev => [...prev, {
            text: `[${(ev.severity ?? 'info').toUpperCase().padEnd(8)}] ${ev.name} — ${ev.host}`,
            color: colorMap[sev] ?? 'text-slate-400',
          }])
        } else if (ev.type === 'done') {
          setEvents(prev => [...prev, {
            text: `Scan complete — ${ev.total_findings ?? 0} finding(s) total`,
            color: 'text-green-400',
          }])
          doneRef.current = true
          es.close()
          onDone()
        } else if (ev.type === 'error') {
          setEvents(prev => [...prev, { text: `Error: ${ev.message ?? 'unknown'}`, color: 'text-red-400' }])
          es.close()
          onDone()
        }
      } catch { /* ignore */ }
    }
    es.onerror = () => {
      es.close()
      if (!doneRef.current) onDone()
    }
    return () => { es.close() }
  }, [scanId, onDone])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  return (
    <div className="rounded-xl border border-slate-700/60 bg-[#0d1117] p-3 font-mono text-xs overflow-auto max-h-64">
      {events.length === 0 && (
        <span className="text-slate-600">Waiting for events…</span>
      )}
      {events.map((e, i) => (
        <div key={i} className={e.color}>{e.text}</div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Schedule types + modal
// ---------------------------------------------------------------------------
interface VulnScanSchedule {
  id: string
  label: string
  scope: string
  cidr: string | null
  enabled: number
  day_of_week: number | null
  hour: number
  minute: number
  created_at: string
  last_run_at: string | null
}

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function ScheduleModal({ scopes, onClose, onSaved }: {
  scopes: Record<string, ScopeProfile>
  onClose: () => void
  onSaved: () => void
}) {
  const [label, setLabel] = useState('')
  const [scope, setScope] = useState('safe')
  const [cidr, setCidr] = useState('')
  const [dowIndex, setDowIndex] = useState('daily')
  const [hour, setHour] = useState('2')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function save() {
    if (!label.trim()) { setErr('Label is required'); return }
    if (!cidr.trim()) { setErr('Target CIDR or IP is required'); return }
    setSaving(true); setErr('')
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/vuln-scans/schedules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          label: label.trim(),
          scope,
          cidr: cidr.trim(),
          day_of_week: dowIndex === 'daily' ? null : parseInt(dowIndex),
          hour: parseInt(hour),
        }),
      })
      if (!r.ok) { setErr(`Failed: ${r.statusText}`); return }
      onSaved(); onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-[440px] rounded-xl border border-slate-700/60 bg-[#0f1520] shadow-2xl">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-white/5">
          <Calendar size={15} className="text-orange-400" />
          <h2 className="text-sm font-bold text-white">Schedule Scan</h2>
          <button onClick={onClose} className="ml-auto text-slate-500 hover:text-slate-300"><X size={15} /></button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1.5">Label</label>
            <input value={label} onChange={e => setLabel(e.target.value)} placeholder="e.g. Weekly mgmt network scan"
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-500" />
          </div>
          <div>
            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1.5">Target (CIDR or IP)</label>
            <input value={cidr} onChange={e => setCidr(e.target.value)} placeholder="e.g. 10.0.0.0/24 or 192.168.1.50"
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-200 font-mono placeholder-slate-600 focus:outline-none focus:border-slate-500" />
          </div>
          <div>
            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1.5">Scope</label>
            <select value={scope} onChange={e => setScope(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-slate-500">
              {Object.entries(scopes).map(([id, p]) => <option key={id} value={id}>{p.label}</option>)}
            </select>
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1.5">Day</label>
              <select value={dowIndex} onChange={e => setDowIndex(e.target.value)}
                className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-slate-500">
                <option value="daily">Every day</option>
                {DOW_LABELS.map((d, i) => <option key={i} value={String(i)}>{d}</option>)}
              </select>
            </div>
            <div className="w-32">
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1.5">Hour (UTC)</label>
              <select value={hour} onChange={e => setHour(e.target.value)}
                className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-slate-500">
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={String(h)}>{String(h).padStart(2, '0')}:00</option>
                ))}
              </select>
            </div>
          </div>
          {err && <p className="text-xs text-red-400">{err}</p>}
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-white/5">
          <button onClick={onClose} className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors">Cancel</button>
          <button onClick={save} disabled={saving}
            className="px-4 py-1.5 bg-orange-600 hover:bg-orange-500 disabled:bg-slate-700 disabled:text-slate-500 text-white text-xs font-semibold rounded-lg transition-colors">
            {saving ? 'Saving…' : 'Save Schedule'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export function VulnScanPage() {
  const [scopes, setScopes] = useState<Record<string, ScopeProfile>>({})
  const [scans, setScans] = useState<VulnScan[]>([])
  const [selectedScope, setSelectedScope] = useState('safe')
  const [targets, setTargets] = useState('')
  const [label, setLabel] = useState('')
  const [estimate, setEstimate] = useState<EstimateResult | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [selectedScanId, setSelectedScanId] = useState<string | null>(null)
  const [loadingScans, setLoadingScans] = useState(true)
  const [liveIds, setLiveIds] = useState<Set<string>>(new Set())
  const [liveCommands, setLiveCommands] = useState<Record<string, string>>({})
  const [showScheduleModal, setShowScheduleModal] = useState(false)
  const [schedules, setSchedules] = useState<VulnScanSchedule[]>([])
  const estimateTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadScans = useCallback(async (autoSelect = false) => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/vuln-scans`)
      if (r.ok) {
        const d = await r.json()
        const list: VulnScan[] = d.vuln_scans ?? d.scans ?? []
        setScans(list)
        if (autoSelect && list.length > 0) {
          const done = list.find(s => s.status === 'done') ?? list[0]
          setSelectedScanId(done.id)
        }
      }
    } finally {
      setLoadingScans(false)
    }
  }, [])

  const loadSchedules = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/vuln-scans/schedules`)
      if (r.ok) {
        const d = await r.json()
        setSchedules(d.schedules ?? [])
      }
    } catch { /* silent */ }
  }, [])

  async function toggleSchedule(id: string, enabled: number) {
    await fetch(`${API_BASE}/api/v1/discovery/vuln-scans/schedules/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled ? 0 : 1 }),
    })
    loadSchedules()
  }

  async function deleteSchedule(id: string) {
    await fetch(`${API_BASE}/api/v1/discovery/vuln-scans/schedules/${id}`, { method: 'DELETE' })
    setSchedules(prev => prev.filter(s => s.id !== id))
  }

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/discovery/vuln-scans/scopes`)
      .then(r => r.ok ? r.json() : {})
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .then((d: any) => {
        const arr: (ScopeProfile & { id: string })[] = d.scopes ?? (Array.isArray(d) ? d : [])
        const map: Record<string, ScopeProfile> = {}
        arr.forEach(s => { if (s.id) map[s.id] = s })
        setScopes(map)
      })
      .catch(() => {})
    loadScans(true)
    loadSchedules()
  }, [loadScans, loadSchedules])

  // Update estimate when scope or targets change
  useEffect(() => {
    if (estimateTimer.current) clearTimeout(estimateTimer.current)
    const hostList = targets.split(/[\n,]+/).map(t => t.trim()).filter(Boolean)
    if (!hostList.length) { setEstimate(null); return }
    estimateTimer.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/v1/discovery/vuln-scans/estimate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: selectedScope, targets: hostList }),
        })
        if (r.ok) setEstimate(await r.json())
      } catch { /* silent */ }
    }, 300)
    return () => { if (estimateTimer.current) clearTimeout(estimateTimer.current) }
  }, [selectedScope, targets])

  async function startScan() {
    const hostList = targets.split(/[\n,]+/).map(t => t.trim()).filter(Boolean)
    if (!hostList.length) { setError('Enter at least one target IP or hostname'); return }
    setError('')
    setStarting(true)
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/vuln-scans`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targets: hostList, scope: selectedScope, label: label.trim() || undefined }),
      })
      if (!r.ok) { setError(`Failed to start scan: ${r.statusText}`); return }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const data: any = await r.json()
      const newId: string = data.vuln_scan_id ?? data.id
      setSelectedScanId(newId)
      setLiveIds(prev => new Set([...prev, newId]))
      await loadScans()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start scan')
    } finally {
      setStarting(false)
    }
  }

  async function stopScan(id: string) {
    await fetch(`${API_BASE}/api/v1/discovery/vuln-scans/${id}/stop`, { method: 'POST' })
    loadScans()
  }

  async function deleteScan(id: string) {
    await fetch(`${API_BASE}/api/v1/discovery/vuln-scans/${id}`, { method: 'DELETE' })
    setScans(prev => prev.filter(s => s.id !== id))
    if (selectedScanId === id) setSelectedScanId(null)
  }

  function onScanDone(id: string) {
    setLiveIds(prev => { const n = new Set(prev); n.delete(id); return n })
    loadScans()
  }

  // Poll every 8s while any scan is in 'running' state
  useEffect(() => {
    const hasRunning = scans.some(s => s.status === 'running')
    if (!hasRunning) return
    const id = setInterval(loadScans, 8000)
    return () => clearInterval(id)
  }, [scans, loadScans])

  const selectedScan = scans.find(s => s.id === selectedScanId)

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] text-slate-200">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-3.5 border-b border-white/5 flex-shrink-0">
        <ShieldAlert size={17} className="text-orange-400" />
        <h1 className="text-sm font-bold text-white">Vulnerability Scan</h1>
        <span className="text-xs text-slate-500">Powered by Nuclei</span>
        <button
          onClick={() => setShowScheduleModal(true)}
          className="ml-auto flex items-center gap-1.5 text-[10px] text-slate-400 hover:text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-700/60 px-2.5 py-1.5 rounded-lg transition-colors"
        >
          <Calendar size={11} />
          Schedule
        </button>
        <button
          onClick={() => loadScans()}
          className="p-1.5 text-slate-500 hover:text-slate-300 transition-colors"
          title="Refresh scan list"
        >
          <RefreshCw size={13} />
        </button>
      </div>

      <div className="flex flex-1 min-h-0">
        {/* LEFT PANEL — config + history */}
        <div className="w-80 flex-shrink-0 border-r border-white/5 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto p-4 space-y-5">
            {/* Scope selector */}
            <section>
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Scan Scope</p>
              <div className="space-y-2">
                {Object.entries(scopes).map(([id, profile]) => (
                  <ScopeCard
                    key={id}
                    id={id}
                    profile={profile}
                    selected={selectedScope === id}
                    onSelect={() => setSelectedScope(id)}
                  />
                ))}
                {Object.keys(scopes).length === 0 && (
                  <div className="text-xs text-slate-500 py-2">Loading scopes…</div>
                )}
              </div>
            </section>

            {/* Targets */}
            <section>
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">
                <Target size={10} className="inline mr-1" />
                Targets
              </p>
              <textarea
                value={targets}
                onChange={e => setTargets(e.target.value)}
                placeholder={"192.168.1.1\n10.0.0.0/24\nhostname.local"}
                rows={5}
                className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-600 font-mono focus:outline-none focus:border-slate-500 resize-none"
              />
              <p className="text-[10px] text-slate-600 mt-1">One IP, CIDR or hostname per line (or comma-separated)</p>
            </section>

            {/* Label (optional) */}
            <section>
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Label (optional)</p>
              <input
                value={label}
                onChange={e => setLabel(e.target.value)}
                placeholder="e.g. Management network — Q2"
                className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-500"
              />
            </section>

            {/* Estimate */}
            {estimate && (
              <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 px-3 py-2.5 space-y-1">
                <div className="flex items-center gap-1.5 text-xs text-slate-300">
                  <Clock size={11} className="text-blue-400" />
                  <span className="font-semibold">Estimated:</span>
                  <span className="text-blue-400">{estimate.estimated_human}</span>
                </div>
                <p className="text-[10px] text-slate-500">
                  {estimate.host_count} host{estimate.host_count !== 1 ? 's' : ''} × {SCOPE_PROFILES_DISPLAY[selectedScope] ?? selectedScope} scope
                </p>
                <div className="flex items-start gap-1 text-[10px] text-orange-400/80">
                  <Info size={9} className="mt-0.5 flex-shrink-0" />
                  <span>{estimate.risk_note}</span>
                </div>
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="flex items-start gap-1.5 text-xs text-red-400 bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">
                <X size={12} className="mt-0.5 flex-shrink-0" />
                {error}
              </div>
            )}

            {/* Start button */}
            <button
              onClick={startScan}
              disabled={starting}
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-orange-600 hover:bg-orange-500 disabled:bg-slate-700 disabled:text-slate-500 text-white text-sm font-semibold transition-colors"
            >
              {starting ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
              {starting ? 'Starting…' : 'Start Scan'}
            </button>

            {/* Schedules */}
            {schedules.length > 0 && (
              <section>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
                  <Calendar size={10} /> Schedules ({schedules.length})
                </p>
                <div className="space-y-1.5">
                  {schedules.map(s => (
                    <div key={s.id} className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs transition-colors ${s.enabled ? 'border-orange-800/40 bg-orange-900/10' : 'border-slate-700/40 bg-slate-800/20'}`}>
                      <div className="flex-1 min-w-0">
                        <p className="font-medium text-slate-200 truncate">{s.label}</p>
                        <p className="text-[10px] text-slate-500 mt-0.5">
                          {s.scope} · {s.cidr || '—'} · {s.day_of_week != null ? DOW_LABELS[s.day_of_week] : 'Daily'} {String(s.hour).padStart(2, '0')}:00
                        </p>
                      </div>
                      <button
                        onClick={() => toggleSchedule(s.id, s.enabled)}
                        className={`text-[10px] font-semibold px-2 py-0.5 rounded border transition-colors ${s.enabled ? 'text-orange-400 border-orange-800/50 hover:bg-orange-900/20' : 'text-slate-500 border-slate-700/50 hover:bg-slate-700/30'}`}
                      >
                        {s.enabled ? 'ON' : 'OFF'}
                      </button>
                      <button onClick={() => deleteSchedule(s.id)} className="text-slate-600 hover:text-red-400 transition-colors">
                        <Trash2 size={11} />
                      </button>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>

          {/* Scan history */}
          <div className="border-t border-white/5 flex flex-col min-h-0" style={{ maxHeight: '40%' }}>
            <div className="px-4 py-2.5 flex items-center gap-2">
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Scan History</p>
              {loadingScans && <RefreshCw size={10} className="text-slate-600 animate-spin" />}
            </div>
            <div className="overflow-y-auto px-4 pb-4 space-y-1.5">
              {scans.length === 0 && !loadingScans && (
                <p className="text-xs text-slate-600 py-2">No scans yet</p>
              )}
              {scans.map(scan => (
                <ScanRow
                  key={scan.id}
                  scan={scan}
                  selected={selectedScanId === scan.id}
                  onSelect={() => setSelectedScanId(scan.id)}
                  onStop={() => stopScan(scan.id)}
                  onDelete={() => deleteScan(scan.id)}
                />
              ))}
            </div>
          </div>
        </div>

        {/* RIGHT PANEL — results */}
        <div className="flex-1 min-w-0 overflow-y-auto p-5 space-y-5">
          {!selectedScan && (
            <div className="flex flex-col items-center justify-center h-full text-center gap-4">
              <ShieldAlert size={40} className="text-slate-700" />
              <div>
                <p className="text-slate-400 text-sm font-medium">Results appear here</p>
                <p className="text-slate-600 text-xs mt-1">Select a scan from the history panel, or configure targets and start a new scan</p>
              </div>
            </div>
          )}

          {selectedScan && (
            <>
              {/* Scan header */}
              <div className="rounded-xl border border-slate-700/60 bg-slate-800/40 px-4 py-3">
                <div className="flex items-center gap-3">
                  <StatusDot status={selectedScan.status} />
                  <div>
                    <p className="text-sm font-semibold text-white">
                      {selectedScan.label || `${selectedScan.scope} scan`}
                    </p>
                    <p className="text-xs text-slate-400">
                      {selectedScan.scope} · {selectedScan.targets.length} target{selectedScan.targets.length !== 1 ? 's' : ''}
                      {' · '}{formatTime(selectedScan.started_at)}
                      {selectedScan.status === 'running' && (
                        <ElapsedTimer startedAt={selectedScan.started_at} />
                      )}
                    </p>
                  </div>
                  <div className="ml-auto flex gap-2 flex-wrap justify-end">
                    <CountPill count={selectedScan.critical_count} severity="critical" />
                    <CountPill count={selectedScan.high_count} severity="high" />
                    <CountPill count={selectedScan.medium_count} severity="medium" />
                    <CountPill count={selectedScan.low_count} severity="low" />
                    {selectedScan.total_findings === 0 && selectedScan.status === 'done' && (
                      <span className="text-xs text-green-400 font-medium">No vulnerabilities found</span>
                    )}
                    {selectedScan.status === 'error' && (
                      <span className="text-xs text-red-400 font-medium">Scan failed or was interrupted</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Full scope warning */}
              {selectedScan.scope === 'full' && selectedScan.status === 'running' && (
                <div className="flex items-start gap-2 rounded-lg border border-orange-800/50 bg-orange-900/20 px-3 py-2.5">
                  <AlertTriangle size={13} className="text-orange-400 mt-0.5 flex-shrink-0" />
                  <p className="text-xs text-orange-300">
                    Full scans load 7 000+ templates and can take <strong>30–90 min per host</strong>. The scan runs inside the pod — a platform update or pod restart will interrupt it. Check back later or use Standard scope for faster results.
                  </p>
                </div>
              )}

              {/* Command block — shown for all scans (live command or stored) */}
              {(selectedScan.command || liveCommands[selectedScan.id]) && (
                <CommandBlock command={selectedScan.command ?? liveCommands[selectedScan.id]} />
              )}

              {/* Live progress (only for running scans that were just started) */}
              {liveIds.has(selectedScan.id) && (
                <div>
                  <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
                    <Zap size={10} className="text-blue-400" /> Live Output
                  </p>
                  <LiveProgress
                    scanId={selectedScan.id}
                    onDone={() => onScanDone(selectedScan.id)}
                    onCommand={cmd => setLiveCommands(prev => ({ ...prev, [selectedScan.id]: cmd }))}
                  />
                </div>
              )}

              {/* Targets list */}
              <div>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
                  <Target size={10} /> Targets ({selectedScan.targets.length})
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {selectedScan.targets.map(t => (
                    <span key={t} className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800/60 border border-slate-700/50 text-slate-300">
                      {t}
                    </span>
                  ))}
                </div>
              </div>

              {/* Findings */}
              {(selectedScan.status === 'done' || selectedScan.total_findings > 0) && (
                <div>
                  <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
                    <ShieldAlert size={10} className="text-orange-400" />
                    Findings ({selectedScan.total_findings})
                  </p>
                  <FindingsTable scanId={selectedScan.id} />
                </div>
              )}

              {selectedScan.status === 'running' && !liveIds.has(selectedScan.id) && (
                <div className="text-center py-6 text-slate-500 text-sm">
                  <RefreshCw size={16} className="animate-spin mx-auto mb-2" />
                  Scan in progress…
                </div>
              )}
            </>
          )}
        </div>
      </div>
      {showScheduleModal && (
        <ScheduleModal
          scopes={scopes}
          onClose={() => setShowScheduleModal(false)}
          onSaved={loadSchedules}
        />
      )}
    </div>
  )
}

// Used in estimate display
const SCOPE_PROFILES_DISPLAY: Record<string, string> = {
  safe: 'Safe',
  standard: 'Standard',
  full: 'Full',
}
