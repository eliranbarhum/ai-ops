import { useState, useRef } from 'react'
import { Upload, Play, CheckCircle, XCircle, Loader2, AlertTriangle, Users, Server, ArrowLeft, Download } from 'lucide-react'
import type { BulkVmRow, BulkAdUserRow } from '../types'

const API_BASE = import.meta.env.VITE_API_URL || ''

type Tab = 'vms' | 'users'
type RowStatus = 'valid' | 'error' | 'creating' | 'done' | 'failed'

interface ParseResult<T> {
  rows: T[]
  errors: { row: number; error: string }[]
  total: number
  valid: number
}

interface ExecResult {
  name?: string
  username?: string
  status: 'done' | 'failed'
  output?: string
  error?: string
}

const VM_SAMPLE = `name,os,cpu,ram_gb,disk_gb,network,folder,datastore,owner_tag,env_tag
web-server-01,windows2022,4,8,80,VM Network,Workloads,vsanDatastore,ops-team,production
app-server-01,ubuntu22,2,4,50,VM Network,Workloads,vsanDatastore,dev-team,staging`

const AD_SAMPLE = `first_name,last_name,username,email,temp_password,ou,groups
John,Smith,jsmith,jsmith@prglab.site,Temp@123!,OU=Users\,DC=prglab\,DC=site,Domain Users
Jane,Doe,jdoe,jdoe@prglab.site,Temp@456!,OU=Users\,DC=prglab\,DC=site,Domain Users`

function StatusBadge({ status }: { status: RowStatus | undefined }) {
  if (!status || status === 'valid') return <span className="text-green-400 text-xs flex items-center gap-1"><CheckCircle size={10} /> Valid</span>
  if (status === 'error') return <span className="text-red-400 text-xs flex items-center gap-1"><XCircle size={10} /> Error</span>
  if (status === 'creating') return <span className="text-blue-400 text-xs flex items-center gap-1"><Loader2 size={10} className="animate-spin" /> Creating</span>
  if (status === 'done') return <span className="text-green-400 text-xs flex items-center gap-1"><CheckCircle size={10} /> Done</span>
  if (status === 'failed') return <span className="text-red-400 text-xs flex items-center gap-1"><XCircle size={10} /> Failed</span>
  return null
}

