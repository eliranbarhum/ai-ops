import { useState, useEffect, useRef, useMemo, lazy, Suspense } from 'react'
import {
  Activity, Server, Zap, AlertTriangle, CheckCircle, XCircle,
  Archive, Printer, BookmarkPlus, Loader2, Cpu, MemoryStick,
  HardDrive, Shield, MonitorCheck, BadgeCheck, ChevronDown, ChevronUp,
  StopCircle, HelpCircle,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeSanitize from 'rehype-sanitize'
import { AnalysisForm } from './components/AnalysisForm'
import { CommandPalette } from './components/CommandPalette'
import { Layout } from './components/Layout'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastProvider, useToast } from './components/Toast'
import { runAnalysisStream } from './api/client'
import { STATUS_COLOR, STATUS_TEXT_CLASS, STATUS_BADGE_CLASS, analysisSeverity } from './tokens'
import type { AnalysisResponse, AnalysisRequest, SubScore, ScoreEntity } from './types'

// ── Lazy-loaded pages (code splitting) ─────────────────────────────────────
const SettingsPage   = lazy(() => import('./components/SettingsPage').then(m => ({ default: m.SettingsPage })))
const FleetPage      = lazy(() => import('./components/FleetPage').then(m => ({ default: m.FleetPage })))
const ArchivePage    = lazy(() => import('./components/ArchivePage').then(m => ({ default: m.ArchivePage })))
const WorkspacePage  = lazy(() => import('./components/WorkspacePage').then(m => ({ default: m.WorkspacePage })))
const AgentPage      = lazy(() => import('./components/AgentPage').then(m => ({ default: m.AgentPage })))
const BulkPage       = lazy(() => import('./components/BulkPage').then(m => ({ default: m.BulkPage })))
const GuestPage      = lazy(() => import('./components/GuestPage').then(m => ({ default: m.GuestPage })))
const KubectlPage    = lazy(() => import('./components/KubectlPage').then(m => ({ default: m.KubectlPage })))
const DiscoveryPage  = lazy(() => import('./components/DiscoveryPage').then(m => ({ default: m.DiscoveryPage })))
const TrendsPage     = lazy(() => import('./components/TrendsPage').then(m => ({ default: m.TrendsPage })))
const AuditPage      = lazy(() => import('./components/AuditPage').then(m => ({ default: m.AuditPage })))
const DirectoryPage  = lazy(() => import('./components/DirectoryPage').then(m => ({ default: m.DirectoryPage })))
const VulnScanPage   = lazy(() => import('./components/VulnScanPage').then(m => ({ default: m.VulnScanPage })))
const AlertsPage      = lazy(() => import('./components/AlertsPage').then(m => ({ default: m.AlertsPage })))
const CompliancePage  = lazy(() => import('./components/CompliancePage').then(m => ({ default: m.CompliancePage })))
const PlatformPage    = lazy(() => import('./components/PlatformPage').then(m => ({ default: m.PlatformPage })))

function PageLoader() {
  return (
    <div className="flex-1 flex items-center justify-center min-h-[200px]">
      <Loader2 size={24} className="animate-spin text-slate-600" />
    </div>
  )
}

const API_BASE = import.meta.env.VITE_API_URL || ''

// ---------------------------------------------------------------------------
// Hash-based router
// ---------------------------------------------------------------------------
type Page = 'fleet' | 'analysis' | 'settings' | 'archive' | 'workspace' | 'agent' | 'bulk' | 'guest' | 'kubectl' | 'discovery' | 'vulnscan' | 'directory' | 'trends' | 'audit' | 'alerts' | 'compliance' | 'platform'

function getPage(): Page {
  const hash = window.location.hash
  if (hash === '#/analysis')  return 'analysis'
  if (hash === '#/settings')  return 'settings'
  if (hash === '#/archive')   return 'archive'
  if (hash === '#/workspace') return 'workspace'
  if (hash === '#/agent')     return 'agent'
  if (hash === '#/bulk')      return 'bulk'
  if (hash === '#/guest')     return 'guest'
  if (hash === '#/kubectl')    return 'kubectl'
  if (hash === '#/discovery') return 'discovery'
  if (hash === '#/vulnscan')  return 'vulnscan'
  if (hash === '#/trends')    return 'trends'
  if (hash === '#/directory') return 'directory'
  if (hash === '#/audit')      return 'audit'
  if (hash === '#/alerts')     return 'alerts'
  if (hash === '#/compliance') return 'compliance'
  if (hash === '#/platform')   return 'platform'
  return 'fleet'
}

function navigate(to: Page) { window.location.hash = `#/${to}` }

const PAGE_TITLES: Record<Page, string> = {
  fleet: 'Fleet', analysis: 'Analysis', settings: 'Settings', archive: 'Archive',
  workspace: 'Workspace', agent: 'AI Agent', bulk: 'Bulk Ops', guest: 'Guest',
  kubectl: 'Kubectl', discovery: 'Discovery', vulnscan: 'Vuln Scan',
  directory: 'Directory', trends: 'Trends', audit: 'Audit Log', alerts: 'Alerts',
  compliance: 'Compliance',
  platform: 'Platform Console',
}

