import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Radar, Zap, Server, Settings, Archive, Activity, Upload, Laptop, TrendingUp,
  Terminal, BotMessageSquare, Plus, Trash2, Play, Square, RefreshCw,
  AlertTriangle, Shield, X, Wifi, Monitor, HardDrive, Cpu, Globe,
  Loader2, CheckCircle, Network, Key, ChevronDown, ChevronUp,
  Eye, EyeOff, Pause, Microscope,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

// ─── Types ────────────────────────────────────────────────────────────────────

interface Network {
  cidr: string
  source: 'k8s-node' | 'fleet' | 'manual' | string
  label: string
}

interface Scan {
  id: string
  cidr: string
  label: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled'
  started_at: string | null
  completed_at: string | null
  host_count: number
  hosts_found?: number
  error?: string
}

interface Port {
  port: number
  protocol: string
  state: string
  service: string
  version: string
}

interface Host {
  ip: string
  scan_id: string
  cidr: string
  dns_names: string[]
  mac: string
  vendor: string
  os_name: string
  os_accuracy: number
  os_family: string
  device_class: string
  risk_level: 'low' | 'medium' | 'high' | 'critical' | string
  risk_score: number
  ports: Port[]
  host_scripts: { id: string; output: string }[]
  first_seen: string
  last_seen: string
}

interface CredInfo {
  cred_type: string
  username: string
  note: string
  added_at: string
  has_password: number
  has_key: number
  has_sudo: number
}

interface DeepScanResult {
  status: string
  ran_at?: string
  error?: string
  results: Record<string, string>
}

// ─── Constants ────────────────────────────────────────────────────────────────

