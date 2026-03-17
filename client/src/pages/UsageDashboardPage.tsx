/**
 * Admin Usage Dashboard
 *
 * Shows Anthropic API, local STT, and local TTS usage over time.
 * Requires is_admin=true on the session (enforced by BFF; this page
 * also redirects non-admins client-side).
 *
 * Layout:
 *   Summary cards (today / week / month)
 *   Time-series charts: cost, tokens, STT audio, TTS chars
 *   Data tables: by call type, by model, by user
 *   Live event feed (1 s polling)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer,
} from 'recharts'
import { cn } from '@/lib/utils'

// ── types ──────────────────────────────────────────────────────────────────────

interface Totals {
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  cost_usd: number
  audio_seconds: number
  transcription_ms: number
  tts_characters: number
  tts_audio_seconds: number
  tts_synthesis_ms: number
}

interface SeriesRow {
  minute_ts?: number
  hour_ts?: number
  user_id: string
  event_type: string
  call_type: string
  model: string
  stt_model: string
  stt_language: string
  tts_voice: string
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  cost_usd: number
  audio_seconds: number
  transcription_ms: number
  tts_characters: number
  tts_audio_seconds: number
  tts_synthesis_ms: number
}

interface LiveEvent {
  id: number
  ts: number
  event_type: string
  call_type: string
  model: string
  stt_model: string
  cost_usd: number
  audio_seconds: number
  tts_voice: string
  tts_characters: number
  user_id: string
}

interface UserRow {
  id: string
  email: string
  display_name: string | null
  is_admin: number
}

// ── formatting helpers ─────────────────────────────────────────────────────────

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(Math.round(n))
}
function fmtCost(usd: number): string {
  if (usd === 0) return '$0'
  if (usd < 0.001) return `${(usd * 100).toFixed(4)}¢`
  return `$${usd.toFixed(4)}`
}
function fmtSecs(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`
  return `${(s / 60).toFixed(1)}m`
}
function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString()
}
function minuteLabel(ts: number): string {
  const d = new Date(ts * 1000)
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}

// ── API helpers ────────────────────────────────────────────────────────────────

async function apiFetch(path: string, sessionId: string, params?: Record<string, string>) {
  const url = new URL(`/api${path}`, window.location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  const r = await fetch(url.toString(), { headers: { 'X-Session-Id': sessionId } })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

// ── call-type color palette ────────────────────────────────────────────────────

const CALL_TYPE_COLORS: Record<string, string> = {
  teach_turn:             'hsl(217 91% 60%)',
  tts_prep:               'hsl(266 67% 55%)',
  intro_turn:             'hsl(142 71% 45%)',
  episode_condensation:   'hsl(31 100% 55%)',
  generate_instructions:  'hsl(349 89% 60%)',
}
const _FALLBACK = ['hsl(200 80% 55%)', 'hsl(60 80% 50%)', 'hsl(180 60% 45%)', 'hsl(320 60% 55%)']
function callTypeColor(ct: string, idx: number): string {
  return CALL_TYPE_COLORS[ct] ?? _FALLBACK[idx % _FALLBACK.length]
}

// ── call-type tooltip with token-breakdown pie ─────────────────────────────────

const TOKEN_PIE_COLORS = {
  'Input':       'hsl(217 91% 60%)',
  'Output':      'hsl(266 67% 55%)',
  'Cache read':  'hsl(142 71% 45%)',
  'Cache write': 'hsl(31 100% 55%)',
}

interface TokenEntry { input: number; output: number; cr: number; cw: number }

interface CallTypeTooltipProps {
  active?: boolean
  payload?: Array<{ dataKey: string; value: number; fill: string; payload: { ts: number } }>
  label?: string
  tokenLookup: Map<string, TokenEntry>
  activeCallType: string | null
}

function CallTypeTooltip({ active, payload, label, tokenLookup, activeCallType }: CallTypeTooltipProps) {
  if (!active || !payload?.length) return null

  const ts = payload[0]?.payload?.ts
  const tokens = (activeCallType != null && ts != null)
    ? tokenLookup.get(`${ts}|${activeCallType}`) ?? null
    : null

  const tokenSegments = tokens
    ? ([
        { name: 'Input' as const,       value: tokens.input  },
        { name: 'Output' as const,      value: tokens.output },
        { name: 'Cache read' as const,  value: tokens.cr     },
        { name: 'Cache write' as const, value: tokens.cw     },
      ] as const).filter(d => d.value > 0)
    : []

  return (
    <div className="rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2.5 py-2 shadow-md text-xs">
      <p className="text-[hsl(var(--muted-foreground))] mb-1">{label}</p>
      <div className="space-y-0.5">
        {payload.map(p => (
          <div key={p.dataKey}
            className={cn('flex justify-between gap-4', activeCallType === p.dataKey ? 'font-semibold' : '')}>
            <span style={{ color: p.fill }}>{p.dataKey}</span>
            <span className="tabular-nums">{fmtCost(p.value)}</span>
          </div>
        ))}
      </div>
      {tokenSegments.length > 0 && (
        <div className="border-t border-[hsl(var(--border))] pt-1.5 mt-1.5">
          <p className="text-[hsl(var(--muted-foreground))] mb-1">{activeCallType} · tokens</p>
          <div className="flex h-2 rounded overflow-hidden w-full">
            {tokenSegments.map(d => (
              <div key={d.name} style={{ flex: d.value, background: TOKEN_PIE_COLORS[d.name] }} />
            ))}
          </div>
          <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 mt-1 text-[hsl(var(--muted-foreground))]">
            {tokenSegments.map(d => (
              <span key={d.name} className="flex items-center gap-1">
                <span className="inline-block w-1.5 h-1.5 rounded-sm flex-shrink-0"
                  style={{ background: TOKEN_PIE_COLORS[d.name] }} />
                {d.name} {fmt(d.value)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── sub-components ─────────────────────────────────────────────────────────────

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
      <p className="text-xs text-[hsl(var(--muted-foreground))] mb-1">{label}</p>
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">{sub}</p>}
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="text-sm font-semibold mt-6 mb-3">{children}</h2>
}

function DataTable({
  cols, rows,
}: {
  cols: { key: string; label: string; align?: 'right' }[]
  rows: Record<string, React.ReactNode>[]
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[hsl(var(--border))]">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[hsl(var(--border))] bg-[hsl(var(--muted))]">
            {cols.map(c => (
              <th key={c.key}
                className={cn('px-3 py-2 font-medium text-[hsl(var(--muted-foreground))]',
                  c.align === 'right' ? 'text-right' : 'text-left')}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={cols.length} className="px-3 py-4 text-center text-[hsl(var(--muted-foreground))]">
                No data yet.
              </td>
            </tr>
          )}
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-[hsl(var(--border))] last:border-0 hover:bg-[hsl(var(--muted)/0.5)]">
              {cols.map(c => (
                <td key={c.key}
                  className={cn('px-3 py-2 tabular-nums', c.align === 'right' ? 'text-right' : '')}>
                  {row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── live feed (isolated so 1-second polling never re-renders charts) ───────────

function LiveFeed({ sessionId }: { sessionId: string }) {
  const [events, setEvents] = useState<LiveEvent[]>([])
  const [lastUpdate, setLastUpdate] = useState(0)
  const eventsRef = useRef<LiveEvent[]>([])

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const d = await apiFetch('/admin/usage/live', sessionId)
        if (cancelled) return
        const incoming: LiveEvent[] = d.events ?? []
        const existingIds = new Set(eventsRef.current.map(e => e.id))
        const newOnes = incoming.filter(e => !existingIds.has(e.id))
        if (newOnes.length > 0) {
          const merged = [...newOnes, ...eventsRef.current].slice(0, 200)
          eventsRef.current = merged
          setEvents(merged)
        }
        setLastUpdate(Date.now())
      } catch { /* non-fatal */ }
    }
    poll()
    const id = setInterval(poll, 1000)
    return () => { cancelled = true; clearInterval(id) }
  }, [sessionId])

  return (
    <>
      <h2 className="text-sm font-semibold mt-6 mb-3 flex items-center gap-2">
        Live events (last 90 s)
        {lastUpdate > 0 && (
          <span className="font-normal text-[hsl(var(--muted-foreground))]">
            · {new Date(lastUpdate).toLocaleTimeString()}
          </span>
        )}
      </h2>
      <div className="rounded-lg border border-[hsl(var(--border))] overflow-hidden">
        <div className="max-h-64 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-[hsl(var(--muted))] border-b border-[hsl(var(--border))]">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Time</th>
                <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Type</th>
                <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Detail</th>
                <th className="px-3 py-2 text-right font-medium text-[hsl(var(--muted-foreground))]">Value</th>
              </tr>
            </thead>
            <tbody>
              {events.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center text-[hsl(var(--muted-foreground))]">
                    Waiting for events…
                  </td>
                </tr>
              )}
              {events.map(e => (
                <tr key={e.id} className="border-b border-[hsl(var(--border))] last:border-0">
                  <td className="px-3 py-1.5 text-[hsl(var(--muted-foreground))]">{fmtTime(e.ts)}</td>
                  <td className="px-3 py-1.5">
                    <span className={cn('px-1.5 py-0.5 rounded text-[10px]',
                      e.event_type === 'api' ? 'bg-blue-500/15 text-blue-400' :
                      e.event_type === 'stt' ? 'bg-green-500/15 text-green-400' :
                      'bg-purple-500/15 text-purple-400'
                    )}>
                      {e.event_type}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-[hsl(var(--muted-foreground))]">
                    {e.event_type === 'api' ? `${e.call_type} · ${e.model}` :
                     e.event_type === 'stt' ? `${fmtSecs(e.audio_seconds)} audio · ${e.stt_model || 'stt'}` :
                     `${fmt(e.tts_characters)} chars · ${e.tts_voice || 'tts'}`}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {e.cost_usd > 0 ? fmtCost(e.cost_usd) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────

interface Props {
  sessionId: string
  isAdmin: boolean
}

export function UsageDashboardPage({ sessionId, isAdmin }: Props) {
  const navigate = useNavigate()

  // Redirect non-admins immediately
  useEffect(() => {
    if (!isAdmin) navigate('/', { replace: true })
  }, [isAdmin, navigate])

  // ── state ─────────────────────────────────────────────────────────────────

  const [window, setWindow] = useState<'today' | 'week' | 'month' | 'all'>('today')
  const [totals, setTotals] = useState<Record<string, Totals>>({})
  const [series, setSeries] = useState<SeriesRow[]>([])
  const [users, setUsers] = useState<UserRow[]>([])
  const [filterUser, setFilterUser] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [activeCallType, setActiveCallType] = useState<string | null>(null)

  // ── data fetching ─────────────────────────────────────────────────────────

  const fetchTotals = useCallback(async (w: string) => {
    try {
      const d = await apiFetch('/admin/usage/totals', sessionId, {
        window: w,
        ...(filterUser ? { user_id: filterUser } : {}),
      })
      setTotals(prev => ({ ...prev, [w]: d.totals }))
    } catch { /* non-fatal */ }
  }, [sessionId, filterUser])

  const fetchSeries = useCallback(async () => {
    const now = Math.floor(Date.now() / 1000)
    const windowMap = { today: 86400, week: 7 * 86400, month: 30 * 86400, all: 90 * 86400 }
    const from_ts = now - (windowMap[window] ?? 86400)
    try {
      const d = await apiFetch('/admin/usage/series', sessionId, {
        from_ts: String(from_ts), to_ts: String(now),
        granularity: window === 'all' ? 'hour' : 'minute',
        ...(filterUser ? { user_id: filterUser } : {}),
      })
      setSeries(d.rows ?? [])
    } catch { /* non-fatal */ }
  }, [sessionId, window, filterUser])

  const fetchUsers = useCallback(async () => {
    try {
      const d = await apiFetch('/admin/usage/users', sessionId)
      setUsers(d.users ?? [])
    } catch { /* non-fatal */ }
  }, [sessionId])

  // Initial load
  useEffect(() => {
    if (!isAdmin) return
    Promise.all([
      fetchTotals('today'), fetchTotals('week'), fetchTotals('month'),
      fetchSeries(), fetchUsers(),
    ]).finally(() => setLoading(false))
  }, [isAdmin]) // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fetch series when window/filter changes
  useEffect(() => {
    if (!isAdmin) return
    fetchSeries()
    fetchTotals(window)
  }, [window, filterUser]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── chart data (memoized — only recomputed when series changes) ───────────

  const chartData = useMemo(() => {
    const byTs: Record<number, { cost_usd: number; input_tokens: number; output_tokens: number; audio_seconds: number; tts_characters: number }> = {}
    for (const r of series) {
      const ts = r.minute_ts ?? r.hour_ts ?? 0
      if (!byTs[ts]) byTs[ts] = { cost_usd: 0, input_tokens: 0, output_tokens: 0, audio_seconds: 0, tts_characters: 0 }
      byTs[ts].cost_usd += r.cost_usd
      byTs[ts].input_tokens += r.input_tokens
      byTs[ts].output_tokens += r.output_tokens
      byTs[ts].audio_seconds += r.audio_seconds
      byTs[ts].tts_characters += r.tts_characters
    }
    return Object.entries(byTs)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([ts, v]) => ({ ts: Number(ts), label: minuteLabel(Number(ts)), ...v }))
  }, [series])

  const apiCallTypes = useMemo(() =>
    [...new Set(series.filter(r => r.event_type === 'api').map(r => r.call_type || 'unknown'))].sort()
  , [series])

  const callTypeChartData = useMemo(() => {
    const byTs: Record<number, Record<string, number>> = {}
    for (const r of series.filter(r => r.event_type === 'api')) {
      const ts = r.minute_ts ?? r.hour_ts ?? 0
      const k = r.call_type || 'unknown'
      if (!byTs[ts]) byTs[ts] = {}
      byTs[ts][k] = (byTs[ts][k] ?? 0) + r.cost_usd
    }
    return Object.entries(byTs)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([ts, v]) => ({ ts: Number(ts), label: minuteLabel(Number(ts)), ...v }))
  }, [series])

  const eventTypeChartData = useMemo(() => {
    const byTs: Record<number, { api_cost: number; stt_cost: number; tts_cost: number }> = {}
    for (const r of series) {
      const ts = r.minute_ts ?? r.hour_ts ?? 0
      if (!byTs[ts]) byTs[ts] = { api_cost: 0, stt_cost: 0, tts_cost: 0 }
      if (r.event_type === 'api') byTs[ts].api_cost += r.cost_usd
      if (r.event_type === 'stt') byTs[ts].stt_cost += r.cost_usd
      if (r.event_type === 'tts') byTs[ts].tts_cost += r.cost_usd
    }
    return Object.entries(byTs)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([ts, v]) => ({ ts: Number(ts), label: minuteLabel(Number(ts)), ...v }))
  }, [series])

  const costByEventType = useMemo(() => {
    const costs = { api: 0, stt: 0, tts: 0 }
    for (const r of series) {
      if (r.event_type === 'api') costs.api += r.cost_usd
      if (r.event_type === 'stt') costs.stt += r.cost_usd
      if (r.event_type === 'tts') costs.tts += r.cost_usd
    }
    return costs
  }, [series])

  const tokenLookup = useMemo(() => {
    const m = new Map<string, TokenEntry>()
    for (const r of series.filter(r => r.event_type === 'api')) {
      const ts = r.minute_ts ?? r.hour_ts ?? 0
      const k = `${ts}|${r.call_type || 'unknown'}`
      const e = m.get(k) ?? { input: 0, output: 0, cr: 0, cw: 0 }
      e.input  += r.input_tokens
      e.output += r.output_tokens
      e.cr     += r.cache_read_tokens
      e.cw     += r.cache_write_tokens
      m.set(k, e)
    }
    return m
  }, [series])

  const byCallType = useMemo(() => {
    const m: Record<string, Totals> = {}
    for (const r of series) {
      const k = r.call_type || r.event_type
      if (!m[k]) m[k] = { calls: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, audio_seconds: 0, transcription_ms: 0, tts_characters: 0, tts_audio_seconds: 0, tts_synthesis_ms: 0 }
      const t = m[k]
      t.calls += r.calls; t.input_tokens += r.input_tokens; t.output_tokens += r.output_tokens
      t.cache_read_tokens += r.cache_read_tokens; t.cache_write_tokens += r.cache_write_tokens
      t.cost_usd += r.cost_usd; t.audio_seconds += r.audio_seconds
      t.transcription_ms += r.transcription_ms; t.tts_characters += r.tts_characters
      t.tts_audio_seconds += r.tts_audio_seconds; t.tts_synthesis_ms += r.tts_synthesis_ms
    }
    return m
  }, [series])

  const byModel = useMemo(() => {
    const m: Record<string, Totals> = {}
    for (const r of series.filter(r => r.event_type === 'api')) {
      const k = r.model || 'unknown'
      if (!m[k]) m[k] = { calls: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, audio_seconds: 0, transcription_ms: 0, tts_characters: 0, tts_audio_seconds: 0, tts_synthesis_ms: 0 }
      const t = m[k]
      t.calls += r.calls; t.input_tokens += r.input_tokens; t.output_tokens += r.output_tokens
      t.cache_read_tokens += r.cache_read_tokens; t.cache_write_tokens += r.cache_write_tokens
      t.cost_usd += r.cost_usd
    }
    return m
  }, [series])

  const curTotals = totals[window]

  if (!isAdmin) return null

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[hsl(var(--background))] text-[hsl(var(--foreground))]">
      {/* Header */}
      <div className="sticky top-0 z-10 border-b border-[hsl(var(--border))] bg-[hsl(var(--background)/0.95)] backdrop-blur px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/')}
            className="text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
          >
            ← Back
          </button>
          <span className="font-semibold text-sm">Usage Dashboard</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[hsl(var(--destructive)/0.15)] text-[hsl(var(--destructive))]">Admin</span>
        </div>
        <div />
      </div>

      <div className="max-w-7xl mx-auto px-6 py-6">
        {loading && (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading…</p>
        )}

        {/* Window + user filters */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="flex gap-1 text-xs">
            {(['today', 'week', 'month', 'all'] as const).map(w => (
              <button key={w}
                onClick={() => setWindow(w)}
                className={cn(
                  'px-2.5 py-1 rounded border transition-colors',
                  window === w
                    ? 'bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-[hsl(var(--primary))]'
                    : 'border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]'
                )}
              >
                {w === 'all' ? 'All time' : w.charAt(0).toUpperCase() + w.slice(1)}
              </button>
            ))}
          </div>
          <select
            value={filterUser}
            onChange={e => setFilterUser(e.target.value)}
            className="text-xs rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2 py-1"
          >
            <option value="">All users</option>
            {users.map(u => (
              <option key={u.id} value={u.id}>{u.email || u.display_name || u.id}</option>
            ))}
          </select>
        </div>

        {/* Summary cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
          <Card
            label="Total cost"
            value={fmtCost(curTotals?.cost_usd ?? 0)}
            sub={`API ${fmtCost(costByEventType.api)} · STT ${fmtCost(costByEventType.stt)} · TTS ${fmtCost(costByEventType.tts)}`}
          />
          <Card label="Tokens" value={fmt((curTotals?.input_tokens ?? 0) + (curTotals?.output_tokens ?? 0))}
            sub={`${fmt(curTotals?.cache_read_tokens ?? 0)} cache reads`} />
          <Card label="STT audio" value={fmtSecs(curTotals?.audio_seconds ?? 0)}
            sub={curTotals?.transcription_ms ? `${(curTotals.transcription_ms / 1000).toFixed(1)}s transcription` : ''} />
          <Card label="TTS chars" value={fmt(curTotals?.tts_characters ?? 0)}
            sub={fmtSecs(curTotals?.tts_audio_seconds ?? 0) + ' generated'} />
        </div>

        {/* Charts */}
        <SectionTitle>Cost over time</SectionTitle>
        <div className="rounded-lg border border-[hsl(var(--border))] p-4 mb-4" style={{ height: 220 }}>
          {chartData.length === 0
            ? <p className="text-xs text-[hsl(var(--muted-foreground))] text-center pt-10">No data.</p>
            : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => `$${v.toFixed(4)}`} width={60} />
                  <Tooltip formatter={(v) => fmtCost(Number(v))} />
                  <Line type="monotone" dataKey="cost_usd" name="Cost (USD)" dot={false}
                    stroke="hsl(var(--primary))" strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            )}
        </div>

        <SectionTitle>Cost by event type</SectionTitle>
        <div className="rounded-lg border border-[hsl(var(--border))] p-4 mb-4" style={{ height: 220 }}>
          {eventTypeChartData.length === 0
            ? <p className="text-xs text-[hsl(var(--muted-foreground))] text-center pt-10">No data.</p>
            : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={eventTypeChartData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => fmtCost(Number(v))} width={64} />
                  <Tooltip formatter={(v) => fmtCost(Number(v))} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="api_cost" name="API" stackId="a" fill="hsl(217 91% 60%)" />
                  <Bar dataKey="stt_cost" name="STT" stackId="a" fill="hsl(142 71% 45%)" />
                  <Bar dataKey="tts_cost" name="TTS" stackId="a" fill="hsl(266 67% 55%)" />
                </BarChart>
              </ResponsiveContainer>
            )}
        </div>

        <SectionTitle>Cost by call type</SectionTitle>
        <div className="rounded-lg border border-[hsl(var(--border))] p-4 mb-4" style={{ height: 240 }}>
          {callTypeChartData.length === 0
            ? <p className="text-xs text-[hsl(var(--muted-foreground))] text-center pt-10">No data.</p>
            : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={callTypeChartData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}
                  onMouseLeave={() => setActiveCallType(null)}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => fmtCost(Number(v))} width={64} />
                  <Tooltip
                    content={(props) => (
                      // eslint-disable-next-line @typescript-eslint/no-explicit-any
                      <CallTypeTooltip {...(props as any)} tokenLookup={tokenLookup} activeCallType={activeCallType} />
                    )}
                    wrapperStyle={{ pointerEvents: 'none' }}
                    offset={24}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {apiCallTypes.map((ct, i) => (
                    <Bar key={ct} dataKey={ct} name={ct} stackId="a" fill={callTypeColor(ct, i)}
                      onMouseEnter={() => setActiveCallType(ct)} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          {/* Token chart */}
          <div className="rounded-lg border border-[hsl(var(--border))] p-4" style={{ height: 200 }}>
            <p className="text-xs font-medium mb-2">Token usage</p>
            {chartData.length === 0
              ? <p className="text-xs text-[hsl(var(--muted-foreground))]">No data.</p>
              : (
                <ResponsiveContainer width="100%" height="85%">
                  <BarChart data={chartData} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
                    <XAxis dataKey="label" tick={{ fontSize: 9 }} />
                    <YAxis tick={{ fontSize: 9 }} tickFormatter={fmt} width={40} />
                    <Tooltip formatter={(v) => fmt(Number(v))} />
                    <Bar dataKey="input_tokens" name="Input" stackId="a" fill="hsl(var(--primary))" />
                    <Bar dataKey="output_tokens" name="Output" stackId="a" fill="hsl(var(--primary)/0.5)" />
                  </BarChart>
                </ResponsiveContainer>
              )}
          </div>

          {/* STT chart */}
          <div className="rounded-lg border border-[hsl(var(--border))] p-4" style={{ height: 200 }}>
            <p className="text-xs font-medium mb-2">STT audio (seconds)</p>
            {chartData.length === 0
              ? <p className="text-xs text-[hsl(var(--muted-foreground))]">No data.</p>
              : (
                <ResponsiveContainer width="100%" height="85%">
                  <BarChart data={chartData} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
                    <XAxis dataKey="label" tick={{ fontSize: 9 }} />
                    <YAxis tick={{ fontSize: 9 }} width={30} />
                    <Tooltip formatter={(v) => fmtSecs(Number(v))} />
                    <Bar dataKey="audio_seconds" name="Audio" fill="hsl(142 71% 45%)" />
                  </BarChart>
                </ResponsiveContainer>
              )}
          </div>

          {/* TTS chart */}
          <div className="rounded-lg border border-[hsl(var(--border))] p-4" style={{ height: 200 }}>
            <p className="text-xs font-medium mb-2">TTS characters</p>
            {chartData.length === 0
              ? <p className="text-xs text-[hsl(var(--muted-foreground))]">No data.</p>
              : (
                <ResponsiveContainer width="100%" height="85%">
                  <BarChart data={chartData} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
                    <XAxis dataKey="label" tick={{ fontSize: 9 }} />
                    <YAxis tick={{ fontSize: 9 }} tickFormatter={fmt} width={40} />
                    <Tooltip formatter={(v) => fmt(Number(v))} />
                    <Bar dataKey="tts_characters" name="Characters" fill="hsl(266 67% 55%)" />
                  </BarChart>
                </ResponsiveContainer>
              )}
          </div>
        </div>

        {/* By call type table */}
        <SectionTitle>By call type</SectionTitle>
        <DataTable
          cols={[
            { key: 'type', label: 'Type' },
            { key: 'calls', label: 'Calls', align: 'right' },
            { key: 'tokens', label: 'Tokens in/out', align: 'right' },
            { key: 'cost', label: 'Cost', align: 'right' },
            { key: 'audio', label: 'STT audio', align: 'right' },
            { key: 'tts', label: 'TTS chars', align: 'right' },
          ]}
          rows={Object.entries(byCallType).sort(([, a], [, b]) => b.cost_usd - a.cost_usd).map(([type, t]) => ({
            type,
            calls: fmt(t.calls),
            tokens: `${fmt(t.input_tokens)} / ${fmt(t.output_tokens)}`,
            cost: fmtCost(t.cost_usd),
            audio: t.audio_seconds > 0 ? fmtSecs(t.audio_seconds) : '—',
            tts: t.tts_characters > 0 ? fmt(t.tts_characters) : '—',
          }))}
        />

        {/* By model table */}
        <SectionTitle>By model</SectionTitle>
        <DataTable
          cols={[
            { key: 'model', label: 'Model' },
            { key: 'calls', label: 'Calls', align: 'right' },
            { key: 'input', label: 'Input tokens', align: 'right' },
            { key: 'output', label: 'Output tokens', align: 'right' },
            { key: 'cache', label: 'Cache read', align: 'right' },
            { key: 'cost', label: 'Cost', align: 'right' },
          ]}
          rows={Object.entries(byModel).sort(([, a], [, b]) => b.cost_usd - a.cost_usd).map(([model, t]) => ({
            model,
            calls: fmt(t.calls),
            input: fmt(t.input_tokens),
            output: fmt(t.output_tokens),
            cache: fmt(t.cache_read_tokens),
            cost: fmtCost(t.cost_usd),
          }))}
        />

        {/* By user table */}
        {users.length > 1 && (
          <>
            <SectionTitle>By user</SectionTitle>
            <DataTable
              cols={[
                { key: 'email', label: 'User' },
                { key: 'calls', label: 'API calls', align: 'right' },
                { key: 'cost', label: 'Total cost', align: 'right' },
                { key: 'stt', label: 'STT audio', align: 'right' },
                { key: 'tts', label: 'TTS chars', align: 'right' },
              ]}
              rows={users.map(u => {
                const uRows = series.filter(r => r.user_id === u.id)
                const apiRows = uRows.filter(r => r.event_type === 'api')
                const sttRows = uRows.filter(r => r.event_type === 'stt')
                const ttsRows = uRows.filter(r => r.event_type === 'tts')
                const cost = uRows.reduce((s, r) => s + r.cost_usd, 0)
                const audioSecs = sttRows.reduce((s, r) => s + r.audio_seconds, 0)
                const ttsChars = ttsRows.reduce((s, r) => s + r.tts_characters, 0)
                const calls = apiRows.reduce((s, r) => s + r.calls, 0)
                return {
                  email: u.email || u.display_name || u.id,
                  calls: fmt(calls),
                  cost: fmtCost(cost),
                  stt: audioSecs > 0 ? fmtSecs(audioSecs) : '—',
                  tts: ttsChars > 0 ? fmt(ttsChars) : '—',
                }
              })}
            />
          </>
        )}

        <LiveFeed sessionId={sessionId} />

        {/* Users table (admin view) */}
        <SectionTitle>All users</SectionTitle>
        <DataTable
          cols={[
            { key: 'email', label: 'Email' },
            { key: 'display_name', label: 'Name' },
            { key: 'is_admin', label: 'Admin', align: 'right' },
            { key: 'id', label: 'ID' },
          ]}
          rows={users.map(u => ({
            email: u.email,
            display_name: u.display_name ?? '—',
            is_admin: u.is_admin ? '✓' : '',
            id: <span className="text-[hsl(var(--muted-foreground))] font-mono text-[10px]">{u.id}</span>,
          }))}
        />

        <div className="h-12" />
      </div>
    </div>
  )
}