function useRoute(): Page {
  const [page, setPage] = useState<Page>(getPage)
  useEffect(() => {
    const handler = () => setPage(getPage())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])
  useEffect(() => {
    document.title = `${PAGE_TITLES[page]} · MCO`
  }, [page])
  return page
}

// Linear-style sequence shortcuts: press "g" then a letter to jump to a page.
const GO_SHORTCUTS: Record<string, Page> = {
  f: 'fleet', a: 'analysis', t: 'trends', r: 'archive',
  w: 'workspace', m: 'agent', b: 'bulk', g: 'guest', k: 'kubectl',
  d: 'discovery', v: 'vulnscan', l: 'audit', s: 'settings', p: 'platform',
}

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
}

// ---------------------------------------------------------------------------
// Sub-score icon map
// ---------------------------------------------------------------------------
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const SUB_ICON: Record<string, React.ElementType<any>> = {
  cpu: Cpu,
  memory: MemoryStick,
  storage: HardDrive,
  platform: Activity,
  hosts: MonitorCheck,
  hcl: BadgeCheck,
  shield: Shield,
}

/** Map sub-score status → CSS variable color string (for SVG inline styles) */
const STATUS_RING: Record<string, string> = {
  ok:      STATUS_COLOR.ok,
  warning: STATUS_COLOR.warning,
  critical:STATUS_COLOR.critical,
  unknown: STATUS_COLOR.unknown,
}

