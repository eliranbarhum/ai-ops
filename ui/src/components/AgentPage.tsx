import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Bot, Send, Plus, Trash2, ChevronDown, ChevronRight,
  Loader2, AlertTriangle, Wrench, CheckCircle, XCircle,
  Zap, Server, Activity, Settings, Upload, Laptop, Archive, Terminal, Radar, TrendingUp,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeSanitize from 'rehype-sanitize'

const API_BASE = import.meta.env.VITE_API_URL || ''

// ─── Types ────────────────────────────────────────────────────────────────────

interface ToolCall {
  tool: string
  params: Record<string, unknown>
  summary?: string
  ok?: boolean
  data?: unknown
}

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  tool_calls?: ToolCall[]
  timestamp: string
}

interface Conversation {
  id: string
  title: string
  provider: string
  created_at: string
  updated_at: string
  messages?: Message[]
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)
}

function fmt(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function toolLabel(name: string) {
  return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: 'text-orange-400',
  openai:    'text-green-400',
  gemini:    'text-blue-400',
  ollama:    'text-emerald-400',
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic',
  openai:    'OpenAI',
  gemini:    'Gemini',
  ollama:    'Local (Ollama)',
}

const CLOUD_PROVIDERS = ['anthropic', 'openai', 'gemini', 'ollama', 'local']

// ─── Tool data renderer ───────────────────────────────────────────────────────

function ToolDataTable({ data }: { data: unknown }) {
  if (data === null || data === undefined) return null

  // Unwrap common shapes: {pods:[...]}, {vms:[...]}, {hosts:[...]}, {items:[...]}, etc.
  let rows: unknown = data
  if (typeof data === 'object' && !Array.isArray(data) && data !== null) {
    const obj = data as Record<string, unknown>
    // Skip metadata keys, find the first key whose value is a non-empty array
    const skipKeys = new Set(['total', 'showing', 'namespace', 'ok', 'previous'])
    const arrayKey = Object.keys(obj).find(k => !skipKeys.has(k) && Array.isArray(obj[k]) && (obj[k] as unknown[]).length > 0)
    if (arrayKey) rows = obj[arrayKey]
  }

  if (Array.isArray(rows) && rows.length > 0 && typeof rows[0] === 'object' && rows[0] !== null) {
    const cols = Object.keys(rows[0] as Record<string, unknown>).slice(0, 8)
    return (
      <div className="mt-2 overflow-x-auto rounded-lg border border-slate-700 max-h-64 overflow-y-auto">
        <table className="w-full text-xs border-collapse">
          <thead className="sticky top-0 bg-slate-800 text-slate-300">
            <tr>{cols.map(c => <th key={c} className="px-2 py-1.5 text-left font-medium border-b border-slate-700 whitespace-nowrap">{c}</th>)}</tr>
          </thead>
          <tbody>
            {(rows as Record<string, unknown>[]).map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? 'bg-slate-900/50' : 'bg-slate-800/30'}>
                {cols.map(c => {
                  const v = row[c]
                  const str = v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v)
                  return <td key={c} className="px-2 py-1 text-slate-300 border-b border-slate-800 max-w-[200px] truncate" title={str}>{str}</td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  if (Array.isArray(rows) && rows.length > 0) {
    return (
      <div className="mt-2 max-h-48 overflow-y-auto rounded-lg border border-slate-700 bg-slate-900/50 p-2 space-y-0.5">
        {(rows as unknown[]).map((item, i) => (
          <div key={i} className="text-xs text-slate-300 px-1">{String(item)}</div>
        ))}
      </div>
    )
  }

  // Scalar or small object
  const str = typeof data === 'object' ? JSON.stringify(data, null, 2) : String(data)
  if (str.length < 500) {
    return (
      <pre className="mt-2 text-xs text-slate-300 bg-slate-900/50 border border-slate-700 rounded-lg p-2 overflow-x-auto max-h-40 overflow-y-auto">{str}</pre>
    )
  }
  return null
}

// ─── Tool trace card ──────────────────────────────────────────────────────────

function ToolTrace({ calls }: { calls: ToolCall[] }) {
  const hasData = calls.some(tc => tc.data !== undefined && tc.data !== null)
  const [open, setOpen] = useState(hasData)
  const [dataOpen, setDataOpen] = useState<Record<number, boolean>>({})
  if (!calls.length) return null

  return (
    <div className="mb-3">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300 transition-colors"
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <Wrench size={11} />
        {calls.length} tool call{calls.length > 1 ? 's' : ''}
        {hasData && !open && <span className="text-blue-400 ml-1">· results available</span>}
      </button>
      {open && (
        <div className="mt-2 space-y-2 pl-4 border-l border-slate-700">
          {calls.map((tc, i) => (
            <div key={i} className="text-xs">
              <div className="flex items-start gap-2">
                {tc.ok === false
                  ? <XCircle size={11} className="text-red-400 mt-0.5 flex-shrink-0" />
                  : tc.ok === true
                    ? <CheckCircle size={11} className="text-green-400 mt-0.5 flex-shrink-0" />
                    : <Loader2 size={11} className="text-blue-400 mt-0.5 flex-shrink-0 animate-spin" />
                }
                <div className="flex-1">
                  <span className="text-slate-300 font-medium">{toolLabel(tc.tool)}</span>
                  {Object.keys(tc.params || {}).length > 0 && (
                    <span className="text-slate-500 ml-1.5">
                      ({Object.entries(tc.params).map(([k, v]) => `${k}: ${v}`).join(', ')})
                    </span>
                  )}
                  {tc.summary && (
                    <span className={`ml-1.5 ${tc.ok === false ? 'text-red-400' : 'text-slate-400'}`}>
                      → {tc.summary}
                    </span>
                  )}
                  {tc.data !== undefined && tc.data !== null && (
                    <button
                      onClick={() => setDataOpen(prev => ({ ...prev, [i]: !prev[i] }))}
                      className="ml-2 text-blue-400 hover:text-blue-300 underline transition-colors"
                    >
                      {dataOpen[i] ? 'hide data' : 'show data'}
                    </button>
                  )}
                  {(dataOpen[i] ?? (tc.data !== undefined && tc.ok === true)) && tc.data !== undefined && (
                    <ToolDataTable data={tc.data} />
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Message bubble ───────────────────────────────────────────────────────────

function MessageBubble({ msg, streaming }: { msg: Message; streaming?: boolean }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end mb-4 animate-scale-in">
        <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-blue-600 px-4 py-2.5">
          <p className="text-sm text-white whitespace-pre-wrap">{msg.content}</p>
          <p className="text-xs text-blue-300 mt-1">{fmt(msg.timestamp)}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start mb-4 animate-scale-in">
      <div className="flex gap-2.5 max-w-[85%]">
        <div className="w-7 h-7 rounded-lg bg-vmware-blue flex items-center justify-center flex-shrink-0 mt-0.5">
          <Bot size={14} className="text-white" />
        </div>
        <div className="rounded-2xl rounded-tl-sm bg-vmware-card border border-vmware-border px-4 py-3 flex-1">
          {msg.tool_calls && msg.tool_calls.length > 0 && (
            <ToolTrace calls={msg.tool_calls} />
          )}
          <div className="text-sm text-slate-200 leading-relaxed prose-ai">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
              {msg.content}
            </ReactMarkdown>
          </div>
          {streaming && (
            <span className="inline-block w-1.5 h-3.5 bg-blue-400 animate-pulse ml-0.5 align-middle" />
          )}
          <p className="text-xs text-slate-500 mt-2">{fmt(msg.timestamp)}</p>
        </div>
      </div>
    </div>
  )
}

// ─── Sidebar conversation item ────────────────────────────────────────────────

function ConvItem({
  conv, active, onClick, onDelete,
}: {
  conv: Conversation
  active: boolean
  onClick: () => void
  onDelete: (e: React.MouseEvent) => void
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }}
      className={`w-full text-left px-3 py-2.5 rounded-lg group flex items-start gap-2 transition-colors cursor-pointer ${
        active ? 'bg-blue-600/20 border border-blue-500/30' : 'hover:bg-slate-800/50'
      }`}
    >
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-slate-200 truncate">{conv.title}</p>
        <p className="text-xs text-slate-500 mt-0.5">{fmt(conv.updated_at)}</p>
      </div>
      <button
        onClick={onDelete}
        aria-label={`Delete conversation: ${conv.title}`}
        className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-slate-500 hover:text-red-400 transition-all flex-shrink-0 mt-0.5"
      >
        <Trash2 size={12} />
      </button>
    </div>
  )
}

// ─── Main page ─────────────────────────────────────────────────────────────────

export function AgentPage() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [loadingConversations, setLoadingConversations] = useState(true)
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [provider, setProvider] = useState<string>('')
  const [isCloud, setIsCloud] = useState(true)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // pending tool calls for current streaming message
  const pendingToolCalls = useRef<ToolCall[]>([])

  // ── Load / refresh provider ──
  function refreshProvider() {
    fetch(`${API_BASE}/api/v1/config`).then(r => r.json()).then(cfg => {
      const p = cfg.agent_llm_provider || cfg.llm_provider || 'anthropic'
      setProvider(p)
      setIsCloud(CLOUD_PROVIDERS.includes(p))
    }).catch(() => {})
  }

  useEffect(() => {
    refreshProvider()
    fetch(`${API_BASE}/api/v1/agent/conversations`).then(r => r.json()).then(d => {
      setConversations(d.conversations || [])
    }).catch(() => {}).finally(() => setLoadingConversations(false))

    // Re-read provider whenever the window regains focus (user may have changed Settings)
    window.addEventListener('focus', refreshProvider)
    return () => window.removeEventListener('focus', refreshProvider)
  }, [])

  // ── Scroll to bottom on new messages ──
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // ── Load a conversation ──
  const loadConversation = useCallback(async (id: string) => {
    setActiveId(id)
    setLoadingMessages(true)
    setMessages([])
    try {
      const r = await fetch(`${API_BASE}/api/v1/agent/conversations/${id}`)
      const conv = await r.json()
      setMessages(conv.messages || [])
    } catch {
      setMessages([])
    } finally {
      setLoadingMessages(false)
    }
  }, [])

  // ── New conversation ──
  const newConversation = useCallback(() => {
    setActiveId(null)
    setMessages([])
    setInput('')
    pendingToolCalls.current = []
  }, [])

  // ── Delete conversation ──
  const deleteConversation = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    await fetch(`${API_BASE}/api/v1/agent/conversations/${id}`, { method: 'DELETE' })
    setConversations(prev => prev.filter(c => c.id !== id))
    if (activeId === id) newConversation()
  }, [activeId, newConversation])

  // ── Save conversation ──
  const saveConversation = useCallback(async (
    id: string, title: string, msgs: Message[]
  ) => {
    const body = { id, title, provider, messages: msgs }
    const r = await fetch(`${API_BASE}/api/v1/agent/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const saved = await r.json()
    setConversations(prev => {
      const existing = prev.find(c => c.id === id)
      if (existing) return prev.map(c => c.id === id ? { ...c, ...saved, messages: undefined } : c)
      return [{ ...saved, messages: undefined }, ...prev]
    })
  }, [provider])

  // ── Send message ──
  const sendMessage = useCallback(async (override?: string) => {
    const text = (override ?? input).trim()
    if (!text || streaming) return
    setInput('')

    // Re-read provider before sending — user may have changed Settings without a page reload
    try {
      const cfg = await fetch(`${API_BASE}/api/v1/config`).then(r => r.json())
      const currentProvider = cfg.agent_llm_provider || cfg.llm_provider || 'anthropic'
      if (currentProvider !== provider) {
        setProvider(currentProvider)
        setIsCloud(CLOUD_PROVIDERS.includes(currentProvider))
      }
    } catch { /* use existing provider */ }

    const convId = activeId || uuid()
    if (!activeId) setActiveId(convId)

    const userMsg: Message = {
      id: uuid(),
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    }

    // History = only text messages (no tool details) for LLM context
    const history = messages.map(m => ({ role: m.role, content: m.content }))

    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setStreaming(true)
    pendingToolCalls.current = []

    // Placeholder for assistant response
    const assistantId = uuid()
    const assistantMsg: Message = {
      id: assistantId,
      role: 'assistant',
      content: '',
      tool_calls: [],
      timestamp: new Date().toISOString(),
    }
    setMessages(prev => [...prev, assistantMsg])

    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    let fullText = ''
    const toolCallsSnapshot: ToolCall[] = []

    try {
      const resp = await fetch(`${API_BASE}/api/v1/agent/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history }),
        signal: ctrl.signal,
      })

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          let event: Record<string, unknown>
          try { event = JSON.parse(line.slice(6)) } catch { continue }

          if (event.type === 'tool_call') {
            const tc: ToolCall = { tool: event.tool as string, params: (event.params as Record<string, unknown>) || {} }
            toolCallsSnapshot.push(tc)
            setMessages(prev => prev.map(m =>
              m.id === assistantId ? { ...m, tool_calls: [...toolCallsSnapshot] } : m
            ))
          } else if (event.type === 'tool_result') {
            const reversed = [...toolCallsSnapshot].reverse()
            const revIdx = reversed.findIndex((tc: ToolCall) => tc.tool === event.tool && tc.ok === undefined)
            const idx = revIdx >= 0 ? toolCallsSnapshot.length - 1 - revIdx : -1
            if (idx >= 0) {
              toolCallsSnapshot[idx] = {
                ...toolCallsSnapshot[idx],
                summary: event.summary as string,
                ok: event.ok as boolean,
                data: event.data,
              }
              setMessages(prev => prev.map(m =>
                m.id === assistantId ? { ...m, tool_calls: [...toolCallsSnapshot] } : m
              ))
            }
          } else if (event.type === 'token') {
            fullText += event.text as string
            setMessages(prev => prev.map(m =>
              m.id === assistantId ? { ...m, content: fullText } : m
            ))
          } else if (event.type === 'error') {
            fullText = `⚠ ${event.message}`
            setMessages(prev => prev.map(m =>
              m.id === assistantId ? { ...m, content: fullText } : m
            ))
          } else if (event.type === 'done') {
            break
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        fullText = `⚠ Connection error: ${(err as Error).message}`
        setMessages(prev => prev.map(m =>
          m.id === assistantId ? { ...m, content: fullText } : m
        ))
      }
    }

    setStreaming(false)

    // Persist conversation including tool result data so tables survive navigation
    const finalMsgs: Message[] = [
      ...newMessages,
      { id: assistantId, role: 'assistant', content: fullText,
        tool_calls: toolCallsSnapshot,
        timestamp: new Date().toISOString() },
    ]
    const title = text.length > 60 ? text.slice(0, 57) + '…' : text
    await saveConversation(convId, title, finalMsgs)
  }, [input, streaming, activeId, messages, saveConversation])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const SUGGESTIONS = [
    'What is the overall health of my VCF environment?',
    'Are any pods in vcf-ai-ops crashing or not ready?',
    'Show me the latest Kubernetes events — any warnings or errors?',
    'Which ESXi hosts are connected and how many are there?',
    'How many VMs are powered on vs off?',
    'List all supervisor namespaces and their resource usage',
    'What VCF domains do I have and what is their status?',
    'Are there any upgrade bundles available for VCF 9.1?',
  ]

  return (
    <div className="h-screen flex flex-col bg-vmware-dark overflow-hidden">
      <div className="flex flex-1 min-h-0">
        {/* Sidebar */}
        <div className="w-60 flex-shrink-0 border-r border-vmware-border bg-vmware-card/40 flex flex-col">
          <div className="p-3 border-b border-vmware-border">
            <button
              onClick={newConversation}
              className="w-full flex items-center gap-2 text-xs font-medium text-white bg-vmware-blue hover:bg-blue-600 transition-colors px-3 py-2 rounded-lg"
            >
              <Plus size={13} /> New conversation
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
            {loadingConversations ? (
              <div className="flex justify-center mt-6">
                <Loader2 size={16} className="animate-spin text-slate-500" />
              </div>
            ) : conversations.length === 0 ? (
              <p className="text-xs text-slate-500 text-center mt-6 px-3">No conversations yet</p>
            ) : (
              conversations.map(conv => (
                <ConvItem
                  key={conv.id}
                  conv={conv}
                  active={conv.id === activeId}
                  onClick={() => loadConversation(conv.id)}
                  onDelete={(e) => deleteConversation(conv.id, e)}
                />
              ))
            )}
          </div>
        </div>

        {/* Chat area */}
        <div className="flex-1 flex flex-col min-w-0">

          {/* Not-cloud warning */}
          {!isCloud && (
            <div className="flex items-center gap-2 px-5 py-2.5 bg-yellow-900/20 border-b border-yellow-800/40 text-xs text-yellow-400">
              <AlertTriangle size={13} />
              MCP AI Agent requires a cloud LLM. Your current provider (<strong>{provider}</strong>) doesn't support tool calling.
              <button onClick={() => { window.location.hash = '#/settings' }} className="underline ml-1">Switch in Settings →</button>
            </div>
          )}

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-6 py-5">
            {loadingMessages ? (
              <div className="flex items-center justify-center h-full gap-2 text-slate-500">
                <Loader2 size={18} className="animate-spin" />
                <span className="text-sm">Loading conversation…</span>
              </div>
            ) : messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full gap-6 max-w-xl mx-auto text-center">
                <div className="w-14 h-14 rounded-2xl bg-vmware-blue/20 border border-vmware-blue/40 flex items-center justify-center">
                  <Bot size={28} className="text-vmware-blue" />
                </div>
                <div>
                  <h2 className="text-white font-semibold text-base">MCP AI Agent</h2>
                  <p className="text-slate-400 text-sm mt-1">
                    Ask me anything about your VCF environment or this platform's Kubernetes pods. I'll query vCenter, SDDC Manager, and the VKS cluster in real time — including pod logs, events, and deployment status.
                  </p>
                </div>
                <div className="grid grid-cols-1 gap-2 w-full">
                  {SUGGESTIONS.map(s => (
                    <button
                      key={s}
                      disabled={!isCloud || streaming}
                      onClick={() => { setInput(s); sendMessage(s) }}
                      className="text-left text-xs text-slate-300 bg-vmware-card border border-vmware-border hover:border-slate-500 hover:text-white px-3.5 py-2.5 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
                {messages.map((msg, i) => (
                  <MessageBubble
                    key={msg.id}
                    msg={msg}
                    streaming={streaming && i === messages.length - 1 && msg.role === 'assistant'}
                  />
                ))}
                <div ref={bottomRef} />
              </>
            )}
          </div>

          {/* Input */}
          <div className="border-t border-vmware-border bg-vmware-card/60 px-5 py-4">
            <div className="flex gap-3 items-end max-w-4xl mx-auto">
              <textarea
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={streaming || !isCloud}
                placeholder={isCloud ? 'Ask about your VCF environment… (Enter to send, Shift+Enter for newline)' : 'Cloud LLM required'}
                rows={1}
                className="flex-1 resize-none bg-vmware-dark border border-vmware-border rounded-xl px-4 py-2.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors disabled:opacity-40"
                style={{ minHeight: '42px', maxHeight: '120px' }}
                onInput={e => {
                  const el = e.currentTarget
                  el.style.height = 'auto'
                  el.style.height = Math.min(el.scrollHeight, 120) + 'px'
                }}
              />
              <button
                onClick={() => sendMessage()}
                disabled={!input.trim() || streaming || !isCloud}
                className="flex-shrink-0 w-10 h-10 rounded-xl bg-vmware-blue hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center"
              >
                {streaming
                  ? <Loader2 size={16} className="text-white animate-spin" />
                  : <Send size={16} className="text-white" />
                }
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
