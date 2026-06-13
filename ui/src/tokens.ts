/**
 * Single source of truth for semantic design tokens.
 * Use these for inline styles and dynamic color logic.
 * Tailwind classes (text-status-ok etc.) map to the same CSS variables.
 */

export const STATUS_COLOR = {
  ok:       'var(--status-ok)',
  warning:  'var(--status-warning)',
  critical: 'var(--status-critical)',
  info:     'var(--status-info)',
  unknown:  'var(--status-unknown)',
} as const

export const STATUS_BG = {
  ok:       'var(--status-ok-bg)',
  warning:  'var(--status-warning-bg)',
  critical: 'var(--status-critical-bg)',
  info:     'var(--status-info-bg)',
  unknown:  'var(--status-unknown-bg)',
} as const

export const STATUS_BORDER = {
  ok:       'var(--status-ok-border)',
  warning:  'var(--status-warning-border)',
  critical: 'var(--status-critical-border)',
  info:     'var(--status-info-border)',
  unknown:  'var(--status-unknown-border)',
} as const

/** Tailwind utility classes for status text — avoids JIT purging */
export const STATUS_TEXT_CLASS: Record<string, string> = {
  ok:       'text-green-400',
  warning:  'text-yellow-400',
  critical: 'text-red-400',
  info:     'text-blue-400',
  unknown:  'text-slate-400',
}

export const STATUS_BADGE_CLASS: Record<string, string> = {
  ok:       'text-green-400 bg-green-900/20 border-green-800',
  warning:  'text-yellow-400 bg-yellow-900/20 border-yellow-800',
  critical: 'text-red-400 bg-red-900/20 border-red-800',
  info:     'text-blue-400 bg-blue-900/20 border-blue-800',
  unknown:  'text-slate-400 bg-slate-800/40 border-slate-700',
}

/** Map AnalysisResponse.status → semantic key */
export function analysisSeverity(status: string): keyof typeof STATUS_COLOR {
  if (status === 'READY')     return 'ok'
  if (status === 'WARNING')   return 'warning'
  if (status === 'NOT_READY') return 'critical'
  return 'unknown'
}
