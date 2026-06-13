import { useState, useEffect } from 'react'
import {
  Bell, Plus, Trash2, Zap, Webhook, TestTube, CheckCircle,
  AlertTriangle, Settings, Activity, Server, Archive, Upload, Laptop,
  BotMessageSquare, Terminal, Radar, TrendingUp, Users,
  ToggleLeft, ToggleRight, ChevronDown,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface AlertChannel {
  id: string
  name: string
  type: 'slack' | 'teams' | 'webhook' | 'pagerduty'
  config: { webhook_url?: string; url?: string; routing_key?: string }
  created_at: string
}

interface AlertRule {
  id: string
  name: string
  event_type: string
  condition: { field?: string; op?: string; threshold?: number | string }
  channel_ids: string[]
  enabled: boolean
  created_at: string
}

const EVENT_TYPES = [
  { value: 'score_critical', label: 'VCF Score critical (readiness drops below threshold)' },
  { value: 'score_update', label: 'VCF Score changed' },
  { value: 'vuln_critical', label: 'Critical vuln finding detected' },
  { value: 'scan_complete', label: 'Network scan completed' },
]

const CHANNEL_ICONS: Record<string, React.ElementType> = {
  slack: Zap,
  teams: Zap,
  webhook: Webhook,
  pagerduty: Bell,
}

function NavBar({ onBack }: { onBack: () => void }) {
  return (
    <div className="h-11 bg-vmware-navy/95 backdrop-blur border-b border-vmware-border flex items-center px-4 gap-2 shrink-0">
      <span className="text-xs font-bold text-vmware-blue tracking-widest uppercase select-none">MCO</span>
      <span className="text-slate-700">|</span>
      {[
        { label: 'Fleet', icon: Server, hash: '#/fleet' },
        { label: 'Workspace', icon: Settings, hash: '#/workspace' },
        { label: 'Analysis', icon: Activity, hash: '#/analysis' },
        { label: 'Agent', icon: BotMessageSquare, hash: '#/agent' },
        { label: 'Bulk', icon: Upload, hash: '#/bulk' },
        { label: 'Guest', icon: Laptop, hash: '#/guest' },
        { label: 'Kubectl', icon: Terminal, hash: '#/kubectl' },
        { label: 'Discovery', icon: Radar, hash: '#/discovery' },
        { label: 'Directory', icon: Users, hash: '#/directory' },
        { label: 'Trends', icon: TrendingUp, hash: '#/trends' },
        { label: 'Archive', icon: Archive, hash: '#/archive' },
      ].map(({ label, icon: Icon, hash }) => (
        <a key={label} href={hash}
          className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-white px-2 py-1 rounded hover:bg-slate-800/60 transition-colors">
          <Icon size={11} /> {label}
        </a>
      ))}
      <span className="flex items-center gap-1 text-[11px] text-vmware-blue px-2 py-1 rounded bg-blue-900/30 font-semibold ml-1">
        <Bell size={11} /> Alerts
      </span>
      <div className="flex-1" />
      <a href="#/settings" className="text-slate-500 hover:text-slate-300 transition-colors">
        <Settings size={14} />
      </a>
    </div>
  )
}

function ChannelForm({ onSave, onCancel }: { onSave: (ch: Partial<AlertChannel>) => void; onCancel: () => void }) {
  const [name, setName] = useState('')
  const [type, setType] = useState<AlertChannel['type']>('slack')
  const [url, setUrl] = useState('')
  const [routingKey, setRoutingKey] = useState('')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="bg-[#0d1320] border border-vmware-border rounded-xl p-6 w-full max-w-md shadow-2xl">
        <h3 className="text-sm font-bold text-white mb-4">Add Alert Channel</h3>
        <div className="space-y-3">
          <div>
            <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Name</label>
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. #ops-alerts"
              className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-600" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Type</label>
            <select value={type} onChange={e => setType(e.target.value as AlertChannel['type'])}
              className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-blue-600">
              <option value="slack">Slack Incoming Webhook</option>
              <option value="teams">Microsoft Teams</option>
              <option value="webhook">Generic Webhook (JSON POST)</option>
              <option value="pagerduty">PagerDuty Events API</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">
              {type === 'pagerduty' ? 'Integration Key' : 'Webhook URL'}
            </label>
            {type === 'pagerduty'
              ? <input value={routingKey} onChange={e => setRoutingKey(e.target.value)}
                  placeholder="abc123..."
                  className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-600" />
              : <input value={url} onChange={e => setUrl(e.target.value)}
                  placeholder="https://hooks.slack.com/..."
                  className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-600" />
            }
          </div>
        </div>
        <div className="flex gap-2 justify-end mt-5">
          <button onClick={onCancel} className="px-4 py-2 text-xs text-slate-400 hover:text-white border border-vmware-border rounded-lg transition-colors">Cancel</button>
          <button
            onClick={() => onSave({
              name, type,
              config: type === 'pagerduty' ? { routing_key: routingKey } : { webhook_url: url },
            })}
            disabled={!name || (type !== 'pagerduty' ? !url : !routingKey)}
            className="px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg transition-colors">
            Save Channel
          </button>
        </div>
      </div>
    </div>
  )
}

