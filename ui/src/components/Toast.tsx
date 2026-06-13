import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react'
import { CheckCircle, XCircle, AlertTriangle, Info, X } from 'lucide-react'

// ── Types ────────────────────────────────────────────────────────────────────

export type ToastKind = 'success' | 'error' | 'warning' | 'info'

export interface ToastItem {
  id: string
  kind: ToastKind
  message: string
  action?: { label: string; onClick: () => void }
  duration?: number   // ms; 0 = sticky
}

interface ToastContextValue {
  toast: (kind: ToastKind, message: string, opts?: Partial<Pick<ToastItem, 'action' | 'duration'>>) => void
  success: (message: string, opts?: Partial<Pick<ToastItem, 'action' | 'duration'>>) => void
  error:   (message: string, opts?: Partial<Pick<ToastItem, 'action' | 'duration'>>) => void
  warning: (message: string, opts?: Partial<Pick<ToastItem, 'action' | 'duration'>>) => void
  info:    (message: string, opts?: Partial<Pick<ToastItem, 'action' | 'duration'>>) => void
  dismiss: (id: string) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

// ── Provider ─────────────────────────────────────────────────────────────────

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const toast = useCallback((
    kind: ToastKind,
    message: string,
    opts?: Partial<Pick<ToastItem, 'action' | 'duration'>>
  ) => {
    const id = Math.random().toString(36).slice(2)
    const duration = opts?.duration ?? (kind === 'error' ? 6000 : 4000)
    setToasts(prev => [...prev.slice(-4), { id, kind, message, ...opts, duration }])
    if (duration > 0) {
      setTimeout(() => dismiss(id), duration)
    }
    return id
  }, [dismiss])

  const value: ToastContextValue = {
    toast,
    success: (m, o) => toast('success', m, o),
    error:   (m, o) => toast('error',   m, o),
    warning: (m, o) => toast('warning', m, o),
    info:    (m, o) => toast('info',    m, o),
    dismiss,
  }

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastHost toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}

// ── UI ───────────────────────────────────────────────────────────────────────

const ICONS: Record<ToastKind, React.ElementType> = {
  success: CheckCircle,
  error:   XCircle,
  warning: AlertTriangle,
  info:    Info,
}

const STYLES: Record<ToastKind, string> = {
  success: 'border-green-800/60 bg-green-900/20 text-green-300',
  error:   'border-red-800/60   bg-red-900/20   text-red-300',
  warning: 'border-yellow-800/60 bg-yellow-900/20 text-yellow-300',
  info:    'border-blue-800/60  bg-blue-900/20  text-blue-300',
}

function ToastHost({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: string) => void }) {
  if (!toasts.length) return null
  return (
    <div
      role="region"
      aria-label="Notifications"
      aria-live="polite"
      className="fixed bottom-5 right-5 z-[9999] flex flex-col gap-2 max-w-sm w-full"
    >
      {toasts.map(t => {
        const Icon = ICONS[t.kind]
        return (
          <div
            key={t.id}
            role="alert"
            className={`flex items-start gap-3 rounded-xl border px-4 py-3 shadow-2xl backdrop-blur-sm
              animate-toast-in ${STYLES[t.kind]}`}
          >
            <Icon size={15} className="flex-shrink-0 mt-0.5" aria-hidden="true" />
            <p className="flex-1 text-sm leading-snug">{t.message}</p>
            {t.action && (
              <button
                onClick={() => { t.action!.onClick(); onDismiss(t.id) }}
                className="text-xs font-semibold underline flex-shrink-0 hover:no-underline"
              >
                {t.action.label}
              </button>
            )}
            <button
              onClick={() => onDismiss(t.id)}
              aria-label="Dismiss notification"
              className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity ml-1"
            >
              <X size={13} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
