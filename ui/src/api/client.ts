import type { AnalysisRequest, AnalysisResponse } from '../types'

const API_BASE = import.meta.env.VITE_API_URL || ''

export async function runAnalysis(request: AnalysisRequest): Promise<AnalysisResponse> {
  const res = await fetch(`${API_BASE}/api/v1/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail || `API error ${res.status}`)
  }

  return res.json()
}

export interface StreamCallbacks {
  onProgress?: (step: string, detail: string) => void
  onScored?:   (data: Partial<AnalysisResponse>) => void
  onToken?:    (text: string) => void
  onDone?:     (explanation: string) => void
  onError?:    (message: string) => void
}

export async function runAnalysisStream(
  request: AnalysisRequest,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/analyze/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail || `API error ${res.status}`)
  }

  const reader  = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer    = ''
  let fullText  = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const raw = line.slice(6).trim()
      if (!raw) continue
      let event: Record<string, unknown>
      try { event = JSON.parse(raw) } catch { continue }

      switch (event.type) {
        case 'progress':
          callbacks.onProgress?.(event.step as string, event.detail as string)
          break
        case 'scored':
          callbacks.onScored?.(event.data as Partial<AnalysisResponse>)
          break
        case 'token':
          fullText += event.text as string
          callbacks.onToken?.(event.text as string)
          break
        case 'done':
          callbacks.onDone?.(fullText)
          return
        case 'error':
          callbacks.onError?.(event.message as string)
          return
      }
    }
  }
}