const RISK_COLORS: Record<string, string> = {
  critical: 'text-red-400 border-red-800/60 bg-red-900/20',
  high:     'text-orange-400 border-orange-800/60 bg-orange-900/20',
  medium:   'text-yellow-400 border-yellow-800/60 bg-yellow-900/20',
  low:      'text-green-400 border-green-800/60 bg-green-900/20',
}
const RISK_DOT: Record<string, string> = {
  critical: 'bg-red-500', high: 'bg-orange-500', medium: 'bg-yellow-500', low: 'bg-green-500',
}
const SOURCE_BADGE: Record<string, string> = {
  'k8s-node': 'bg-blue-900/40 text-blue-300 border-blue-700/50',
  'fleet':    'bg-purple-900/40 text-purple-300 border-purple-700/50',
  'manual':   'bg-slate-800 text-slate-300 border-slate-600/50',
}
const DEVICE_ICON_MAP: Record<string, React.ElementType> = {
  esxi: Server, vcenter: Monitor, nsx: Network, linux: Terminal,
  windows: Monitor, macos: Laptop, network: Wifi, storage: HardDrive,
  container: Cpu, printer: Globe, unknown: Globe,
}
function deviceIcon(deviceClass: string): React.ElementType {
  const k = deviceClass?.toLowerCase() || 'unknown'
  for (const [key, Icon] of Object.entries(DEVICE_ICON_MAP)) {
    if (k.includes(key)) return Icon
  }
  return Globe
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function RiskBadge({ level }: { level: string }) {
  const cls = RISK_COLORS[level] || RISK_COLORS.low
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wider ${cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${RISK_DOT[level] || 'bg-green-500'}`} />
      {level}
    </span>
  )
}

function ScanStatusBadge({ status }: { status: string }) {
  if (status === 'running') return (
    <span className="inline-flex items-center gap-1.5 text-[10px] font-bold text-blue-400 uppercase tracking-wider">
      <Loader2 size={10} className="animate-spin" /> Scanning
    </span>
  )
  if (status === 'done') return (
    <span className="inline-flex items-center gap-1.5 text-[10px] font-bold text-green-400 uppercase tracking-wider">
      <CheckCircle size={10} /> Done
    </span>
  )
  if (status === 'failed') return (
    <span className="inline-flex items-center gap-1.5 text-[10px] font-bold text-red-400 uppercase tracking-wider">
      <AlertTriangle size={10} /> Failed
    </span>
  )
  if (status === 'cancelled') return (
    <span className="inline-flex items-center gap-1.5 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
      <Square size={10} /> Stopped
    </span>
  )
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
      <Loader2 size={10} /> Pending
    </span>
  )
}

/** Coerce any host payload (API row or SSE event) into a safe renderable Host. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function normalizeHost(raw: any): Host {
  let scripts = raw.host_scripts
  if (scripts && !Array.isArray(scripts)) {
    // Backend stores scripts as {id: output} — the drawer needs [{id, output}]
    scripts = Object.entries(scripts).map(([id, output]) => ({ id, output: String(output) }))
  }
  return {
    ...raw,
    ports: Array.isArray(raw.ports) ? raw.ports : [],
    dns_names: Array.isArray(raw.dns_names) ? raw.dns_names : [],
    host_scripts: scripts ?? [],
    risk_level: raw.risk_level || 'low',
    risk_score: raw.risk_score ?? 0,
  }
}

function HostCard({ host, onClick, isNew, scanning }: {
  host: Host; onClick: () => void; isNew?: boolean; scanning?: boolean
}) {
  const Icon = deviceIcon(host.device_class)
  const riskCls = RISK_COLORS[host.risk_level] || RISK_COLORS.low
  const openPorts = (host.ports ?? []).filter(p => p.state === 'open')
  const pending = scanning && openPorts.length === 0 && !host.os_name
  return (
    <button onClick={onClick}
      className={`text-left rounded-xl border p-4 hover:brightness-110 transition-all cursor-pointer w-full animate-scale-in ${pending ? 'border-slate-700/60 bg-slate-800/30 text-slate-400' : riskCls}`}>
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-slate-800/60 flex items-center justify-center flex-shrink-0">
          {pending ? <Loader2 size={15} className="text-slate-500 animate-spin" /> : <Icon size={16} className="text-slate-300" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm font-bold text-white">{host.ip}</span>
            {isNew && (
              <span className="text-[9px] font-bold uppercase tracking-wider text-emerald-300 bg-emerald-900/40 border border-emerald-700/50 px-1.5 py-0.5 rounded-full">new</span>
            )}
            {host.mac && <span className="text-[10px] text-slate-500 font-mono">{host.mac}</span>}
          </div>
          {host.dns_names.length > 0 && (
            <p className="text-xs text-slate-400 truncate mt-0.5">{host.dns_names[0]}</p>
          )}
          {host.os_name && (
            <p className="text-xs text-slate-300 mt-1">{host.os_name}
              {host.os_accuracy ? <span className="text-slate-500"> ({host.os_accuracy}%)</span> : null}
            </p>
          )}
          {host.vendor && <p className="text-[11px] text-slate-500 mt-0.5">{host.vendor}</p>}
          {pending && <p className="text-[11px] text-slate-500 mt-1 italic">discovered — awaiting port scan…</p>}
        </div>
        <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
          {!pending && <RiskBadge level={host.risk_level} />}
          {host.device_class && host.device_class !== 'unknown' && (
            <span className="text-[10px] text-slate-400 capitalize">{host.device_class.replace('-', ' ')}</span>
          )}
          {!pending && <span className="text-[10px] text-slate-500">{openPorts.length} ports</span>}
        </div>
      </div>
      {openPorts.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {openPorts.slice(0, 5).map(p => (
            <span key={p.port} className="text-[10px] font-mono bg-slate-800/80 text-slate-300 px-1.5 py-0.5 rounded border border-slate-700/50">
              {p.port}/{p.protocol}
              {p.service && <span className="text-slate-500"> {p.service}</span>}
            </span>
          ))}
          {openPorts.length > 5 && (
            <span className="text-[10px] text-slate-500 px-1">+{openPorts.length - 5} more</span>
          )}
        </div>
      )}
    </button>
  )
}

// ─── CredentialForm ───────────────────────────────────────────────────────────

function CredentialForm({ ip, onSaved, onCancel }: {
  ip: string
  onSaved: () => void
  onCancel: () => void
}) {
  const [credType, setCredType] = useState('ssh')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [sshKey, setSshKey] = useState('')
  const [sudoPassword, setSudoPassword] = useState('')
  const [note, setNote] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [saving, setSaving] = useState(false)

  async function save() {
    if (!username) return
    setSaving(true)
    await fetch(`${API_BASE}/api/v1/discovery/hosts/${ip}/credentials`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        cred_type: credType,
        username,
        password: password || null,
        ssh_key: sshKey || null,
        sudo_password: sudoPassword || null,
        note: note || null,
      }),
    })
    setSaving(false)
    onSaved()
  }

  const input = "w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-xs text-white placeholder-slate-500 font-mono"

  return (
    <div className="bg-slate-800/60 rounded-xl border border-slate-700/50 p-4 space-y-3">
      <div className="flex items-center gap-2 mb-2">
        <Key size={13} className="text-blue-400" />
        <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Add Credentials</span>
      </div>

      <div className="flex gap-2">
        {['ssh', 'winrm'].map(t => (
          <button key={t} onClick={() => setCredType(t)}
            className={`flex-1 text-xs py-1.5 rounded-lg border transition-colors ${credType === t
              ? 'bg-blue-600/30 text-blue-300 border-blue-600/50'
              : 'bg-slate-800 text-slate-400 border-slate-600 hover:text-white'}`}>
            {t.toUpperCase()}
          </button>
        ))}
      </div>

      <input className={input} placeholder="Username *" value={username} onChange={e => setUsername(e.target.value)} />

      <div className="relative">
        <input className={input} type={showPw ? 'text' : 'password'}
          placeholder="Password (or leave blank for key auth)"
          value={password} onChange={e => setPassword(e.target.value)} />
        <button onClick={() => setShowPw(v => !v)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300">
          {showPw ? <EyeOff size={12} /> : <Eye size={12} />}
        </button>
      </div>

      {credType === 'ssh' && (
        <textarea className={`${input} h-20 resize-none`}
          placeholder="SSH private key (PEM format — optional)"
          value={sshKey} onChange={e => setSshKey(e.target.value)} />
      )}

      {credType === 'ssh' && (
        <input className={input} type="password" placeholder="sudo password (optional)"
          value={sudoPassword} onChange={e => setSudoPassword(e.target.value)} />
      )}

      <input className={input} placeholder="Note (optional)" value={note} onChange={e => setNote(e.target.value)} />

      <div className="flex gap-2 pt-1">
        <button onClick={save} disabled={!username || saving}
          className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-xs py-1.5 rounded-lg transition-colors flex items-center justify-center gap-1.5">
          {saving ? <Loader2 size={11} className="animate-spin" /> : <Key size={11} />}
          Save Credentials
        </button>
        <button onClick={onCancel}
          className="flex-1 bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs py-1.5 rounded-lg transition-colors">
          Cancel
        </button>
      </div>
    </div>
  )
}

// ─── HostDrawer ───────────────────────────────────────────────────────────────

function HostDrawer({ host, onClose }: { host: Host; onClose: () => void }) {
  const Icon = deviceIcon(host.device_class)
  const openPorts = host.ports.filter(p => p.state === 'open')
  const [creds, setCreds] = useState<CredInfo[]>([])
  const [deepScan, setDeepScan] = useState<DeepScanResult | null>(null)
  const [showCredForm, setShowCredForm] = useState(false)
  const [deepScanning, setDeepScanning] = useState(false)
  const [expandedSection, setExpandedSection] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadCreds = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/hosts/${host.ip}/credentials`)
      if (r.ok) setCreds((await r.json()).credentials || [])
    } catch { /* ignore */ }
  }, [host.ip])

  const loadDeepScan = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/hosts/${host.ip}/deep-scan`)
      if (r.ok) {
        const data = await r.json()
        setDeepScan(data)
        if (data.status === 'running') return true // still running
      }
    } catch { /* ignore */ }
    return false
  }, [host.ip])

  useEffect(() => {
    loadCreds()
    loadDeepScan()
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [loadCreds, loadDeepScan])

  async function triggerDeepScan() {
    setDeepScanning(true)
    await fetch(`${API_BASE}/api/v1/discovery/hosts/${host.ip}/deep-scan`, { method: 'POST' })
    setDeepScan({ status: 'running', results: {} })
    // Poll until done
    pollRef.current = setInterval(async () => {
      const stillRunning = await loadDeepScan()
      if (!stillRunning) {
        setDeepScanning(false)
        if (pollRef.current) clearInterval(pollRef.current)
      }
    }, 3000)
  }

  async function deleteCred(credType: string) {
    await fetch(`${API_BASE}/api/v1/discovery/hosts/${host.ip}/credentials/${credType}`, { method: 'DELETE' })
    loadCreds()
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-lg bg-[#0d1117] border-l border-slate-700/50 overflow-y-auto shadow-2xl flex flex-col">
        {/* Header */}
        <div className="sticky top-0 bg-[#0d1117] border-b border-slate-700/50 px-5 py-4 flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-slate-800 flex items-center justify-center">
            <Icon size={16} className="text-slate-300" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-mono font-bold text-white">{host.ip}</p>
            {host.dns_names.length > 0 && (
              <p className="text-xs text-slate-400 truncate">{host.dns_names.join(', ')}</p>
            )}
          </div>
          <RiskBadge level={host.risk_level} />
          <button onClick={onClose} className="text-slate-500 hover:text-white ml-2"><X size={16} /></button>
        </div>

        <div className="p-5 space-y-5">
          {/* Summary */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            {[
              ['OS', host.os_name || '—'],
              ['Vendor', host.vendor || '—'],
              ['MAC', host.mac || '—'],
              ['Device Class', host.device_class || '—'],
              ['Risk Score', String(host.risk_score)],
              ['OS Accuracy', host.os_accuracy ? `${host.os_accuracy}%` : '—'],
            ].map(([label, val]) => (
              <div key={label} className="bg-slate-800/40 rounded-lg p-3">
                <p className="text-slate-500 mb-1">{label}</p>
                <p className="text-slate-200 font-mono truncate">{val}</p>
              </div>
            ))}
          </div>

          {/* Open Ports */}
          {openPorts.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                Open Ports ({openPorts.length})
              </h3>
              <div className="rounded-lg border border-slate-700/50 overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-700/50 bg-slate-800/40">
                      <th className="text-left px-3 py-2 text-slate-500">Port</th>
                      <th className="text-left px-3 py-2 text-slate-500">Service</th>
                      <th className="text-left px-3 py-2 text-slate-500">Version</th>
                    </tr>
                  </thead>
                  <tbody>
                    {openPorts.map((p, i) => (
                      <tr key={i} className="border-b border-slate-800/50">
                        <td className="px-3 py-2 font-mono text-slate-300">{p.port}/{p.protocol}</td>
                        <td className="px-3 py-2 text-slate-400">{p.service || '—'}</td>
                        <td className="px-3 py-2 text-slate-500 max-w-[180px] truncate">{p.version || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Host Scripts */}
          {host.host_scripts.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Script Output</h3>
              <div className="space-y-2">
                {host.host_scripts.map((s, i) => (
                  <div key={i} className="bg-slate-800/40 rounded-lg p-3">
                    <p className="text-[10px] text-blue-400 font-mono mb-1.5">{s.id}</p>
                    <p className="text-xs text-slate-300 whitespace-pre-wrap font-mono leading-relaxed">{s.output}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Authenticated Deep Scan ───────────────────────────────────── */}
          <div className="border-t border-slate-800 pt-4">
            <div className="flex items-center gap-2 mb-3">
              <Microscope size={13} className="text-purple-400" />
              <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Authenticated Scan</span>
              <span className="text-[10px] text-slate-600 ml-1">(optional — OS-level intel)</span>
            </div>

            {/* Stored credentials */}
            {creds.length > 0 && !showCredForm && (
              <div className="space-y-2 mb-3">
                {creds.map(c => (
                  <div key={c.cred_type} className="flex items-center gap-2 bg-slate-800/40 rounded-lg px-3 py-2">
                    <Key size={11} className="text-green-400 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <span className="text-xs text-slate-300">{c.username}</span>
                      <span className="text-[10px] text-slate-500 ml-2">{c.cred_type.toUpperCase()}</span>
                      {c.has_key ? <span className="text-[10px] text-blue-400 ml-2">key</span> : null}
                      {c.has_password ? <span className="text-[10px] text-yellow-400 ml-2">pw</span> : null}
                      {c.has_sudo ? <span className="text-[10px] text-orange-400 ml-2">sudo</span> : null}
                    </div>
                    <button onClick={() => deleteCred(c.cred_type)}
                      className="text-slate-600 hover:text-red-400 transition-colors">
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Add creds form or button */}
            {showCredForm ? (
              <CredentialForm ip={host.ip} onSaved={() => { setShowCredForm(false); loadCreds() }} onCancel={() => setShowCredForm(false)} />
            ) : (
              <button onClick={() => setShowCredForm(true)}
                className="w-full flex items-center justify-center gap-1.5 text-xs text-slate-400 border border-dashed border-slate-700 hover:border-blue-600/50 hover:text-blue-400 rounded-lg py-2 transition-colors">
                <Key size={11} /> {creds.length > 0 ? 'Add another credential' : 'Add SSH / WinRM credentials'}
              </button>
            )}

            {/* Deep scan trigger */}
            {creds.length > 0 && !showCredForm && (
              <div className="mt-3">
                <button onClick={triggerDeepScan}
                  disabled={deepScanning || deepScan?.status === 'running'}
                  className="w-full flex items-center justify-center gap-1.5 text-xs bg-purple-900/30 text-purple-300 border border-purple-700/50 hover:bg-purple-900/50 disabled:opacity-40 rounded-lg py-2 transition-colors">
                  {deepScanning || deepScan?.status === 'running'
                    ? <><Loader2 size={11} className="animate-spin" /> Scanning…</>
                    : <><Microscope size={11} /> Run Deep Scan</>
                  }
                </button>
              </div>
            )}

            {/* Deep scan results */}
            {deepScan && deepScan.status === 'done' && Object.keys(deepScan.results).length > 0 && (
              <div className="mt-4 space-y-2">
                <h4 className="text-xs font-semibold text-purple-400 uppercase tracking-wider">
                  Deep Scan Results
                  {deepScan.ran_at && (
                    <span className="text-slate-600 normal-case font-normal ml-2">
                      {new Date(deepScan.ran_at).toLocaleString()}
                    </span>
                  )}
                </h4>
                {Object.entries(deepScan.results).map(([label, output]) => {
                  if (!output || output.trim() === '' || output.startsWith('TIMEOUT') || output.startsWith('ERROR')) return null
                  const isExpanded = expandedSection === label
                  return (
                    <div key={label} className="bg-slate-800/40 rounded-lg overflow-hidden border border-slate-700/30">
                      <button
                        onClick={() => setExpandedSection(isExpanded ? null : label)}
                        className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-mono text-blue-400 hover:bg-slate-800/60"
                      >
                        <span className="uppercase tracking-wider">{label.replace(/_/g, ' ')}</span>
                        {isExpanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
                      </button>
                      {isExpanded && (
                        <div className="px-3 pb-3">
                          <pre className="text-[10px] text-slate-300 whitespace-pre-wrap font-mono leading-relaxed max-h-48 overflow-y-auto">
                            {output}
                          </pre>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {deepScan?.status === 'failed' && (
              <div className="mt-3 flex items-center gap-2 bg-red-900/20 border border-red-800/40 rounded-lg px-3 py-2 text-xs text-red-400">
                <AlertTriangle size={11} />
                <span>{deepScan.error || 'Deep scan failed'}</span>
              </div>
            )}
          </div>

          {/* Timestamps */}
          <div className="text-xs text-slate-600 space-y-1 border-t border-slate-800 pt-3">
            <p>First seen: {host.first_seen ? new Date(host.first_seen).toLocaleString() : '—'}</p>
            <p>Last seen: {host.last_seen ? new Date(host.last_seen).toLocaleString() : '—'}</p>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Main DiscoveryPage ───────────────────────────────────────────────────────

interface ScanDiff {
  previous_scan_id: string | null
  new_ips: string[]
  missing_ips: string[]
}

export function DiscoveryPage() {
  const [networks, setNetworks] = useState<Network[]>([])
  const [scans, setScans] = useState<Scan[]>([])
  const [activeScan, setActiveScan] = useState<Scan | null>(null)
  const [hosts, setHosts] = useState<Host[]>([])
  const [selectedHost, setSelectedHost] = useState<Host | null>(null)
  const [loadingNetworks, setLoadingNetworks] = useState(false)
  const [loadingHosts, setLoadingHosts] = useState(false)
  const [scanProgress, setScanProgress] = useState<string>('')
  const [progressPct, setProgressPct] = useState(0)
  const [progressPhase, setProgressPhase] = useState('')
  const [scanLive, setScanLive] = useState(false)
  const [addCidr, setAddCidr] = useState('')
  const [addLabel, setAddLabel] = useState('')
  const [showAddForm, setShowAddForm] = useState(false)
  const [filterRisk, setFilterRisk] = useState('')
  const [deviceFilter, setDeviceFilter] = useState('')
  const [searchQ, setSearchQ] = useState('')
  const [liveHostCount, setLiveHostCount] = useState(0)
  const [diff, setDiff] = useState<ScanDiff | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const activeScanRef = useRef<Scan | null>(null)
  activeScanRef.current = activeScan

  const loadNetworks = useCallback(async () => {
    setLoadingNetworks(true)
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/networks`)
      if (r.ok) setNetworks((await r.json()).networks || [])
    } catch { /* ignore */ }
    finally { setLoadingNetworks(false) }
  }, [])

  const loadScans = useCallback(async (): Promise<Scan[]> => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/scans`)
      if (r.ok) {
        const list: Scan[] = (await r.json()).scans || []
        setScans(list)
        return list
      }
    } catch { /* ignore */ }
    return []
  }, [])

  // silent=true keeps current cards rendered (merge instead of flash-empty)
  const loadHosts = useCallback(async (scan: Scan, silent = false) => {
    if (!silent) { setLoadingHosts(true); setHosts([]) }
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/scans/${scan.id}/hosts`)
      if (r.ok) {
        const fresh: Host[] = ((await r.json()).hosts || []).map(normalizeHost)
        setHosts(fresh)
      }
    } catch { /* ignore */ }
    finally { if (!silent) setLoadingHosts(false) }
  }, [])

  const loadDiff = useCallback(async (scanId: string) => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/discovery/scans/${scanId}/diff`)
      if (r.ok) setDiff(await r.json())
      else setDiff(null)
    } catch { setDiff(null) }
  }, [])

  // Upsert a host coming from the live event stream
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const upsertHost = useCallback((raw: any, pending = false) => {
    if (!raw?.ip) return
    const h = normalizeHost(raw)
    setHosts(prev => {
      const i = prev.findIndex(x => x.ip === h.ip)
      if (i >= 0) {
        // never downgrade an enriched host back to a ping-only placeholder
        if (pending && ((prev[i].ports?.length ?? 0) > 0 || prev[i].os_name)) return prev
        const next = [...prev]
        next[i] = h
        return next
      }
      return [...prev, h]
    })
  }, [])

  const startEventStream = useCallback((scanId: string) => {
    if (eventSourceRef.current) eventSourceRef.current.close()
    setScanLive(true)
    setScanProgress('')
    setProgressPct(0)
    setProgressPhase('')
    setLiveHostCount(0)

    const es = new EventSource(`${API_BASE}/api/v1/discovery/scans/${scanId}/events`)
    eventSourceRef.current = es

    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data)
        if (ev.type === 'progress') {
          setScanProgress(ev.message || '')
          setProgressPhase(ev.phase || '')
          setProgressPct(ev.phase === 'port-scan' ? (ev.phase_progress ?? 0) : 0)
        } else if (ev.type === 'host_discovered') {
          upsertHost(ev.host, true)
        } else if (ev.type === 'host_scanned') {
          setLiveHostCount(c => c + 1)
          upsertHost(ev.host)
        } else if (ev.type === 'done' || ev.type === 'error' || ev.type === 'cancelled') {
          setScanLive(false)
          es.close()
          loadScans()
          const cur = activeScanRef.current
          if (cur) { loadHosts(cur, true); loadDiff(cur.id) }
        }
      } catch { /* ignore */ }
    }
    es.onerror = () => { es.close() /* polling keeps the data flowing */ }
  }, [loadHosts, loadScans, loadDiff, upsertHost])

  // Initial load + auto-resume: a scan started earlier keeps running server-side —
  // reattach to it instead of showing an empty page.
  useEffect(() => {
    (async () => {
      loadNetworks()
      const list = await loadScans()
      const running = list.find(s => s.status === 'running')
      const target = running || list[0] || null
      if (target) {
        setActiveScan(target)
        loadHosts(target)
        loadDiff(target.id)
        if (running) startEventStream(running.id)
      }
    })()
    return () => { eventSourceRef.current?.close() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Polling fallback while a scan runs — survives SSE drops/proxy hiccups
  useEffect(() => {
    if (!scanLive || !activeScan) return
    const id = setInterval(() => {
      loadHosts(activeScan, true)
      loadScans()
    }, 10_000)
    return () => clearInterval(id)
  }, [scanLive, activeScan, loadHosts, loadScans])

  function selectScan(scan: Scan) {
    setActiveScan(scan)
    setHosts([])
    setDiff(null)
    loadHosts(scan)
    loadDiff(scan.id)
    if (scan.status === 'running') startEventStream(scan.id)
    else { eventSourceRef.current?.close(); setScanLive(false) }
  }

  const [scanProfile, setScanProfile] = useState<'fast' | 'standard' | 'deep' | 'stealth'>('standard')

  async function startScan(cidr: string, label: string) {
    const r = await fetch(`${API_BASE}/api/v1/discovery/scans`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cidr, label, profile: scanProfile }),
    })
    if (!r.ok) return
    const scan = await r.json()
    const scanId: string = scan.scan_id || scan.id
    await loadScans()
    const full: Scan = { id: scanId, cidr, label, status: 'running', started_at: new Date().toISOString(), completed_at: null, host_count: 0 }
    setActiveScan(full)
    setHosts([])
    setDiff(null)
    startEventStream(scanId)
    loadDiff(scanId)
  }

  async function stopScan(scanId: string) {
    await fetch(`${API_BASE}/api/v1/discovery/scans/${scanId}/stop`, { method: 'POST' })
    setScanLive(false)
    eventSourceRef.current?.close()
    loadScans()
  }

  async function deleteScan(scanId: string) {
    await fetch(`${API_BASE}/api/v1/discovery/scans/${scanId}`, { method: 'DELETE' })
    setScanLive(false)
    eventSourceRef.current?.close()
    if (activeScan?.id === scanId) { setActiveScan(null); setHosts([]); setDiff(null) }
    loadScans()
  }

  async function addManualNetwork() {
    if (!addCidr.trim()) return
    await fetch(`${API_BASE}/api/v1/discovery/networks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cidr: addCidr.trim(), label: addLabel.trim() || addCidr.trim() }),
    })
    setAddCidr('')
    setAddLabel('')
    setShowAddForm(false)
    loadNetworks()
  }

  async function removeNetwork(cidr: string) {
    await fetch(`${API_BASE}/api/v1/discovery/networks/${encodeURIComponent(cidr)}`, { method: 'DELETE' })
    loadNetworks()
  }

  const riskCounts = hosts.reduce<Record<string, number>>((acc, h) => {
    acc[h.risk_level] = (acc[h.risk_level] || 0) + 1; return acc
  }, {})

  const deviceClasses = [...new Set(hosts.map(h => h.device_class).filter(c => c && c !== 'unknown'))].sort()
  const newIpSet = new Set(diff?.new_ips ?? [])

  // Client-side filter + search + sort (risk first, then numeric IP)
  const visibleHosts = (() => {
    let list = hosts
    if (filterRisk) list = list.filter(h => h.risk_level === filterRisk)
    if (deviceFilter) list = list.filter(h => h.device_class === deviceFilter)
    if (searchQ.trim()) {
      const q = searchQ.trim().toLowerCase()
      list = list.filter(h =>
        h.ip.includes(q) ||
        h.dns_names.some(d => d.toLowerCase().includes(q)) ||
        (h.os_name || '').toLowerCase().includes(q) ||
        (h.vendor || '').toLowerCase().includes(q) ||
        (h.mac || '').toLowerCase().includes(q) ||
        h.ports.some(p => String(p.port) === q || (p.service || '').toLowerCase().includes(q))
      )
    }
    return [...list].sort((a, b) =>
      (b.risk_score - a.risk_score) || a.ip.localeCompare(b.ip, undefined, { numeric: true }))
  })()

  const totalOpenPorts = hosts.reduce((n, h) => n + h.ports.filter(p => p.state === 'open').length, 0)

  return (
    <div className="min-h-screen bg-[#0a0e17]">
      <div className="max-w-[1800px] mx-auto px-6 py-6 flex gap-5">

        {/* ── LEFT: Networks + Scan History ────────────────────────────────── */}
        <div className="w-72 flex-shrink-0 space-y-4">

          {/* Networks */}
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/60 overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-700/40 flex items-center gap-2">
              <Radar size={13} className="text-blue-400" />
              <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Networks</span>
              <button onClick={loadNetworks} className="ml-auto text-slate-500 hover:text-slate-300" title="Refresh">
                <RefreshCw size={11} className={loadingNetworks ? 'animate-spin' : ''} />
              </button>
              <button onClick={() => setShowAddForm(v => !v)} className="text-slate-500 hover:text-blue-400" title="Add network">
                <Plus size={13} />
              </button>
            </div>

            {/* Scan profile selector */}
            <div className="px-4 py-2 border-b border-slate-700/40 bg-slate-800/20 flex items-center gap-1.5 flex-wrap">
              <span className="text-[10px] text-slate-500 mr-1">Profile:</span>
              {(['fast', 'standard', 'deep', 'stealth'] as const).map(p => (
                <button
                  key={p}
                  onClick={() => setScanProfile(p)}
                  className={`text-[10px] px-2 py-0.5 rounded-full border transition-colors capitalize ${
                    scanProfile === p
                      ? 'bg-blue-600/30 border-blue-500/60 text-blue-300'
                      : 'border-slate-700 text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>

            {showAddForm && (
              <div className="px-4 py-3 border-b border-slate-700/40 space-y-2 bg-slate-800/40">
                <input className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-xs text-white placeholder-slate-500 font-mono"
                  placeholder="10.0.0.0/24" value={addCidr} onChange={e => setAddCidr(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && addManualNetwork()} />
                <input className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-xs text-white placeholder-slate-500"
                  placeholder="Label (optional)" value={addLabel} onChange={e => setAddLabel(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && addManualNetwork()} />
                <div className="flex gap-2">
                  <button onClick={addManualNetwork} className="flex-1 bg-blue-600 hover:bg-blue-500 text-white text-xs py-1.5 rounded-lg transition-colors">Add</button>
                  <button onClick={() => setShowAddForm(false)} className="flex-1 bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs py-1.5 rounded-lg transition-colors">Cancel</button>
                </div>
              </div>
            )}

            <div className="divide-y divide-slate-800/60 max-h-[50vh] overflow-y-auto">
              {networks.length === 0 && !loadingNetworks && (
                <p className="text-xs text-slate-500 px-4 py-4 text-center">
                  No networks discovered yet.<br />
                  <span className="text-slate-600">Connect to vCenter or add manually.</span>
                </p>
              )}
              {networks.map(net => (
                <div key={net.cidr} className="px-4 py-3 flex items-center gap-2 group hover:bg-slate-800/30">
                  <div className="flex-1 min-w-0">
                    <p className="font-mono text-xs text-white">{net.cidr}</p>
                    <p className="text-[10px] text-slate-500 truncate">{net.label}</p>
                  </div>
                  <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border ${SOURCE_BADGE[net.source] || SOURCE_BADGE.manual}`}>
                    {net.source === 'k8s-node' ? 'K8s' : net.source}
                  </span>
                  <button onClick={() => startScan(net.cidr, net.label)} disabled={scanLive}
                    className="opacity-0 group-hover:opacity-100 text-blue-400 hover:text-blue-300 disabled:opacity-30 transition-all" title="Scan">
                    <Play size={12} />
                  </button>
                  {net.source === 'manual' && (
                    <button onClick={() => removeNetwork(net.cidr)}
                      className="opacity-0 group-hover:opacity-100 text-slate-600 hover:text-red-400 transition-all" title="Remove">
                      <Trash2 size={11} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Scan history */}
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/60 overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-700/40 flex items-center gap-2">
              <Shield size={13} className="text-slate-400" />
              <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Scan History</span>
              <button onClick={loadScans} className="ml-auto text-slate-500 hover:text-slate-300"><RefreshCw size={11} /></button>
            </div>
            <div className="divide-y divide-slate-800/60 max-h-72 overflow-y-auto">
              {scans.length === 0 && (
                <p className="text-xs text-slate-500 px-4 py-4 text-center">No scans yet</p>
              )}
              {scans.map(scan => (
                <div key={scan.id}
                  className={`flex items-start gap-1 px-2 py-2 hover:bg-slate-800/40 group ${activeScan?.id === scan.id ? 'bg-blue-900/20 border-l-2 border-blue-500' : ''}`}>
                  <button
                    className="flex-1 text-left"
                    onClick={() => selectScan(scan)}>
                    <div className="flex items-center justify-between gap-1 flex-wrap">
                      <span className="font-mono text-[11px] text-white truncate">{scan.cidr}</span>
                      <ScanStatusBadge status={scan.status} />
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      {(scan.host_count || scan.hosts_found || 0) > 0 && (
                        <span className="text-[10px] text-slate-500">{scan.host_count || scan.hosts_found} hosts</span>
                      )}
                      {scan.started_at && (
                        <span className="text-[10px] text-slate-600">{new Date(scan.started_at).toLocaleTimeString()}</span>
                      )}
                    </div>
                    {scan.status === 'failed' && scan.error && (
                      <p className="text-[10px] text-red-400 mt-0.5 truncate" title={scan.error}>{scan.error}</p>
                    )}
                  </button>
                  {/* Stop button (only running) */}
                  {scan.status === 'running' && (
                    <button onClick={() => stopScan(scan.id)}
                      className="opacity-0 group-hover:opacity-100 flex-shrink-0 text-yellow-500 hover:text-yellow-400 p-1 transition-all" title="Stop scan">
                      <Pause size={11} />
                    </button>
                  )}
                  {/* Delete button (all scans) */}
                  <button onClick={() => deleteScan(scan.id)}
                    className="opacity-0 group-hover:opacity-100 flex-shrink-0 text-slate-600 hover:text-red-400 p-1 transition-all" title="Delete scan">
                    <Trash2 size={11} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── RIGHT: Hosts panel ───────────────────────────────────────────── */}
        <div className="flex-1 min-w-0 space-y-4">

          {activeScan && (
            <div className="rounded-xl border border-slate-700/40 bg-slate-900/60 px-5 py-4">
              <div className="flex items-center gap-3 flex-wrap">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-bold text-white">{activeScan.cidr}</span>
                    <ScanStatusBadge status={scanLive ? 'running' : activeScan.status} />
                  </div>
                  {activeScan.label && activeScan.label !== activeScan.cidr && (
                    <p className="text-xs text-slate-500 mt-0.5">{activeScan.label}</p>
                  )}
                  {activeScan.status === 'failed' && (
                    <p className="text-xs text-red-400 mt-1 flex items-center gap-1">
                      <AlertTriangle size={11} />
                      {activeScan.error || 'Scan failed — check nmap availability and network connectivity'}
                    </p>
                  )}
                </div>

                {scanLive && (
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      {scanProgress && <p className="text-xs text-blue-400 truncate">{scanProgress}</p>}
                      <span className="text-[10px] text-slate-500 flex-shrink-0 italic">
                        runs in background — you can leave this page
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 bg-slate-800 rounded-full overflow-hidden w-full">
                      {progressPhase === 'port-scan' && progressPct > 0 ? (
                        <div className="h-full bg-blue-500 rounded-full transition-all duration-700" style={{ width: `${progressPct}%` }} />
                      ) : (
                        <div className="h-full bg-blue-500/60 animate-pulse rounded-full" style={{ width: '100%' }} />
                      )}
                    </div>
                    {(liveHostCount > 0 || progressPct > 0) && (
                      <p className="text-[10px] text-blue-400 mt-1">
                        {progressPhase === 'port-scan' ? `${progressPct}% · ` : ''}
                        {liveHostCount > 0 ? `${liveHostCount} hosts port-scanned` : 'discovering hosts…'}
                      </p>
                    )}
                  </div>
                )}

                <div className="ml-auto flex items-center gap-2 flex-shrink-0">
                  {hosts.length > 0 && (
                    <a href={`${API_BASE}/api/v1/discovery/scans/${activeScan.id}/export`}
                      download={`discovery-${activeScan.id}.csv`}
                      className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-slate-700 px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors">
                      <Upload size={11} className="rotate-180" /> CSV
                    </a>
                  )}
                  {scanLive ? (
                    <>
                      <button onClick={() => stopScan(activeScan.id)}
                        className="flex items-center gap-1.5 text-xs bg-yellow-900/30 text-yellow-400 border border-yellow-800/50 px-3 py-1.5 rounded-lg hover:bg-yellow-900/50 transition-colors">
                        <Pause size={11} /> Stop
                      </button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => startScan(activeScan.cidr, activeScan.label)}
                        className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors border ${activeScan.status === 'failed' ? 'bg-red-600/20 text-red-400 border-red-700/50 hover:bg-red-600/30' : 'bg-blue-600/30 text-blue-400 border-blue-700/50 hover:bg-blue-600/50'}`}>
                        <RefreshCw size={11} /> {activeScan.status === 'failed' ? 'Retry Scan' : 'Re-scan'}
                      </button>
                      <button onClick={() => deleteScan(activeScan.id)}
                        className="flex items-center gap-1.5 text-xs bg-slate-800 text-slate-400 border border-slate-700 px-3 py-1.5 rounded-lg hover:bg-red-900/30 hover:text-red-400 hover:border-red-800/50 transition-colors">
                        <Trash2 size={11} /> Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Stat tiles */}
          {activeScan && hosts.length > 0 && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { label: 'Hosts', value: hosts.length, cls: 'text-white' },
                { label: 'Critical / High risk', value: (riskCounts.critical || 0) + (riskCounts.high || 0), cls: (riskCounts.critical || 0) + (riskCounts.high || 0) > 0 ? 'text-red-400' : 'text-green-400' },
                { label: 'New since last scan', value: diff?.previous_scan_id ? newIpSet.size : '—', cls: newIpSet.size > 0 ? 'text-emerald-400' : 'text-slate-400' },
                { label: 'Open ports', value: totalOpenPorts, cls: 'text-white' },
              ].map(t => (
                <div key={t.label} className="rounded-xl border border-slate-700/40 bg-slate-900/60 px-4 py-3">
                  <div className={`text-xl font-bold tabular-nums ${t.cls}`}>{t.value}</div>
                  <div className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">{t.label}</div>
                </div>
              ))}
            </div>
          )}

          {/* Disappeared hosts notice */}
          {diff && diff.missing_ips.length > 0 && (
            <div className="rounded-xl border border-amber-800/40 bg-amber-900/15 px-4 py-3 flex items-start gap-2 text-xs text-amber-300">
              <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold">{diff.missing_ips.length} host(s) present in the previous scan did not respond this time: </span>
                <span className="font-mono">{diff.missing_ips.slice(0, 8).join(', ')}{diff.missing_ips.length > 8 ? ` +${diff.missing_ips.length - 8} more` : ''}</span>
              </div>
            </div>
          )}

          {/* Search + filters */}
          {hosts.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <div className="relative">
                <input
                  value={searchQ}
                  onChange={e => setSearchQ(e.target.value)}
                  placeholder="Search IP, DNS, OS, port, service…"
                  aria-label="Search hosts"
                  className="w-64 bg-slate-900/80 border border-slate-700/60 rounded-lg pl-8 pr-3 py-1.5 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-700"
                />
                <Radar size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-600" />
              </div>
              {['', 'critical', 'high', 'medium', 'low'].map(level => (
                <button key={level} onClick={() => setFilterRisk(level)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${filterRisk === level
                    ? 'bg-blue-600/30 text-blue-300 border-blue-600/50'
                    : 'text-slate-400 border-slate-700/50 hover:text-white'
                  }`}>
                  {level || 'All'} {level && riskCounts[level] ? `(${riskCounts[level]})` : ''}
                </button>
              ))}
              {deviceClasses.length > 1 && (
                <select
                  value={deviceFilter}
                  onChange={e => setDeviceFilter(e.target.value)}
                  aria-label="Filter by device class"
                  className="bg-slate-900/80 border border-slate-700/60 rounded-lg px-2 py-1 text-xs text-slate-300 focus:outline-none"
                >
                  <option value="">All devices</option>
                  {deviceClasses.map(c => <option key={c} value={c}>{c.replace('-', ' ')}</option>)}
                </select>
              )}
              <span className="ml-auto text-xs text-slate-500">
                {visibleHosts.length === hosts.length ? `${hosts.length} hosts` : `${visibleHosts.length} of ${hosts.length} hosts`}
              </span>
            </div>
          )}

          {!activeScan && (
            <div className="rounded-xl border border-slate-700/40 bg-slate-900/60 p-16 flex flex-col items-center justify-center text-center gap-4">
              <Radar size={40} className="text-slate-600" />
              <div>
                <p className="text-slate-300 font-medium">No scan selected</p>
                <p className="text-slate-500 text-xs mt-1">
                  Hover a network and click <Play size={10} className="inline" /> to scan, or pick a past scan.
                </p>
              </div>
            </div>
          )}

          {activeScan && !scanLive && hosts.length === 0 && !loadingHosts && (
            <div className="rounded-xl border border-slate-700/40 bg-slate-900/60 p-12 flex flex-col items-center justify-center text-center gap-3">
              <Globe size={32} className="text-slate-600" />
              <p className="text-slate-500 text-sm">No hosts found in this scan</p>
            </div>
          )}

          {scanLive && hosts.length === 0 && (
            <div className="rounded-xl border border-blue-900/40 bg-slate-900/60 p-12 flex flex-col items-center justify-center text-center gap-3">
              <Radar size={32} className="text-blue-500 animate-pulse" />
              <p className="text-slate-300 text-sm">Sweeping {activeScan?.cidr} for live hosts…</p>
              <p className="text-slate-500 text-xs">Hosts appear here the moment they respond — the scan keeps running if you leave this page.</p>
            </div>
          )}

          {loadingHosts && (
            <div className="flex items-center justify-center py-16 gap-2 text-slate-500">
              <Loader2 size={18} className="animate-spin" /> Loading hosts…
            </div>
          )}

          {visibleHosts.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {visibleHosts.map(host => (
                <HostCard
                  key={host.ip}
                  host={host}
                  isNew={newIpSet.has(host.ip)}
                  scanning={scanLive}
                  onClick={() => setSelectedHost(host)}
                />
              ))}
            </div>
          )}

          {hosts.length > 0 && visibleHosts.length === 0 && (
            <div className="rounded-xl border border-slate-700/40 bg-slate-900/60 p-10 text-center">
              <p className="text-slate-500 text-sm">No hosts match the current search/filters</p>
              <button onClick={() => { setSearchQ(''); setFilterRisk(''); setDeviceFilter('') }}
                className="mt-2 text-xs text-blue-400 hover:text-blue-300">Clear filters</button>
            </div>
          )}
        </div>
      </div>

      {selectedHost && <HostDrawer host={selectedHost} onClose={() => setSelectedHost(null)} />}
    </div>
  )
}
