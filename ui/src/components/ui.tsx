/**
 * Shared UI primitives — keep pages visually consistent.
 *
 * Skeleton    — shimmering placeholder block (sized via className)
 * EmptyState  — icon + title + hint, used when a list/table has no data
 * PageHeader  — page title row with optional subtitle and right-aligned actions
 */
import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />
}

/** Card-shaped skeleton group for list/table loading states. */
export function SkeletonRows({ rows = 3, className = '' }: { rows?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-lg border border-vmware-border bg-vmware-card px-3 py-2.5">
          <Skeleton className="w-7 h-7 rounded-lg flex-shrink-0" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3 w-2/5" />
            <Skeleton className="h-2.5 w-3/5" />
          </div>
        </div>
      ))}
    </div>
  )
}

export function EmptyState({
  icon: Icon, title, hint, action,
}: {
  icon: LucideIcon
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center gap-3 py-12 px-6">
      <div className="w-12 h-12 rounded-2xl bg-slate-800/60 border border-vmware-border flex items-center justify-center">
        <Icon size={20} className="text-slate-500" aria-hidden="true" />
      </div>
      <div>
        <p className="text-sm font-medium text-slate-300">{title}</p>
        {hint && <p className="text-xs text-slate-400 mt-1 max-w-sm">{hint}</p>}
      </div>
      {action}
    </div>
  )
}

export function PageHeader({
  icon: Icon, title, subtitle, children,
}: {
  icon?: LucideIcon
  title: string
  subtitle?: ReactNode
  children?: ReactNode   // right-aligned actions
}) {
  return (
    <div className="flex items-center gap-3 mb-5 flex-wrap">
      {Icon && (
        <div className="w-8 h-8 rounded-xl bg-vmware-blue/30 border border-blue-800/40 flex items-center justify-center flex-shrink-0">
          <Icon size={15} className="text-blue-300" aria-hidden="true" />
        </div>
      )}
      <div className="min-w-0">
        <h1 className="text-xl font-bold text-white leading-tight tracking-tight">{title}</h1>
        {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
      </div>
      {children && <div className="ml-auto flex items-center gap-2">{children}</div>}
    </div>
  )
}
