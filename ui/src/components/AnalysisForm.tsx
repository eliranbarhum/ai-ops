import { useState } from 'react'
import { Play, Loader2 } from 'lucide-react'
import type { AnalysisRequest } from '../types'

interface Props {
  onSubmit: (req: AnalysisRequest) => void
  loading: boolean
}

const TARGETS: { value: AnalysisRequest['target']; label: string; description: string; defaultQuery: string }[] = [
  {
    value: 'vcf_readiness',
    label: 'VCF Readiness',
    description: 'Full VCF 9.1 upgrade readiness analysis',
    defaultQuery: 'Run a full VCF 9.1 readiness analysis. Identify all upgrade blockers, hardware HCL gaps, and deprecated components. Provide a go/no-go recommendation with action items for each team.',
  },
  {
    value: 'capacity',
    label: 'Capacity',
    description: 'CPU, RAM, and storage headroom evaluation',
    defaultQuery: 'Evaluate cluster CPU, memory, and storage headroom. Identify any resources at risk of exhaustion during a VCF 9.1 upgrade maintenance window.',
  },
  {
    value: 'anomaly_detection',
    label: 'Anomaly Detection',
    description: 'Log-based anomaly and incident analysis',
    defaultQuery: 'Analyze recent log events for anomalies, hardware errors, and recurring warnings. Flag any issues that would block or risk a VCF upgrade.',
  },
  {
    value: 'network',
    label: 'Network',
    description: 'NSX / DVS network health analysis',
    defaultQuery: 'Assess NSX Manager and distributed virtual switch health. Identify any network configuration issues that must be resolved before upgrading to VCF 9.1.',
  },
]

export function AnalysisForm({ onSubmit, loading }: Props) {
  const [target, setTarget] = useState<AnalysisRequest['target']>('vcf_readiness')
  const [query, setQuery] = useState(TARGETS[0].defaultQuery)

  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card p-5">
      <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wider mb-4">
        Analysis Configuration
      </h3>

      <div className="space-y-4">
        {/* Target selector */}
        <div>
          <label className="block text-xs text-slate-400 mb-2">Analysis Target</label>
          <div className="grid grid-cols-2 gap-2">
            {TARGETS.map((t) => (
              <button
                key={t.value}
                onClick={() => { setTarget(t.value); setQuery(t.defaultQuery) }}
                className={`text-left p-3 rounded-lg border text-xs transition-all ${
                  target === t.value
                    ? 'border-blue-500 bg-blue-900/30 text-blue-300'
                    : 'border-vmware-border bg-slate-800/50 text-slate-400 hover:border-slate-500'
                }`}
              >
                <div className="font-semibold">{t.label}</div>
                <div className="text-slate-500 mt-0.5">{t.description}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Query input */}
        <div>
          <label className="block text-xs text-slate-400 mb-2">Query</label>
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            rows={2}
            className="w-full bg-slate-800/70 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
            placeholder="Describe your analysis goal..."
          />
        </div>

        {/* Submit */}
        <button
          onClick={() => onSubmit({ target, query })}
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 bg-vmware-blue hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-2.5 px-4 rounded-lg transition-colors text-sm"
        >
          {loading ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              Analyzing...
            </>
          ) : (
            <>
              <Play size={16} />
              Run Analysis
            </>
          )}
        </button>
      </div>
    </div>
  )
}
