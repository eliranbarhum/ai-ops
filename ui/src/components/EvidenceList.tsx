import { Database } from 'lucide-react'
import type { Evidence } from '../types'

const SOURCE_COLORS: Record<string, string> = {
  VCENTER: 'text-blue-400 bg-blue-900/30 border-blue-800',
  ESXI: 'text-cyan-400 bg-cyan-900/30 border-cyan-800',
  VCF_OPERATIONS: 'text-purple-400 bg-purple-900/30 border-purple-800',
  VCF_OPERATIONS_FOR_LOGS: 'text-orange-400 bg-orange-900/30 border-orange-800',
  VCF_OPERATIONS_FOR_NETWORKS: 'text-teal-400 bg-teal-900/30 border-teal-800',
}

interface Props {
  evidence: Evidence[]
}

export function EvidenceList({ evidence }: Props) {
  if (evidence.length === 0) return null

  const grouped = evidence.reduce<Record<string, Evidence[]>>((acc, e) => {
    const key = e.source
    acc[key] = acc[key] ?? []
    acc[key].push(e)
    return acc
  }, {})

  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card p-5">
      <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
        <Database size={14} className="text-blue-400" />
        Evidence ({evidence.length} data points)
      </h3>

      <div className="space-y-4">
        {Object.entries(grouped).map(([source, items]) => {
          const colorClass = SOURCE_COLORS[source] ?? 'text-slate-400 bg-slate-800 border-slate-700'
          return (
            <div key={source}>
              <p className={`inline-block text-xs font-bold px-2 py-0.5 rounded border mb-2 ${colorClass}`}>
                {source}
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                {items.map((e, i) => (
                  <div key={i} className="flex justify-between items-center bg-slate-800/50 rounded px-3 py-1.5 text-xs">
                    <span className="text-slate-400 truncate">{e.metric}</span>
                    <span className="font-mono text-slate-200 ml-2 flex-shrink-0">{e.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
