import { useEffect } from 'react'

/**
 * Shows a browser "Leave site?" confirm dialog when the user tries to close the
 * tab or navigate away while `dirty` is true.
 */
export function useUnsavedGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
}