// ---------------------------------------------------------------------------
// Score donut
// ---------------------------------------------------------------------------
function ScoreDonut({
  score, status, signalsScored, signalsTotal, confidenceNote,
}: {
  score: number
  status: AnalysisResponse['status']
  signalsScored?: number
  signalsTotal?: number
  confidenceNote?: string | null
}) {
  const sev = analysisSeverity(status)
  const color = STATUS_COLOR[sev]
  const r = 48
  const circ = 2 * Math.PI * r
  const offset = circ - (score / 100) * circ
  const label = { READY: 'READY', WARNING: 'CAUTION', NOT_READY: 'NOT READY', UNKNOWN: 'UNKNOWN' }[status] ?? status
  const hasPartialData = signalsScored !== undefined && signalsTotal !== undefined && signalsScored < signalsTotal
  return (
    <div className="flex flex-col items-center gap-2">
      {hasPartialData && (
        <div
          role="alert"
          title={confidenceNote ?? `Scored on ${signalsScored}/${signalsTotal} signals`}
          className="flex items-center gap-1 text-[10px] text-yellow-400 bg-yellow-900/20 border border-yellow-800/40 rounded-full px-2 py-0.5 cursor-help"
        >
          <HelpCircle size={9} aria-hidden="true" />
          {signalsScored}/{signalsTotal} signals
        </div>
      )}
      <div className="relative w-28 h-28">
        <svg viewBox="0 0 120 120" className="w-full h-full" role="img" aria-label={`Readiness score: ${score} out of 100 — ${label}`}>
          <circle cx="60" cy="60" r={r} fill="none" stroke="#1e3a5f" strokeWidth="10" />
          <circle cx="60" cy="60" r={r} fill="none" stroke={color} strokeWidth="10"
            strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
            transform="rotate(-90 60 60)" style={{ transition: 'stroke-dashoffset 1s ease' }} />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center" aria-hidden="true">
          <span className="text-3xl font-bold" style={{ color }}>{score}</span>
          <span className="text-xs text-slate-400">/100</span>
        </div>
      </div>
      <span className={`text-xs font-bold tracking-widest px-3 py-1 rounded-full border ${STATUS_BADGE_CLASS[sev]}`}>
        {label}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-score card (expandable entity trace)
// ---------------------------------------------------------------------------
const ENTITY_STATUS_COLOR: Record<string, string> = STATUS_TEXT_CLASS

function EntityRow({ e }: { e: ScoreEntity }) {
  const color = ENTITY_STATUS_COLOR[e.status] ?? 'text-slate-400'
  return (
    <div className="flex items-center justify-between text-[10px] py-0.5">
      <span className="text-slate-400 truncate mr-2" title={e.name}>{e.name}</span>
      <span className={`font-mono font-semibold flex-shrink-0 ${color}`}>
        {typeof e.value === 'number' ? e.value.toFixed(1) : e.value}
        {e.unit && <span className="text-slate-500 ml-0.5">{e.unit}</span>}
      </span>
    </div>
  )
}

function SubScoreCard({ sub }: { sub: SubScore }) {
  const [expanded, setExpanded] = useState(false)
  const Icon = SUB_ICON[sub.icon] ?? Activity
  const color = STATUS_RING[sub.status]
  const r = 14
  const circ = 2 * Math.PI * r
  const offset = circ - (sub.pct / 100) * circ
  const hasEntities = sub.entities && sub.entities.length > 0
  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
      <div
        className={`p-4 flex items-center gap-4 ${hasEntities ? 'cursor-pointer hover:bg-slate-800/30' : ''}`}
        onClick={() => hasEntities && setExpanded(e => !e)}
        role={hasEntities ? 'button' : undefined}
        aria-expanded={hasEntities ? expanded : undefined}
        aria-label={hasEntities ? `${sub.label}: ${sub.score} of ${sub.max}. Click to expand details` : undefined}
        tabIndex={hasEntities ? 0 : undefined}
        onKeyDown={hasEntities ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(x => !x) } } : undefined}
      >
        <div className="relative w-10 h-10 flex-shrink-0">
          <svg viewBox="0 0 36 36" className="w-full h-full">
            <circle cx="18" cy="18" r={r} fill="none" stroke="#1e293b" strokeWidth="3" />
            <circle cx="18" cy="18" r={r} fill="none" stroke={color} strokeWidth="3"
              strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
              transform="rotate(-90 18 18)" />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span style={{ color }}><Icon size={12} /></span>
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-slate-200">{sub.label}</p>
          <div className="flex items-center gap-2 mt-1">
            <div className="flex-1 h-1 bg-slate-800 rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all duration-700"
                style={{ width: `${sub.pct}%`, backgroundColor: color }} />
            </div>
            <span className="text-xs text-slate-400 flex-shrink-0">{sub.score}/{sub.max}</span>
          </div>
          {(sub.critical_count > 0 || sub.warning_count > 0) && (
            <p className="text-xs mt-1" style={{ color }}>
              {sub.critical_count > 0 ? `${sub.critical_count} critical` : `${sub.warning_count} warning`}
            </p>
          )}
        </div>
        {hasEntities && (
          expanded ? <ChevronUp size={11} className="text-slate-500 flex-shrink-0" />
                   : <ChevronDown size={11} className="text-slate-500 flex-shrink-0" />
        )}
      </div>
      {expanded && sub.entities && sub.entities.length > 0 && (
        <div className="border-t border-vmware-border px-4 py-2 space-y-0.5 bg-slate-900/40">
          {sub.entities.map((e, i) => <EntityRow key={i} e={e} />)}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Version compatibility table
// ---------------------------------------------------------------------------
function VersionTable({ sub }: { sub: SubScore }) {
  if (!sub.detail) return null
  const { components, target_version, version_gaps, interop_gaps } = sub.detail
  // Only include true blocker gaps — filter out workflow/deprecation/consolidation annotations
  const blockerGaps = [...(version_gaps ?? []), ...(interop_gaps ?? [])].filter(
    (g: string) => !g.startsWith('[Upgrade Workflow]') && !g.startsWith('[Consolidation]') && !g.startsWith('[New Requirement]') && !g.startsWith('[Deprecation')
  )

  // Min required source version to qualify for a direct upgrade, keyed by target version
  const MIN_REQ_BY_TARGET: Record<string, Record<string, string>> = {
    '9.1': { vcenter: '9.0.0', esxi: '9.0.0', nsx: '9.0.0', sddc_manager: '9.0.0' },
    '9.0': { vcenter: '9.0.0', esxi: '9.0.0', nsx: '9.0.0', sddc_manager: '9.0.0' },
    '5.2': { vcenter: '8.0.2', esxi: '8.0.2', nsx: '4.1.0', sddc_manager: '5.2.0' },
  }
  const MIN_REQ = MIN_REQ_BY_TARGET[target_version] ?? MIN_REQ_BY_TARGET['9.1']

  const rows = Object.entries(components as Record<string, string>).filter(([, v]) => v)
  if (!rows.length) return null

  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
      <div className="px-4 py-3 border-b border-vmware-border flex items-center gap-2">
        <BadgeCheck size={14} className="text-blue-400" />
        <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
          Version Compatibility — Target VCF {target_version}
        </span>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-vmware-border">
            <th className="text-left px-4 py-2 text-slate-400 font-medium">Component</th>
            <th className="text-left px-4 py-2 text-slate-400 font-medium">Installed</th>
            <th className="text-left px-4 py-2 text-slate-400 font-medium">Min Required</th>
            <th className="text-left px-4 py-2 text-slate-400 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([comp, ver]) => {
            // Match gaps that explicitly reference this component (word-boundary safe)
            const compLower = comp.toLowerCase().replace('_', ' ')
            const hasGap = blockerGaps.some((g: string) => {
              const gl = g.toLowerCase()
              return gl.includes(`${compLower} `) || gl.includes(`${compLower}:`) || gl.startsWith(compLower)
            })
            const minReq = MIN_REQ[comp] ?? '—'
            return (
              <tr key={comp} className="border-b border-vmware-border/50 hover:bg-slate-800/30">
                <td className="px-4 py-2 text-slate-300 font-medium capitalize">{comp.replace('_', ' ')}</td>
                <td className="px-4 py-2 font-mono text-slate-200">{ver || '—'}</td>
                <td className="px-4 py-2 font-mono text-slate-400">{minReq}</td>
                <td className="px-4 py-2">
                  {hasGap
                    ? <span className="text-red-400 flex items-center gap-1"><XCircle size={10} /> Incompatible</span>
                    : ver
                      ? <span className="text-green-400 flex items-center gap-1"><CheckCircle size={10} /> OK</span>
                      : <span className="text-slate-500">Unknown</span>
                  }
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {blockerGaps.length > 0 && (
        <div className="px-4 py-3 bg-red-900/10 border-t border-red-900/30">
          {blockerGaps.map((g: string, i: number) => (
            <p key={i} className="text-xs text-red-400 flex items-start gap-1.5 mb-1">
              <XCircle size={10} className="mt-0.5 flex-shrink-0" /> {g}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// HCL hardware table
// ---------------------------------------------------------------------------
function HclTable({ sub }: { sub: SubScore }) {
  if (!sub.detail?.hcl_results?.length) return null
  const { hcl_results, hcl_warnings } = sub.detail

  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
      <div className="px-4 py-3 border-b border-vmware-border flex items-center gap-2">
        <Shield size={14} className="text-purple-400" />
        <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
          Hardware HCL Certification
        </span>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-vmware-border">
            <th className="text-left px-4 py-2 text-slate-400 font-medium">Host</th>
            <th className="text-left px-4 py-2 text-slate-400 font-medium">CPU Platform</th>
            <th className="text-left px-4 py-2 text-slate-400 font-medium">ESXi Version</th>
            <th className="text-left px-4 py-2 text-slate-400 font-medium">HCL Status</th>
          </tr>
        </thead>
        <tbody>
          {hcl_results.map((r, i) => (
            <tr key={i} className="border-b border-vmware-border/50 hover:bg-slate-800/30">
              <td className="px-4 py-2 font-mono text-slate-300 text-xs">{r.host}</td>
              <td className="px-4 py-2 text-slate-200">{r.platform_name}</td>
              <td className="px-4 py-2 font-mono text-slate-300">{r.esxi_version}</td>
              <td className="px-4 py-2">
                {r.certified === true
                  ? <span className="text-green-400 flex items-center gap-1"><CheckCircle size={10} /> Certified</span>
                  : r.certified === false
                    ? <span className="text-red-400 flex items-center gap-1"><XCircle size={10} /> Not Certified</span>
                    : <span className="text-yellow-400 flex items-center gap-1"><AlertTriangle size={10} /> Unconfirmed</span>
                }
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {hcl_warnings && hcl_warnings.length > 0 && (
        <div className="px-4 py-3 bg-yellow-900/10 border-t border-yellow-900/30">
          {hcl_warnings.slice(0, 3).map((w, i) => (
            <p key={i} className="text-xs text-yellow-400 flex items-start gap-1.5 mb-1">
              <AlertTriangle size={10} className="mt-0.5 flex-shrink-0" /> {w}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SDDC Domain table
// ---------------------------------------------------------------------------
function SddcDomainsTable({ rawMetrics }: { rawMetrics: Record<string, unknown> }) {
  const sddc = rawMetrics?.get_sddc_health as Record<string, unknown> | undefined
  if (!sddc) return null
  const domains = (sddc.domains as { name: string; type: string; status: string; upgrade_state: string; cluster_count: number }[]) || []
  const blockers = (sddc.upgrade_blockers as string[]) || []
  const warnings = (sddc.upgrade_warnings as string[]) || []
  if (!domains.length && !blockers.length) return null

  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
      <div className="px-4 py-3 border-b border-vmware-border flex items-center gap-2">
        <Server size={14} className="text-cyan-400" />
        <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
          SDDC Manager — Domain & Upgrade Lifecycle
        </span>
        <span className="ml-auto text-xs text-slate-500 font-mono">{(sddc.sddc_version as string) || ''}</span>
      </div>
      {domains.length > 0 && (
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-vmware-border">
              <th className="text-left px-4 py-2 text-slate-400 font-medium">Domain</th>
              <th className="text-left px-4 py-2 text-slate-400 font-medium">Type</th>
              <th className="text-left px-4 py-2 text-slate-400 font-medium">Status</th>
              <th className="text-left px-4 py-2 text-slate-400 font-medium">Upgrade State</th>
              <th className="text-left px-4 py-2 text-slate-400 font-medium">Clusters</th>
            </tr>
          </thead>
          <tbody>
            {domains.map((d, i) => (
              <tr key={i} className="border-b border-vmware-border/50 hover:bg-slate-800/30">
                <td className="px-4 py-2 text-slate-200 font-medium">{d.name}</td>
                <td className="px-4 py-2 text-slate-400">{d.type}</td>
                <td className="px-4 py-2">
                  <span className={d.status?.toUpperCase() === 'ACTIVE'
                    ? 'text-green-400' : 'text-red-400'}>
                    {d.status || '—'}
                  </span>
                </td>
                <td className="px-4 py-2 text-slate-400">{d.upgrade_state || '—'}</td>
                <td className="px-4 py-2 text-slate-400">{d.cluster_count ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(blockers.length > 0 || warnings.length > 0) && (
        <div className="px-4 py-3 border-t border-vmware-border space-y-1">
          {blockers.map((b, i) => (
            <p key={i} className="text-xs text-red-400 flex items-start gap-1.5">
              <XCircle size={10} className="mt-0.5 flex-shrink-0" /> {b}
            </p>
          ))}
          {warnings.slice(0, 4).map((w, i) => (
            <p key={i} className="text-xs text-yellow-400 flex items-start gap-1.5">
              <AlertTriangle size={10} className="mt-0.5 flex-shrink-0" /> {w}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AI Explanation renderer — react-markdown (sanitized, no dangerouslySetInnerHTML)
// ---------------------------------------------------------------------------
const SECTION_TITLE_COLORS: Record<string, string> = {
  'executive summary': 'text-blue-400',
  'critical findings': 'text-red-400',
  'upgrade path':      'text-purple-400',
  'hardware':          'text-cyan-400',
  'action items':      'text-yellow-400',
}

function ExplanationPanel({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const [openSections, setOpenSections] = useState<Set<number>>(new Set([0, 1, 2]))

  // Memoize parsing so we don't re-parse the entire text on every streaming token
  const sections = useMemo(() => {
    const result: { title: string; body: string }[] = []
    let current: { title: string; lines: string[] } | null = null
    for (const line of text.split('\n')) {
      const m = line.match(/^\*\*(.+?)\*\*\s*$/)
      if (m) {
        if (current) result.push({ title: current.title, body: current.lines.join('\n').trim() })
        current = { title: m[1], lines: [] }
      } else if (current) {
        current.lines.push(line)
      }
    }
    if (current) result.push({ title: current.title, body: current.lines.join('\n').trim() })
    return result
  }, [text])

  function toggle(i: number) {
    setOpenSections(prev => {
      const next = new Set(prev); next.has(i) ? next.delete(i) : next.add(i); return next
    })
  }

  // react-markdown v10 has no className prop — wrap in a .prose-ai div instead
  const mdProps = {
    remarkPlugins: [remarkGfm] as Parameters<typeof ReactMarkdown>[0]['remarkPlugins'],
    rehypePlugins: [rehypeSanitize] as Parameters<typeof ReactMarkdown>[0]['rehypePlugins'],
  }

  if (!sections.length) {
    return (
      <div className="rounded-xl border border-vmware-border bg-vmware-card p-5">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
          <Activity size={14} className="text-blue-400" aria-hidden="true" /> AI Analysis
          {streaming && <span className="ml-auto text-xs text-blue-400 animate-pulse" aria-live="polite">● generating…</span>}
        </h3>
        <div className="prose-ai"><ReactMarkdown {...mdProps}>{text}</ReactMarkdown></div>
        {streaming && <span className="inline-block w-1.5 h-4 bg-blue-400 animate-pulse ml-0.5 align-middle" aria-hidden="true" />}
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-vmware-border bg-vmware-card overflow-hidden">
      <div className="px-4 py-3 border-b border-vmware-border flex items-center gap-2">
        <Activity size={14} className="text-blue-400" aria-hidden="true" />
        <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">AI Analysis</span>
        {streaming && <span className="ml-auto text-xs text-blue-400 animate-pulse" aria-live="polite">● generating…</span>}
      </div>
      <div className="divide-y divide-vmware-border">
        {sections.map((sec, i) => {
          const open = openSections.has(i)
          const colorKey = Object.keys(SECTION_TITLE_COLORS).find(k => sec.title.toLowerCase().includes(k))
          const titleColor = colorKey ? SECTION_TITLE_COLORS[colorKey] : 'text-slate-300'
          return (
            <div key={i}>
              <button
                onClick={() => toggle(i)}
                aria-expanded={open}
                aria-controls={`explanation-section-${i}`}
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-800/40 transition-colors"
              >
                <span className={`text-xs font-semibold uppercase tracking-wider ${titleColor}`}>{sec.title}</span>
                {open
                  ? <ChevronUp size={12} className="text-slate-500" aria-hidden="true" />
                  : <ChevronDown size={12} className="text-slate-500" aria-hidden="true" />
                }
              </button>
              {open && (
                <div id={`explanation-section-${i}`} className="px-4 pb-4 prose-ai">
                  <ReactMarkdown {...mdProps}>{sec.body}</ReactMarkdown>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Risk factors panel
// ---------------------------------------------------------------------------
function RiskPanel({ riskFactors, recommendations }: { riskFactors: AnalysisResponse['risk_factors']; recommendations: string[] }) {
  const sorted = [...riskFactors].sort((a, b) => {
    const o = { critical: 0, warning: 1, info: 2 }
    return (o[a.severity] ?? 3) - (o[b.severity] ?? 3)
  })
  const SVCFG = {
    critical: { color: 'text-red-400', bg: 'bg-red-900/20 border-red-800' },
    warning: { color: 'text-yellow-400', bg: 'bg-yellow-900/20 border-yellow-800' },
    info: { color: 'text-blue-400', bg: 'bg-blue-900/20 border-blue-800' },
  }
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-vmware-border bg-vmware-card p-4">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
          <AlertTriangle size={13} className="text-yellow-400" />
          Risk Factors
          {riskFactors.length > 0 && (
            <span className="ml-auto text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded-full">{riskFactors.length}</span>
          )}
        </h3>
        {sorted.length === 0
          ? <p className="text-sm text-green-400">No risk factors detected</p>
          : <ul className="space-y-2">
            {sorted.map((r, i) => {
              const c = SVCFG[r.severity] ?? SVCFG.info
              return (
                <li key={i} className={`rounded-lg border p-2.5 ${c.bg}`}>
                  <p className={`text-xs font-semibold uppercase ${c.color}`}>{r.severity} · {r.component}</p>
                  <p className="text-sm text-slate-200 mt-0.5">{r.message}</p>
                </li>
              )
            })}
          </ul>
        }
      </div>
      {recommendations.length > 0 && (
        <div className="rounded-xl border border-vmware-border bg-vmware-card p-4">
          <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3">Recommendations</h3>
          <ul className="space-y-2">
            {recommendations.map((rec, i) => (
              <li key={i} className="flex items-start gap-2.5 text-sm text-slate-300">
                <span className="flex-shrink-0 w-5 h-5 rounded-full bg-vmware-blue/40 text-blue-300 text-xs flex items-center justify-center font-bold mt-0.5">{i + 1}</span>
                {rec}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Analysis page
// ---------------------------------------------------------------------------
const STREAM_STEPS = ['collecting', 'scoring', 'reasoning'] as const
type StreamStep = typeof STREAM_STEPS[number]
const STEP_LABELS: Record<StreamStep, { label: string; desc: string }> = {
  collecting: { label: 'Collecting', desc: 'vCenter · VCF Operations · SDDC' },
  scoring:    { label: 'Scoring',    desc: '8 categories' },
  reasoning:  { label: 'Reasoning',  desc: 'AI analysis' },
}

function AnalysisPage({ archiveLoad }: { archiveLoad?: { result: AnalysisResponse; id: number } | null }) {
  const toast = useToast()
  const [result, setResult] = useState<AnalysisResponse | null>(null)
  const [isArchived, setIsArchived] = useState(false)
  const lastLoadedId = useRef(-1)
  const [lastReq, setLastReq] = useState<AnalysisRequest | null>(null)
  const [llmStatus, setLlmStatus] = useState<{ provider: string; model?: string; status: string; slow_warning?: boolean } | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/workspace/llm-status`)
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setLlmStatus(d))
      .catch(() => {})
  }, [])

  // Load an archived scan when parent signals one
  useEffect(() => {
    if (archiveLoad && archiveLoad.id !== lastLoadedId.current) {
      lastLoadedId.current = archiveLoad.id
      abortRef.current?.abort()
      setResult(archiveLoad.result)
      setIsArchived(true)
      setStreamText('')
      setLoading(false)
      setError(null)
      setSaved(false)
      setLastReq(null)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [archiveLoad?.id])
  const [loading, setLoading] = useState(false)
  const [streamStep, setStreamStep] = useState<StreamStep>('collecting')
  const [streamText, setStreamText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const tokenBufRef = useRef('')
  const tokenFlushTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  async function handleAnalysis(req: AnalysisRequest) {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    setLoading(true)
    setError(null)
    setSaved(false)
    setIsArchived(false)
    setResult(null)
    setStreamText('')
    setStreamStep('collecting')
    setLastReq(req)

    try {
      await runAnalysisStream(req, {
        onProgress(_step) {
          const s = _step as StreamStep
          if (STREAM_STEPS.includes(s)) setStreamStep(s)
        },
        onScored(data) {
          setResult(data as AnalysisResponse)
          setStreamStep('reasoning')
        },
        onToken(text) {
          // Batch DOM updates every 50 ms to prevent renderer freeze during fast token streams
          tokenBufRef.current += text
          if (!tokenFlushTimer.current) {
            tokenFlushTimer.current = setTimeout(() => {
              const buf = tokenBufRef.current
              tokenBufRef.current = ''
              tokenFlushTimer.current = null
              setStreamText(prev => prev + buf)
            }, 50)
          }
        },
        onDone(explanation) {
          if (tokenFlushTimer.current) { clearTimeout(tokenFlushTimer.current); tokenFlushTimer.current = null }
          tokenBufRef.current = ''
          setResult(prev => prev ? { ...prev, explanation } : null)
          setStreamText('')
          setLoading(false)
        },
        onError(message) {
          setError(message)
          setLoading(false)
        },
      }, ctrl.signal)
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError(err instanceof Error ? err.message : 'Analysis failed')
      }
      setLoading(false)
    }
  }

  async function handleSave() {
    if (!result || !lastReq) return
    setSaving(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/scans`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: lastReq.target, query: lastReq.query, result }),
      })
      if (!resp.ok) throw new Error(`Save failed (HTTP ${resp.status})`)
      setSaved(true)
      toast.success('Scan saved to archive')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save scan')
    } finally {
      setSaving(false)
    }
  }

  const compatSub = result?.sub_scores?.find(s => s.name === 'compatibility')

  return (
    <div className="min-h-screen bg-vmware-dark print:bg-white">
      <main className="max-w-[1600px] mx-auto px-6 py-6">
        {/* Action bar */}
        <div className="flex items-center gap-2 mb-4 print:hidden">
          <button onClick={() => navigate('archive')}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors">
            <Archive size={12} /> Archive
          </button>
          {llmStatus && (
            <div className="flex items-center gap-1.5 text-xs">
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                llmStatus.status === 'ready' ? 'bg-green-400' :
                llmStatus.status === 'no_key' || llmStatus.status === 'model_not_loaded' ? 'bg-yellow-400' :
                'bg-red-400'
              }`} />
              <span className="text-slate-400">
                {llmStatus.provider}{llmStatus.model ? ` · ${llmStatus.model}` : ''}
              </span>
              {llmStatus.slow_warning && llmStatus.status === 'ready' && (
                <span className="text-slate-400">· CPU mode</span>
              )}
            </div>
          )}
          <div className="ml-auto flex items-center gap-2">
            {result && lastReq && !isArchived && (
              <button onClick={handleSave} disabled={saving || saved}
                className={`flex items-center gap-1.5 text-xs border px-3 py-1.5 rounded-lg transition-colors ${saved ? 'border-green-700 text-green-400 bg-green-900/20' : 'border-vmware-border text-slate-400 hover:text-white hover:border-slate-500'} disabled:opacity-60`}>
                {saving ? <Loader2 size={12} className="animate-spin" /> : <BookmarkPlus size={12} />}
                {saved ? 'Saved' : 'Save Scan'}
              </button>
            )}
            {result && (
              <button onClick={() => window.print()}
                className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-vmware-border px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors">
                <Printer size={12} /> Export PDF
              </button>
            )}
          </div>
        </div>
        {error && (
          <div className="mb-4 rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-sm text-red-400 flex items-center gap-2">
            <XCircle size={14} /> {error}
          </div>
        )}

        <div className="flex gap-5 items-start">
          {/* ── Left sidebar ── */}
          <div className="w-64 xl:w-72 flex-shrink-0 space-y-4 print:hidden">
            {isArchived ? (
              <div className="rounded-xl border border-amber-700/40 bg-amber-900/20 px-4 py-3 space-y-1.5">
                <p className="text-xs font-semibold text-amber-400 flex items-center gap-1.5">
                  <Archive size={12} /> Archived Result — Read-only
                </p>
                <p className="text-[11px] text-slate-400">Viewing a historical scan. Navigate to <a href="#/analysis" className="text-blue-400 hover:text-blue-300 underline">Analysis</a> to run a new one.</p>
              </div>
            ) : (
              <AnalysisForm onSubmit={handleAnalysis} loading={loading} />
            )}
            {result && (
              <div className="rounded-xl border border-vmware-border bg-vmware-card p-5 flex flex-col items-center gap-1">
                <p className="text-xs font-semibold tracking-widest text-slate-400 uppercase mb-2">VCF Readiness Score</p>
                <ScoreDonut
                  score={result.readiness_score}
                  status={result.status}
                  signalsScored={result.signals_scored}
                  signalsTotal={result.signals_total}
                  confidenceNote={result.confidence_note}
                />
              </div>
            )}
          </div>

          {/* ── Main content ── */}
          <div className="flex-1 min-w-0 space-y-5">

            {/* Loading — only shown before first scored event */}
            {loading && !result && (
              <div className="rounded-xl border border-vmware-border bg-vmware-card p-12 flex flex-col items-center justify-center gap-5">
                <div className="flex gap-8">
                  {STREAM_STEPS.map((step, i) => {
                    const isActive = streamStep === step
                    const isDone = STREAM_STEPS.indexOf(streamStep) > i
                    return (
                      <div key={step} className="flex flex-col items-center gap-2">
                        <div className={`w-2.5 h-2.5 rounded-full transition-colors ${
                          isActive ? 'bg-blue-400 animate-bounce' : isDone ? 'bg-green-500' : 'bg-slate-600'
                        }`} style={{ animationDelay: `${i * 0.15}s` }} />
                        <span className={`text-xs font-medium ${isActive ? 'text-white' : isDone ? 'text-green-400' : 'text-slate-500'}`}>
                          {STEP_LABELS[step].label}
                        </span>
                        <span className="text-xs text-slate-500">{STEP_LABELS[step].desc}</span>
                      </div>
                    )
                  })}
                </div>
                <p className="text-sm text-slate-300">Running VCF readiness pipeline…</p>
                <p className="text-xs text-slate-400">CPU inference may take 5–10 minutes</p>
              </div>
            )}

            {/* Empty state */}
            {!result && !loading && (
              <div className="rounded-xl border border-vmware-border bg-vmware-card p-16 flex flex-col items-center justify-center text-center gap-4">
                <Activity size={36} className="text-slate-500" />
                <div>
                  <p className="text-slate-300 text-sm font-medium">Run an analysis to see results</p>
                  <p className="text-slate-400 text-xs mt-1">
                    Collects data from vCenter, VCF Operations, SDDC Manager · Checks HCL & interop · AI-powered explanation
                  </p>
                </div>
              </div>
            )}

            {result && (
              <>
                {/* Sub-score cards grid */}
                {result.sub_scores && result.sub_scores.length > 0 && (
                  <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
                    {result.sub_scores.map(sub => (
                      <SubScoreCard key={sub.name} sub={sub} />
                    ))}
                  </div>
                )}

                {/* Two-column: version compat + HCL */}
                {compatSub && (
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                    <VersionTable sub={compatSub} />
                    <HclTable sub={compatSub} />
                  </div>
                )}

                {/* SDDC Domain table */}
                <SddcDomainsTable rawMetrics={result.raw_metrics} />

                {/* AI explanation — visible as soon as reasoning starts, streams tokens in */}
                {(loading && streamStep === 'reasoning' || streamText || result.explanation) && (
                  <ExplanationPanel
                    text={loading ? streamText : (result.explanation ?? '')}
                    streaming={loading && streamStep === 'reasoning'}
                  />
                )}

                {/* Risk factors + recommendations */}
                <RiskPanel riskFactors={result.risk_factors} recommendations={result.recommendations} />
              </>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Root — router
// ---------------------------------------------------------------------------
export default function App() {
  const page = useRoute()
  const [archiveLoad, setArchiveLoad] = useState<{ result: AnalysisResponse; id: number } | null>(null)
  const archiveLoadCounter = useRef(0)
  const [paletteOpen, setPaletteOpen] = useState(false)

  function loadArchivedScan(scan: AnalysisResponse) {
    archiveLoadCounter.current += 1
    setArchiveLoad({ result: scan, id: archiveLoadCounter.current })
    navigate('analysis')
  }

  useEffect(() => {
    let goPending = false
    let goTimer: ReturnType<typeof setTimeout> | undefined
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setPaletteOpen(v => !v)
        return
      }
      // "g" then letter — skip while typing or while palette is open
      if (e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return
      if (goPending) {
        goPending = false
        clearTimeout(goTimer)
        const target = GO_SHORTCUTS[e.key.toLowerCase()]
        if (target) {
          e.preventDefault()
          navigate(target)
        }
      } else if (e.key === 'g') {
        goPending = true
        goTimer = setTimeout(() => { goPending = false }, 1200)
      }
    }
    const onOpenPalette = () => setPaletteOpen(true)
    window.addEventListener('keydown', onKey)
    window.addEventListener('mco:open-palette', onOpenPalette)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mco:open-palette', onOpenPalette)
      clearTimeout(goTimer)
    }
  }, [])

  return (
    <ErrorBoundary>
      <ToastProvider>
        <Layout page={page} navigate={navigate}>
          <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} navigate={(p) => navigate(p as Page)} />
          <Suspense fallback={<PageLoader />}>
            {/* Conditionally mounted pages — keyed wrapper re-runs the enter animation */}
            <div key={page} className={page === 'kubectl' ? 'contents' : 'animate-page-enter'}>
              {page === 'bulk' && <BulkPage />}
              {page === 'guest' && <GuestPage />}
              {page === 'kubectl' && <KubectlPage />}
              {page === 'discovery' && <DiscoveryPage />}
              {page === 'vulnscan' && <VulnScanPage />}
              {page === 'directory' && <DirectoryPage />}
              {page === 'audit' && <AuditPage />}
              {page === 'alerts' && <AlertsPage onBack={() => navigate('fleet')} />}
              {page === 'compliance' && <CompliancePage onBack={() => navigate('fleet')} />}
              {page === 'platform' && <PlatformPage />}
              {page === 'settings' && <SettingsPage />}
              {page === 'trends' && <TrendsPage />}
              {page === 'archive' && <ArchivePage onViewScan={(scan) => loadArchivedScan(scan.result as AnalysisResponse)} />}
              {page === 'agent' && <AgentPage />}
            </div>
            {/* Keep-alive pages — class flip from hidden re-triggers the animation */}
            <div className={page === 'fleet' ? 'animate-page-enter' : 'hidden'}>
              <FleetPage />
            </div>
            <div className={page === 'analysis' ? 'animate-page-enter' : 'hidden'}>
              <AnalysisPage archiveLoad={archiveLoad} />
            </div>
            <div className={page === 'workspace' ? 'animate-page-enter' : 'hidden'}>
              <WorkspacePage />
            </div>
          </Suspense>
        </Layout>
      </ToastProvider>
    </ErrorBoundary>
  )
}
