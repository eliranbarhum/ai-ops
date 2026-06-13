import { useState, useEffect, useCallback } from 'react'
import {
  RefreshCw, Download, ChevronDown, ChevronRight, AlertTriangle,
  CheckCircle2, XCircle, Clock, Shield, ArrowRight, Loader2, Info,
} from 'lucide-react'
import { useToast } from './Toast'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface BlockerItem {
  component_type: string
  current_version: string
  blocking_reasons: string[]
  explanation?: string
  remediation?: string
}

interface ComponentStep {
  order: number
  component_type: string
  current_version: string
  target_version: string
  blocking_reasons: string[]
}

interface DomainStep {
  step: number
  domain_id: string
  domain_name: string
  domain_type: string
  status: string
  components_to_upgrade: ComponentStep[]
  blockers: BlockerItem[]
  has_blockers: boolean
  upgradable_count: number
  blocked_count: number
}

interface RollbackRisk {
  score: number
  level: 'low' | 'medium' | 'high'
  reasons: string[]
  host_count: number
  degraded_cluster_count: number
  powered_on_vm_count: number
}

interface Verdict {
  safe: boolean
  confidence: 'high' | 'medium' | 'low'
  summary: string
}

interface UpgradePlan {
  generated_at: string
  sddc_version: string
  steps: DomainStep[]
  total_domains: number
  total_upgradable: number
  total_blockers: number
  blockers_present: boolean
  safe_to_proceed: boolean
  rollback_risk: RollbackRisk
  estimated_window: string
  verdict: Verdict
  runbook_md: string
  error?: string
}

const RISK_COLOR = {
  low: 'text-green-400',
  medium: 'text-yellow-400',
  high: 'text-red-400',
}

const RISK_BG = {
  low: 'bg-green-900/20 border-green-800/40',
  medium: 'bg-yellow-900/20 border-yellow-800/40',
  high: 'bg-red-900/20 border-red-800/40',
}

const COMP_COLOR: Record<string, string> = {
  NSX: 'text-purple-300',
  VCENTER: 'text-blue-300',
  SDDC: 'text-cyan-300',
  ESXI: 'text-orange-300',
  HOST: 'text-orange-300',
  VROPS: 'text-teal-300',
  VRLI: 'text-indigo-300',
}

function compColor(type: string) {
  for (const [k, v] of Object.entries(COMP_COLOR)) {
    if (type.toUpperCase().includes(k)) return v
  }
  return 'text-slate-300'
}

