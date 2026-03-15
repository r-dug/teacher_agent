/**
 * TokenUsageDisplay: shows Anthropic API token usage and estimated costs.
 * Auto-refreshes every 15 s. Collapsible. Reset button clears server-side data.
 */

import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'

interface CallTypeStats {
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  total_tokens: number
  estimated_cost_usd: number
}

interface UsageSummary {
  totals: CallTypeStats
  by_call_type: Record<string, CallTypeStats>
  by_model: Record<string, CallTypeStats>
}

const CALL_TYPE_LABELS: Record<string, string> = {
  decompose_pdf:         'Decompose PDF',
  intro_turn:            'Intro turn',
  teach_turn:            'Teach turn',
  tts_prep:              'TTS prep',
  generate_instructions: 'Gen instructions',
  episode_condensation:  'Episode condensation',
}

function fmt(n: number): string {
  return n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(2)}M`
    : n >= 1_000
    ? `${(n / 1_000).toFixed(1)}k`
    : String(n)
}

function fmtCost(usd: number): string {
  if (usd < 0.001) return `$${(usd * 100).toFixed(4)}¢`
  return `$${usd.toFixed(4)}`
}

function StatRow({ label, stats }: { label: string; stats: CallTypeStats }) {
  return (
    <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-2 items-baseline py-0.5 text-[11px]">
      <span className="truncate text-[hsl(var(--foreground))]">{label}</span>
      <span className="text-right tabular-nums text-[hsl(var(--muted-foreground))]">{fmt(stats.input_tokens)}↑</span>
      <span className="text-right tabular-nums text-[hsl(var(--muted-foreground))]">{fmt(stats.output_tokens)}↓</span>
      <span className="text-right tabular-nums font-medium">{fmtCost(stats.estimated_cost_usd)}</span>
    </div>
  )
}

export function TokenUsageDisplay() {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<UsageSummary | null>(null)
  const [lastFetch, setLastFetch] = useState(0)

  const fetchUsage = useCallback(async () => {
    try {
      const r = await fetch('/api/usage')
      if (r.ok) {
        setData(await r.json())
        setLastFetch(Date.now())
      }
    } catch { /* non-fatal */ }
  }, [])

  // Fetch on open, then every 15 s while open
  useEffect(() => {
    if (!open) return
    fetchUsage()
    const id = setInterval(fetchUsage, 15_000)
    return () => clearInterval(id)
  }, [open, fetchUsage])

  async function handleReset() {
    await fetch('/api/usage', { method: 'DELETE' })
    fetchUsage()
  }

  const totals = data?.totals
  const byType = data ? Object.entries(data.by_call_type) : []

  return (
    <div className="border-t border-[hsl(var(--border))] px-3 py-2">
      {/* Header row — always visible */}
      <button
        className="flex w-full items-center justify-between text-xs font-medium text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
        onClick={() => setOpen((v) => !v)}
      >
        <span>Token usage</span>
        <span className="flex items-center gap-2">
          {totals && (
            <span className="font-mono tabular-nums text-[10px]">
              {fmt(totals.total_tokens)} · {fmtCost(totals.estimated_cost_usd)}
            </span>
          )}
          <span>{open ? '▲' : '▼'}</span>
        </span>
      </button>

      {open && (
        <div className="mt-2 flex flex-col gap-1">
          {/* Column headers */}
          <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-2 text-[10px] text-[hsl(var(--muted-foreground))] pb-0.5 border-b border-[hsl(var(--border))]">
            <span>Call type</span>
            <span className="text-right">In</span>
            <span className="text-right">Out</span>
            <span className="text-right">Cost</span>
          </div>

          {byType.length === 0 && (
            <p className="text-[11px] text-[hsl(var(--muted-foreground))] py-1">No data yet.</p>
          )}

          {byType.map(([type, stats]) => (
            <StatRow key={type} label={CALL_TYPE_LABELS[type] ?? type} stats={stats} />
          ))}

          {totals && byType.length > 0 && (
            <>
              <div className="border-t border-[hsl(var(--border))] mt-0.5" />
              <StatRow label="Total" stats={totals} />
              {totals.cache_read_tokens > 0 && (
                <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                  Cache: {fmt(totals.cache_read_tokens)} read · {fmt(totals.cache_write_tokens)} write
                </p>
              )}
            </>
          )}

          <div className="flex items-center justify-between mt-1">
            <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
              {lastFetch ? `Updated ${new Date(lastFetch).toLocaleTimeString()}` : ''}
            </span>
            <button
              onClick={handleReset}
              className={cn(
                'text-[10px] rounded px-1.5 py-0.5 border border-[hsl(var(--border))]',
                'text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--destructive))]',
                'hover:border-[hsl(var(--destructive))] transition-colors'
              )}
            >
              Reset
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
