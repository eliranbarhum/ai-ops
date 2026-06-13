import { AlertTriangle, AlertCircle, Info } from 'lucide-react'
import type { RiskFactor } from '../types'

interface Props {
  riskFactors: RiskFactor[]
  recommendations: string[]
}

const SEVERITY_CONFIG = {
  critical: { icon: AlertCircle, color: 'text-red-400', bg: 'bg-red-900/20 border-red-800', dot: 'bg-red-500' },
  warning: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-900/20 border-yellow-800', dot: 'bg-yellow-500' },
  info: { icon: Info, color: 'text-blue-400', bg: 'bg-blue-900/20 border-blue-800', dot: 'bg-blue-500' },
}

export function RiskFactors({ riskFactors, recommendations }: Props) {
  const sorted = [...riskFactors].sort((a, b) => {
    const order = { critical: 0, warning: 1, info: 2 }
    return (order[a.severity] ?? 3) - (order[b.severity] ?? 3)
  })

  return (
    <div className="space-y-4">
      {/* Risk Factors */}
      <div className="rounded-xl border border-vmware-border bg-vmware-card p-5">
        <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
          <AlertTriangle size={14} className="text-yellow-400" />
          Risk Factors
          {riskFactors.length > 0 && (
            <span className="ml-auto text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded-full">
              {riskFactors.length}
            </span>
          )}
        </h3>

        {sorted.length === 0 ? (
          <p className="text-sm text-green-400 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
            No risk factors detected
          </p>
        ) : (
          <ul className="space-y-2">
            {sorted.map((risk, i) => {
              const cfg = SEVERITY_CONFIG[risk.severity] ?? SEVERITY_CONFIG.info
              const Icon = cfg.icon
              return (
                <li key={i} className={`flex items-start gap-3 rounded-lg border p-3 ${cfg.bg}`}>
                  <Icon size={14} className={`mt-0.5 flex-shrink-0 ${cfg.color}`} />
                  <div className="min-w-0">
                    <p className={`text-xs font-semibold uppercase tracking-wide ${cfg.color}`}>
                      {risk.severity} · {risk.component}
                    </p>
                    <p className="text-sm text-slate-200 mt-0.5">{risk.message}</p>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <div className="rounded-xl border border-vmware-border bg-vmware-card p-5">
          <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wider mb-4">
            Recommendations
          </h3>
          <ul className="space-y-2">
            {recommendations.map((rec, i) => (
              <li key={i} className="flex items-start gap-3 text-sm text-slate-300">
                <span className="flex-shrink-0 w-5 h-5 rounded-full bg-vmware-blue/40 text-blue-300 text-xs flex items-center justify-center font-bold mt-0.5">
                  {i + 1}
                </span>
                {rec}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
