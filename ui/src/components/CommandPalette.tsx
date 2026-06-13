import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  Search, Server, Activity, Archive, Code2, BotMessageSquare, Upload, Laptop,
  Terminal, Radar, TrendingUp, Settings, X, Zap, ShieldAlert, BookUser,
  ShieldCheck, Bell, ClipboardCheck, ArrowUpCircle, Container, LogOut, Clock, Layers,
} from 'lucide-react'

interface Command {
  id: string
  label: string
  description?: string
  section: string
  icon: React.ElementType
  action: () => void
  keywords?: string[]
}

const RECENTS_KEY = 'mco-palette-recents'
const MAX_RECENTS = 4

function loadRecents(): string[] {
  try { return JSON.parse(localStorage.getItem(RECENTS_KEY) || '[]') } catch { return [] }
}

function saveRecent(id: string) {
  const next = [id, ...loadRecents().filter(r => r !== id)].slice(0, MAX_RECENTS)
  localStorage.setItem(RECENTS_KEY, JSON.stringify(next))
}

/** Subsequence fuzzy match. Returns matched char indices for highlighting, or null. */
function fuzzyMatch(query: string, target: string): number[] | null {
  if (!query) return []
  const q = query.toLowerCase()
  const t = target.toLowerCase()
  const sub = t.indexOf(q)
  if (sub >= 0) return Array.from({ length: q.length }, (_, i) => sub + i)
  const idx: number[] = []
  let qi = 0
  for (let i = 0; i < t.length && qi < q.length; i++) {
    if (t[i] === q[qi]) { idx.push(i); qi++ }
  }
  return qi === q.length ? idx : null
}

function Highlight({ text, indices }: { text: string; indices: number[] }) {
  if (!indices.length) return <>{text}</>
  const set = new Set(indices)
  return (
    <>
      {text.split('').map((ch, i) =>
        set.has(i) ? <span key={i} className="text-blue-300 font-bold">{ch}</span> : ch
      )}
    </>
  )
}

