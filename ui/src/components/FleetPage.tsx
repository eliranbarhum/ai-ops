import { useState, useEffect, useRef } from 'react'
import { Activity, Archive, Server, Network, Shield, Cpu, HardDrive, Loader2, RefreshCw, Layers, Settings, Zap, Box, AlertTriangle, CheckCircle, Lock, GitBranch, TrendingUp, AlertCircle, BotMessageSquare, Upload, Laptop, Terminal, Radar, X, ChevronRight, Play, Square, Database, Wifi, Users, Monitor } from 'lucide-react'
import { useVisibilityPolling } from '../hooks/useVisibilityPolling'
import { PageHeader } from './ui'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface ESXiHost {
  name: string
  host_id?: string
  connection_state: string
  power_state: string
  esxi_version: string
  esxi_build: string
  cpu_model: string
  cpu_sockets: number
  cpu_cores: number
  memory_gb: number
  cpu_usage: number
  ram_usage: number
  management_ip?: string
}

interface Datastore {
  name: string
  type: string
  capacity_gb: number
  free_gb: number
  used_pct: number
}

interface NetworkItem {
  name: string
  type: string
  network: string
}

interface Appliance {
  name: string
  fqdn: string
  id?: string
  version?: string
  build?: string
  status?: string
  status_detail?: string
  status_source?: string
  ip?: string
  cluster_vip?: string
}

interface Cluster {
  name: string
  cluster: string
  ha_enabled: boolean
  drs_enabled: boolean
  cpu_usage_pct: number
  ram_usage_pct: number
  storage_capacity_gb: number
  storage_provisioned_gb: number
}

interface FleetSources {
  vcenter: boolean
  vrops: boolean
  sddc: boolean
  datastores_source: 'vcenter' | 'vrops' | 'none'
  networks_source: 'vcenter' | 'vrops' | 'none'
  hardware_source: 'vrops' | 'vcenter' | 'vcenter-ns' | 'none'
  usage_source: 'vrops' | 'vcenter' | 'vcenter-ns' | 'none'
  cluster_usage_source?: 'vrops' | 'vcenter-ns' | 'none'
  vrops_error?: boolean
}

interface FleetData {
  datacenters: { name: string; datacenter: string }[]
  clusters: Cluster[]
  hosts: ESXiHost[]
  vm_count: number
  datastores: Datastore[]
  networks: NetworkItem[]
  management_plane: {
    vcenter?: Appliance[]
    nsx_manager?: Appliance[]
    sddc_manager?: Appliance[]
    vcf_operations?: Appliance[]
    esxi_hosts?: Appliance[]
    vcf_management_services?: Appliance[]
  }
  component_versions?: Record<string, { version: string; build?: string; name?: string; fqdn?: string }>
  _sources?: FleetSources
}

function fmtRam(gb: number): string {
  if (!gb) return '—'
  if (gb > 999) return `${(gb / 1024).toFixed(1)} TB`
  return `${gb} GB`
}

function fmtStorage(gb: number): string {
  if (!gb) return '—'
  const tib = gb / 1024
  if (tib < 0.1) return `${gb.toFixed(1)} GB`
  return `${tib.toFixed(1)} TiB`
}

function UsageBar({ pct }: { pct: number }) {
  const color = pct >= 85 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-500' : 'bg-emerald-500'
  const textColor = pct >= 85 ? 'text-red-400' : pct >= 70 ? 'text-amber-400' : 'text-emerald-400'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-slate-700/80 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${Math.min(100, pct || 0)}%` }} />
      </div>
      <span className={`text-xs font-medium w-10 text-right flex-shrink-0 ${textColor}`}>
        {pct ? `${pct.toFixed(0)}%` : '—'}
      </span>
    </div>
  )
}

function StatusPill({ state }: { state: string }) {
  const connected = state === 'CONNECTED'
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${
      connected ? 'bg-emerald-900/40 text-emerald-300 border border-emerald-800/60' : 'bg-red-900/40 text-red-300 border border-red-800/60'
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald-400' : 'bg-red-400'}`} />
      {state}
    </span>
  )
}

function NetworkTypeBadge({ type }: { type: string }) {
  const label = type?.replace(/_/g, ' ') || '—'
  const colors: Record<string, string> = {
    DISTRIBUTED_PORTGROUP: 'bg-blue-900/50 text-blue-300 border-blue-800/60',
    OPAQUE_NETWORK: 'bg-purple-900/50 text-purple-300 border-purple-800/60',
    STANDARD_PORTGROUP: 'bg-slate-700/80 text-slate-300 border-slate-600/60',
  }
  const cls = colors[type] ?? 'bg-slate-700/80 text-slate-300 border-slate-600/60'
  return <span className={`text-xs px-2 py-0.5 rounded border font-medium ${cls}`}>{label}</span>
}

function SectionHeader({ icon: Icon, title, meta }: { icon: React.ElementType; title: string; meta?: string }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon size={14} className="text-blue-400 flex-shrink-0" />
      <h2 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">{title}</h2>
      {meta && <span className="text-xs text-slate-500 font-normal normal-case tracking-normal">· {meta}</span>}
    </div>
  )
}

function ClusterCard({ cluster }: { cluster: Cluster }) {
  const storCapTib = cluster.storage_capacity_gb / 1024
  const storProvTib = cluster.storage_provisioned_gb / 1024
  const storUsedPct = storCapTib > 0 ? (storProvTib / storCapTib) * 100 : 0
  const health = Math.max(cluster.cpu_usage_pct || 0, cluster.ram_usage_pct || 0, storUsedPct)
  const healthColor = health >= 85 ? 'border-red-800/60' : health >= 70 ? 'border-amber-800/60' : 'border-vmware-border'

  return (
    <div className={`rounded-xl border ${healthColor} bg-vmware-card p-4 space-y-3`}>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-sm font-semibold text-white leading-tight">{cluster.name}</div>
          <div className="flex items-center gap-2 mt-1">
            {cluster.ha_enabled
              ? <span className="text-xs text-emerald-400 font-semibold bg-emerald-900/30 px-1.5 py-0.5 rounded">HA</span>
              : <span className="text-xs text-slate-500 font-semibold bg-slate-800 px-1.5 py-0.5 rounded">HA off</span>}
            {cluster.drs_enabled
              ? <span className="text-xs text-blue-400 font-semibold bg-blue-900/30 px-1.5 py-0.5 rounded">DRS</span>
              : <span className="text-xs text-slate-500 font-semibold bg-slate-800 px-1.5 py-0.5 rounded">DRS off</span>}
          </div>
        </div>
        <Layers size={15} className="text-slate-500 flex-shrink-0 mt-0.5" />
      </div>

      <div className="space-y-2.5">
        <div>
          <div className="flex justify-between items-center mb-1">
            <span className="text-xs text-slate-400">CPU</span>
          </div>
          <UsageBar pct={cluster.cpu_usage_pct} />
        </div>
        <div>
          <div className="flex justify-between items-center mb-1">
            <span className="text-xs text-slate-400">Memory</span>
          </div>
          <UsageBar pct={cluster.ram_usage_pct} />
        </div>
        <div>
          <div className="flex justify-between items-center mb-1">
            <span className="text-xs text-slate-400">Storage</span>
            {storCapTib > 0 && <span className="text-xs text-slate-400">{storProvTib.toFixed(1)} / {storCapTib.toFixed(1)} TiB</span>}
          </div>
          <UsageBar pct={storUsedPct} />
        </div>
      </div>
    </div>
  )
}

interface HostDetailVM {
  vm_id: string
  name: string
  power_state: string
  memory_size_MiB: number
  cpu_count: number
}

interface HostDetailDatastore {
  datastore_id: string
  name: string
  type: string
  capacity_gb: number
  free_gb: number
  used_pct: number
}

interface VMKernelAdapter {
  name: string
  ip: string
  mac: string
  enabled: boolean
}

