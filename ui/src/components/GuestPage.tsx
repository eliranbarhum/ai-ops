import { useState, useRef } from 'react'
import { Laptop, Play, RefreshCw, Loader2, CheckCircle, XCircle, AlertTriangle, ArrowLeft, Terminal, Download, Search, Wrench, Sparkles } from 'lucide-react'
import type { GuestVm } from '../types'

const API_BASE = import.meta.env.VITE_API_URL || ''

type ScriptType = 'PowerShell' | 'Bash'

const TOOLS_ORDER: Record<string, number> = {
  guestToolsCurrent:      0,
  guestToolsNeedUpgrade:  1,
  guestToolsUnmanaged:    2,
  guestToolsNotInstalled: 3,
}

const TOOL_STATUS_COLOR: Record<string, string> = {
  guestToolsCurrent:      'text-green-400',
  guestToolsNeedUpgrade:  'text-yellow-400',
  guestToolsNotInstalled: 'text-red-400',
  guestToolsUnmanaged:    'text-slate-400',
}

const TOOL_STATUS_LABEL: Record<string, string> = {
  guestToolsCurrent:      'Current',
  guestToolsNeedUpgrade:  'Needs Upgrade',
  guestToolsNotInstalled: 'Not Installed',
  guestToolsUnmanaged:    'Unmanaged',
}

const QUICK_SCRIPTS: Record<ScriptType, { label: string; script: string }[]> = {
  PowerShell: [
    { label: 'Disk Space',
      script: "Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{N='Used_GB';E={[math]::Round($_.Used/1GB,2)}},@{N='Free_GB';E={[math]::Round($_.Free/1GB,2)}} | ConvertTo-Json -AsArray" },
    { label: 'Running Services',
      script: "Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object Name,DisplayName | ConvertTo-Json -AsArray" },
    { label: 'CPU & Memory',
      script: "[PSCustomObject]@{cpu_pct=(Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average; mem_free_gb=[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,2); mem_total_gb=[math]::Round((Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize/1MB,2)} | ConvertTo-Json" },
    { label: 'System Info',
      script: "[PSCustomObject]@{hostname=$env:COMPUTERNAME; os=(Get-CimInstance Win32_OperatingSystem).Caption; uptime=(New-TimeSpan -Start (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).ToString(); ip=(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notmatch '^(127|169)'} | Select-Object -First 1 -ExpandProperty IPAddress)} | ConvertTo-Json" },
    { label: 'Top Processes',
      script: "Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name,Id,@{N='CPU_s';E={[math]::Round($_.CPU,1)}},@{N='Mem_MB';E={[math]::Round($_.WorkingSet/1MB,1)}} | ConvertTo-Json -AsArray" },
    { label: 'Installed Software',
      script: "Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | Where-Object {$_.DisplayName} | Select-Object DisplayName,DisplayVersion,Publisher | Sort-Object DisplayName | ConvertTo-Json -AsArray" },
    { label: 'Network Connections',
      script: "Get-NetTCPConnection | Where-Object {$_.State -eq 'Established'} | Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State | ConvertTo-Json -AsArray" },
    { label: 'Event Log Errors',
      script: "Get-EventLog -LogName System -EntryType Error -Newest 20 | Select-Object TimeGenerated,Source,@{N='Msg';E={$_.Message.Substring(0,[math]::Min(120,$_.Message.Length))}} | ConvertTo-Json -AsArray" },
    { label: 'Firewall Rules',
      script: "Get-NetFirewallRule | Where-Object {$_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound'} | Select-Object DisplayName,Profile,Action | ConvertTo-Json -AsArray" },
    { label: 'Pending Reboots',
      script: "$keys=@('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired','HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager'); [PSCustomObject]@{pending_reboot=($keys | ForEach-Object {Test-Path $_}) -contains $true} | ConvertTo-Json" },
  ],
  Bash: [
    { label: 'Disk Space',
      script: "df -h | awk 'NR==1 || /^\\//'" },
    { label: 'Memory Usage',
      script: "free -h && echo '---' && awk '/MemTotal|MemFree|MemAvailable/{print}' /proc/meminfo" },
    { label: 'CPU & Load',
      script: "echo \"CPU cores: $(nproc)\" && uptime && echo '---' && top -bn1 | grep 'Cpu(s)'" },
    { label: 'System Info',
      script: "echo \"{\\\"hostname\\\": \\\"$(hostname)\\\", \\\"os\\\": \\\"$(. /etc/os-release && echo $PRETTY_NAME)\\\", \\\"kernel\\\": \\\"$(uname -r)\\\", \\\"uptime\\\": \\\"$(uptime -p)\\\"}\"" },
    { label: 'Top Processes',
      script: "ps aux --sort=-%cpu | awk 'NR<=16{print}' | column -t" },
    { label: 'Network Ports',
      script: "ss -tulnp" },
    { label: 'Established Connections',
      script: "ss -tnp | grep ESTAB | awk '{print $4, $5, $6}' | column -t" },
    { label: 'Recent Errors',
      script: "journalctl -p err --since '24 hours ago' --no-pager | tail -30" },
    { label: 'Last Logins',
      script: "last -n 20" },
    { label: 'Cron Jobs',
      script: "for u in $(cut -f1 -d: /etc/passwd); do crontab -u $u -l 2>/dev/null | grep -v '^#' | grep -v '^$' | sed \"s/^/$u: /\"; done" },
  ],
}