export function CommandPalette({
  open,
  onClose,
  navigate,
}: {
  open: boolean
  onClose: () => void
  navigate: (page: string) => void
}) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)

  const commands: Command[] = useMemo(() => [
    // Observe
    { id: 'fleet',      label: 'Fleet',         description: 'ESXi hosts, VMs, datastores, networks', section: 'Observe', icon: Server,         action: () => navigate('fleet'),      keywords: ['hosts', 'vms', 'esxi', 'datastores', 'home'] },
    { id: 'analysis',   label: 'Analysis',      description: 'VCF readiness score & AI explanation',  section: 'Observe', icon: Activity,       action: () => navigate('analysis'),   keywords: ['score', 'readiness', 'vcf', 'scan'] },
    { id: 'upgrade',    label: 'Upgrade Plan',  description: 'Patch bundles & upgrade sequencing',    section: 'Observe', icon: ArrowUpCircle,  action: () => navigate('upgrade'),    keywords: ['patch', 'bundle', 'lcm', 'rollback'] },
    { id: 'k8s',        label: 'K8s Clusters',  description: 'Supervisor & workload cluster view',    section: 'Observe', icon: Container,      action: () => navigate('k8s'),        keywords: ['kubernetes', 'vks', 'tanzu', 'containers'] },
    { id: 'platform',   label: 'Platform Console', description: 'Manage VKS clusters without kubectl', section: 'Observe', icon: Layers,         action: () => navigate('platform'),   keywords: ['vks', 'tanzu', 'kubernetes', 'pods', 'deployments', 'console'] },
    { id: 'trends',     label: 'Trends',        description: 'Score history & sparklines',           section: 'Observe', icon: TrendingUp,     action: () => navigate('trends'),     keywords: ['history', 'chart', 'sparkline'] },
    { id: 'archive',    label: 'Archive',       description: 'Saved scan results',                    section: 'Observe', icon: Archive,        action: () => navigate('archive'),    keywords: ['saved', 'scans', 'history'] },
    { id: 'alerts',     label: 'Alerts',        description: 'Alert rules & notifications',           section: 'Observe', icon: Bell,           action: () => navigate('alerts'),     keywords: ['notify', 'telegram', 'rules'] },
    { id: 'compliance', label: 'Compliance',    description: 'Compliance reports',                    section: 'Observe', icon: ClipboardCheck, action: () => navigate('compliance'), keywords: ['report', 'policy'] },
    // Operate
    { id: 'workspace',  label: 'Workspace',     description: 'PowerCLI & vSphere API scripts',        section: 'Operate', icon: Code2,           action: () => navigate('workspace'), keywords: ['powercli', 'scripts', 'terminal', 'api'] },
    { id: 'agent',      label: 'MCP AI Agent',  description: 'Chat with the AI operations agent',     section: 'Operate', icon: BotMessageSquare, action: () => navigate('agent'),    keywords: ['ai', 'chat', 'mcp', 'llm'] },
    { id: 'bulk',       label: 'Bulk Ops',      description: 'Batch VM & AD user creation',           section: 'Operate', icon: Upload,          action: () => navigate('bulk'),      keywords: ['batch', 'csv', 'create', 'ad'] },
    { id: 'guest',      label: 'Guest',         description: 'VM guest OS operations',                section: 'Operate', icon: Laptop,          action: () => navigate('guest'),     keywords: ['vm', 'guest', 'os', 'tools'] },
    { id: 'kubectl',    label: 'Kubectl',       description: 'Natural-language kubectl console',      section: 'Operate', icon: Terminal,        action: () => navigate('kubectl'),   keywords: ['k8s', 'kubernetes', 'pods', 'broadcast'] },
    // Discover
    { id: 'discovery',  label: 'Discovery',     description: 'Network scan & device inventory',       section: 'Discover', icon: Radar,        action: () => navigate('discovery'),  keywords: ['nmap', 'network', 'scan', 'cidr'] },
    { id: 'vulnscan',   label: 'Vuln Scan',     description: 'Vulnerability scans & schedules',       section: 'Discover', icon: ShieldAlert,  action: () => navigate('vulnscan'),   keywords: ['cve', 'security', 'vulnerability'] },
    { id: 'directory',  label: 'Directory',     description: 'Active Directory users & groups',       section: 'Discover', icon: BookUser,     action: () => navigate('directory'),  keywords: ['ad', 'ldap', 'users', 'groups'] },
    { id: 'audit',      label: 'Audit Log',     description: 'Who did what, when',                    section: 'Discover', icon: ShieldCheck,  action: () => navigate('audit'),      keywords: ['log', 'activity', 'heatmap'] },
    // System
    { id: 'settings',   label: 'Settings',      description: 'Connections, LLM, maintenance windows', section: 'System', icon: Settings,       action: () => navigate('settings'),   keywords: ['config', 'vcenter', 'api', 'key', 'llm'] },
    { id: 'signout',    label: 'Sign out',      description: 'End this session',                      section: 'System', icon: LogOut,         action: () => { window.location.href = '/oauth2/sign_out?rd=/' }, keywords: ['logout', 'exit'] },
  ], [navigate])

  const recents = useMemo(() => (open ? loadRecents() : []), [open])

  // Filter + group. Empty query: recents first, then all by section.
  const { flat, groups } = useMemo(() => {
    type Scored = { cmd: Command; indices: number[] }
    let matched: Scored[]
    if (!query) {
      matched = commands.map(cmd => ({ cmd, indices: [] }))
    } else {
      matched = []
      for (const cmd of commands) {
        const m = fuzzyMatch(query, cmd.label)
          ?? fuzzyMatch(query, cmd.description ?? '')
          ?? (cmd.keywords ?? []).reduce<number[] | null>((acc, k) => acc ?? (fuzzyMatch(query, k) ? [] : null), null)
        if (m !== null) matched.push({ cmd, indices: fuzzyMatch(query, cmd.label) ?? [] })
      }
    }

    const sections: { label: string; items: Scored[] }[] = []
    if (!query && recents.length) {
      const recentItems = recents
        .map(id => matched.find(s => s.cmd.id === id))
        .filter((s): s is Scored => !!s)
      if (recentItems.length) sections.push({ label: 'Recent', items: recentItems })
    }
    for (const sec of ['Observe', 'Operate', 'Discover', 'System']) {
      const items = matched.filter(s => s.cmd.section === sec)
      if (items.length) sections.push({ label: sec, items })
    }
    return { flat: sections.flatMap(s => s.items), groups: sections }
  }, [commands, query, recents])

  useEffect(() => { setSelected(0) }, [query])

  useEffect(() => {
    if (open) {
      previousFocus.current = document.activeElement as HTMLElement
      setQuery('')
      setSelected(0)
      document.body.style.overflow = 'hidden'
      setTimeout(() => inputRef.current?.focus(), 50)
      return () => {
        document.body.style.overflow = ''
        previousFocus.current?.focus?.()
      }
    }
  }, [open])

  // Keep the selected row in view while arrowing
  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-index="${selected}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  const run = useCallback((cmd: Command) => {
    saveRecent(cmd.id)
    cmd.action()
    onClose()
  }, [onClose])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelected(s => Math.min(s + 1, flat.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelected(s => Math.max(s - 1, 0))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        if (flat[selected]) run(flat[selected].cmd)
      } else if (e.key === 'Escape') {
        onClose()
      } else if (e.key === 'Tab') {
        e.preventDefault() // focus stays in the palette
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, flat, selected, run, onClose])

  if (!open) return null

  let rowIndex = -1

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/50 backdrop-blur-[2px] animate-fade-in"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="w-full max-w-xl bg-[#0f1629] border border-vmware-border rounded-2xl shadow-2xl overflow-hidden animate-scale-in"
        onClick={e => e.stopPropagation()}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-vmware-border">
          <div className="w-6 h-6 rounded-lg bg-vmware-blue flex items-center justify-center flex-shrink-0">
            <Zap size={12} className="text-white" />
          </div>
          <Search size={14} className="text-slate-500 flex-shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search pages and actions…"
            aria-label="Search pages and actions"
            className="flex-1 bg-transparent text-sm text-white placeholder-slate-500 outline-none"
          />
          <button onClick={onClose} aria-label="Close palette" className="text-slate-600 hover:text-slate-400">
            <X size={14} />
          </button>
        </div>

        {/* Results */}
        <div ref={listRef} className="max-h-80 overflow-y-auto py-1">
          {flat.length === 0 && (
            <p className="text-xs text-slate-500 px-4 py-6 text-center">No results for "{query}"</p>
          )}
          {groups.map(group => (
            <div key={group.label}>
              <p className="flex items-center gap-1.5 text-[9px] font-bold text-slate-600 uppercase tracking-widest px-4 pt-2 pb-1">
                {group.label === 'Recent' && <Clock size={9} />}
                {group.label}
              </p>
              {group.items.map(({ cmd, indices }) => {
                rowIndex++
                const i = rowIndex
                const Icon = cmd.icon
                return (
                  <button
                    key={`${group.label}-${cmd.id}`}
                    data-index={i}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                      i === selected ? 'bg-blue-600/20 text-white' : 'text-slate-300 hover:bg-slate-800/60'
                    }`}
                    onMouseEnter={() => setSelected(i)}
                    onClick={() => run(cmd)}
                  >
                    <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${
                      i === selected ? 'bg-blue-600/30' : 'bg-slate-800'
                    }`}>
                      <Icon size={13} className={i === selected ? 'text-blue-300' : 'text-slate-400'} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-semibold"><Highlight text={cmd.label} indices={indices} /></p>
                      {cmd.description && <p className="text-[10px] text-slate-500 truncate">{cmd.description}</p>}
                    </div>
                    {i === selected && (
                      <span className="text-[10px] text-slate-600 border border-slate-700 px-1.5 py-0.5 rounded flex-shrink-0">↵</span>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </div>

        {/* Footer hint */}
        <div className="border-t border-vmware-border px-4 py-2 flex items-center gap-3 text-[10px] text-slate-600">
          <span>↑↓ navigate</span>
          <span>↵ select</span>
          <span>Esc close</span>
          <span title="From any page: press g, then a letter — g f Fleet · g a Analysis · g k Kubectl · g d Discovery · g s Settings">
            g+key jumps
          </span>
          <span className="ml-auto">⌘K / Ctrl+K</span>
        </div>
      </div>
    </div>
  )
}
