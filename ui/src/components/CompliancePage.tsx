import { useState } from 'react'
import {
  ShieldCheck, Download, Loader2, CheckCircle, AlertTriangle,
  FileText, Server, Settings, Activity, Archive, Upload, Laptop,
  BotMessageSquare, Terminal, Radar, TrendingUp, Users, Bell,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

const PERIODS = [
  { value: '30d', label: 'Last 30 days' },
  { value: '90d', label: 'Last 90 days (recommended)' },
  { value: '180d', label: 'Last 180 days' },
  { value: '365d', label: 'Last 365 days (full year)' },
]

const INCLUDED = [
  { icon: Activity, label: 'VCF readiness scoring history', detail: 'Last 10 scoring runs with sub-scores and risk factors' },
  { icon: Users, label: 'AD privileged group memberships', detail: 'Domain Admins, privileged accounts, stale accounts' },
  { icon: AlertTriangle, label: 'Vulnerability findings', detail: 'All vuln scans with severity breakdown' },
  { icon: Server, label: 'Fleet configuration snapshot', detail: 'vCenter, hosts, clusters, datastores at export time' },
  { icon: FileText, label: 'Audit log', detail: 'All API actions within the selected period' },
  { icon: ShieldCheck, label: 'SHA-256 manifest', detail: 'Integrity manifest for all included files' },
]

function NavBar({ onBack }: { onBack: () => void }) {
  return (
    <div className="h-11 bg-vmware-navy/95 backdrop-blur border-b border-vmware-border flex items-center px-4 gap-2 shrink-0">
      <span className="text-xs font-bold text-vmware-blue tracking-widest uppercase select-none">MCO</span>
      <span className="text-slate-700">|</span>
      {[
        { label: 'Fleet', icon: Server, hash: '#/fleet' },
        { label: 'Workspace', icon: Settings, hash: '#/workspace' },
        { label: 'Analysis', icon: Activity, hash: '#/analysis' },
        { label: 'Agent', icon: BotMessageSquare, hash: '#/agent' },
        { label: 'Bulk', icon: Upload, hash: '#/bulk' },
        { label: 'Guest', icon: Laptop, hash: '#/guest' },
        { label: 'Kubectl', icon: Terminal, hash: '#/kubectl' },
        { label: 'Discovery', icon: Radar, hash: '#/discovery' },
        { label: 'Directory', icon: Users, hash: '#/directory' },
        { label: 'Trends', icon: TrendingUp, hash: '#/trends' },
        { label: 'Alerts', icon: Bell, hash: '#/alerts' },
        { label: 'Archive', icon: Archive, hash: '#/archive' },
      ].map(({ label, icon: Icon, hash }) => (
        <a key={label} href={hash}
          className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-white px-2 py-1 rounded hover:bg-slate-800/60 transition-colors">
          <Icon size={11} /> {label}
        </a>
      ))}
      <span className="flex items-center gap-1 text-[11px] text-vmware-blue px-2 py-1 rounded bg-blue-900/30 font-semibold ml-1">
        <ShieldCheck size={11} /> Compliance
      </span>
      <div className="flex-1" />
      <a href="#/settings" className="text-slate-500 hover:text-slate-300 transition-colors">
        <Settings size={14} />
      </a>
    </div>
  )
}

export function CompliancePage({ onBack }: { onBack: () => void }) {
  const [period, setPeriod] = useState('90d')
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [lastExport, setLastExport] = useState('')

  async function handleExport() {
    setExporting(true)
    setError('')
    try {
      const r = await fetch(`${API_BASE}/api/v1/compliance/export?period=${period}`)
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: 'Export failed' }))
        throw new Error(err.detail || 'Export failed')
      }
      const blob = await r.blob()
      const cd = r.headers.get('content-disposition') || ''
      const filenameMatch = cd.match(/filename="?([^"]+)"?/)
      const filename = filenameMatch?.[1] || `mco-compliance-${new Date().toISOString().slice(0, 10)}.tar.gz`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      setLastExport(new Date().toLocaleString())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Export failed')
    }
    setExporting(false)
  }

  return (
    <div className="flex flex-col h-screen bg-vmware-dark text-white overflow-hidden">
      <NavBar onBack={onBack} />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl mx-auto">

          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-xl bg-blue-900/30 border border-blue-800/40 flex items-center justify-center">
              <ShieldCheck size={20} className="text-blue-400" />
            </div>
            <div>
              <h1 className="text-base font-bold text-white">Compliance Snapshot Export</h1>
              <p className="text-xs text-slate-400">Generate a signed audit package for PCI, SOC2, or SOX auditors</p>
            </div>
          </div>

          {/* What's included */}
          <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 mb-5">
            <h2 className="text-xs font-bold text-white mb-3 uppercase tracking-wider">Package Contents</h2>
            <div className="space-y-2.5">
              {INCLUDED.map(({ icon: Icon, label, detail }) => (
                <div key={label} className="flex items-start gap-3">
                  <div className="w-6 h-6 rounded-lg bg-slate-800/60 flex items-center justify-center shrink-0 mt-0.5">
                    <Icon size={11} className="text-slate-400" />
                  </div>
                  <div>
                    <p className="text-xs text-white font-medium">{label}</p>
                    <p className="text-[10px] text-slate-500">{detail}</p>
                  </div>
                  <CheckCircle size={13} className="text-green-500 ml-auto shrink-0 mt-0.5" />
                </div>
              ))}
            </div>
            <div className="mt-3 pt-3 border-t border-vmware-border">
              <p className="text-[10px] text-slate-500">
                Output: <span className="text-slate-400 font-mono">mco-compliance-YYYY-MM-DD.tar.gz</span> containing
                JSON data files + SHA-256 manifest
              </p>
            </div>
          </div>

          {/* Period selector */}
          <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 mb-5">
            <h2 className="text-xs font-bold text-white mb-3 uppercase tracking-wider">Audit Period</h2>
            <div className="grid grid-cols-2 gap-2">
              {PERIODS.map(p => (
                <button key={p.value} onClick={() => setPeriod(p.value)}
                  className={`text-left text-xs px-3 py-2.5 rounded-lg border transition-colors ${period === p.value ? 'border-blue-600 bg-blue-900/20 text-blue-300' : 'border-vmware-border text-slate-400 hover:border-slate-500'}`}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-800/50 bg-red-900/10 px-4 py-3 mb-4">
              <AlertTriangle size={13} className="text-red-400 mt-0.5 shrink-0" />
              <p className="text-xs text-red-300">{error}</p>
            </div>
          )}

          <button onClick={handleExport} disabled={exporting}
            className="w-full flex items-center justify-center gap-2 px-6 py-3 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-xl text-sm font-semibold transition-colors">
            {exporting
              ? <><Loader2 size={15} className="animate-spin" /> Generating package…</>
              : <><Download size={15} /> Export Compliance Package</>
            }
          </button>

          {lastExport && (
            <p className="text-center text-[10px] text-slate-500 mt-3">
              Last exported: {lastExport}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
