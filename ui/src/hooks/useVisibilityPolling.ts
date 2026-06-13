import { useEffect, useRef, useCallback } from 'react'

/**
 * Runs `fn` immediately and then on `interval` ms — but PAUSES when the tab
 * is hidden and BACKS OFF (doubles interval, caps at maxInterval) after
 * repeated failures. Resumes immediately when the tab becomes visible again.
 */
export function useVisibilityPolling(
  fn: () => Promise<void> | void,
  interval: number,
  options: { enabled?: boolean; maxInterval?: number } = {}
) {
  const { enabled = true, maxInterval = interval * 8 } = options
  const timerRef    = useRef<ReturnType<typeof setTimeout> | null>(null)
  const failsRef    = useRef(0)
  const fnRef       = useRef(fn)

  useEffect(() => { fnRef.current = fn }, [fn])

  const schedule = useCallback((delay: number) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(async () => {
      if (document.hidden) {
        // Tab hidden — check again when visible
        const onVisible = () => {
          document.removeEventListener('visibilitychange', onVisible)
          schedule(0)
        }
        document.addEventListener('visibilitychange', onVisible)
        return
      }
      try {
        await fnRef.current()
        failsRef.current = 0
        schedule(interval)
      } catch {
        failsRef.current += 1
        const backoff = Math.min(interval * 2 ** failsRef.current, maxInterval)
        schedule(backoff)
      }
    }, delay)
  }, [interval, maxInterval])

  useEffect(() => {
    if (!enabled) return
    // Run immediately, then schedule
    ;(async () => {
      try {
        await fnRef.current()
        failsRef.current = 0
      } catch {
        failsRef.current += 1
      }
      schedule(interval)
    })()

    const onVisible = () => {
      if (!document.hidden) schedule(0)
    }
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [enabled, interval, schedule])
}