function DropZone({ onFile, accept }: { onFile: (f: File) => void; accept: string }) {
  const [drag, setDrag] = useState(false)
  const ref = useRef<HTMLInputElement>(null)
  return (
    <div
      onDragOver={e => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={e => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) onFile(f) }}
      onClick={() => ref.current?.click()}
      className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${drag ? 'border-blue-500 bg-blue-900/10' : 'border-vmware-border hover:border-slate-500 hover:bg-slate-800/30'}`}
    >
      <Upload size={24} className="mx-auto mb-2 text-slate-500" />
      <p className="text-sm text-slate-300 font-medium">Drop CSV here or click to upload</p>
      <p className="text-xs text-slate-500 mt-1">Max 20 rows per batch</p>
      <input ref={ref} type="file" accept={accept} className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }} />
    </div>
  )
}

function VmBulkTab() {
  const [rows, setRows] = useState<BulkVmRow[]>([])
  const [parseErrors, setParseErrors] = useState<{ row: number; error: string }[]>([])
  const [execResults, setExecResults] = useState<Map<string, ExecResult>>(new Map())
  const [parsing, setParsing] = useState(false)
  const [executing, setExecuting] = useState(false)

  async function handleFile(file: File) {
    setParsing(true)
    setRows([])
    setParseErrors([])
    setExecResults(new Map())
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch(`${API_BASE}/api/v1/bulk/parse-csv/vms`, { method: 'POST', body: fd })
      const data: ParseResult<BulkVmRow> = await r.json()
      setRows(data.rows)
      setParseErrors(data.errors.filter(e => e.row === 0))
    } catch (e) {
      setParseErrors([{ row: 0, error: String(e) }])
    } finally {
      setParsing(false)
    }
  }

  async function execute() {
    const valid = rows.filter(r => r._status === 'valid')
    if (!valid.length) return
    setExecuting(true)
    setRows(prev => prev.map(r => r._status === 'valid' ? { ...r, _status: 'creating' } : r))
    try {
      const r = await fetch(`${API_BASE}/api/v1/bulk/execute/vms`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: valid }),
      })
      const data: { results: ExecResult[] } = await r.json()
      const map = new Map<string, ExecResult>()
      data.results.forEach(res => map.set(res.name!, res))
      setExecResults(map)
      setRows(prev => prev.map(row => {
        const res = map.get(row.name)
        if (!res) return row
        return { ...row, _status: res.status === 'done' ? 'done' : 'failed', _error: res.error }
      }))
    } catch (e) {
      setRows(prev => prev.map(r => r._status === 'creating' ? { ...r, _status: 'failed', _error: String(e) } : r))
    } finally {
      setExecuting(false)
    }
  }

  const validCount = rows.filter(r => r._status === 'valid').length
  const doneCount = rows.filter(r => r._status === 'done').length

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm text-slate-300">Upload a CSV to provision VMs in bulk via PowerCLI.</p>
          <p className="text-xs text-slate-500 mt-1">Required columns: name, os, cpu, ram_gb, disk_gb, network, folder, datastore</p>
        </div>
        <button
          onClick={() => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([VM_SAMPLE], { type: 'text/csv' })); a.download = 'vm-bulk-template.csv'; a.click() }}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors flex-shrink-0"
        >
          <Download size={12} /> Template
        </button>
      </div>

      <DropZone onFile={handleFile} accept=".csv" />

      {parsing && <div className="flex items-center gap-2 text-sm text-slate-400"><Loader2 size={14} className="animate-spin" /> Parsing CSV…</div>}

      {parseErrors.length > 0 && (
        <div className="rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 space-y-1">
          {parseErrors.map((e, i) => (
            <p key={i} className="text-xs text-red-400 flex items-start gap-1.5"><XCircle size={10} className="mt-0.5" /> {e.error}</p>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <div className="text-xs text-slate-400">
              {rows.length} rows — <span className="text-green-400">{validCount} valid</span>
              {rows.filter(r => r._status === 'error').length > 0 && <span className="text-red-400 ml-2">{rows.filter(r => r._status === 'error').length} errors</span>}
              {doneCount > 0 && <span className="text-green-400 ml-2">{doneCount} created</span>}
            </div>
            <button
              onClick={execute}
              disabled={executing || validCount === 0}
              className="flex items-center gap-1.5 bg-vmware-blue hover:bg-blue-700 disabled:opacity-40 text-white text-sm font-semibold px-4 py-1.5 rounded-lg transition-colors"
            >
              {executing ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
              Provision {validCount} VM{validCount !== 1 ? 's' : ''}
            </button>
          </div>

          <div className="rounded-xl border border-vmware-border overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-vmware-border bg-vmware-card/60">
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Name</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">OS</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">CPU</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">RAM</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Disk</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Network</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Datastore</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-b border-vmware-border/50 hover:bg-slate-800/20">
                    <td className="px-3 py-2 font-mono text-slate-200">{row.name}</td>
                    <td className="px-3 py-2 text-slate-300">{row.os}</td>
                    <td className="px-3 py-2 text-slate-300">{row.cpu}</td>
                    <td className="px-3 py-2 text-slate-300">{row.ram_gb} GB</td>
                    <td className="px-3 py-2 text-slate-300">{row.disk_gb} GB</td>
                    <td className="px-3 py-2 text-slate-300 truncate max-w-32">{row.network}</td>
                    <td className="px-3 py-2 text-slate-300 truncate max-w-32">{row.datastore}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={row._status as RowStatus} />
                      {row._error && <p className="text-xs text-red-400 mt-0.5 truncate max-w-48" title={row._error}>{row._error}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

function AdUserBulkTab() {
  const [rows, setRows] = useState<BulkAdUserRow[]>([])
  const [parseErrors, setParseErrors] = useState<{ row: number; error: string }[]>([])
  const [executing, setExecuting] = useState(false)
  const [parsing, setParsing] = useState(false)

  async function handleFile(file: File) {
    setParsing(true)
    setRows([])
    setParseErrors([])
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch(`${API_BASE}/api/v1/bulk/parse-csv/users`, { method: 'POST', body: fd })
      const data: ParseResult<BulkAdUserRow> = await r.json()
      setRows(data.rows)
      setParseErrors(data.errors.filter(e => e.row === 0))
    } catch (e) {
      setParseErrors([{ row: 0, error: String(e) }])
    } finally {
      setParsing(false)
    }
  }

  async function execute() {
    const valid = rows.filter(r => r._status === 'valid')
    if (!valid.length) return
    setExecuting(true)
    setRows(prev => prev.map(r => r._status === 'valid' ? { ...r, _status: 'creating' } : r))
    try {
      const r = await fetch(`${API_BASE}/api/v1/bulk/execute/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: valid }),
      })
      if (!r.ok) {
        const err = await r.json()
        setParseErrors([{ row: 0, error: err.detail || 'Server error' }])
        setRows(prev => prev.map(row => row._status === 'creating' ? { ...row, _status: 'failed' } : row))
        return
      }
      const data: { results: ExecResult[] } = await r.json()
      const map = new Map<string, ExecResult>()
      data.results.forEach(res => map.set(res.username!, res))
      setRows(prev => prev.map(row => {
        const res = map.get(row.username)
        if (!res) return row
        return { ...row, _status: res.status === 'done' ? 'done' : 'failed', _error: res.error }
      }))
    } catch (e) {
      setRows(prev => prev.map(r => r._status === 'creating' ? { ...r, _status: 'failed', _error: String(e) } : r))
    } finally {
      setExecuting(false)
    }
  }

  const validCount = rows.filter(r => r._status === 'valid').length
  const doneCount = rows.filter(r => r._status === 'done').length

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm text-slate-300">Upload a CSV to create AD users via PowerShell DirectoryServices (no RSAT needed).</p>
          <p className="text-xs text-slate-500 mt-1">Required: first_name, last_name, username, email, temp_password, ou — AD credentials must be set in Settings</p>
        </div>
        <button
          onClick={() => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([AD_SAMPLE], { type: 'text/csv' })); a.download = 'ad-users-template.csv'; a.click() }}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors flex-shrink-0"
        >
          <Download size={12} /> Template
        </button>
      </div>

      <DropZone onFile={handleFile} accept=".csv" />

      {parsing && <div className="flex items-center gap-2 text-sm text-slate-400"><Loader2 size={14} className="animate-spin" /> Parsing CSV…</div>}

      {parseErrors.length > 0 && (
        <div className="rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 space-y-1">
          {parseErrors.map((e, i) => (
            <p key={i} className="text-xs text-red-400 flex items-start gap-1.5"><XCircle size={10} className="mt-0.5" /> {e.error}</p>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <div className="text-xs text-slate-400">
              {rows.length} rows — <span className="text-green-400">{validCount} valid</span>
              {rows.filter(r => r._status === 'error').length > 0 && <span className="text-red-400 ml-2">{rows.filter(r => r._status === 'error').length} errors</span>}
              {doneCount > 0 && <span className="text-green-400 ml-2">{doneCount} created</span>}
            </div>
            <button
              onClick={execute}
              disabled={executing || validCount === 0}
              className="flex items-center gap-1.5 bg-vmware-blue hover:bg-blue-700 disabled:opacity-40 text-white text-sm font-semibold px-4 py-1.5 rounded-lg transition-colors"
            >
              {executing ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
              Create {validCount} User{validCount !== 1 ? 's' : ''}
            </button>
          </div>

          <div className="rounded-xl border border-vmware-border overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-vmware-border bg-vmware-card/60">
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Full Name</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Username</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Email</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">OU</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Groups</th>
                  <th className="text-left px-3 py-2 text-slate-400 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-b border-vmware-border/50 hover:bg-slate-800/20">
                    <td className="px-3 py-2 text-slate-200">{row.first_name} {row.last_name}</td>
                    <td className="px-3 py-2 font-mono text-slate-300">{row.username}</td>
                    <td className="px-3 py-2 text-slate-300">{row.email}</td>
                    <td className="px-3 py-2 text-slate-400 truncate max-w-40 font-mono text-xs" title={row.ou}>{row.ou}</td>
                    <td className="px-3 py-2 text-slate-400">{row.groups || '—'}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={row._status as RowStatus} />
                      {row._error && <p className="text-xs text-red-400 mt-0.5 truncate max-w-48" title={row._error}>{row._error}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

export function BulkPage() {
  const [tab, setTab] = useState<Tab>('vms')

  return (
    <div className="min-h-screen bg-vmware-dark">
      <main className="max-w-5xl mx-auto px-6 py-6">
        <div className="flex gap-1 mb-5 bg-slate-800/60 p-1 rounded-lg w-fit">
          <button
            onClick={() => setTab('vms')}
            className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md transition-colors font-medium ${tab === 'vms' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}
          >
            <Server size={12} /> VM Provisioning
          </button>
          <button
            onClick={() => setTab('users')}
            className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md transition-colors font-medium ${tab === 'users' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'}`}
          >
            <Users size={12} /> AD Users
          </button>
        </div>
        {tab === 'vms' && <VmBulkTab />}
        {tab === 'users' && (
          <>
            <div className="mb-4 rounded-lg border border-yellow-800 bg-yellow-900/15 px-4 py-2.5 flex items-start gap-2">
              <AlertTriangle size={13} className="text-yellow-400 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-yellow-300">Active Directory credentials must be configured in <strong>Settings → Active Directory</strong> before using this feature.</p>
            </div>
            <AdUserBulkTab />
          </>
        )}
      </main>
    </div>
  )
}
