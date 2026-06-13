import { useState, useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  AreaChart, Area,
} from 'recharts'
import {
  TrendingUp, Zap, Server, Code2, Activity, Archive, BotMessageSquare,
  Upload, Laptop, Terminal, Radar, Settings, RefreshCw, Loader2,
  Cpu, MemoryStick, HardDrive, Shield, MonitorCheck, BadgeCheck,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface HistoryRow {
  id: number
  timestamp: string
  target: string
  readiness_score: number
  status: string
  sub_scores: { name: string; label: string; score: number; max: number; pct: number; status: string; icon: string }[]
  risk_factor_count: number
}

const SUB_ICON: Record<string, React.ElementType> = {
  cpu: Cpu,
  memory: MemoryStick,
  storage: HardDrive,
  platform: Activity,
  hosts: MonitorCheck,
  hcl: BadgeCheck,
  shield: Shield,
}

const STATUS_COLOR: Record<string, string> = {
  READY: '#22c55e',
  WARNING: '#eab308',
  NOT_READY: '#ef4444',
  UNKNOWN: '#64748b',
}

function fmt(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function fmtShort(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric' })
}

const SCORE_GRADIENT = [
  { offset: '0%', color: '#22c55e' },
  { offset: '50%', color: '#eab308' },
  { offset: '100%', color: '#ef4444' },
]

function ScoreDot({ value }: { value: number }) {
  const color = value >= 80 ? '#22c55e' : value >= 50 ? '#eab308' : '#ef4444'
  return <span style={{ color }} className="font-bold">{value}</span>
}

export function TrendsPage() {
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [loading, setLoading] = useState(true)
  const [target, setTarget] = useState<string>('all')

  async function load() {
    setLoading(true)
    try {
      const r = await fetch(`${API_BASE}/api/v1/scoring/history?limit=100`)
      const data = await r.json()
      setHistory((data.history || []).reverse())
    } catch {
      setHistory([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const targets = ['all', ...Array.from(new Set(history.map(r => r.target)))]
  const filtered = target === 'all' ? history : history.filter(r => r.target === target)

  const chartData = filtered.map(r => ({
    time: fmtShort(r.timestamp),
    fullTime: fmt(r.timestamp),
    score: r.readiness_score,
    status: r.status,
    risks: r.risk_factor_count,
    ...Object.fromEntries((r.sub_scores || []).map(s => [s.name, s.score])),
  }))

  const subNames = filtered.length > 0
    ? (filtered[filtered.length - 1].sub_scores || []).map(s => s.name)
    : []

  const SUB_COLORS: Record<string, string> = {
    cpu: '#60a5fa',
    ram: '#34d399',
    storage: '#f59e0b',
    platform: '#a78bfa',
    hosts: '#fb923c',
    compatibility: '#38bdf8',
    network_security: '#f472b6',
  }

  const latestScore = filtered.length > 0 ? filtered[filtered.length - 1].readiness_score : null
  const prevScore = filtered.length > 1 ? filtered[filtered.length - 2].readiness_score : null
  const trend = latestScore !== null && prevScore !== null ? latestScore - prevScore : null

  return (
    <div className="min-h-screen bg-vmware-dark">
      <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">

        {/* Header row */}
        <div className="flex items-center gap-4 flex-wrap">
          <div>
            <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
              <TrendingUp size={14} className="text-blue-400" /> Score Trends
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">{filtered.length} analysis runs</p>
          </div>

          {latestScore !== null && (
            <div className="rounded-xl border border-vmware-border bg-vmware-card px-4 py-2 flex items-center gap-3">
              <div>
                <p className="text-[10px] text-slate-500 uppercase tracking-wider">Latest Score</p>
                <p className="text-2xl font-bold mt-0.5"><ScoreDot value={latestScore} /></p>
              </div>
              {trend !== null && (
                <div className={`text-xs font-semibold ${trend > 0 ? 'text-green-400' : trend < 0 ? 'text-red-400' : 'text-slate-400'}`}>
                  {trend > 0 ? '↑' : trend < 0 ? '↓' : '→'} {Math.abs(trend)} pts
                </div>
              )}
            </div>
          )}

          {/* Target filter */}
          <div className="flex items-center gap-1.5 ml-auto">
            {targets.map(t => (
              <button
                key={t}
                onClick={() => setTarget(t)}
                className={`text-xs px-2.5 py-1 rounded-lg border transition-colors capitalize ${
                  target === t
                    ? 'bg-blue-600/20 border-blue-500/40 text-blue-300'
                    : 'border-vmware-border text-slate-400 hover:text-white hover:border-slate-500'
                }`}
              >
                {t.replace('_', ' ')}
              </button>
            ))}
          </div>
          <button onClick={load} disabled={loading} className="ml-auto flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors disabled:opacity-40">
            {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh
          </button>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-blue-400" />
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="rounded-xl border border-vmware-border bg-vmware-card p-16 flex flex-col items-center gap-3 text-center">
            <TrendingUp size={32} className="text-slate-500" />
            <p className="text-slate-300 text-sm">No scoring history yet</p>
            <p className="text-slate-400 text-xs">Run an analysis to populate trends</p>
            <a href="#/analysis" className="mt-1 inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-lg transition-colors">
              Go to Analysis
            </a>
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <>
            {/* Main score chart */}
            <div className="rounded-xl border border-vmware-border bg-vmware-card p-5">
              <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
                <TrendingUp size={13} className="text-blue-400" /> Readiness Score Over Time
              </p>
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                  <defs>
                    <linearGradient id="scoreGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="time" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} />
                  <YAxis domain={[0, 100]} tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 11 }}
                    labelStyle={{ color: '#94a3b8' }}
                    formatter={(val: number, name: string) => [val, name === 'score' ? 'Score' : name]}
                    labelFormatter={(label, payload) => payload?.[0]?.payload?.fullTime ?? label}
                  />
                  <Area type="monotone" dataKey="score" stroke="#3b82f6" strokeWidth={2} fill="url(#scoreGrad)"
                    dot={(props) => {
                      const { cx, cy, payload } = props
                      const color = STATUS_COLOR[payload.status] ?? '#64748b'
                      return <circle key={`dot-${props.index}`} cx={cx} cy={cy} r={3} fill={color} stroke="none" />
                    }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* Sub-score sparklines */}
            {subNames.length > 0 && (
              <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">
                {subNames.map(name => {
                  const label = filtered[filtered.length - 1].sub_scores?.find(s => s.name === name)?.label ?? name
                  const IconComp = SUB_ICON[name] ?? Activity
                  const color = SUB_COLORS[name] ?? '#60a5fa'
                  const sparkData = chartData.map(d => ({ time: d.time, v: (d as unknown as Record<string, number>)[name] ?? 0 }))
                  const latest = sparkData[sparkData.length - 1]?.v ?? 0
                  return (
                    <div key={name} className="rounded-xl border border-vmware-border bg-vmware-card p-4">
                      <div className="flex items-center gap-2 mb-3">
                        <IconComp size={12} style={{ color }} />
                        <span className="text-xs font-semibold text-slate-300">{label}</span>
                        <span className="ml-auto text-xs font-bold" style={{ color }}>{latest}</span>
                      </div>
                      <ResponsiveContainer width="100%" height={60}>
                        <LineChart data={sparkData} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
                          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} />
                          <YAxis domain={[0, 'dataMax']} hide />
                          <Tooltip
                            contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, fontSize: 10, padding: '4px 8px' }}
                            formatter={(val: number) => [val, label]}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )
                })}
              </div>
            )}

            {/* Risk factor trend */}
            <div className="rounded-xl border border-vmware-border bg-vmware-card p-5">
              <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
                <Shield size={13} className="text-red-400" /> Risk Factor Count Over Time
              </p>
              <ResponsiveContainer width="100%" height={120}>
                <AreaChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                  <defs>
                    <linearGradient id="riskGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="time" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} />
                  <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 11 }}
                    formatter={(val: number) => [val, 'Risk factors']}
                  />
                  <Area type="monotone" dataKey="risks" stroke="#ef4444" strokeWidth={1.5} fill="url(#riskGrad)" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* History table */}
            <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
              <div className="px-4 py-3 border-b border-vmware-border">
                <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Run History</span>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-vmware-border">
                    <th className="text-left px-4 py-2 text-slate-400 font-medium">Timestamp</th>
                    <th className="text-left px-4 py-2 text-slate-400 font-medium">Target</th>
                    <th className="text-left px-4 py-2 text-slate-400 font-medium">Score</th>
                    <th className="text-left px-4 py-2 text-slate-400 font-medium">Status</th>
                    <th className="text-left px-4 py-2 text-slate-400 font-medium">Risks</th>
                  </tr>
                </thead>
                <tbody>
                  {[...filtered].reverse().slice(0, 30).map(row => (
                    <tr key={row.id} className="border-b border-vmware-border/50 hover:bg-slate-800/30">
                      <td className="px-4 py-2 text-slate-400 font-mono">{fmt(row.timestamp)}</td>
                      <td className="px-4 py-2 text-slate-300 capitalize">{row.target.replace('_', ' ')}</td>
                      <td className="px-4 py-2 font-bold"><ScoreDot value={row.readiness_score} /></td>
                      <td className="px-4 py-2">
                        <span style={{ color: STATUS_COLOR[row.status] ?? '#64748b' }}>{row.status}</span>
                      </td>
                      <td className="px-4 py-2 text-slate-400">{row.risk_factor_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