function RuleForm({ channels, onSave, onCancel }: {
  channels: AlertChannel[]; onSave: (r: Partial<AlertRule>) => void; onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [eventType, setEventType] = useState('score_critical')
  const [threshold, setThreshold] = useState('40')
  const [selectedChannels, setSelectedChannels] = useState<string[]>([])

  const needsThreshold = eventType === 'score_critical'

  function toggleChannel(id: string) {
    setSelectedChannels(prev => prev.includes(id) ? prev.filter(c => c !== id) : [...prev, id])
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="bg-[#0d1320] border border-vmware-border rounded-xl p-6 w-full max-w-md shadow-2xl">
        <h3 className="text-sm font-bold text-white mb-4">Add Alert Rule</h3>
        <div className="space-y-3">
          <div>
            <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Rule Name</label>
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. Critical score alert"
              className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-blue-600" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Event Type</label>
            <select value={eventType} onChange={e => setEventType(e.target.value)}
              className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-blue-600">
              {EVENT_TYPES.map(et => <option key={et.value} value={et.value}>{et.label}</option>)}
            </select>
          </div>
          {needsThreshold && (
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">
                Fire when score is below
              </label>
              <input type="number" value={threshold} onChange={e => setThreshold(e.target.value)}
                min={0} max={100}
                className="w-full bg-slate-900/60 border border-vmware-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-blue-600" />
            </div>
          )}
          <div>
            <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Send to channels</label>
            {channels.length === 0
              ? <p className="text-xs text-slate-500">No channels configured yet — add one first</p>
              : <div className="space-y-1">
                  {channels.map(ch => {
                    const Icon = CHANNEL_ICONS[ch.type] || Bell
                    const selected = selectedChannels.includes(ch.id)
                    return (
                      <button key={ch.id} onClick={() => toggleChannel(ch.id)}
                        className={`w-full flex items-center gap-2 text-xs px-3 py-2 rounded-lg border transition-colors ${selected ? 'border-blue-600 bg-blue-900/20 text-blue-300' : 'border-vmware-border text-slate-400 hover:border-slate-500'}`}>
                        <Icon size={11} />
                        {ch.name}
                        <span className="ml-auto text-[10px] text-slate-600">{ch.type}</span>
                      </button>
                    )
                  })}
                </div>
            }
          </div>
        </div>
        <div className="flex gap-2 justify-end mt-5">
          <button onClick={onCancel} className="px-4 py-2 text-xs text-slate-400 hover:text-white border border-vmware-border rounded-lg transition-colors">Cancel</button>
          <button
            onClick={() => onSave({
              name, event_type: eventType,
              condition: needsThreshold ? { field: 'readiness_score', op: 'lt', threshold: Number(threshold) } : {},
              channel_ids: selectedChannels,
            })}
            disabled={!name || selectedChannels.length === 0}
            className="px-4 py-2 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg transition-colors">
            Save Rule
          </button>
        </div>
      </div>
    </div>
  )
}

export function AlertsPage({ onBack }: { onBack: () => void }) {
  const [channels, setChannels] = useState<AlertChannel[]>([])
  const [rules, setRules] = useState<AlertRule[]>([])
  const [loading, setLoading] = useState(true)
  const [showChannelForm, setShowChannelForm] = useState(false)
  const [showRuleForm, setShowRuleForm] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<Record<string, boolean | null>>({})

  async function load() {
    setLoading(true)
    try {
      const [ch, ru] = await Promise.all([
        fetch(`${API_BASE}/api/v1/alert-channels`).then(r => r.json()),
        fetch(`${API_BASE}/api/v1/alert-rules`).then(r => r.json()),
      ])
      setChannels(ch.channels || [])
      setRules(ru.rules || [])
    } catch {}
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  async function saveChannel(ch: Partial<AlertChannel>) {
    await fetch(`${API_BASE}/api/v1/alert-channels`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ch),
    })
    setShowChannelForm(false)
    load()
  }

  async function deleteChannel(id: string) {
    await fetch(`${API_BASE}/api/v1/alert-channels/${id}`, { method: 'DELETE' })
    load()
  }

  async function testChannel(id: string) {
    setTesting(id)
    try {
      const r = await fetch(`${API_BASE}/api/v1/alert-channels/${id}/test`, { method: 'POST' })
      setTestResult(prev => ({ ...prev, [id]: r.ok }))
    } catch {
      setTestResult(prev => ({ ...prev, [id]: false }))
    }
    setTesting(null)
    setTimeout(() => setTestResult(prev => { const n = { ...prev }; delete n[id]; return n }), 5000)
  }

  async function saveRule(rule: Partial<AlertRule>) {
    await fetch(`${API_BASE}/api/v1/alert-rules`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rule),
    })
    setShowRuleForm(false)
    load()
  }

  async function toggleRule(rule: AlertRule) {
    await fetch(`${API_BASE}/api/v1/alert-rules/${rule.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !rule.enabled }),
    })
    load()
  }

  async function deleteRule(id: string) {
    await fetch(`${API_BASE}/api/v1/alert-rules/${id}`, { method: 'DELETE' })
    load()
  }

  const channelMap = Object.fromEntries(channels.map(c => [c.id, c]))

  return (
    <div className="flex flex-col h-screen bg-vmware-dark text-white overflow-hidden">
      <NavBar onBack={onBack} />
      {showChannelForm && <ChannelForm onSave={saveChannel} onCancel={() => setShowChannelForm(false)} />}
      {showRuleForm && <RuleForm channels={channels} onSave={saveRule} onCancel={() => setShowRuleForm(false)} />}

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">

          {/* Channels */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-white flex items-center gap-2">
                <Bell size={14} className="text-blue-400" /> Alert Channels
              </h2>
              <button onClick={() => setShowChannelForm(true)}
                className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/30 text-blue-400 border border-blue-800/50 rounded-lg transition-colors">
                <Plus size={11} /> Add Channel
              </button>
            </div>
            {loading ? (
              <div className="text-xs text-slate-500 py-4 text-center">Loading…</div>
            ) : channels.length === 0 ? (
              <div className="rounded-xl border border-vmware-border bg-vmware-card p-6 text-center">
                <Bell size={24} className="text-slate-600 mx-auto mb-2" />
                <p className="text-sm text-slate-500">No channels configured</p>
                <p className="text-xs text-slate-600 mt-1">Add a Slack, Teams, or webhook channel to receive alerts</p>
              </div>
            ) : (
              <div className="space-y-2">
                {channels.map(ch => {
                  const Icon = CHANNEL_ICONS[ch.type] || Bell
                  const tr = testResult[ch.id]
                  return (
                    <div key={ch.id} className="rounded-xl border border-vmware-border bg-vmware-card px-4 py-3 flex items-center gap-3">
                      <div className="w-7 h-7 rounded-lg bg-blue-900/30 flex items-center justify-center shrink-0">
                        <Icon size={13} className="text-blue-400" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-semibold text-white">{ch.name}</p>
                        <p className="text-[10px] text-slate-500 capitalize">{ch.type}</p>
                      </div>
                      {tr !== undefined && (
                        <span className={`text-[10px] ${tr ? 'text-green-400' : 'text-red-400'}`}>
                          {tr ? '✓ Delivered' : '✗ Failed'}
                        </span>
                      )}
                      <button onClick={() => testChannel(ch.id)} disabled={testing === ch.id}
                        className="flex items-center gap-1 text-[10px] px-2.5 py-1.5 text-slate-400 hover:text-white border border-vmware-border hover:border-slate-500 rounded-lg transition-colors">
                        {testing === ch.id ? '…' : <><TestTube size={10} /> Test</>}
                      </button>
                      <button onClick={() => deleteChannel(ch.id)}
                        className="text-slate-600 hover:text-red-400 transition-colors p-1">
                        <Trash2 size={13} />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Rules */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-white flex items-center gap-2">
                <Zap size={14} className="text-amber-400" /> Alert Rules
              </h2>
              <button onClick={() => setShowRuleForm(true)}
                className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 bg-amber-600/20 hover:bg-amber-600/30 text-amber-400 border border-amber-800/50 rounded-lg transition-colors">
                <Plus size={11} /> Add Rule
              </button>
            </div>
            {loading ? (
              <div className="text-xs text-slate-500 py-4 text-center">Loading…</div>
            ) : rules.length === 0 ? (
              <div className="rounded-xl border border-vmware-border bg-vmware-card p-6 text-center">
                <Zap size={24} className="text-slate-600 mx-auto mb-2" />
                <p className="text-sm text-slate-500">No alert rules</p>
                <p className="text-xs text-slate-600 mt-1">Rules define when to fire an alert and to which channels</p>
              </div>
            ) : (
              <div className="space-y-2">
                {rules.map(rule => {
                  const channelNames = rule.channel_ids.map(id => channelMap[id]?.name || id)
                  const eventLabel = EVENT_TYPES.find(e => e.value === rule.event_type)?.label || rule.event_type
                  return (
                    <div key={rule.id} className={`rounded-xl border bg-vmware-card px-4 py-3 ${rule.enabled ? 'border-vmware-border' : 'border-slate-800/50 opacity-60'}`}>
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-semibold text-white">{rule.name}</p>
                          <p className="text-[10px] text-slate-400 mt-0.5">{eventLabel}</p>
                          {rule.condition?.threshold !== undefined && (
                            <p className="text-[10px] text-slate-500 mt-0.5">
                              When {rule.condition.field} {rule.condition.op} {rule.condition.threshold}
                            </p>
                          )}
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {channelNames.map((n, i) => (
                              <span key={i} className="text-[10px] px-1.5 py-0.5 bg-blue-900/20 text-blue-400 rounded border border-blue-900/30">{n}</span>
                            ))}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <button onClick={() => toggleRule(rule)} className="text-slate-500 hover:text-white transition-colors">
                            {rule.enabled ? <ToggleRight size={18} className="text-green-400" /> : <ToggleLeft size={18} />}
                          </button>
                          <button onClick={() => deleteRule(rule.id)} className="text-slate-600 hover:text-red-400 transition-colors p-1">
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

        </div>
      </div>
    </div>
  )
}