interface HostDetail {
  host_id: string
  cluster_name: string
  vm_count: number
  vms: HostDetailVM[]
  datastores: HostDetailDatastore[]
  vmkernel_adapters: VMKernelAdapter[]
}

function PowerStateDot({ state }: { state: string }) {
  const on = state === 'POWERED_ON'
  const suspended = state === 'SUSPENDED'
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded ${
      on ? 'bg-emerald-900/40 text-emerald-300' :
      suspended ? 'bg-amber-900/40 text-amber-300' :
      'bg-slate-700 text-slate-400'
    }`}>
      <span className={`w-1 h-1 rounded-full ${on ? 'bg-emerald-400' : suspended ? 'bg-amber-400' : 'bg-slate-500'}`} />
      {on ? 'On' : suspended ? 'Suspended' : 'Off'}
    </span>
  )
}

function HostDrawer({ host, onClose, discoveryData }: {
  host: ESXiHost
  onClose: () => void
  discoveryData?: { risk_level: string; open_port_count: number; top_ports: number[] }
}) {
  const [detail, setDetail] = useState<HostDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const drawerRef = useRef<HTMLDivElement>(null)

  const hostId = (host as { host_id?: string }).host_id || ''

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  useEffect(() => {
    if (!hostId) { setLoading(false); return }
    setLoading(true)
    setError('')
    fetch(`${API_BASE}/api/v1/fleet/hosts/${hostId}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setDetail(d); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [hostId])

  const riskColor: Record<string, string> = {
    critical: 'text-red-400 bg-red-900/20 border-red-800/50',
    high: 'text-orange-400 bg-orange-900/20 border-orange-800/50',
    medium: 'text-yellow-400 bg-yellow-900/20 border-yellow-800/50',
    low: 'text-green-400 bg-green-900/20 border-green-800/50',
  }

  const poweredOn = detail?.vms.filter(v => v.power_state === 'POWERED_ON').length ?? 0

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />

      {/* Drawer */}
      <div ref={drawerRef}
        className="fixed right-0 top-0 h-full w-[520px] max-w-full bg-[#0d1117] border-l border-slate-700/80 z-50 flex flex-col shadow-2xl"
        style={{ animation: 'slideInRight 0.2s ease-out' }}
      >
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-slate-700/60 flex-shrink-0 bg-slate-800/40">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              <Server size={14} className="text-blue-400 flex-shrink-0" />
              <span className="text-sm font-semibold text-white font-mono truncate" title={host.name}>{host.name}</span>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <StatusPill state={host.connection_state} />
              {host.esxi_version && (
                <span className="text-[10px] font-mono text-slate-400 bg-slate-700/60 px-2 py-0.5 rounded">ESXi {host.esxi_version}</span>
              )}
              {detail?.cluster_name && (
                <span className="text-[10px] text-blue-300 bg-blue-900/20 border border-blue-800/40 px-2 py-0.5 rounded">{detail.cluster_name}</span>
              )}
            </div>
          </div>
          <button onClick={onClose} className="ml-3 flex-shrink-0 text-slate-400 hover:text-white p-1 rounded hover:bg-slate-700 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto min-h-0 px-5 py-4 space-y-5">

          {/* Hardware summary */}
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Cpu size={12} className="text-blue-400" />
              <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Hardware</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: 'CPU Model', value: host.cpu_model || '—', full: true },
                { label: 'CPU Cores', value: host.cpu_cores ? `${host.cpu_sockets} socket${host.cpu_sockets !== 1 ? 's' : ''} · ${host.cpu_cores} cores` : '—' },
                { label: 'Memory', value: fmtRam(host.memory_gb) },
                { label: 'Power', value: host.power_state || '—' },
              ].map(({ label, value, full }) => (
                <div key={label} className={`${full ? 'col-span-2' : ''} bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5`}>
                  <div className="text-[10px] text-slate-500 mb-1">{label}</div>
                  <div className="text-xs text-slate-200 font-mono truncate" title={value}>{value}</div>
                </div>
              ))}
              <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5">
                <div className="text-[10px] text-slate-500 mb-1">CPU Usage</div>
                <UsageBar pct={host.cpu_usage || 0} />
              </div>
              <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5">
                <div className="text-[10px] text-slate-500 mb-1">RAM Usage</div>
                <UsageBar pct={host.ram_usage || 0} />
              </div>
            </div>
          </section>

          {/* Network / Discovery scan data */}
          {discoveryData && (
            <section>
              <div className="flex items-center gap-2 mb-3">
                <Radar size={12} className="text-purple-400" />
                <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Network Scan</span>
              </div>
              <div className={`rounded-lg border px-3 py-2.5 flex items-center gap-3 ${riskColor[discoveryData.risk_level] || 'text-slate-400 bg-slate-800/40 border-slate-700/50'}`}>
                <span className="text-xs font-bold uppercase">{discoveryData.risk_level} risk</span>
                <span className="text-xs text-slate-400">{discoveryData.open_port_count} open ports</span>
                <div className="flex items-center gap-1 ml-auto">
                  {(discoveryData.top_ports || []).map((p: number) => (
                    <span key={p} className="text-[9px] font-mono bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded border border-slate-700/50">{p}</span>
                  ))}
                </div>
              </div>
            </section>
          )}

          {loading && (
            <div className="flex items-center gap-2 py-8 justify-center text-slate-500">
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">Loading host detail…</span>
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 text-xs text-amber-400 bg-amber-900/15 border border-amber-800/40 rounded-lg px-3 py-2.5">
              <AlertTriangle size={12} />
              {hostId ? error : 'Host ID unavailable — detail requires vCenter host record.'}
            </div>
          )}

          {detail && (
            <>
              {/* Virtual Machines */}
              <section>
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Monitor size={12} className="text-blue-400" />
                    <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Virtual Machines</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-emerald-400">{poweredOn} on</span>
                    <span className="text-[10px] text-slate-500">/ {detail.vm_count} total</span>
                  </div>
                </div>
                {detail.vms.length === 0 ? (
                  <div className="text-xs text-slate-500 py-2">No VMs found on this host</div>
                ) : (
                  <div className="rounded-lg border border-slate-700/50 overflow-hidden">
                    <div className="divide-y divide-slate-700/40">
                      {detail.vms.map((vm) => (
                        <div key={vm.vm_id} className="flex items-center gap-3 px-3 py-2 hover:bg-slate-800/30 transition-colors">
                          <PowerStateDot state={vm.power_state} />
                          <span className="text-xs text-slate-200 font-mono flex-1 truncate min-w-0">{vm.name}</span>
                          <div className="flex items-center gap-2 text-[10px] text-slate-500 flex-shrink-0">
                            {vm.cpu_count > 0 && <span>{vm.cpu_count} vCPU</span>}
                            {vm.memory_size_MiB > 0 && <span>{fmtRam(vm.memory_size_MiB / 1024)}</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                    {detail.vm_count > detail.vms.length && (
                      <div className="text-[10px] text-slate-500 text-center py-1.5 border-t border-slate-700/40 bg-slate-800/20">
                        Showing {detail.vms.length} of {detail.vm_count} VMs
                      </div>
                    )}
                  </div>
                )}
              </section>

              {/* Datastores */}
              {detail.datastores.length > 0 && (
                <section>
                  <div className="flex items-center gap-2 mb-3">
                    <Database size={12} className="text-blue-400" />
                    <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Datastores</span>
                    <span className="text-[10px] text-slate-500">({detail.datastores.length})</span>
                  </div>
                  <div className="rounded-lg border border-slate-700/50 overflow-hidden">
                    <div className="divide-y divide-slate-700/40">
                      {detail.datastores.map((ds) => (
                        <div key={ds.datastore_id} className="px-3 py-2.5 hover:bg-slate-800/30 transition-colors">
                          <div className="flex items-center gap-2 mb-1.5">
                            <span className="text-xs text-slate-200 font-mono flex-1 truncate">{ds.name}</span>
                            <span className="text-[9px] bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded flex-shrink-0">{ds.type}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            <div className="flex-1">
                              <UsageBar pct={ds.used_pct} />
                            </div>
                            <span className="text-[10px] text-slate-500 flex-shrink-0 w-24 text-right">
                              {fmtStorage(ds.free_gb)} free / {fmtStorage(ds.capacity_gb)}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </section>
              )}

              {/* VMkernel adapters */}
              {detail.vmkernel_adapters.length > 0 && (
                <section>
                  <div className="flex items-center gap-2 mb-3">
                    <Wifi size={12} className="text-blue-400" />
                    <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">VMkernel Adapters</span>
                  </div>
                  <div className="rounded-lg border border-slate-700/50 overflow-hidden">
                    <div className="divide-y divide-slate-700/40">
                      {detail.vmkernel_adapters.map((vmk, i) => (
                        <div key={i} className="flex items-center gap-3 px-3 py-2 hover:bg-slate-800/30 transition-colors">
                          <span className="text-xs font-mono text-slate-300 w-10 flex-shrink-0">{vmk.name || `vmk${i}`}</span>
                          <span className="text-xs font-mono text-blue-300 flex-1">{vmk.ip || '—'}</span>
                          {vmk.mac && <span className="text-[10px] font-mono text-slate-500">{vmk.mac}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                </section>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-slate-700/60 px-5 py-3 flex-shrink-0 bg-slate-800/20">
          <div className="text-[10px] text-slate-600 text-center font-mono">{host.name}</div>
        </div>
      </div>

      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </>
  )
}

interface NetworkDetail {
  network_id: string
  name: string
  type: string
  vm_count: number | null
  host_count: number | null
  vm_filter_supported: boolean
}

function NetworkDrawer({ network, onClose }: { network: NetworkItem; onClose: () => void }) {
  const [detail, setDetail] = useState<NetworkDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  useEffect(() => {
    const netId = network.network
    if (!netId) { setLoading(false); return }
    setLoading(true); setError('')
    fetch(`${API_BASE}/api/v1/fleet/networks/${encodeURIComponent(netId)}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setDetail(d); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [network.network])

  const typeLabels: Record<string, { label: string; desc: string; color: string }> = {
    DISTRIBUTED_PORTGROUP: { label: 'DVS Port Group', desc: 'vSphere Distributed Switch port group', color: 'text-blue-300 bg-blue-900/20 border-blue-800/40' },
    OPAQUE_NETWORK:        { label: 'Opaque Network', desc: 'NSX-T or third-party managed network', color: 'text-purple-300 bg-purple-900/20 border-purple-800/40' },
    STANDARD_PORTGROUP:    { label: 'Standard Port Group', desc: 'vSphere Standard Switch port group', color: 'text-slate-300 bg-slate-700/50 border-slate-600/50' },
  }
  const typeInfo = typeLabels[network.type] ?? { label: network.type || '—', desc: '', color: 'text-slate-300 bg-slate-700/50 border-slate-600/50' }

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <div className="fixed right-0 top-0 h-full w-[420px] max-w-full bg-[#0d1117] border-l border-slate-700/80 z-50 flex flex-col shadow-2xl"
        style={{ animation: 'slideInRight 0.2s ease-out' }}>
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-slate-700/60 flex-shrink-0 bg-slate-800/40">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              <Network size={14} className="text-blue-400 flex-shrink-0" />
              <span className="text-sm font-semibold text-white font-mono truncate" title={network.name}>{network.name}</span>
            </div>
            <span className={`text-[10px] font-medium px-2 py-0.5 rounded border ${typeInfo.color}`}>{typeInfo.label}</span>
          </div>
          <button onClick={onClose} className="ml-3 flex-shrink-0 text-slate-400 hover:text-white p-1 rounded hover:bg-slate-700 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto min-h-0 px-5 py-4 space-y-5">
          {/* Network info */}
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Wifi size={12} className="text-blue-400" />
              <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Network Details</span>
            </div>
            <div className="grid grid-cols-1 gap-2">
              <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5">
                <div className="text-[10px] text-slate-500 mb-1">Network ID</div>
                <div className="text-xs text-slate-200 font-mono break-all">{network.network || '—'}</div>
              </div>
              <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5">
                <div className="text-[10px] text-slate-500 mb-1">Type</div>
                <div className="text-xs text-slate-200">{typeInfo.label}</div>
                {typeInfo.desc && <div className="text-[10px] text-slate-500 mt-0.5">{typeInfo.desc}</div>}
              </div>
            </div>
          </section>

          {loading && (
            <div className="flex items-center gap-2 py-6 justify-center text-slate-500">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">Loading network detail…</span>
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 text-xs text-amber-400 bg-amber-900/15 border border-amber-800/40 rounded-lg px-3 py-2.5">
              <AlertTriangle size={12} /> {error}
            </div>
          )}

          {detail && (
            <section>
              <div className="flex items-center gap-2 mb-3">
                <Users size={12} className="text-blue-400" />
                <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Connectivity</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5">
                  <div className="text-[10px] text-slate-500 mb-1">Connected VMs</div>
                  {detail.vm_count !== null
                    ? <div className="text-lg font-bold text-white">{detail.vm_count}</div>
                    : <div className="text-xs text-slate-500 italic">unavailable</div>}
                </div>
                <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5">
                  <div className="text-[10px] text-slate-500 mb-1">Connected Hosts</div>
                  {detail.host_count !== null
                    ? <div className="text-lg font-bold text-white">{detail.host_count}</div>
                    : <div className="text-xs text-slate-500 italic">unavailable</div>}
                </div>
              </div>
              {!detail.vm_filter_supported && (
                <div className="mt-2 text-[10px] text-slate-600 flex items-center gap-1.5">
                  <AlertCircle size={10} />
                  Per-network VM/host filtering not available in VCF 9.x
                </div>
              )}
            </section>
          )}
        </div>

        <div className="border-t border-slate-700/60 px-5 py-3 flex-shrink-0 bg-slate-800/20">
          <div className="text-[10px] text-slate-600 text-center font-mono">{network.network}</div>
        </div>
      </div>
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </>
  )
}

const APPLIANCE_GROUP_META: Record<string, { icon: React.ElementType; color: string; desc: string }> = {
  'vCenter':        { icon: Server,       color: 'text-blue-400',   desc: 'VMware vCenter Server' },
  'NSX Manager':    { icon: GitBranch,    color: 'text-purple-400', desc: 'VMware NSX Manager' },
  'SDDC Manager':   { icon: Layers,       color: 'text-amber-400',  desc: 'VMware SDDC Manager' },
  'VCF Operations': { icon: TrendingUp,   color: 'text-teal-400',   desc: 'VCF Operations (vROps)' },
  'Mgmt Services':  { icon: CheckCircle,  color: 'text-emerald-400',desc: 'VCF Internal Service' },
}

function ApplianceDrawer({ appliance, group, onClose }: {
  appliance: Appliance
  group: string
  onClose: () => void
}) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  const meta = APPLIANCE_GROUP_META[group] ?? { icon: Shield, color: 'text-slate-400', desc: group }
  const Icon = meta.icon
  const status = appliance.status ?? ''
  const isUp = status === 'UP' || status === 'ACTIVE' || status === 'RUNNING'
  const isDown = status && !isUp

  const fields: { label: string; value: string | undefined; mono?: boolean; full?: boolean }[] = [
    { label: 'FQDN',          value: appliance.fqdn || appliance.name, mono: true, full: true },
    { label: 'IP Address',    value: appliance.ip,  mono: true },
    { label: 'Status',        value: status || undefined },
    { label: 'Version',       value: appliance.version, mono: true },
    { label: 'Build',         value: appliance.build,   mono: true },
    { label: 'Cluster VIP',   value: appliance.cluster_vip, mono: true },
    { label: 'Component ID',  value: appliance.id },
  ].filter(f => f.value)

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <div className="fixed right-0 top-0 h-full w-[440px] max-w-full bg-[#0d1117] border-l border-slate-700/80 z-50 flex flex-col shadow-2xl"
        style={{ animation: 'slideInRight 0.2s ease-out' }}>

        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-slate-700/60 flex-shrink-0 bg-slate-800/40">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1.5">
              <Icon size={14} className={`${meta.color} flex-shrink-0`} />
              <span className="text-sm font-semibold text-white font-mono truncate" title={appliance.name || appliance.fqdn}>{appliance.name || appliance.fqdn}</span>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] text-slate-400 bg-slate-700/60 px-2 py-0.5 rounded">{meta.desc}</span>
              {status && (
                <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded border ${
                  isUp   ? 'bg-emerald-900/30 border-emerald-800/50 text-emerald-300' :
                  isDown ? 'bg-red-900/30 border-red-800/50 text-red-300' :
                           'bg-slate-700/50 border-slate-600/50 text-slate-400'
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${isUp ? 'bg-emerald-400' : isDown ? 'bg-red-400' : 'bg-slate-500'}`} />
                  {status}
                </span>
              )}
            </div>
          </div>
          <button onClick={onClose} className="ml-3 flex-shrink-0 text-slate-400 hover:text-white p-1 rounded hover:bg-slate-700 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto min-h-0 px-5 py-4 space-y-5">

          {/* Why is this in error? — full context, not just a red word */}
          {isDown && appliance.status_detail && (
            <section role="alert">
              <div className="flex items-center gap-2 mb-3">
                <AlertTriangle size={12} className="text-amber-400" />
                <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Why {status}?</span>
              </div>
              <div className="bg-amber-900/15 border border-amber-800/40 rounded-lg px-3 py-2.5 space-y-2">
                <p className="text-xs text-amber-100/90 leading-relaxed">{appliance.status_detail}</p>
                {appliance.status_source && (
                  <p className="text-[10px] text-amber-400/70">Source: {appliance.status_source}</p>
                )}
              </div>
            </section>
          )}

          {/* Identity & Network */}
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Shield size={12} className="text-blue-400" />
              <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Appliance Details</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {fields.map(({ label, value, mono, full }) => (
                <div key={label} className={`${full ? 'col-span-2' : ''} bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5`}>
                  <div className="text-[10px] text-slate-500 mb-1">{label}</div>
                  <div className={`text-xs break-all ${mono ? 'font-mono text-slate-200' : 'text-slate-200'}`}>{value}</div>
                </div>
              ))}
            </div>
          </section>

          {/* Version breakdown (if build is present) */}
          {appliance.version && (
            <section>
              <div className="flex items-center gap-2 mb-3">
                <GitBranch size={12} className="text-blue-400" />
                <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Software Version</span>
              </div>
              <div className="rounded-lg border border-slate-700/50 overflow-hidden">
                <div className="px-3 py-2.5 bg-slate-800/30">
                  <div className="text-[10px] text-slate-500 mb-1">Full Version String</div>
                  <div className="text-xs font-mono text-slate-200 break-all">{appliance.version}</div>
                </div>
                {appliance.build && (
                  <div className="px-3 py-2.5 border-t border-slate-700/40">
                    <div className="text-[10px] text-slate-500 mb-1">Build Number</div>
                    <div className="text-xs font-mono text-slate-200">{appliance.build}</div>
                  </div>
                )}
                {appliance.version && (
                  <div className="px-3 py-2.5 border-t border-slate-700/40 bg-slate-800/10">
                    <div className="text-[10px] text-slate-500 mb-1">Major.Minor</div>
                    <div className="text-xs font-mono text-blue-300">
                      {appliance.version.split('.').slice(0, 2).join('.')}
                    </div>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* NSX cluster info */}
          {appliance.cluster_vip && (
            <section>
              <div className="flex items-center gap-2 mb-3">
                <Network size={12} className="text-purple-400" />
                <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Cluster Info</span>
              </div>
              <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2.5">
                <div className="text-[10px] text-slate-500 mb-1">Cluster VIP (FQDN)</div>
                <div className="text-xs font-mono text-slate-200 break-all">{appliance.cluster_vip}</div>
              </div>
            </section>
          )}

        </div>

        <div className="border-t border-slate-700/60 px-5 py-3 flex-shrink-0 bg-slate-800/20">
          <div className="text-[10px] text-slate-600 text-center font-mono truncate">{appliance.fqdn || appliance.name}</div>
        </div>
      </div>
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </>
  )
}

interface ServiceHealth {
  overall: string
  services: Record<string, string>
  llm_provider: string
  llm_model: string
  timestamp: string
}

function StatusBar({ lastRefresh }: { lastRefresh: Date | null }) {
  const [health, setHealth] = useState<ServiceHealth | null>(null)
  const [elapsed, setElapsed] = useState('')

  useVisibilityPolling(async () => {
    const r = await fetch(`${API_BASE}/api/v1/health/services`)
    if (r.ok) setHealth(await r.json())
  }, 60_000)

  useEffect(() => {
    if (!lastRefresh) return
    const tick = () => {
      const secs = Math.round((Date.now() - lastRefresh.getTime()) / 1000)
      if (secs < 60) setElapsed(`${secs}s ago`)
      else setElapsed(`${Math.floor(secs / 60)}m ago`)
    }
    tick()
    const id = setInterval(tick, 10_000)
    return () => clearInterval(id)
  }, [lastRefresh])

  const dotColor = (s: string) => s === 'ok' ? 'bg-emerald-400' : s === 'error' ? 'bg-amber-400' : 'bg-red-500'
  const overallColor = health?.overall === 'ok' ? 'text-emerald-400' : health?.overall === 'degraded' ? 'text-amber-400' : 'text-red-400'
  const SERVICE_LABELS: Record<string, string> = {
    vcenter: 'vCenter', sddc: 'SDDC', vrops: 'vROps', logs: 'Logs',
    tools: 'Tools', orchestrator: 'Orch', llm_gateway: 'LLM', discovery: 'Discovery',
  }

  const providerLabel = health ? `${health.llm_provider}${health.llm_model ? ` · ${health.llm_model}` : ''}` : '…'

  return (
    <div className="bg-[#070b12] border-b border-slate-800/80 px-4 py-1 flex items-center gap-4 text-[10px] text-slate-500 overflow-x-auto">
      {/* Service dots */}
      <div className="flex items-center gap-2.5 flex-shrink-0">
        {health ? Object.entries(health.services).map(([svc, status]) => (
          <div key={svc} className="flex items-center gap-1" title={`${SERVICE_LABELS[svc] ?? svc}: ${status}`}>
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor(status)}`} />
            <span className="text-slate-600">{SERVICE_LABELS[svc] ?? svc}</span>
          </div>
        )) : (
          <span className="text-slate-700">checking services…</span>
        )}
      </div>

      <span className="text-slate-700">|</span>

      {/* Overall status */}
      {health && (
        <span className={`font-medium flex-shrink-0 ${overallColor}`}>
          {health.overall === 'ok' ? 'All services healthy' : health.overall === 'degraded' ? 'Degraded' : 'Services unreachable'}
        </span>
      )}

      <span className="text-slate-700">|</span>

      {/* LLM provider */}
      <span className="flex-shrink-0 text-slate-500">LLM: <span className="text-slate-400">{providerLabel}</span></span>

      {/* Fleet refresh */}
      {lastRefresh && (
        <>
          <span className="text-slate-700">|</span>
          <span className="flex-shrink-0">fleet refreshed <span className="text-slate-400">{elapsed}</span></span>
        </>
      )}
    </div>
  )
}

function MetricTile({ icon: Icon, label, value, sub, alert }: { icon: React.ElementType; label: string; value: string | number; sub: string; alert?: boolean }) {
  return (
    <div className={`rounded-xl border ${alert ? 'border-red-800/60 bg-red-900/10 hover:border-red-700' : 'border-vmware-border bg-vmware-card hover:border-slate-600'} px-4 py-3 transition-colors`}>
      <div className="flex items-center gap-2 mb-2">
        <Icon size={13} className={alert ? 'text-red-400' : 'text-blue-400'} />
        <span className="text-xs text-slate-400 font-medium">{label}</span>
      </div>
      <div className={`text-xl font-bold leading-none tabular-nums ${alert ? 'text-red-300' : 'text-white'}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-1">{sub}</div>
    </div>
  )
}

interface BundleData {
  sddc_version: string
  upgradable_components: Array<{ component_type: string; current_version: string; target_version: string; domain_name?: string }>
  blocked_components: Array<{ component_type: string; current_version: string; blocking_reasons?: string[] }>
  host_count: number
  cluster_count: number
  vm_count?: number
  vsan_enabled?: boolean
  rollback_risk: { score: number; level: 'low' | 'medium' | 'high'; reasons: string[]; host_count?: number; vm_count?: number; vsan_enabled?: boolean }
  recommendations: string[]
}

function RiskBadge({ level }: { level: string }) {
  if (level === 'high') return <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-900/30 text-red-400 border border-red-800/40 font-bold uppercase tracking-wider">High Risk</span>
  if (level === 'medium') return <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-900/30 text-amber-400 border border-amber-800/40 font-bold uppercase tracking-wider">Medium Risk</span>
  return <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-900/30 text-green-400 border border-green-800/40 font-bold uppercase tracking-wider">Low Risk</span>
}

function PatchBundlePanel() {
  const [data, setData] = useState<BundleData | null>(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/fleet/bundles`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return (
    <section aria-label="Patch Bundle Advisor loading">
      <div className="rounded-xl border border-vmware-border bg-vmware-card px-4 py-3 flex items-center gap-3 animate-pulse">
        <div className="w-7 h-7 rounded-lg bg-slate-700/60 shrink-0" />
        <div className="flex-1 space-y-1.5">
          <div className="h-3 w-40 rounded bg-slate-700/60" />
          <div className="h-2.5 w-56 rounded bg-slate-700/40" />
        </div>
        <div className="h-6 w-20 rounded bg-slate-700/40" />
      </div>
    </section>
  )
  if (!data || (data.upgradable_components.length === 0 && data.blocked_components.length === 0)) return null

  const actionable = data.upgradable_components.length
  const blocked = data.blocked_components.length
  const risk = data.rollback_risk

  return (
    <section>
      <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
        <div
          className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-800/30 transition-colors"
          onClick={() => setExpanded(e => !e)}
        >
          <div className="w-7 h-7 rounded-lg bg-amber-900/30 flex items-center justify-center shrink-0">
            <Upload size={13} className="text-amber-400" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-semibold text-white">Patch Bundle Advisor</span>
              <RiskBadge level={risk.level} />
              {blocked > 0 && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-900/20 text-red-400 border border-red-800/30">
                  {blocked} blocker{blocked > 1 ? 's' : ''}
                </span>
              )}
              {actionable > 0 && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-900/20 text-blue-400 border border-blue-800/30">
                  {actionable} upgrade{actionable > 1 ? 's' : ''} available
                </span>
              )}
            </div>
            <p className="text-[10px] text-slate-500 mt-0.5">
              SDDC <span className="font-mono">{data.sddc_version}</span> · {data.host_count} hosts · {data.cluster_count} clusters
              {data.vm_count != null && data.vm_count > 0 ? ` · ${data.vm_count} VMs` : ''}
              {data.vsan_enabled ? ' · vSAN' : ''}
            </p>
          </div>
          <button
            onClick={e => { e.stopPropagation(); window.location.hash = '#/upgrade' }}
            className="flex items-center gap-1 text-[10px] text-blue-400 hover:text-blue-300 bg-blue-900/20 hover:bg-blue-900/30 border border-blue-800/40 px-2 py-1 rounded transition-colors flex-shrink-0"
          >
            Plan Upgrade
          </button>
          <ChevronRight size={13} className={`text-slate-500 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        </div>

        {expanded && (
          <div className="border-t border-vmware-border px-4 py-4 space-y-4">
            {/* Recommendations */}
            {data.recommendations.length > 0 && (
              <div>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Recommendations</p>
                <div className="space-y-1.5">
                  {data.recommendations.map((r, i) => (
                    <div key={i} className="flex items-start gap-2 text-xs text-slate-300">
                      <span className="text-blue-400 font-bold mt-0.5 shrink-0">{i + 1}.</span> {r}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Risk breakdown */}
            <div>
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Rollback Risk</p>
              <div className="rounded-lg border border-vmware-border bg-slate-800/30 px-3 py-2 mb-2">
                <div className="flex items-center gap-3 mb-2">
                  <div className={`text-lg font-bold tabular-nums ${risk.score >= 70 ? 'text-red-400' : risk.score >= 40 ? 'text-amber-400' : 'text-green-400'}`}>
                    {risk.score}
                  </div>
                  <div className="flex-1">
                    <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${risk.score >= 70 ? 'bg-red-500' : risk.score >= 40 ? 'bg-amber-500' : 'bg-green-500'}`}
                        style={{ width: `${risk.score}%` }}
                      />
                    </div>
                  </div>
                  <span className="text-[10px] text-slate-400">/ 100</span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  {risk.host_count != null && (
                    <div>
                      <div className="text-sm font-bold text-white">{risk.host_count}</div>
                      <div className="text-[9px] text-slate-500 uppercase tracking-wide">Hosts</div>
                    </div>
                  )}
                  {risk.vm_count != null && (
                    <div>
                      <div className="text-sm font-bold text-white">{risk.vm_count}</div>
                      <div className="text-[9px] text-slate-500 uppercase tracking-wide">VMs</div>
                    </div>
                  )}
                  {risk.vsan_enabled != null && (
                    <div>
                      <div className={`text-sm font-bold ${risk.vsan_enabled ? 'text-amber-400' : 'text-slate-500'}`}>
                        {risk.vsan_enabled ? 'Yes' : 'No'}
                      </div>
                      <div className="text-[9px] text-slate-500 uppercase tracking-wide">vSAN</div>
                    </div>
                  )}
                </div>
              </div>
              {risk.reasons.length > 0 && (
                <div className="space-y-1">
                  {risk.reasons.map((r, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs text-amber-300/80">
                      <AlertTriangle size={10} className="text-amber-500 shrink-0" /> {r}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Upgradable components */}
            {data.upgradable_components.length > 0 && (
              <div>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Available Upgrades</p>
                <div className="space-y-1">
                  {data.upgradable_components.map((c, i) => (
                    <div key={i} className="flex items-center gap-3 text-xs bg-slate-800/40 rounded-lg px-3 py-2">
                      <span className="text-slate-300 font-medium min-w-0 flex-1">{c.component_type}</span>
                      <span className="font-mono text-slate-500">{c.current_version}</span>
                      <span className="text-slate-600">→</span>
                      <span className="font-mono text-green-400">{c.target_version}</span>
                      {c.domain_name && <span className="text-[10px] text-slate-600 ml-auto">{c.domain_name}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Blockers */}
            {data.blocked_components.length > 0 && (
              <div>
                <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Upgrade Blockers</p>
                <div className="space-y-1">
                  {data.blocked_components.filter(c => c.component_type).map((c, i) => (
                    <div key={i} className="rounded-lg border border-red-900/40 bg-red-900/10 px-3 py-2">
                      <p className="text-xs font-medium text-red-300">{[c.component_type, c.current_version].filter(Boolean).join(' ')}</p>
                      {c.blocking_reasons?.filter((r: string) => r?.trim()).map((r: string, j: number) => (
                        <p key={j} className="text-[10px] text-red-400/70 mt-0.5">{r}</p>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

export function FleetPage() {
  const [fleet, setFleet] = useState<FleetData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [discoveryMap, setDiscoveryMap] = useState<Record<string, { risk_level: string; open_port_count: number; top_ports: number[]; management_ip?: string }>>({})
  const [selectedHost, setSelectedHost] = useState<ESXiHost | null>(null)
  const [selectedNetwork, setSelectedNetwork] = useState<NetworkItem | null>(null)
  const [selectedAppliance, setSelectedAppliance] = useState<{ item: Appliance; group: string } | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [scoreDiff, setScoreDiff] = useState<{ delta: number; current: { score: number; timestamp: string }; previous: { score: number; timestamp: string } } | null>(null)

  // silent=true (background auto-refresh) keeps the current view rendered
  // instead of flashing the loading panel.
  async function load(silent = false) {
    if (!silent) setLoading(true)
    setError('')
    try {
      const [fleetResp, discoveryResp] = await Promise.all([
        fetch(`${API_BASE}/api/v1/fleet`),
        fetch(`${API_BASE}/api/v1/discovery/summary`).catch(() => null),
      ])
      // Load score diff in background (non-blocking)
      fetch(`${API_BASE}/api/v1/fleet/score-diff`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d?.diff && setScoreDiff(d.diff))
        .catch(() => {})
      if (!fleetResp.ok) throw new Error(`HTTP ${fleetResp.status}`)
      const fleetData = await fleetResp.json()
      setFleet(fleetData)

      if (discoveryResp?.ok) {
        const disc = await discoveryResp.json()
        // Build a map: management_ip → discovery data
        // Also try to match by resolved management_ip from host records
        const ipMap: Record<string, { risk_level: string; open_port_count: number; top_ports: number[] }> = disc.host_ip_map || {}
        // Build a lookup by resolved host name → IP from fleet hosts
        const nameToIp: Record<string, string> = {}
        for (const h of (fleetData.hosts || [])) {
          if (h.management_ip) nameToIp[h.name] = h.management_ip
        }
        // Merge: host name → discovery data
        const enriched: Record<string, { risk_level: string; open_port_count: number; top_ports: number[]; management_ip?: string }> = {}
        for (const [ip, data] of Object.entries(ipMap)) {
          enriched[ip] = { ...data as { risk_level: string; open_port_count: number; top_ports: number[] } }
        }
        // Also index by host name via management_ip
        for (const [name, ip] of Object.entries(nameToIp)) {
          if (ipMap[ip]) enriched[name] = { ...(ipMap[ip] as { risk_level: string; open_port_count: number; top_ports: number[] }), management_ip: ip }
        }
        setDiscoveryMap(enriched)
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
      setLastRefresh(new Date())
    }
  }

  // Initial load + auto-refresh every 60s (matches the fleet Redis cache TTL).
  // Pauses while the tab is hidden; backs off if the API keeps failing.
  const loadedOnce = useRef(false)
  useVisibilityPolling(async () => {
    await load(loadedOnce.current)
    loadedOnce.current = true
  }, 60_000)

  const mp = fleet?.management_plane ?? {}

  const totalCores = fleet?.hosts.reduce((s, h) => s + (h.cpu_cores || 0), 0) ?? 0
  const totalRamGb = fleet?.hosts.reduce((s, h) => s + (h.memory_gb || 0), 0) ?? 0
  const totalCapGb = fleet?.datastores.reduce((s, d) => s + (d.capacity_gb || 0), 0) ?? 0
  const disconnectedHosts = fleet?.hosts.filter(h => h.connection_state !== 'CONNECTED').length ?? 0
  const criticalDatastores = fleet?.datastores.filter(d => d.used_pct >= 85).length ?? 0
  const avgCpuUsage = fleet?.hosts.length
    ? fleet.hosts.reduce((s, h) => s + (h.cpu_usage || 0), 0) / fleet.hosts.length
    : 0
  const avgRamUsage = fleet?.hosts.length
    ? fleet.hosts.reduce((s, h) => s + (h.ram_usage || 0), 0) / fleet.hosts.length
    : 0

  // Network breakdown
  const dvpgCount = fleet?.networks.filter(n => n.type === 'DISTRIBUTED_PORTGROUP').length ?? 0
  const opaqueCount = fleet?.networks.filter(n => n.type === 'OPAQUE_NETWORK').length ?? 0
  const stdCount = fleet?.networks.filter(n => n.type === 'STANDARD_PORTGROUP' || !n.type?.includes('DISTRIBUTED')).length ?? 0

  return (
    <div className="min-h-screen bg-vmware-dark">
      <div className="sticky top-0 z-10">
        <StatusBar lastRefresh={lastRefresh} />
      </div>

      {selectedHost && (
        <HostDrawer
          host={selectedHost}
          onClose={() => setSelectedHost(null)}
          discoveryData={discoveryMap[selectedHost.name] || (selectedHost.management_ip ? discoveryMap[selectedHost.management_ip] : undefined)}
        />
      )}
      {selectedNetwork && (
        <NetworkDrawer network={selectedNetwork} onClose={() => setSelectedNetwork(null)} />
      )}
      {selectedAppliance && (
        <ApplianceDrawer
          appliance={selectedAppliance.item}
          group={selectedAppliance.group}
          onClose={() => setSelectedAppliance(null)}
        />
      )}

      <div className="max-w-[1600px] mx-auto px-6 py-6">
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          {scoreDiff && (
            <a href="#/analysis"
              title={`vs. ${new Date(scoreDiff.previous.timestamp).toLocaleString()} — click to view latest analysis`}
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg font-medium border transition-opacity hover:opacity-80 ${
                scoreDiff.delta > 0
                  ? 'text-green-400 bg-green-900/20 border-green-800/50'
                  : scoreDiff.delta < 0
                    ? 'text-red-400 bg-red-900/20 border-red-800/50'
                    : 'text-slate-400 bg-slate-800/40 border-slate-700'
              }`}>
              {scoreDiff.delta > 0 ? '▲' : scoreDiff.delta < 0 ? '▼' : '●'}
              {' '}Score {scoreDiff.delta > 0 ? '+' : ''}{scoreDiff.delta} vs last run
              <span className="opacity-60 font-normal ml-1">
                ({scoreDiff.current.score} now · {scoreDiff.previous.score} before)
              </span>
            </a>
          )}
          {fleet && disconnectedHosts > 0 && (
            <span className="flex items-center gap-1.5 text-xs text-red-400 bg-red-900/20 border border-red-800/50 px-3 py-1.5 rounded-lg font-medium">
              <AlertTriangle size={12} /> {disconnectedHosts} host{disconnectedHosts > 1 ? 's' : ''} disconnected
            </span>
          )}
          {fleet && criticalDatastores > 0 && (
            <span className="flex items-center gap-1.5 text-xs text-amber-400 bg-amber-900/20 border border-amber-800/50 px-3 py-1.5 rounded-lg font-medium">
              <AlertCircle size={12} /> {criticalDatastores} datastore{criticalDatastores > 1 ? 's' : ''} critical
            </span>
          )}
          <button onClick={() => load()} disabled={loading}
            className="ml-auto flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors disabled:opacity-40">
            {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh
          </button>
        </div>
        {fleet?._sources && (
          <div className="mb-4 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              {[
                { key: 'vcenter', label: 'vCenter' },
                { key: 'vrops',   label: 'VCF Operations' },
                { key: 'sddc',    label: 'SDDC Manager' },
              ].map(({ key, label }) => {
                const active = fleet._sources![key as keyof FleetSources] as boolean
                const isError = key === 'vrops' && fleet._sources!.vrops_error
                return (
                  <span key={key} className={`inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border font-medium ${
                    isError
                      ? 'bg-amber-900/20 border-amber-800/50 text-amber-400'
                      : active
                        ? 'bg-green-900/20 border-green-800/50 text-green-400'
                        : 'bg-slate-800/40 border-slate-700 text-slate-500'
                  }`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${isError ? 'bg-amber-400' : active ? 'bg-green-400' : 'bg-slate-600'}`} />
                    {label}{isError ? ' (auth error)' : ''}
                  </span>
                )
              })}
              {fleet._sources.hardware_source !== 'none' && (
                <span className="text-xs text-slate-500 pl-1">
                  hardware via {fleet._sources.hardware_source === 'vcenter-ns' ? 'vCenter (namespace-mgmt)' : fleet._sources.hardware_source}
                  {' · '}usage via {fleet._sources.usage_source === 'vcenter-ns' ? 'vCenter (namespace-mgmt)' : fleet._sources.usage_source}
                  {' · '}datastores via {fleet._sources.datastores_source}
                </span>
              )}
            </div>
            {fleet._sources.vrops_error && (
              <div className="flex items-center gap-2 text-xs text-amber-300 bg-amber-900/15 border border-amber-800/40 rounded-lg px-3 py-2">
                <AlertTriangle size={12} className="flex-shrink-0" />
                VCF Operations credentials are invalid (401) — update them in{' '}
                <button onClick={() => { window.location.hash = '#/settings' }} className="underline hover:text-amber-200">Settings</button>
                {' '}to restore full hardware data from vROps.
              </div>
            )}
          </div>
        )}
        {error && (
          <div className="rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-sm text-red-300 mb-6 flex items-center gap-2">
            <AlertTriangle size={14} /> {error}
          </div>
        )}

        {loading && !fleet && (
          <div className="animate-pulse space-y-5">
            <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-3">
              {Array.from({length: 8}).map((_, i) => (
                <div key={i} className="h-20 rounded-xl bg-vmware-card border border-vmware-border" />
              ))}
            </div>
            <div className="h-40 rounded-xl bg-vmware-card border border-vmware-border" />
            <div className="h-64 rounded-xl bg-vmware-card border border-vmware-border" />
          </div>
        )}

        {fleet && (
          <>
            <PageHeader icon={Server} title="Fleet Overview"
              subtitle={`${fleet.hosts.length} host${fleet.hosts.length !== 1 ? 's' : ''} · ${fleet.vm_count.toLocaleString()} VMs (incl. templates & vApps) · ${fleet.clusters.length} cluster${fleet.clusters.length !== 1 ? 's' : ''}`}
            />
            {/* Summary tiles */}
            <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-3 mb-6">
              <MetricTile icon={Server}    label="ESXi Hosts"   value={fleet.hosts.length}              sub={`${fleet.clusters.length} cluster${fleet.clusters.length !== 1 ? 's' : ''}`} alert={disconnectedHosts > 0} />
              <MetricTile icon={Cpu}       label="Phys. Cores"  value={totalCores.toLocaleString()}     sub={`avg CPU ${avgCpuUsage.toFixed(0)}%`} />
              <MetricTile icon={Box}       label="Total RAM"    value={fmtRam(totalRamGb)}              sub={`avg ${avgRamUsage.toFixed(0)}% used`} />
              <MetricTile icon={Zap}       label="Running VMs"  value={fleet.vm_count.toLocaleString()} sub="virtual machines" />
              <MetricTile icon={HardDrive} label="Storage"      value={fmtStorage(totalCapGb)}          sub={`${fleet.datastores.length} datastores`} alert={criticalDatastores > 0} />
              <MetricTile icon={Network}   label="Networks"     value={fleet.networks.length}           sub={`${dvpgCount} DVS port groups`} />
              <MetricTile icon={Shield}    label="Mgmt Plane"   value={Object.values(mp).reduce((a, arr) => a + (arr?.length ?? 0), 0)} sub="appliances" />
              <MetricTile icon={Lock}      label="Security"     value={disconnectedHosts === 0 ? 'OK' : 'RISK'} sub={disconnectedHosts === 0 ? 'All hosts connected' : `${disconnectedHosts} disconnected`} alert={disconnectedHosts > 0} />
            </div>

            {/* Single-column layout */}
            <div className="space-y-5">

                {/* Clusters */}
                {fleet.clusters.length > 0 && (
                  <section>
                    <SectionHeader icon={Layers} title="Cluster Capacity" meta={`${fleet.clusters.length} cluster${fleet.clusters.length !== 1 ? 's' : ''}`} />
                    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                      {fleet.clusters.map((cl) => (
                        <ClusterCard key={cl.cluster} cluster={cl} />
                      ))}
                    </div>
                  </section>
                )}

                {/* Compute / ESXi Hosts */}
                <section>
                  <SectionHeader icon={Server} title="Compute — ESXi Hosts"
                    meta={`${fleet.hosts.length} hosts${disconnectedHosts > 0 ? ` · ${disconnectedHosts} disconnected` : ''}`} />
                  <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-vmware-border bg-slate-800/40">
                          <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5">Host</th>
                          <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5">Status</th>
                          <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5">ESXi</th>
                          <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5 hidden 2xl:table-cell">CPU Model</th>
                          <th className="text-right text-xs text-slate-400 font-semibold px-4 py-2.5">Cores</th>
                          <th className="text-right text-xs text-slate-400 font-semibold px-4 py-2.5">RAM</th>
                          <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5 w-28">CPU %</th>
                          <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5 w-28">RAM %</th>
                          <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5">Network Scan</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fleet.hosts.map((h, i) => {
                          const disc = discoveryMap[h.name] || (h.management_ip ? discoveryMap[h.management_ip] : null)
                          const riskColor: Record<string, string> = { critical: 'text-red-400', high: 'text-orange-400', medium: 'text-yellow-400', low: 'text-green-400' }
                          const isSelected = selectedHost?.name === h.name
                          return (
                          <tr key={i} onClick={() => { setSelectedHost(isSelected ? null : h); setSelectedNetwork(null) }}
                            className={`border-b border-vmware-border/40 hover:bg-slate-800/40 transition-colors cursor-pointer select-none ${i === fleet.hosts.length - 1 ? 'border-0' : ''} ${isSelected ? 'bg-blue-900/20 border-blue-800/30' : ''}`}>
                            <td className="px-4 py-3 font-mono text-xs">
                              <div className="flex items-center gap-1.5">
                                <ChevronRight size={10} className={`text-blue-400 flex-shrink-0 transition-transform ${isSelected ? 'rotate-90' : ''}`} />
                                <span className="text-slate-100">{h.name}</span>
                              </div>
                              {h.management_ip && (
                                <div className="text-[10px] text-slate-500 mt-0.5 ml-4 font-mono">{h.management_ip}</div>
                              )}
                            </td>
                            <td className="px-4 py-3"><StatusPill state={h.connection_state} /></td>
                            <td className="px-4 py-3">
                              {h.esxi_version
                                ? <span className="text-xs text-slate-200 font-mono">{h.esxi_version}</span>
                                : <span className="text-xs text-slate-500">—</span>}
                            </td>
                            <td className="px-4 py-3 text-xs text-slate-400 hidden 2xl:table-cell max-w-[180px] truncate">{h.cpu_model || '—'}</td>
                            <td className="px-4 py-3 text-right text-xs text-slate-200 font-mono">{h.cpu_cores ? `${h.cpu_cores}c` : '—'}</td>
                            <td className="px-4 py-3 text-right text-xs text-slate-200 whitespace-nowrap font-mono">{fmtRam(h.memory_gb)}</td>
                            <td className="px-4 py-3 w-28"><UsageBar pct={h.cpu_usage || 0} /></td>
                            <td className="px-4 py-3 w-28"><UsageBar pct={h.ram_usage || 0} /></td>
                            <td className="px-4 py-3">
                              {disc ? (
                                <div className="flex items-center gap-1.5 flex-wrap">
                                  <span className={`text-[10px] font-bold uppercase ${riskColor[disc.risk_level] || 'text-slate-400'}`}>{disc.risk_level}</span>
                                  {(disc.top_ports || []).slice(0, 3).map((p: number) => (
                                    <span key={p} className="text-[9px] font-mono bg-slate-800 text-slate-400 px-1 py-0.5 rounded border border-slate-700/50">{p}</span>
                                  ))}
                                  {(disc.open_port_count || 0) > 3 && <span className="text-[9px] text-slate-600">+{(disc.open_port_count||0)-3}</span>}
                                </div>
                              ) : (
                                <span className="text-[10px] text-slate-600">not scanned</span>
                              )}
                            </td>
                          </tr>
                        )})}

                      </tbody>
                    </table>
                  </div>
                </section>

                {/* Storage */}
                {fleet.datastores.length > 0 && (
                  <section>
                    <SectionHeader icon={HardDrive} title="Storage"
                      meta={`${fleet.datastores.length} datastores${criticalDatastores > 0 ? ` · ${criticalDatastores} critical` : ''}`} />
                    <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-vmware-border bg-slate-800/40">
                            <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5">Datastore</th>
                            <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5">Type</th>
                            <th className="text-right text-xs text-slate-400 font-semibold px-4 py-2.5">Capacity</th>
                            <th className="text-right text-xs text-slate-400 font-semibold px-4 py-2.5">Free</th>
                            <th className="text-left text-xs text-slate-400 font-semibold px-4 py-2.5 w-36">Usage</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fleet.datastores.map((ds, i) => (
                            <tr key={i} className={`border-b border-vmware-border/40 hover:bg-slate-800/30 transition-colors ${i === fleet.datastores.length - 1 ? 'border-0' : ''} ${ds.used_pct >= 85 ? 'bg-red-900/10' : ''}`}>
                              <td className="px-4 py-3 text-slate-100 text-xs font-mono">{ds.name}</td>
                              <td className="px-4 py-3">
                                <span className="text-xs bg-slate-700 px-2 py-0.5 rounded text-slate-300 font-medium">{ds.type}</span>
                              </td>
                              <td className="px-4 py-3 text-right text-xs text-slate-200 whitespace-nowrap font-mono">{fmtStorage(ds.capacity_gb)}</td>
                              <td className="px-4 py-3 text-right text-xs text-slate-200 whitespace-nowrap font-mono">{fmtStorage(ds.free_gb)}</td>
                              <td className="px-4 py-3 w-36"><UsageBar pct={ds.used_pct} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                )}

                {/* Management Plane */}
                {Object.values(mp).some(arr => arr && arr.length > 0) && (
                  <section>
                    <SectionHeader icon={Shield} title="Management Plane" />
                    <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 gap-3">
                      {[
                        { label: 'vCenter', badge: 'VMware vCenter', items: mp.vcenter ?? [], icon: Server },
                        { label: 'NSX Manager', badge: 'VMware NSX', items: mp.nsx_manager ?? [], icon: GitBranch },
                        { label: 'SDDC Manager', badge: 'VMware VCF', items: mp.sddc_manager ?? [], icon: Layers },
                        { label: 'VCF Operations', badge: 'VCF Operations', items: mp.vcf_operations ?? [], icon: TrendingUp },
                        { label: 'ESXi Hosts', badge: 'VMware ESXi', items: mp.esxi_hosts ?? [], icon: Server },
                        { label: 'Mgmt Services', badge: 'VCF Internal Services', items: mp.vcf_management_services ?? [], icon: CheckCircle },
                      ].filter(g => g.items.length > 0).map(({ label, badge, items, icon: Icon }) => (
                        <div key={label} className="rounded-xl border border-vmware-border bg-vmware-card p-4 space-y-3">
                          <div className="flex items-center justify-between">
                            <div>
                              <div className="text-xs font-semibold text-slate-200">{label}</div>
                              <div className="text-xs text-slate-500 mt-0.5">{badge}</div>
                            </div>
                            <div className="flex items-center gap-1.5">
                              <span className="text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded-full font-medium">{items.length}</span>
                              <Icon size={13} className="text-slate-500" />
                            </div>
                          </div>
                          <div className="space-y-1.5">
                            {items.map((a, idx) => {
                              const status = a.status ?? ''
                              const isUp = status === 'UP' || status === 'ACTIVE' || status === 'RUNNING'
                              const isDown = status && !isUp && status !== ''
                              const isSelected = selectedAppliance?.item === a
                              return (
                                <div key={idx}
                                  onClick={() => { setSelectedAppliance(isSelected ? null : { item: a, group: label }); setSelectedHost(null); setSelectedNetwork(null) }}
                                  className={`flex items-start gap-2 rounded-lg px-2 py-1.5 cursor-pointer transition-colors select-none ${
                                    isSelected ? 'bg-blue-900/25 border border-blue-700/40' : 'hover:bg-slate-700/40 border border-transparent'
                                  }`}>
                                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1.5 ${isDown ? 'bg-red-400' : 'bg-emerald-400'}`} />
                                  <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 min-w-0">
                                      <span className="text-xs text-slate-100 truncate font-mono" title={a.fqdn || a.name}>{a.fqdn || a.name}</span>
                                      {status && (
                                        <span
                                          title={a.status_detail || undefined}
                                          className={`text-[9px] font-bold px-1.5 py-0.5 rounded flex-shrink-0 inline-flex items-center gap-1 ${
                                          isUp ? 'bg-emerald-900/40 text-emerald-400' : 'bg-red-900/40 text-red-400'
                                        }`}>
                                          {status}
                                          {isDown && a.status_detail && <AlertCircle size={9} aria-label="Explanation available — click for details" />}
                                        </span>
                                      )}
                                    </div>
                                    {a.version && (
                                      <div className="text-[10px] text-slate-500 mt-0.5 font-mono">
                                        {a.version}{a.build ? ` · ${a.build}` : ''}
                                      </div>
                                    )}
                                  </div>
                                  <ChevronRight size={10} className={`text-slate-600 flex-shrink-0 mt-1.5 transition-transform ${isSelected ? 'rotate-90 text-blue-400' : ''}`} />
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {/* Patch Bundle Advisor */}
                <PatchBundlePanel />

                {/* Datacenters */}
                {fleet.datacenters.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {fleet.datacenters.map((dc) => (
                      <span key={dc.datacenter} className="text-xs bg-slate-800 border border-vmware-border rounded-full px-3 py-1 text-slate-300 font-medium">
                        DC: {dc.name}
                      </span>
                    ))}
                  </div>
                )}

                {/* Networks */}
                {fleet.networks.length > 0 && (
                  <section>
                    <div className="flex items-center gap-2 mb-3">
                      <Network size={14} className="text-blue-400 flex-shrink-0" />
                      <h2 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Networks</h2>
                      <span className="text-xs text-slate-500 font-normal normal-case tracking-normal">· {fleet.networks.length} total</span>
                      <div className="flex items-center gap-3 ml-2">
                        {dvpgCount > 0 && <span className="text-xs text-blue-400">{dvpgCount} DVS</span>}
                        {opaqueCount > 0 && <span className="text-xs text-purple-400">{opaqueCount} Opaque</span>}
                        {stdCount > 0 && <span className="text-xs text-slate-500">{stdCount} Standard</span>}
                      </div>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-2">
                      {fleet.networks.map((n, i) => {
                        const isNetSelected = selectedNetwork?.network === n.network
                        return (
                          <div key={i}
                            onClick={() => { setSelectedNetwork(isNetSelected ? null : n); setSelectedHost(null) }}
                            className={`rounded-lg border px-3 py-2.5 cursor-pointer select-none hover:bg-slate-800/40 transition-colors ${isNetSelected ? 'border-blue-600/60 bg-blue-900/20' : 'border-vmware-border bg-vmware-card'}`}>
                            <div className="text-xs text-slate-100 font-mono truncate mb-1.5" title={n.name}>{n.name}</div>
                            <NetworkTypeBadge type={n.type} />
                          </div>
                        )
                      })}
                    </div>
                  </section>
                )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
