import { useState, useEffect } from 'react'
import {
  Zap, Server, Activity, TrendingUp, Archive, Code2, BotMessageSquare,
  Upload, Laptop, Terminal, Radar, Settings, Pin, PinOff, ShieldCheck, BookUser, ShieldAlert,
  LogOut, User, Search, Layers,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

export type Page =
  | 'fleet' | 'analysis' | 'trends' | 'archive'
  | 'workspace' | 'agent' | 'bulk' | 'guest' | 'kubectl'
  | 'discovery' | 'vulnscan' | 'directory' | 'audit' | 'settings'
  | 'alerts' | 'compliance' | 'platform'

interface NavItem {
  id: Page
  label: string
  icon: React.ElementType
}

const SECTIONS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Observe',
    items: [
      { id: 'fleet',    label: 'Fleet',      icon: Server },
      { id: 'analysis', label: 'Analysis',   icon: Activity },
      { id: 'platform', label: 'Platform Console', icon: Layers },
      { id: 'trends',   label: 'Trends',     icon: TrendingUp },
      { id: 'archive',  label: 'Archive',    icon: Archive },
    ],
  },
  {
    label: 'Operate',
    items: [
      { id: 'workspace', label: 'Workspace',    icon: Code2 },
      { id: 'agent',     label: 'MCP AI Agent', icon: BotMessageSquare },
      { id: 'bulk',      label: 'Bulk Ops',     icon: Upload },
      { id: 'guest',     label: 'Guest',         icon: Laptop },
      { id: 'kubectl',   label: 'Kubectl',       icon: Terminal },
    ],
  },
  {
    label: 'Discover',
    items: [
      { id: 'discovery',  label: 'Discovery',  icon: Radar },
      { id: 'vulnscan',   label: 'Vuln Scan',  icon: ShieldAlert },
      { id: 'directory',  label: 'Directory',  icon: BookUser },
      { id: 'audit',      label: 'Audit Log',  icon: ShieldCheck },
    ],
  },
]

interface ServiceHealth {
  overall: string
  llm_provider: string
  llm_model: string
}