function ToolsBadge({ status }: { status: string }) {
  const color = TOOL_STATUS_COLOR[status] ?? 'text-slate-400'
  const label = TOOL_STATUS_LABEL[status] ?? status
  return <span className={`text-xs ${color}`}>{label}</span>
}

interface ToolsOp { loading: boolean; ok?: boolean; msg?: string }

export function GuestPage() {
  const [vms, setVms] = useState<GuestVm[]>(() => {
    try { return JSON.parse(localStorage.getItem('guest-inventory') || '[]') } catch { return [] }
  })
  const [loadingInv, setLoadingInv] = useState(false)
  const [invError, setInvError] = useState('')
  const [search, setSearch] = useState('')
  const [selectedVm, setSelectedVm] = useState('')
  const [scriptType, setScriptType] = useState<ScriptType>('PowerShell')
  const [script, setScript] = useState('')
  const [guestUser, setGuestUser] = useState('')
  const [guestPass, setGuestPass] = useState('')
  const [showPass, setShowPass] = useState(false)
  const [running, setRunning] = useState(false)
  const [runOutput, setRunOutput] = useState<{ output?: string; error?: string; exit_code?: number } | null>(null)
  const [toolsOps, setToolsOps] = useState<Record<string, ToolsOp>>({})
  const [lastRefreshed, setLastRefreshed] = useState<string>(() => localStorage.getItem('guest-inventory-ts') || '')
  const [nlPrompt, setNlPrompt] = useState('')
  const [generating, setGenerating] = useState(false)
  const outputRef = useRef<HTMLPreElement>(null)

  async function refreshInventory() {
    setLoadingInv(true)
    setInvError('')
    try {
      const r = await fetch(`${API_BASE}/api/v1/guest/inventory`, { method: 'POST' })
      if (!r.ok) {
        let msg = `HTTP ${r.status}`
        try { const d = await r.json(); msg = d.detail || d.error || msg } catch { /* ignore */ }
        setInvError(msg)
        return
      }
      const data = await r.json()
      if (data.response?.error) { setInvError(data.response.error); return }
      const resp = data.response || data
      let parsed: GuestVm[] = []
      try {
        const raw = resp.output ? JSON.parse(resp.output) : (Array.isArray(resp) ? resp : [])
        parsed = Array.isArray(raw) ? raw : (raw ? [raw] : [])
      } catch { parsed = [] }
      if (parsed.length > 0) {
        setVms(parsed)
        setToolsOps({})
        const ts = new Date().toLocaleString()
        setLastRefreshed(ts)
        localStorage.setItem('guest-inventory', JSON.stringify(parsed))
        localStorage.setItem('guest-inventory-ts', ts)
      } else {
        setInvError('No powered-on VMs returned — vCenter may be unreachable. Showing last known inventory.')
      }
    } catch (e) {
      const msg = e instanceof TypeError ? 'Cannot reach API gateway — check that the service is running.' : String(e)
      setInvError(msg)
    } finally {
      setLoadingInv(false)
    }
  }

  async function runToolsOp(vmName: string) {
    setToolsOps(prev => ({ ...prev, [vmName]: { loading: true } }))
    try {
      const r = await fetch(`${API_BASE}/api/v1/guest/tools`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vm_name: vmName }),
      })
      const data = await r.json()
      const ok = data.status_code === 200
      const resp = data.response
      let msg = ok ? 'Initiated' : 'Failed'
      try {
        const parsed = typeof resp === 'string' ? JSON.parse(resp) : resp?.output ? JSON.parse(resp.output) : resp
        msg = parsed?.message || parsed?.output || resp?.error || msg
      } catch {
        msg = resp?.output || resp?.error || msg
      }
      setToolsOps(prev => ({ ...prev, [vmName]: { loading: false, ok, msg } }))
    } catch (e) {
      setToolsOps(prev => ({ ...prev, [vmName]: { loading: false, ok: false, msg: String(e) } }))
    }
  }

  async function generateScript() {
    if (!nlPrompt.trim()) return
    setGenerating(true)
    try {
      // Pick up the OS hint from the selected VM if available
      const vm = vms.find(v => v.name === selectedVm)
      const os_hint = vm?.os || ''
      const r = await fetch(`${API_BASE}/api/v1/guest/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: nlPrompt.trim(), script_type: scriptType, os_hint }),
      })
      const data = await r.json()
      if (data.script) setScript(data.script)
    } catch (e) {
      setScript(`# Generate failed: ${e}`)
    } finally {
      setGenerating(false)
    }
  }

  async function runScript() {
    if (!selectedVm || !script.trim() || !guestUser) return
    setRunning(true)
    setRunOutput(null)
    try {
      const r = await fetch(`${API_BASE}/api/v1/guest/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vm_name: selectedVm, script, script_type: scriptType, guest_username: guestUser, guest_password: guestPass }),
      })
      const data = await r.json()
      const resp = data.response
      try {
        const parsed = typeof resp === 'string' ? JSON.parse(resp) : resp.output ? JSON.parse(resp.output) : resp
        setRunOutput(parsed)
      } catch {
        setRunOutput({ output: resp?.output || JSON.stringify(resp), exit_code: resp?.exit_code })
      }
      setTimeout(() => { if (outputRef.current) outputRef.current.scrollTop = 0 }, 50)
    } catch (e) {
      setRunOutput({ error: String(e) })
    } finally {
      setRunning(false)
    }
  }

  function exportCsv() {
    const header = 'name,os,hostname,ip,tools,tools_ver,power,cluster,host'
    const lines = vms.map(v => [v.name, v.os, v.hostname, v.ip, v.tools, v.tools_ver, v.power, v.cluster, v.host].map(x => `"${(x || '').replace(/"/g, '""')}"`).join(','))
    const blob = new Blob([[header, ...lines].join('\n')], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `guest-inventory-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
  }

  const filtered = [...vms]
    .filter(v => !search || v.name.toLowerCase().includes(search.toLowerCase()) || (v.os || '').toLowerCase().includes(search.toLowerCase()) || (v.hostname || '').toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => (TOOLS_ORDER[a.tools] ?? 4) - (TOOLS_ORDER[b.tools] ?? 4))

  const toolsInstalled = vms.filter(v => v.tools !== 'guestToolsNotInstalled').length
  const needsUpgrade   = vms.filter(v => v.tools === 'guestToolsNeedUpgrade').length
  const notInstalled   = vms.filter(v => v.tools === 'guestToolsNotInstalled').length

  return (
    <div className="min-h-screen bg-vmware-dark">
      <main className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        {/* ── Inventory section ── */}
        <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                <Laptop size={14} className="text-teal-400" /> VM Guest Inventory
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">Powered-on VMs sorted by VMware Tools status — tools installed first</p>
            </div>
            <div className="flex items-center gap-2">
              {vms.length > 0 && (
                <button onClick={exportCsv} className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors">
                  <Download size={12} /> Export CSV
                </button>
              )}
              <button
                onClick={refreshInventory}
                disabled={loadingInv}
                className="flex items-center gap-1.5 text-xs bg-teal-700/40 hover:bg-teal-700/60 border border-teal-700/50 text-teal-300 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-40"
              >
                {loadingInv ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                {loadingInv ? 'Refreshing…' : 'Refresh Inventory'}
              </button>
            </div>
          </div>

          {/* Summary pills */}
          {vms.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-vmware-border">{vms.length} total</span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-900/30 text-green-400 border border-green-800/40">{toolsInstalled} with tools</span>
              {needsUpgrade > 0 && <span className="text-[10px] px-2 py-0.5 rounded-full bg-yellow-900/30 text-yellow-400 border border-yellow-800/40">{needsUpgrade} need upgrade</span>}
              {notInstalled > 0 && <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-900/30 text-red-400 border border-red-800/40">{notInstalled} no tools</span>}
              {loadingInv && <span className="text-[10px] text-teal-400 animate-pulse">● refreshing…</span>}
            </div>
          )}

          {invError && (
            <div className="rounded-lg border border-red-800 bg-red-900/20 px-3 py-2 flex items-start gap-2">
              <XCircle size={12} className="text-red-400 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-red-400">{invError}</p>
            </div>
          )}

          {vms.length > 0 && (
            <>
              <div className="relative">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="Filter by name, OS, hostname…"
                  className="w-full bg-slate-800 border border-vmware-border rounded-lg pl-8 pr-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div className="rounded-xl border border-vmware-border overflow-hidden">
                <div className="overflow-y-auto max-h-[460px]">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 z-10">
                      <tr className="border-b border-vmware-border bg-[#0d1117]">
                        <th className="text-left px-3 py-2 text-slate-400 font-medium">VM Name</th>
                        <th className="text-left px-3 py-2 text-slate-400 font-medium">OS</th>
                        <th className="text-left px-3 py-2 text-slate-400 font-medium">Hostname</th>
                        <th className="text-left px-3 py-2 text-slate-400 font-medium">IP</th>
                        <th className="text-left px-3 py-2 text-slate-400 font-medium">Tools</th>
                        <th className="text-left px-3 py-2 text-slate-400 font-medium">Host</th>
                        <th className="text-left px-3 py-2 text-slate-400 font-medium">Cluster</th>
                        <th className="px-3 py-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((vm, i) => {
                        const op = toolsOps[vm.name]
                        const canAct = vm.tools === 'guestToolsNeedUpgrade' || vm.tools === 'guestToolsNotInstalled'
                        return (
                          <tr
                            key={i}
                            onClick={() => setSelectedVm(vm.name)}
                            className={`border-b border-vmware-border/50 cursor-pointer transition-colors ${selectedVm === vm.name ? 'bg-blue-900/20 border-l-2 border-l-blue-500' : 'hover:bg-slate-800/20'}`}
                          >
                            <td className="px-3 py-2 font-medium text-slate-200">{vm.name}</td>
                            <td className="px-3 py-2 text-slate-300 truncate max-w-40" title={vm.os}>{vm.os || '—'}</td>
                            <td className="px-3 py-2 font-mono text-slate-300">{vm.hostname || '—'}</td>
                            <td className="px-3 py-2 font-mono text-slate-300">{vm.ip || '—'}</td>
                            <td className="px-3 py-2">
                              <div className="flex flex-col gap-0.5">
                                <div className="flex items-center gap-1.5">
                                  <ToolsBadge status={vm.tools} />
                                  {canAct && (
                                    op?.loading ? (
                                      <Loader2 size={10} className="animate-spin text-slate-400 flex-shrink-0" />
                                    ) : op?.ok !== undefined ? (
                                      op.ok
                                        ? <CheckCircle size={10} className="text-green-400 flex-shrink-0" />
                                        : <XCircle size={10} className="text-red-400 flex-shrink-0" />
                                    ) : (
                                      <button
                                        onClick={e => { e.stopPropagation(); runToolsOp(vm.name) }}
                                        className={`flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
                                          vm.tools === 'guestToolsNeedUpgrade'
                                            ? 'text-amber-400 border-amber-800/60 hover:border-amber-500 hover:bg-amber-900/20'
                                            : 'text-orange-400 border-orange-800/60 hover:border-orange-500 hover:bg-orange-900/20'
                                        }`}
                                      >
                                        <Wrench size={9} />
                                        {vm.tools === 'guestToolsNeedUpgrade' ? 'Update' : 'Install'}
                                      </button>
                                    )
                                  )}
                                </div>
                                {op?.msg && (
                                  <span className={`text-[9px] leading-tight ${op.ok ? 'text-green-500' : 'text-red-400'}`}>
                                    {op.msg.length > 50 ? op.msg.slice(0, 50) + '…' : op.msg}
                                  </span>
                                )}
                              </div>
                            </td>
                            <td className="px-3 py-2 text-slate-400">{vm.host || '—'}</td>
                            <td className="px-3 py-2 text-slate-400">{vm.cluster || '—'}</td>
                            <td className="px-3 py-2 text-right">
                              <button
                                onClick={e => { e.stopPropagation(); setSelectedVm(vm.name); document.getElementById('script-runner')?.scrollIntoView({ behavior: 'smooth' }) }}
                                className="text-xs text-blue-400 hover:text-blue-300 px-2 py-0.5 border border-blue-800 rounded hover:border-blue-600 transition-colors"
                              >
                                Run Script
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                      {filtered.length === 0 && (
                        <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-500">No VMs match filter</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
              <p className="text-xs text-slate-500">
                {filtered.length} of {vms.length} VMs shown · Click a row to select for script runner
                {lastRefreshed && <span className="ml-2 text-slate-600">· Last refreshed: {lastRefreshed}</span>}
              </p>
            </>
          )}

          {!loadingInv && vms.length === 0 && !invError && (
            <div className="rounded-xl border border-vmware-border p-10 text-center text-slate-500">
              <Laptop size={28} className="mx-auto mb-2 opacity-40" />
              <p className="text-sm">Click "Refresh Inventory" to load powered-on VMs</p>
            </div>
          )}
        </div>

        {/* ── Script runner ── */}
        <div id="script-runner" className="rounded-xl border border-vmware-border bg-vmware-card p-5 space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-white flex items-center gap-2">
              <Terminal size={14} className="text-yellow-400" /> In-Guest Script Runner
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">Executes scripts inside the guest OS via VMware Tools (Invoke-VMScript). Requires guest OS credentials — never stored.</p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Target VM</label>
              <input
                value={selectedVm}
                onChange={e => setSelectedVm(e.target.value)}
                placeholder="Select from inventory or type VM name"
                className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Script Type</label>
              <div className="flex gap-1 bg-slate-800/60 p-1 rounded-lg">
                {(['PowerShell', 'Bash'] as ScriptType[]).map(t => (
                  <button
                    key={t}
                    onClick={() => setScriptType(t)}
                    className={`flex-1 text-xs py-1.5 rounded-md transition-colors font-medium ${scriptType === t ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Quick scripts */}
          <div className="rounded-lg border border-vmware-border bg-slate-800/30 p-3">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Quick Scripts</p>
            <div className="flex flex-wrap gap-1.5">
              {QUICK_SCRIPTS[scriptType].map(qs => (
                <button
                  key={qs.label}
                  onClick={() => setScript(qs.script)}
                  title={qs.script}
                  className="text-[11px] px-2.5 py-1 rounded-lg bg-slate-700/60 hover:bg-slate-600/60 text-slate-300 hover:text-white border border-vmware-border hover:border-slate-500 transition-colors"
                >
                  {qs.label}
                </button>
              ))}
            </div>
          </div>

          {/* NL script generator */}
          <div className="rounded-lg border border-green-900/40 bg-green-950/20 p-3 space-y-2">
            <p className="text-[10px] font-bold text-green-500/80 uppercase tracking-widest flex items-center gap-1.5">
              <Sparkles size={9} /> AI Script Generator
            </p>
            <div className="flex gap-2">
              <input
                value={nlPrompt}
                onChange={e => setNlPrompt(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) generateScript() }}
                placeholder={scriptType === 'PowerShell' ? 'e.g. check disk space and list services…' : 'e.g. show CPU usage and open ports…'}
                className="flex-1 bg-slate-900 border border-green-900/50 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-green-700 transition-colors"
              />
              <button
                onClick={generateScript}
                disabled={generating || !nlPrompt.trim()}
                className="flex items-center gap-1.5 text-xs font-semibold text-white bg-green-700/80 hover:bg-green-600/80 disabled:opacity-40 disabled:cursor-not-allowed px-3 py-2 rounded-lg transition-colors whitespace-nowrap"
              >
                {generating ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
                {generating ? 'Generating…' : 'Generate'}
              </button>
            </div>
            <p className="text-[10px] text-slate-600">
              {selectedVm && vms.find(v => v.name === selectedVm)?.os
                ? `OS hint: ${vms.find(v => v.name === selectedVm)?.os}`
                : 'Select a VM to include OS context in generation'}
            </p>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Script</label>
            <textarea
              value={script}
              onChange={e => setScript(e.target.value)}
              rows={6}
              placeholder={scriptType === 'PowerShell' ? '$env:COMPUTERNAME\nGet-Date' : 'hostname\ndate'}
              className="w-full bg-slate-900 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 font-mono placeholder-slate-600 focus:outline-none focus:border-blue-500 resize-y"
            />
          </div>

          <div className="rounded-lg border border-yellow-800/50 bg-yellow-900/10 px-3 py-2.5">
            <p className="text-xs text-yellow-300 font-medium mb-2 flex items-center gap-1.5">
              <AlertTriangle size={12} /> Guest OS Credentials — not stored, runtime only
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Username</label>
                <input
                  value={guestUser}
                  onChange={e => setGuestUser(e.target.value)}
                  placeholder="Administrator or root"
                  className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-yellow-600"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Password</label>
                <div className="relative">
                  <input
                    type={showPass ? 'text' : 'password'}
                    value={guestPass}
                    onChange={e => setGuestPass(e.target.value)}
                    placeholder="Guest OS password"
                    className="w-full bg-slate-800 border border-vmware-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-yellow-600 pr-9"
                  />
                  <button type="button" onClick={() => setShowPass(s => !s)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 text-xs">
                    {showPass ? 'hide' : 'show'}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <button
            onClick={runScript}
            disabled={running || !selectedVm || !script.trim() || !guestUser}
            className="flex items-center gap-1.5 bg-yellow-700/60 hover:bg-yellow-700/80 border border-yellow-700/40 text-yellow-200 text-sm font-semibold px-4 py-2 rounded-lg transition-colors disabled:opacity-40"
          >
            {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
            Run in Guest
          </button>

          {runOutput && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                {runOutput.exit_code === 0 || (!runOutput.error && runOutput.output !== undefined)
                  ? <CheckCircle size={13} className="text-green-400" />
                  : <XCircle size={13} className="text-red-400" />}
                <span className="text-xs text-slate-400">
                  {runOutput.exit_code !== undefined ? `Exit code: ${runOutput.exit_code}` : runOutput.error ? 'Error' : 'Output'}
                </span>
              </div>
              <pre
                ref={outputRef}
                className="bg-black/60 border border-slate-700 rounded-lg p-3 text-xs text-green-300 font-mono max-h-64 overflow-y-auto whitespace-pre-wrap leading-5"
              >
                {runOutput.error || runOutput.output || '(no output)'}
              </pre>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
