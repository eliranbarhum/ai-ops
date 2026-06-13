import { useState, useEffect, useCallback } from 'react'
import {
  Users, Monitor, ShieldAlert, RefreshCw, Search, ChevronDown, ChevronUp,
  Lock, UserX, Clock, Server, Shield, Wifi, KeyRound, AlertTriangle,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Overview {
  domain: string
  total_users: number
  enabled_users: number
  disabled_users: number
  locked_users: number
  stale_users: number
  pwd_never_expires_users: number
  service_accounts: number
  total_computers: number
  stale_computers: number
  domain_admins_count: number
  domain_controllers: string[]
  dns_servers: string[]
  kerberoastable_count?: number
  kerberoastable_accounts?: string[]
}

interface ADUser {
  username: string
  display_name: string
  email: string
  department: string
  title: string
  enabled: boolean
  locked: boolean
  locked_since: string | null
  stale: boolean
  last_logon_days: number | null
  password_never_expires: boolean
  password_last_set_days: number | null
}

interface ADComputer {
  name: string
  dns_hostname: string
  os: string
  os_version: string
  enabled: boolean
  is_dc: boolean
  is_dns_server: boolean
  stale: boolean
  last_logon_days: number | null
  ou: string
}

interface PrivGroup {
  group: string
  member_count: number
  members: { username: string; display_name: string; email: string; enabled: boolean }[]
}

type Tab = 'overview' | 'users' | 'computers' | 'privileged'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function LastLogon({ days }: { days: number | null }) {
  if (days === null) return <span className="text-slate-600">Never</span>
  if (days === 0) return <span className="text-green-400">Today</span>
  if (days <= 7) return <span className="text-green-400">{days}d ago</span>
  if (days <= 30) return <span className="text-slate-300">{days}d ago</span>
  if (days <= 90) return <span className="text-amber-400">{days}d ago</span>
  return <span className="text-red-400">{days}d ago</span>
}

function StatusBadges({ user }: { user: ADUser }) {
  return (
    <div className="flex gap-1 flex-wrap">
      {!user.enabled && (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-700 text-slate-400">Disabled</span>
      )}
      {user.locked && (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/20 text-red-400">Locked</span>
      )}
      {user.stale && user.enabled && (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/20 text-amber-400">Stale</span>
      )}
      {user.password_never_expires && (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-orange-500/20 text-orange-400">PwdNeverExpires</span>
      )}
    </div>
  )
}

function KpiCard({
  label, value, sub, icon: Icon, color = 'blue', alert = false,
}: {
  label: string; value: number | string; sub?: string
  icon: React.ElementType; color?: string; alert?: boolean
}) {
  const colors: Record<string, string> = {
    blue: 'text-blue-400',
    green: 'text-green-400',
    red: 'text-red-400',
    amber: 'text-amber-400',
    slate: 'text-slate-400',
    purple: 'text-purple-400',
  }
  return (
    <div className={`rounded-lg border p-4 bg-[#0d1117] flex flex-col gap-2
      ${alert ? 'border-red-500/40 bg-red-500/5' : 'border-white/5'}`}>
      <div className="flex items-center gap-2">
        <Icon size={14} className={colors[color]} />
        <span className="text-[11px] text-slate-500 uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className={`text-2xl font-bold ${colors[color]}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-600">{sub}</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function OverviewTab({ data }: { data: Overview }) {
  return (
    <div className="space-y-6">
      {(data.kerberoastable_count ?? 0) > 0 && (
        <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/30">
          <AlertTriangle size={16} className="text-red-400 flex-shrink-0 mt-0.5" />
          <div className="min-w-0">
            <span className="text-sm text-red-300">
              <strong>{data.kerberoastable_count} Kerberoastable account{data.kerberoastable_count! > 1 ? 's' : ''}</strong> — user accounts with a ServicePrincipalName are vulnerable to offline password cracking.
            </span>
            {(data.kerberoastable_accounts?.length ?? 0) > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {data.kerberoastable_accounts!.slice(0, 8).map(a => (
                  <span key={a} className="text-xs font-mono text-red-400 bg-red-900/30 px-1.5 py-0.5 rounded">{a}</span>
                ))}
                {data.kerberoastable_accounts!.length > 8 && (
                  <span className="text-xs text-red-500">+{data.kerberoastable_accounts!.length - 8} more</span>
                )}
              </div>
            )}
          </div>
        </div>
      )}
      {data.locked_users > 0 && (
        <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/30">
          <AlertTriangle size={16} className="text-red-400 flex-shrink-0" />
          <span className="text-sm text-red-300">
            <strong>{data.locked_users}</strong> account{data.locked_users > 1 ? 's are' : ' is'} currently locked out —
            may indicate a password spray or brute-force attack.
          </span>
        </div>
      )}

      <div>
        <h3 className="text-[11px] text-slate-500 uppercase tracking-wider font-medium mb-3">Users</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <KpiCard label="Total Users" value={data.total_users} icon={Users} color="blue"
            sub={`${data.enabled_users} enabled`} />
          <KpiCard label="Locked Out" value={data.locked_users} icon={Lock}
            color={data.locked_users > 0 ? 'red' : 'green'} alert={data.locked_users > 0}
            sub="Active lockouts" />
          <KpiCard label="Stale Accounts" value={data.stale_users} icon={Clock}
            color={data.stale_users > 5 ? 'amber' : 'slate'}
            sub="No logon 90+ days" />
          <KpiCard label="Disabled" value={data.disabled_users} icon={UserX} color="slate"
            sub="Deprovisioned" />
          <KpiCard label="Pwd Never Expires" value={data.pwd_never_expires_users} icon={KeyRound}
            color={data.pwd_never_expires_users > 0 ? 'amber' : 'slate'}
            sub="Security risk" />
          <KpiCard label="Service Accounts" value={data.service_accounts} icon={Shield} color="purple"
            sub="Users with SPNs" />
          <KpiCard label="Domain Admins" value={data.domain_admins_count} icon={ShieldAlert}
            color={data.domain_admins_count > 5 ? 'amber' : 'green'}
            sub="DA group members" />
        </div>
      </div>

      <div>
        <h3 className="text-[11px] text-slate-500 uppercase tracking-wider font-medium mb-3">Computers</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <KpiCard label="Total Computers" value={data.total_computers} icon={Monitor} color="blue" />
          <KpiCard label="Stale Computers" value={data.stale_computers} icon={Clock}
            color={data.stale_computers > 0 ? 'amber' : 'slate'}
            sub="No logon 90+ days" />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="rounded-lg border border-white/5 bg-[#0d1117] p-4">
          <div className="flex items-center gap-2 mb-3">
            <Server size={14} className="text-blue-400" />
            <span className="text-[11px] text-slate-500 uppercase tracking-wider font-medium">
              Domain Controllers ({data.domain_controllers.length})
            </span>
          </div>
          {data.domain_controllers.length === 0
            ? <p className="text-xs text-slate-600">None detected</p>
            : data.domain_controllers.map(dc => (
              <div key={dc} className="text-sm text-slate-300 font-mono py-0.5">{dc}</div>
            ))}
        </div>
        <div className="rounded-lg border border-white/5 bg-[#0d1117] p-4">
          <div className="flex items-center gap-2 mb-3">
            <Wifi size={14} className="text-green-400" />
            <span className="text-[11px] text-slate-500 uppercase tracking-wider font-medium">
              DNS Servers ({data.dns_servers.length})
            </span>
          </div>
          {data.dns_servers.length === 0
            ? <p className="text-xs text-slate-600">None detected</p>
            : data.dns_servers.map(s => (
              <div key={s} className="text-sm text-slate-300 font-mono py-0.5">{s}</div>
            ))}
        </div>
      </div>
    </div>
  )
}

type UserFilter = 'all' | 'locked' | 'disabled' | 'stale' | 'pwd_never'

function UsersTab({ data }: { data: ADUser[] }) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<UserFilter>('all')
  const [sortCol, setSortCol] = useState<'username' | 'last_logon_days' | 'department'>('username')
  const [sortAsc, setSortAsc] = useState(true)

  function toggleSort(col: typeof sortCol) {
    if (sortCol === col) setSortAsc(a => !a)
    else { setSortCol(col); setSortAsc(true) }
  }

  const filtered = data
    .filter(u => {
      const q = search.toLowerCase()
      if (q && !u.username.toLowerCase().includes(q) &&
          !u.display_name.toLowerCase().includes(q) &&
          !u.department.toLowerCase().includes(q)) return false
      if (filter === 'locked')   return u.locked
      if (filter === 'disabled') return !u.enabled
      if (filter === 'stale')    return u.stale && u.enabled
      if (filter === 'pwd_never') return u.password_never_expires
      return true
    })
    .sort((a, b) => {
      let va: string | number = '', vb: string | number = ''
      if (sortCol === 'username')      { va = a.username;        vb = b.username }
      if (sortCol === 'last_logon_days') { va = a.last_logon_days ?? 9999; vb = b.last_logon_days ?? 9999 }
      if (sortCol === 'department')    { va = a.department;      vb = b.department }
      if (typeof va === 'string') return sortAsc ? va.localeCompare(vb as string) : (vb as string).localeCompare(va)
      return sortAsc ? va - (vb as number) : (vb as number) - va
    })

  const SortIcon = ({ col }: { col: typeof sortCol }) => (
    sortCol === col
      ? (sortAsc ? <ChevronUp size={12} className="inline ml-1 opacity-60" /> : <ChevronDown size={12} className="inline ml-1 opacity-60" />)
      : null
  )

  const counts = {
    locked: data.filter(u => u.locked).length,
    disabled: data.filter(u => !u.enabled).length,
    stale: data.filter(u => u.stale && u.enabled).length,
    pwd_never: data.filter(u => u.password_never_expires).length,
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <div className="relative flex-1 min-w-48">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search username, name, department…"
            className="w-full bg-[#0d1117] border border-white/10 rounded-lg pl-8 pr-3 py-2 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-blue-500/50"
          />
        </div>
        {(['all', 'locked', 'disabled', 'stale', 'pwd_never'] as UserFilter[]).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-2 rounded-lg text-xs font-medium transition-colors
              ${filter === f ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                            : 'bg-[#0d1117] text-slate-500 border border-white/5 hover:text-slate-300'}`}>
            {f === 'all' ? `All (${data.length})`
             : f === 'locked' ? `Locked (${counts.locked})`
             : f === 'disabled' ? `Disabled (${counts.disabled})`
             : f === 'stale' ? `Stale (${counts.stale})`
             : `Pwd Never Expires (${counts.pwd_never})`}
          </button>
        ))}
      </div>

      <div className="rounded-lg border border-white/5 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/5 bg-[#0a0e17]">
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider cursor-pointer hover:text-slate-300"
                onClick={() => toggleSort('username')}>
                Username <SortIcon col="username" />
              </th>
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider cursor-pointer hover:text-slate-300"
                onClick={() => toggleSort('department')}>
                Department <SortIcon col="department" />
              </th>
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider">
                Status
              </th>
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider cursor-pointer hover:text-slate-300"
                onClick={() => toggleSort('last_logon_days')}>
                Last Logon <SortIcon col="last_logon_days" />
              </th>
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider">
                Pwd Age
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-600 text-sm">No users match</td></tr>
            )}
            {filtered.map((u, i) => (
              <tr key={u.username} className={`border-b border-white/3 hover:bg-white/3 transition-colors
                ${u.locked ? 'bg-red-500/3' : i % 2 === 0 ? '' : 'bg-white/1'}`}>
                <td className="px-4 py-2.5">
                  <div className="font-mono text-slate-200 text-sm">{u.username}</div>
                  {u.display_name && <div className="text-xs text-slate-500">{u.display_name}</div>}
                </td>
                <td className="px-4 py-2.5 text-slate-400 text-sm">{u.department || '—'}</td>
                <td className="px-4 py-2.5"><StatusBadges user={u} /></td>
                <td className="px-4 py-2.5 text-sm"><LastLogon days={u.last_logon_days} /></td>
                <td className="px-4 py-2.5 text-sm">
                  {u.password_last_set_days === null
                    ? <span className="text-slate-600">—</span>
                    : <span className={u.password_last_set_days > 180 ? 'text-amber-400' : 'text-slate-400'}>
                        {u.password_last_set_days}d ago
                      </span>
                  }
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-600">{filtered.length} of {data.length} users</p>
    </div>
  )
}

function ComputersTab({ data }: { data: ADComputer[] }) {
  const [search, setSearch] = useState('')
  const [staleOnly, setStaleOnly] = useState(false)

  const filtered = data
    .filter(c => {
      if (staleOnly && !c.stale) return false
      const q = search.toLowerCase()
      if (!q) return true
      return c.name.toLowerCase().includes(q) ||
             c.dns_hostname.toLowerCase().includes(q) ||
             c.os.toLowerCase().includes(q) ||
             c.ou.toLowerCase().includes(q)
    })
    .sort((a, b) => a.name.localeCompare(b.name))

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <div className="relative flex-1 min-w-48">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search name, OS, OU…"
            className="w-full bg-[#0d1117] border border-white/10 rounded-lg pl-8 pr-3 py-2 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-blue-500/50"
          />
        </div>
        <button onClick={() => setStaleOnly(s => !s)}
          className={`px-3 py-2 rounded-lg text-xs font-medium border transition-colors
            ${staleOnly ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                        : 'bg-[#0d1117] text-slate-500 border-white/5 hover:text-slate-300'}`}>
          Stale only ({data.filter(c => c.stale).length})
        </button>
      </div>

      <div className="rounded-lg border border-white/5 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/5 bg-[#0a0e17]">
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider">Name</th>
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider">Operating System</th>
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider">OU</th>
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider">Roles</th>
              <th className="text-left px-4 py-2.5 text-[11px] text-slate-500 font-medium uppercase tracking-wider">Last Logon</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-600">No computers match</td></tr>
            )}
            {filtered.map(c => (
              <tr key={c.name} className="border-b border-white/3 hover:bg-white/3 transition-colors">
                <td className="px-4 py-2.5">
                  <div className="font-mono text-slate-200">{c.name}</div>
                  {c.dns_hostname && c.dns_hostname !== c.name && (
                    <div className="text-xs text-slate-600">{c.dns_hostname}</div>
                  )}
                  {!c.enabled && (
                    <span className="text-[10px] text-slate-500 bg-slate-800 px-1.5 rounded">Disabled</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-slate-400">
                  {c.os || '—'}
                  {c.os_version && <span className="text-slate-600 ml-1 text-xs">{c.os_version}</span>}
                </td>
                <td className="px-4 py-2.5 text-xs text-slate-500">{c.ou}</td>
                <td className="px-4 py-2.5">
                  <div className="flex gap-1 flex-wrap">
                    {c.is_dc && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-500/20 text-blue-400">DC</span>
                    )}
                    {c.is_dns_server && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/20 text-green-400">DNS</span>
                    )}
                    {c.stale && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/20 text-amber-400">Stale</span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-2.5"><LastLogon days={c.last_logon_days} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-600">{filtered.length} of {data.length} computers</p>
    </div>
  )
}

function PrivilegedTab({ data }: { data: PrivGroup[] }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const toggle = (g: string) => setExpanded(e => ({ ...e, [g]: !e[g] }))

  const risk: Record<string, string> = {
    'Domain Admins':     'bg-red-500/20 text-red-400 border-red-500/30',
    'Enterprise Admins': 'bg-red-500/20 text-red-400 border-red-500/30',
    'Schema Admins':     'bg-orange-500/20 text-orange-400 border-orange-500/30',
    'Administrators':    'bg-amber-500/20 text-amber-400 border-amber-500/30',
    'DNS Admins':        'bg-amber-500/20 text-amber-400 border-amber-500/30',
    'Account Operators': 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-600">
        Recursive membership lookup — shows all users who are effective members,
        including nested group memberships.
      </p>
      {data.map(grp => (
        <div key={grp.group} className={`rounded-lg border ${risk[grp.group] ?? 'border-white/5 bg-[#0d1117]'} overflow-hidden`}>
          <button
            className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/3 transition-colors"
            onClick={() => toggle(grp.group)}
          >
            <div className="flex items-center gap-3">
              <Shield size={14} className="flex-shrink-0" />
              <span className="font-medium text-sm">{grp.group}</span>
              <span className={`px-2 py-0.5 rounded-full text-xs font-bold
                ${grp.member_count > 10 ? 'bg-red-500/30 text-red-300'
                  : grp.member_count > 5 ? 'bg-amber-500/30 text-amber-300'
                  : 'bg-slate-700 text-slate-300'}`}>
                {grp.member_count} member{grp.member_count !== 1 ? 's' : ''}
              </span>
            </div>
            {expanded[grp.group]
              ? <ChevronUp size={14} className="text-slate-500" />
              : <ChevronDown size={14} className="text-slate-500" />
            }
          </button>

          {expanded[grp.group] && (
            <div className="border-t border-white/5">
              {grp.members.length === 0
                ? <p className="px-4 py-3 text-sm text-slate-600">No members found</p>
                : (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-black/20">
                        <th className="text-left px-4 py-2 text-[11px] text-slate-500 font-medium uppercase tracking-wider">Username</th>
                        <th className="text-left px-4 py-2 text-[11px] text-slate-500 font-medium uppercase tracking-wider">Display Name</th>
                        <th className="text-left px-4 py-2 text-[11px] text-slate-500 font-medium uppercase tracking-wider">Email</th>
                        <th className="text-left px-4 py-2 text-[11px] text-slate-500 font-medium uppercase tracking-wider">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {grp.members.map(m => (
                        <tr key={m.username} className="border-t border-white/3 hover:bg-white/3">
                          <td className="px-4 py-2 font-mono text-slate-200">{m.username}</td>
                          <td className="px-4 py-2 text-slate-400">{m.display_name || '—'}</td>
                          <td className="px-4 py-2 text-slate-500 text-xs">{m.email || '—'}</td>
                          <td className="px-4 py-2">
                            {m.enabled
                              ? <span className="text-xs text-green-400">Enabled</span>
                              : <span className="text-xs text-slate-500">Disabled</span>
                            }
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              }
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export function DirectoryPage() {
  const [tab, setTab] = useState<Tab>('overview')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [users, setUsers] = useState<ADUser[] | null>(null)
  const [computers, setComputers] = useState<ADComputer[] | null>(null)
  const [privileged, setPrivileged] = useState<PrivGroup[] | null>(null)
  const [loading, setLoading] = useState<Record<string, boolean>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  async function fetchTab(t: Tab, force = false) {
    const key = t
    if (!force && loading[key]) return

    setLoading(l => ({ ...l, [key]: true }))
    setErrors(e => ({ ...e, [key]: '' }))
    try {
      const endpoint = t === 'overview' ? 'overview'
                     : t === 'users' ? 'users'
                     : t === 'computers' ? 'computers'
                     : 'privileged'
      const r = await fetch(`${API_BASE}/api/v1/ad/${endpoint}`)
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }))
        throw new Error(err.detail || r.statusText)
      }
      const data = await r.json()
      if (t === 'overview') setOverview(data)
      else if (t === 'users') setUsers(data.users)
      else if (t === 'computers') setComputers(data.computers)
      else setPrivileged(data.groups)
    } catch (e: unknown) {
      setErrors(err => ({ ...err, [key]: (e as Error).message }))
    } finally {
      setLoading(l => ({ ...l, [key]: false }))
    }
  }

  useEffect(() => { fetchTab('overview') }, [])
  useEffect(() => {
    if (tab === 'users' && !users) fetchTab('users')
    if (tab === 'computers' && !computers) fetchTab('computers')
    if (tab === 'privileged' && !privileged) fetchTab('privileged')
  }, [tab])

  async function refresh() {
    await fetch(`${API_BASE}/api/v1/ad/refresh`, { method: 'POST' })
    setOverview(null); setUsers(null); setComputers(null); setPrivileged(null)
    fetchTab(tab, true)
    if (tab !== 'overview') fetchTab('overview', true)
  }

  const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
    { id: 'overview',   label: 'Overview',         icon: Shield },
    { id: 'users',      label: 'Users',             icon: Users },
    { id: 'computers',  label: 'Computers',         icon: Monitor },
    { id: 'privileged', label: 'Privileged Groups', icon: ShieldAlert },
  ]

  function renderContent() {
    const err = errors[tab]
    if (err) return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <AlertTriangle size={32} className="text-red-400" />
        <p className="text-red-400 font-medium text-sm">{err}</p>
        <p className="text-slate-600 text-xs">Check AD credentials in Settings → Active Directory</p>
        <button onClick={() => fetchTab(tab, true)}
          className="mt-2 px-4 py-2 rounded-lg bg-blue-500/20 text-blue-300 text-sm hover:bg-blue-500/30">
          Retry
        </button>
      </div>
    )
    if (loading[tab]) return (
      <div className="flex items-center justify-center py-20">
        <div className="flex items-center gap-3 text-slate-500">
          <RefreshCw size={16} className="animate-spin" />
          <span className="text-sm">Querying Active Directory…</span>
        </div>
      </div>
    )
    if (tab === 'overview' && overview) return <OverviewTab data={overview} />
    if (tab === 'users' && users) return <UsersTab data={users} />
    if (tab === 'computers' && computers) return <ComputersTab data={computers} />
    if (tab === 'privileged' && privileged) return <PrivilegedTab data={privileged} />
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex items-center gap-3 text-slate-500">
          <RefreshCw size={16} className="animate-spin" />
          <span className="text-sm">Loading…</span>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] text-white overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-white/5 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-white">Active Directory Intelligence</h1>
            {overview && (
              <p className="text-xs text-slate-500 mt-0.5">
                {overview.domain} · {overview.total_users} users · {overview.total_computers} computers
              </p>
            )}
          </div>
          <button
            onClick={refresh}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10
                       text-slate-400 hover:text-slate-200 hover:bg-white/8 transition-colors text-sm"
          >
            <RefreshCw size={13} className={loading[tab] ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mt-4">
          {TABS.map(t => {
            const Icon = t.icon
            const active = tab === t.id
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors
                  ${active
                    ? 'bg-blue-500/15 text-blue-300 border border-blue-500/25'
                    : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
                  }`}
              >
                <Icon size={13} />
                {t.label}
                {t.id === 'overview' && overview?.locked_users ? (
                  <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-red-500/30 text-red-300 ml-1">
                    {overview.locked_users} locked
                  </span>
                ) : null}
              </button>
            )
          })}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        {renderContent()}
      </div>
    </div>
  )
}