function StepCard({ step, index }: { step: DomainStep; index: number }) {
  const [open, setOpen] = useState(index === 0)

  return (
    <div className={`rounded-xl border ${step.has_blockers ? 'border-red-800/50' : 'border-slate-700/50'} overflow-hidden`}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 bg-slate-800/40 hover:bg-slate-800/60 transition-colors text-left"
      >
        <div className={`flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold flex-shrink-0 ${step.has_blockers ? 'bg-red-900/50 text-red-300 border border-red-700' : 'bg-blue-900/40 text-blue-300 border border-blue-700/50'}`}>
          {step.step}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-100">{step.domain_name}</span>
            <span className="text-[10px] bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded">{step.domain_type}</span>
            {step.has_blockers && (
              <span className="text-[10px] bg-red-900/40 text-red-400 border border-red-800/40 px-1.5 py-0.5 rounded">
                {step.blocked_count} blocker{step.blocked_count !== 1 ? 's' : ''}
              </span>
            )}
          </div>
          <div className="text-[11px] text-slate-500 mt-0.5">
            {step.upgradable_count} component{step.upgradable_count !== 1 ? 's' : ''} to upgrade
            {step.status && ` · Status: ${step.status}`}
          </div>
        </div>
        {open ? <ChevronDown size={14} className="text-slate-500 flex-shrink-0" /> : <ChevronRight size={14} className="text-slate-500 flex-shrink-0" />}
      </button>

      {open && (
        <div className="px-4 pb-4 pt-3 space-y-3 bg-slate-900/30">
          {/* Blockers */}
          {step.blockers.length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-red-400 uppercase tracking-wider mb-2">Blockers</p>
              <div className="space-y-2">
                {step.blockers.map((b, i) => (
                  <div key={i} className="rounded-lg border border-red-900/40 bg-red-900/10 px-3 py-2.5">
                    <p className="text-xs font-semibold text-red-300">{b.component_type} {b.current_version}</p>
                    {b.explanation && <p className="text-[11px] text-slate-300 mt-1">{b.explanation}</p>}
                    {b.remediation && (
                      <div className="flex items-start gap-1.5 mt-1.5">
                        <ArrowRight size={10} className="text-orange-400 mt-0.5 flex-shrink-0" />
                        <p className="text-[11px] text-orange-300">{b.remediation}</p>
                      </div>
                    )}
                    {!b.explanation && b.blocking_reasons.length > 0 && (
                      <ul className="mt-1 space-y-0.5">
                        {b.blocking_reasons.map((r, j) => (
                          <li key={j} className="text-[10px] text-red-400/70">{r}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Components to upgrade */}
          {step.components_to_upgrade.length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Upgrade Order</p>
              <div className="space-y-1.5">
                {step.components_to_upgrade.map((c, i) => (
                  <div key={i} className="flex items-center gap-3 bg-slate-800/40 rounded-lg px-3 py-2">
                    <span className="text-[10px] text-slate-600 w-4 text-right flex-shrink-0">{c.order}.</span>
                    <span className={`text-xs font-medium flex-1 ${compColor(c.component_type)}`}>{c.component_type}</span>
                    <span className="font-mono text-[11px] text-slate-500">{c.current_version}</span>
                    <ArrowRight size={10} className="text-slate-600 flex-shrink-0" />
                    <span className="font-mono text-[11px] text-green-400">{c.target_version}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {step.components_to_upgrade.length === 0 && step.blockers.length === 0 && (
            <p className="text-xs text-slate-500 italic">No components to upgrade in this domain.</p>
          )}

          <div className="flex items-center gap-1.5 bg-slate-800/30 rounded px-2.5 py-1.5 mt-1">
            <Shield size={10} className="text-slate-500 flex-shrink-0" />
            <span className="text-[10px] text-slate-500">Rollback checkpoint: verify cluster health before proceeding to next domain.</span>
          </div>
        </div>
      )}
    </div>
  )
}

export function UpgradePlanPage() {
  const toast = useToast()
  const [plan, setPlan] = useState<UpgradePlan | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`${API_BASE}/api/v1/upgrade/plan`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      if (data.error) throw new Error(data.error)
      setPlan(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load upgrade plan')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  function downloadRunbook() {
    if (!plan?.runbook_md) return
    const blob = new Blob([plan.runbook_md], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `vcf-upgrade-runbook-${new Date().toISOString().slice(0, 10)}.md`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    toast.success('Runbook downloaded')
  }

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Upgrade Sequencing Assistant</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Safe ordered upgrade plan — the view SDDC Manager doesn't give you
          </p>
        </div>
        <div className="flex items-center gap-2">
          {plan?.runbook_md && (
            <button
              onClick={downloadRunbook}
              className="flex items-center gap-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1.5 rounded-lg transition-colors"
            >
              <Download size={12} />
              Runbook
            </button>
          )}
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg transition-colors"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {loading && !plan && (
        <div className="flex items-center gap-2 justify-center py-16 text-slate-500">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Building upgrade plan — querying SDDC Manager…</span>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 text-sm text-amber-400 bg-amber-900/15 border border-amber-800/40 rounded-xl px-4 py-3">
          <AlertTriangle size={14} className="flex-shrink-0" />
          {error}
        </div>
      )}

      {plan && (
        <>
          {/* Verdict banner */}
          <div className={`rounded-xl border px-4 py-4 ${plan.verdict.safe ? 'border-green-800/40 bg-green-900/15' : 'border-red-800/40 bg-red-900/15'}`}>
            <div className="flex items-start gap-3">
              {plan.verdict.safe
                ? <CheckCircle2 size={20} className="text-green-400 flex-shrink-0 mt-0.5" />
                : <XCircle size={20} className="text-red-400 flex-shrink-0 mt-0.5" />
              }
              <div className="flex-1">
                <p className={`text-sm font-semibold ${plan.verdict.safe ? 'text-green-300' : 'text-red-300'}`}>
                  {plan.verdict.summary}
                </p>
                <div className="flex items-center gap-4 mt-2 text-[11px] text-slate-400">
                  <span>SDDC {plan.sddc_version}</span>
                  <span>·</span>
                  <span className="flex items-center gap-1"><Clock size={10} />{plan.estimated_window}</span>
                  <span>·</span>
                  <span>Confidence: {plan.verdict.confidence}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Stats row */}
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: 'Domains', value: plan.total_domains, color: 'text-blue-300' },
              { label: 'To Upgrade', value: plan.total_upgradable, color: 'text-green-300' },
              { label: 'Blockers', value: plan.total_blockers, color: plan.total_blockers ? 'text-red-400' : 'text-slate-400' },
              { label: 'Risk', value: plan.rollback_risk.level.toUpperCase(), color: RISK_COLOR[plan.rollback_risk.level] },
            ].map(s => (
              <div key={s.label} className="bg-slate-800/40 rounded-xl border border-slate-700/50 px-4 py-3 text-center">
                <p className={`text-xl font-bold ${s.color}`}>{s.value}</p>
                <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">{s.label}</p>
              </div>
            ))}
          </div>

          {/* Rollback risk */}
          <div className={`rounded-xl border px-4 py-3 ${RISK_BG[plan.rollback_risk.level]}`}>
            <div className="flex items-center gap-2 mb-2">
              <Shield size={13} className={RISK_COLOR[plan.rollback_risk.level]} />
              <span className={`text-xs font-bold uppercase tracking-wider ${RISK_COLOR[plan.rollback_risk.level]}`}>
                Rollback Risk: {plan.rollback_risk.level} ({plan.rollback_risk.score}/100)
              </span>
            </div>
            <div className="flex gap-4 text-[11px] text-slate-400 mb-2">
              <span>{plan.rollback_risk.host_count} ESXi hosts</span>
              <span>·</span>
              <span>{plan.rollback_risk.powered_on_vm_count} powered-on VMs</span>
              {plan.rollback_risk.degraded_cluster_count > 0 && (
                <>
                  <span>·</span>
                  <span className="text-red-400">{plan.rollback_risk.degraded_cluster_count} degraded cluster(s)</span>
                </>
              )}
            </div>
            {plan.rollback_risk.reasons.length > 0 && (
              <ul className="space-y-0.5">
                {plan.rollback_risk.reasons.map((r, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-[11px] text-slate-400">
                    <Info size={9} className="mt-0.5 flex-shrink-0" />
                    {r}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Pre-flight checklist */}
          <div className="rounded-xl border border-slate-700/50 bg-slate-800/20 px-4 py-4">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-3">Pre-flight Checklist</p>
            <div className="grid grid-cols-2 gap-x-6 gap-y-2">
              {[
                'All clusters ACTIVE / NORMAL',
                'vSAN resync complete (0%)',
                'Maintenance window scheduled',
                'Backup completed and verified',
                'SDDC Manager reachable',
                'NSX Manager cluster status green',
              ].map(item => (
                <label key={item} className="flex items-center gap-2 text-[11px] text-slate-400 cursor-pointer">
                  <input type="checkbox" className="w-3 h-3 rounded accent-blue-500" />
                  {item}
                </label>
              ))}
            </div>
          </div>

          {/* Domain steps */}
          <div>
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-3">
              Upgrade Sequence ({plan.total_domains} domain{plan.total_domains !== 1 ? 's' : ''})
            </p>
            <div className="space-y-3">
              {plan.steps.map((step, i) => (
                <StepCard key={step.domain_id} step={step} index={i} />
              ))}
              {plan.steps.length === 0 && (
                <div className="rounded-xl border border-slate-700/50 px-4 py-8 text-center">
                  <p className="text-sm text-slate-500">No upgrade data available. Configure SDDC Manager in Settings.</p>
                </div>
              )}
            </div>
          </div>

          <p className="text-[10px] text-slate-600 text-right">
            Generated {new Date(plan.generated_at).toLocaleString()}
          </p>
        </>
      )}
    </div>
  )
}
