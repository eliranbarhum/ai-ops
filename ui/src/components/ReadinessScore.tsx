import type { AnalysisResponse } from '../types'

interface Props {
  score: number
  status: AnalysisResponse['status']
}

const STATUS_CONFIG = {
  READY: { color: '#00B050', label: 'READY', bg: 'bg-green-900/30 border-green-700' },
  WARNING: { color: '#FFB900', label: 'WARNING', bg: 'bg-yellow-900/30 border-yellow-700' },
  NOT_READY: { color: '#E02020', label: 'NOT READY', bg: 'bg-red-900/30 border-red-700' },
  UNKNOWN: { color: '#64748B', label: 'UNKNOWN', bg: 'bg-slate-800 border-slate-600' },
}

export function ReadinessScore({ score, status }: Props) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.UNKNOWN
  const r = 54
  const circumference = 2 * Math.PI * r
  const dashOffset = circumference - (score / 100) * circumference

  return (
    <div className={`rounded-xl border p-6 flex flex-col items-center gap-4 ${cfg.bg}`}>
      <p className="text-xs font-semibold tracking-widest text-slate-400 uppercase">VCF Readiness Score</p>

      <div className="relative score-ring rounded-full">
        <svg width="140" height="140" viewBox="0 0 140 140">
          <circle cx="70" cy="70" r={r} fill="none" stroke="#1E3A5F" strokeWidth="12" />
          <circle
            cx="70" cy="70" r={r} fill="none"
            stroke={cfg.color} strokeWidth="12"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
            transform="rotate(-90 70 70)"
            style={{ transition: 'stroke-dashoffset 1s ease' }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-4xl font-bold" style={{ color: cfg.color }}>{score}</span>
          <span className="text-xs text-slate-400">/100</span>
        </div>
      </div>

      <span
        className="px-4 py-1 rounded-full text-sm font-bold tracking-wider"
        style={{ backgroundColor: cfg.color + '22', color: cfg.color, border: `1px solid ${cfg.color}55` }}
      >
        {cfg.label}
      </span>
    </div>
  )
}