function useHealth() {
  const [health, setHealth] = useState<ServiceHealth | null>(null)
  useEffect(() => {
    let cancelled = false
    async function fetch_() {
      try {
        const r = await fetch(`${API_BASE}/api/v1/health/services`)
        if (!r.ok || cancelled) return
        setHealth(await r.json())
      } catch { /* silent */ }
    }
    fetch_()
    const id = setInterval(fetch_, 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])
  return health
}

function useMe() {
  const [username, setUsername] = useState('')
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/me`)
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setUsername(d.username || d.email || ''))
      .catch(() => {})
  }, [])
  return username
}

const HEALTH_DOT: Record<string, string> = {
  ok:          'bg-green-400',
  degraded:    'bg-yellow-400',
  unreachable: 'bg-red-500',
}

export function Sidebar({ page, navigate }: { page: Page; navigate: (p: Page) => void }) {
  const [hovered, setHovered] = useState(false)
  const [pinned, setPinned] = useState(() => localStorage.getItem('sidebar-pinned') === 'true')
  const health = useHealth()
  const username = useMe()

  const expanded = hovered || pinned

  function togglePin() {
    const next = !pinned
    setPinned(next)
    localStorage.setItem('sidebar-pinned', String(next))
  }

  return (
    <div
      className={`
        flex flex-col h-screen
        bg-[#080c15] border-r border-white/5
        transition-all duration-200 ease-out flex-shrink-0 z-20
        ${expanded ? 'w-[220px]' : 'w-12'}
      `}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-3 h-12 border-b border-white/5 flex-shrink-0 overflow-hidden">
        <div className="w-6 h-6 rounded-lg bg-vmware-blue flex items-center justify-center flex-shrink-0">
          <Zap size={12} className="text-white" />
        </div>
        {expanded && (
          <span className="text-sm font-bold text-white tracking-wide whitespace-nowrap">MCO</span>
        )}
      </div>

      {/* Search / command palette trigger */}
      <button
        onClick={() => window.dispatchEvent(new CustomEvent('mco:open-palette'))}
        aria-label="Open command palette (Ctrl+K)"
        title={!expanded ? 'Search (Ctrl+K)' : undefined}
        className="flex items-center gap-2.5 mx-2 mt-2 px-2 py-1.5 rounded-lg border border-white/5 text-slate-500 hover:text-slate-300 hover:border-white/10 hover:bg-white/5 transition-colors flex-shrink-0 overflow-hidden"
      >
        <Search size={13} className="flex-shrink-0 ml-0.5" />
        {expanded && (
          <>
            <span className="text-[11px] whitespace-nowrap">Search…</span>
            <kbd className="ml-auto text-[9px] text-slate-600 border border-white/10 rounded px-1 py-0.5 font-mono whitespace-nowrap">⌘K</kbd>
          </>
        )}
      </button>

      {/* Nav */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden py-2 scrollbar-none">
        {SECTIONS.map((section, si) => (
          <div key={section.label} className={si > 0 ? 'mt-2 pt-2 border-t border-white/5' : ''}>
            {expanded && (
              <p className="text-[9px] font-bold text-slate-600 uppercase tracking-widest px-3.5 pb-1">
                {section.label}
              </p>
            )}
            {section.items.map(item => {
              const Icon = item.icon
              const active = page === item.id
              return (
                <button
                  key={item.id}
                  onClick={() => navigate(item.id)}
                  title={!expanded ? item.label : undefined}
                  aria-label={item.label}
                  aria-current={active ? 'page' : undefined}
                  className={`
                    relative w-full flex items-center gap-2.5 px-3 py-2 text-left
                    transition-colors duration-100
                    ${active
                      ? 'text-blue-300 bg-blue-500/10'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'
                    }
                  `}
                >
                  {active && (
                    <span className="absolute left-0 inset-y-1 w-0.5 bg-blue-400 rounded-r-full" />
                  )}
                  <Icon size={15} className="flex-shrink-0" />
                  {expanded && (
                    <span className="text-[13px] font-medium whitespace-nowrap leading-none">
                      {item.label}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="border-t border-white/5 flex-shrink-0 pb-1">
        {/* Settings */}
        <button
          onClick={() => navigate('settings')}
          title={!expanded ? 'Settings' : undefined}
          className={`
            relative w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors
            ${page === 'settings'
              ? 'text-blue-300 bg-blue-500/10'
              : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'
            }
          `}
        >
          {page === 'settings' && (
            <span className="absolute left-0 inset-y-1 w-0.5 bg-blue-400 rounded-r-full" />
          )}
          <Settings size={15} className="flex-shrink-0" />
          {expanded && <span className="text-[13px] font-medium">Settings</span>}
        </button>

        {/* Health + LLM status */}
        <div className={`flex items-center gap-2 px-3 py-1.5 overflow-hidden ${expanded ? '' : 'justify-center'}`}>
          {health && (
            <>
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${HEALTH_DOT[health.overall] ?? 'bg-slate-500'}`} />
              {expanded && (
                <span className="text-[10px] text-slate-600 whitespace-nowrap truncate">
                  {health.overall === 'ok' ? 'All services OK' : health.overall}
                  {health.llm_model ? ` · ${health.llm_model}` : ''}
                </span>
              )}
            </>
          )}
        </div>

        {/* User + Logout */}
        {username && (
          <div className={`flex items-center gap-2 px-3 py-1.5 overflow-hidden ${expanded ? 'justify-between' : 'justify-center'}`}>
            <div className="flex items-center gap-1.5 min-w-0">
              <User size={11} className="text-slate-600 flex-shrink-0" />
              {expanded && (
                <span className="text-[10px] text-slate-600 truncate" title={username}>
                  {username}
                </span>
              )}
            </div>
            <a
              href="/oauth2/sign_out?rd=/"
              title="Sign out"
              className="flex items-center gap-1 text-slate-700 hover:text-red-400 transition-colors flex-shrink-0"
            >
              <LogOut size={11} />
              {expanded && <span className="text-[10px] whitespace-nowrap">Sign out</span>}
            </a>
          </div>
        )}

        {/* Pin toggle */}
        <button
          onClick={togglePin}
          title={pinned ? 'Unpin sidebar' : 'Pin sidebar'}
          className="w-full flex items-center gap-2.5 px-3 py-1.5 text-slate-700 hover:text-slate-500 transition-colors"
        >
          {pinned
            ? <PinOff size={12} className="flex-shrink-0" />
            : <Pin size={12} className="flex-shrink-0" />
          }
          {expanded && <span className="text-[10px] whitespace-nowrap">{pinned ? 'Unpin' : 'Pin sidebar'}</span>}
        </button>
      </div>
    </div>
  )
}
