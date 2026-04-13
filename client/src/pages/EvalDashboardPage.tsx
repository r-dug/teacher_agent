/**
 * Admin Eval Dashboard
 *
 * Shows eval run results, grader pass rates, failure details,
 * and model comparison. Requires is_admin=true.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts'
import { cn } from '@/lib/utils'

// ── types ────────────────────────────────────────────────────────────────────

interface GraderStats {
  passed: number
  total: number
}

interface RunSummary {
  filename: string
  model: string
  timestamp: string | null
  total_cases: number
  total_passed: number
  grader_summary: Record<string, GraderStats>
  avg_latency_ms: number
}

interface GradeDetail {
  passed: boolean
  score: number
  detail: string
}

interface CaseResult {
  case_id: string
  model: string
  response: string
  tool_calls: Array<{ function: { name: string; arguments: string } }>
  latency_ms: number
  grades: Record<string, GradeDetail>
}

interface RunDetail {
  summary: RunSummary
  results: CaseResult[]
}

interface CompareChange {
  case_id: string
  grader: string
  direction: 'fixed' | 'regressed'
  detail_a: string
  detail_b: string
}

interface CompareResult {
  a: RunSummary
  b: RunSummary
  changes: CompareChange[]
}

// ── helpers ──────────────────────────────────────────────────────────────────

async function apiFetch(path: string, sessionId: string, params?: Record<string, string>) {
  const url = new URL(`/api${path}`, window.location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  const r = await fetch(url.toString(), { headers: { 'X-Session-Id': sessionId } })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

const GRADER_COLORS: Record<string, string> = {
  voice_rules: 'hsl(142 71% 45%)',
  conciseness: 'hsl(217 91% 60%)',
  engagement:  'hsl(266 67% 55%)',
  tool_use:    'hsl(31 100% 55%)',
  accuracy:    'hsl(349 89% 60%)',
  error:       'hsl(0 72% 51%)',
}
function graderColor(name: string): string {
  return GRADER_COLORS[name] ?? 'hsl(200 80% 55%)'
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return 'Unknown date'
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return ts
  }
}

// ── main component ───────────────────────────────────────────────────────────

interface Props {
  sessionId: string
  isAdmin: boolean
}

export function EvalDashboardPage({ sessionId, isAdmin }: Props) {
  const navigate = useNavigate()

  useEffect(() => {
    if (!isAdmin) navigate('/', { replace: true })
  }, [isAdmin, navigate])

  // ── state ──────────────────────────────────────────────────────────────────

  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selectedRun, setSelectedRun] = useState<string>('')
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedCase, setExpandedCase] = useState<string | null>(null)

  // Tabs
  const [activeTab, setActiveTab] = useState<'results' | 'run' | 'training'>('results')

  // Compare state
  const [compareMode, setCompareMode] = useState(false)
  const [compareA, setCompareA] = useState<string>('')
  const [compareB, setCompareB] = useState<string>('')
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null)

  // ── data fetching ──────────────────────────────────────────────────────────

  const fetchRuns = useCallback(async () => {
    try {
      const data = await apiFetch('/admin/evals/runs', sessionId)
      setRuns(data)
      if (data.length > 0 && !selectedRun) {
        setSelectedRun(data[0].filename)
      }
    } catch { /* non-fatal */ }
  }, [sessionId, selectedRun])

  const fetchRunDetail = useCallback(async (filename: string) => {
    if (!filename) return
    try {
      const data = await apiFetch(`/admin/evals/runs/${filename}`, sessionId)
      setRunDetail(data)
    } catch { /* non-fatal */ }
  }, [sessionId])

  const fetchCompare = useCallback(async () => {
    if (!compareA || !compareB || compareA === compareB) return
    try {
      const data = await apiFetch('/admin/evals/compare', sessionId, { a: compareA, b: compareB })
      setCompareResult(data)
    } catch { /* non-fatal */ }
  }, [sessionId, compareA, compareB])

  // Initial load
  useEffect(() => {
    if (!isAdmin) return
    setLoading(true)
    fetchRuns().finally(() => setLoading(false))
  }, [isAdmin, fetchRuns])

  // Fetch detail when selection changes
  useEffect(() => {
    if (selectedRun && !compareMode) fetchRunDetail(selectedRun)
  }, [selectedRun, compareMode, fetchRunDetail])

  // Fetch compare when both selected
  useEffect(() => {
    if (compareMode && compareA && compareB) fetchCompare()
  }, [compareMode, compareA, compareB, fetchCompare])

  // ── derived data ───────────────────────────────────────────────────────────

  const graderChartData = useMemo(() => {
    if (!runDetail) return []
    const s = runDetail.summary.grader_summary
    return Object.entries(s).map(([name, stats]) => ({
      name,
      passed: stats.passed,
      failed: stats.total - stats.passed,
      total: stats.total,
      rate: stats.total > 0 ? stats.passed / stats.total : 0,
    }))
  }, [runDetail])

  const failures = useMemo(() => {
    if (!runDetail) return []
    const f: Array<{ case_id: string; grader: string; detail: string; response: string; tool_calls: CaseResult['tool_calls'] }> = []
    for (const r of runDetail.results) {
      for (const [name, g] of Object.entries(r.grades)) {
        if (!g.passed) {
          f.push({ case_id: r.case_id, grader: name, detail: g.detail, response: r.response, tool_calls: r.tool_calls })
        }
      }
    }
    return f
  }, [runDetail])

  const compareChartData = useMemo(() => {
    if (!compareResult) return []
    const allGraders = new Set([
      ...Object.keys(compareResult.a.grader_summary),
      ...Object.keys(compareResult.b.grader_summary),
    ])
    return Array.from(allGraders).map(name => {
      const a = compareResult.a.grader_summary[name] ?? { passed: 0, total: 0 }
      const b = compareResult.b.grader_summary[name] ?? { passed: 0, total: 0 }
      return {
        name,
        a_rate: a.total > 0 ? (a.passed / a.total) * 100 : 0,
        b_rate: b.total > 0 ? (b.passed / b.total) * 100 : 0,
      }
    })
  }, [compareResult])

  // ── render ─────────────────────────────────────────────────────────────────

  if (!isAdmin) return null

  return (
    <div className="min-h-screen bg-[hsl(var(--background))] text-[hsl(var(--foreground))]">
      {/* Header */}
      <header className="border-b border-[hsl(var(--border))] px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/')}
              className="text-sm text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
            >
              &larr; Home
            </button>
            <h1 className="text-lg font-semibold">Eval Dashboard</h1>
          </div>
          {activeTab === 'results' && (
            <div className="flex items-center gap-3">
              <button
                onClick={() => { setCompareMode(!compareMode); setCompareResult(null) }}
                className={cn(
                  'px-3 py-1.5 rounded text-sm transition-colors',
                  compareMode
                    ? 'bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))]'
                    : 'bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))]'
                )}
              >
                {compareMode ? 'Exit Compare' : 'Compare Runs'}
              </button>
            </div>
          )}
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 mt-3">
          {(['results', 'run', 'training'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn(
                'px-4 py-1.5 rounded-t text-sm font-medium transition-colors',
                activeTab === tab
                  ? 'bg-[hsl(var(--card))] text-[hsl(var(--foreground))] border border-b-0 border-[hsl(var(--border))]'
                  : 'text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]'
              )}
            >
              {{ results: 'Results', run: 'Run Evals', training: 'Training Data' }[tab]}
            </button>
          ))}
        </div>
      </header>

      {activeTab === 'results' && <div className="mx-auto max-w-6xl px-6 py-6 space-y-6">
        {loading ? (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading runs...</p>
        ) : runs.length === 0 ? (
          <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-8 text-center">
            <p className="text-[hsl(var(--muted-foreground))]">No eval runs found.</p>
            <p className="text-sm text-[hsl(var(--muted-foreground))] mt-2">
              Run an eval first: <code className="bg-[hsl(var(--muted))] px-1.5 py-0.5 rounded text-xs">
                uv run python -m backend.scripts.run_evals --model gpt-4.1-nano
              </code>
            </p>
          </div>
        ) : compareMode ? (
          <CompareView
            runs={runs}
            compareA={compareA}
            compareB={compareB}
            onChangeA={setCompareA}
            onChangeB={setCompareB}
            result={compareResult}
            chartData={compareChartData}
          />
        ) : (
          <>
            {/* Run selector */}
            <div className="flex items-center gap-4">
              <label className="text-sm font-medium">Run:</label>
              <select
                value={selectedRun}
                onChange={e => setSelectedRun(e.target.value)}
                className="rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm"
              >
                {runs.map(r => (
                  <option key={r.filename} value={r.filename}>
                    {r.model} — {formatTimestamp(r.timestamp)} ({r.total_cases} cases)
                  </option>
                ))}
              </select>
            </div>

            {runDetail && (
              <>
                {/* Summary cards */}
                <SummaryCards summary={runDetail.summary} />

                {/* Grader chart */}
                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
                  <h2 className="text-sm font-semibold mb-3">Pass Rate by Grader</h2>
                  <ResponsiveContainer width="100%" height={250}>
                    <BarChart data={graderChartData} layout="vertical" margin={{ left: 90, right: 30 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                      <XAxis type="number" domain={[0, 'dataMax']} tick={{ fontSize: 12 }} />
                      <YAxis type="category" dataKey="name" tick={{ fontSize: 12 }} width={80} />
                      <Tooltip
                        contentStyle={{
                          background: 'hsl(var(--background))',
                          border: '1px solid hsl(var(--border))',
                          borderRadius: 6,
                          fontSize: 12,
                        }}
                        formatter={(value) => [String(value)]}
                      />
                      <Legend />
                      <Bar dataKey="passed" stackId="a" name="Passed" fill="hsl(142 71% 45%)" radius={[0, 0, 0, 0]} />
                      <Bar dataKey="failed" stackId="a" name="Failed" fill="hsl(0 72% 51%)" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* Failures table */}
                {failures.length > 0 && (
                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
                    <h2 className="text-sm font-semibold mb-3">
                      Failures ({failures.length})
                    </h2>
                    <div className="space-y-1">
                      {failures.map((f, i) => {
                        const key = `${f.case_id}-${f.grader}-${i}`
                        const isExpanded = expandedCase === key
                        return (
                          <div key={key} className="border border-[hsl(var(--border))] rounded">
                            <button
                              onClick={() => setExpandedCase(isExpanded ? null : key)}
                              className="w-full flex items-center justify-between px-3 py-2 text-left text-sm hover:bg-[hsl(var(--accent))] transition-colors"
                            >
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-xs">{f.case_id}</span>
                                <span
                                  className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
                                  style={{ backgroundColor: graderColor(f.grader) + '22', color: graderColor(f.grader) }}
                                >
                                  {f.grader}
                                </span>
                              </div>
                              <span className="text-xs text-[hsl(var(--muted-foreground))]">{f.detail}</span>
                            </button>
                            {isExpanded && (
                              <div className="border-t border-[hsl(var(--border))] px-3 py-3 space-y-2 bg-[hsl(var(--muted))]">
                                <div>
                                  <p className="text-xs font-medium text-[hsl(var(--muted-foreground))] mb-1">Response:</p>
                                  <p className="text-sm whitespace-pre-wrap">{f.response || '(empty)'}</p>
                                </div>
                                {f.tool_calls.length > 0 && (
                                  <div>
                                    <p className="text-xs font-medium text-[hsl(var(--muted-foreground))] mb-1">Tool calls:</p>
                                    {f.tool_calls.map((tc, j) => (
                                      <p key={j} className="text-xs font-mono">
                                        {tc.function.name}({tc.function.arguments.slice(0, 120)})
                                      </p>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* All results table */}
                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
                  <h2 className="text-sm font-semibold mb-3">All Cases</h2>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-[hsl(var(--border))] text-left text-[hsl(var(--muted-foreground))]">
                          <th className="pb-2 pr-4 font-medium">Case</th>
                          {Object.keys(runDetail.summary.grader_summary).map(g => (
                            <th key={g} className="pb-2 px-2 font-medium text-center">{g}</th>
                          ))}
                          <th className="pb-2 pl-4 font-medium text-right">Latency</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runDetail.results.map(r => (
                          <tr key={r.case_id} className="border-b border-[hsl(var(--border))] last:border-0">
                            <td className="py-1.5 pr-4 font-mono text-xs">{r.case_id}</td>
                            {Object.keys(runDetail.summary.grader_summary).map(g => {
                              const grade = r.grades[g]
                              if (!grade) return <td key={g} className="py-1.5 px-2 text-center">—</td>
                              return (
                                <td key={g} className="py-1.5 px-2 text-center" title={grade.detail}>
                                  <span className={cn(
                                    'inline-block w-5 h-5 rounded-full text-xs leading-5 text-center font-medium',
                                    grade.passed
                                      ? 'bg-green-500/20 text-green-400'
                                      : 'bg-red-500/20 text-red-400'
                                  )}>
                                    {grade.passed ? '\u2713' : '\u2717'}
                                  </span>
                                </td>
                              )
                            })}
                            <td className="py-1.5 pl-4 text-right tabular-nums text-xs text-[hsl(var(--muted-foreground))]">
                              {r.latency_ms}ms
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>}

      {activeTab === 'run' && <RunEvalsTab sessionId={sessionId} onRunComplete={() => { setActiveTab('results'); fetchRuns() }} />}
      {activeTab === 'training' && <TrainingDataTab sessionId={sessionId} />}
    </div>
  )
}

// ── Summary Cards ────────────────────────────────────────────────────────────

function SummaryCards({ summary }: { summary: RunSummary }) {
  const overall = summary.total_cases > 0
    ? (summary.total_passed / summary.total_cases * 100).toFixed(1)
    : '0'

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <Card label="Overall Pass" value={`${overall}%`} sub={`${summary.total_passed}/${summary.total_cases}`} />
      <Card label="Model" value={summary.model} />
      <Card label="Cases" value={String(summary.total_cases)} />
      <Card label="Avg Latency" value={`${summary.avg_latency_ms}ms`} />
    </div>
  )
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
      <p className="text-xs text-[hsl(var(--muted-foreground))]">{label}</p>
      <p className="text-xl font-semibold mt-1 truncate">{value}</p>
      {sub && <p className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">{sub}</p>}
    </div>
  )
}

// ── Compare View ─────────────────────────────────────────────────────────────

function CompareView({
  runs, compareA, compareB, onChangeA, onChangeB, result, chartData,
}: {
  runs: RunSummary[]
  compareA: string
  compareB: string
  onChangeA: (v: string) => void
  onChangeB: (v: string) => void
  result: CompareResult | null
  chartData: Array<{ name: string; a_rate: number; b_rate: number }>
}) {
  return (
    <div className="space-y-6">
      {/* Selectors */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium w-6">A:</label>
          <select
            value={compareA}
            onChange={e => onChangeA(e.target.value)}
            className="rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm"
          >
            <option value="">Select run A...</option>
            {runs.map(r => (
              <option key={r.filename} value={r.filename}>
                {r.model} — {formatTimestamp(r.timestamp)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium w-6">B:</label>
          <select
            value={compareB}
            onChange={e => onChangeB(e.target.value)}
            className="rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm"
          >
            <option value="">Select run B...</option>
            {runs.map(r => (
              <option key={r.filename} value={r.filename}>
                {r.model} — {formatTimestamp(r.timestamp)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {result && (
        <>
          {/* Summary comparison cards */}
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
              <p className="text-xs text-[hsl(var(--muted-foreground))]">A: {result.a.model}</p>
              <p className="text-xl font-semibold mt-1">
                {result.a.total_cases > 0 ? (result.a.total_passed / result.a.total_cases * 100).toFixed(1) : 0}%
              </p>
              <p className="text-xs text-[hsl(var(--muted-foreground))]">{result.a.total_passed}/{result.a.total_cases} passed</p>
            </div>
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
              <p className="text-xs text-[hsl(var(--muted-foreground))]">B: {result.b.model}</p>
              <p className="text-xl font-semibold mt-1">
                {result.b.total_cases > 0 ? (result.b.total_passed / result.b.total_cases * 100).toFixed(1) : 0}%
              </p>
              <p className="text-xs text-[hsl(var(--muted-foreground))]">{result.b.total_passed}/{result.b.total_cases} passed</p>
            </div>
          </div>

          {/* Side-by-side bar chart */}
          <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
            <h2 className="text-sm font-semibold mb-3">Pass Rate Comparison (%)</h2>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={chartData} layout="vertical" margin={{ left: 90, right: 30 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 12 }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 12 }} width={80} />
                <Tooltip
                  contentStyle={{
                    background: 'hsl(var(--background))',
                    border: '1px solid hsl(var(--border))',
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                  formatter={(value) => [`${Number(value).toFixed(1)}%`]}
                />
                <Legend />
                <Bar dataKey="a_rate" name={result.a.model} fill="hsl(217 91% 60%)" barSize={12} />
                <Bar dataKey="b_rate" name={result.b.model} fill="hsl(142 71% 45%)" barSize={12} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Changes table */}
          {result.changes.length > 0 && (
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
              <h2 className="text-sm font-semibold mb-3">
                Changed Cases ({result.changes.length})
              </h2>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[hsl(var(--border))] text-left text-[hsl(var(--muted-foreground))]">
                    <th className="pb-2 pr-4 font-medium">Case</th>
                    <th className="pb-2 px-2 font-medium">Grader</th>
                    <th className="pb-2 px-2 font-medium">Change</th>
                    <th className="pb-2 pl-4 font-medium">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {result.changes.map((c, i) => (
                    <tr key={i} className="border-b border-[hsl(var(--border))] last:border-0">
                      <td className="py-1.5 pr-4 font-mono text-xs">{c.case_id}</td>
                      <td className="py-1.5 px-2">
                        <span
                          className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
                          style={{ backgroundColor: graderColor(c.grader) + '22', color: graderColor(c.grader) }}
                        >
                          {c.grader}
                        </span>
                      </td>
                      <td className="py-1.5 px-2">
                        <span className={cn(
                          'text-xs font-semibold',
                          c.direction === 'fixed' ? 'text-green-400' : 'text-red-400'
                        )}>
                          {c.direction === 'fixed' ? '\u2191 FIXED' : '\u2193 REGRESSED'}
                        </span>
                      </td>
                      <td className="py-1.5 pl-4 text-xs text-[hsl(var(--muted-foreground))]">
                        {c.detail_a} &rarr; {c.detail_b}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.changes.length === 0 && (
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-6 text-center">
              <p className="text-[hsl(var(--muted-foreground))]">No case-level changes between runs.</p>
            </div>
          )}
        </>
      )}
    </div>
  )
}


// ═══════════════════════════════════════════════════════════════════════════════
// Run Evals Tab
// ═══════════════════════════════════════════════════════════════════════════════

function RunEvalsTab({ sessionId, onRunComplete }: { sessionId: string; onRunComplete: () => void }) {
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [casesFile, setCasesFile] = useState('backend/evals/cases/seed.jsonl')
  const [judgeModel, setJudgeModel] = useState('')
  const [tag, setTag] = useState('')
  const [caseFiles, setCaseFiles] = useState<Array<{ filename: string; case_count: number }>>([])
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<{ status: string; progress: string; error?: string; result_filename?: string } | null>(null)
  const [starting, setStarting] = useState(false)

  // Load case files
  useEffect(() => {
    apiFetch('/admin/evals/cases', sessionId).then(setCaseFiles).catch(() => {})
  }, [sessionId])

  // Poll status
  useEffect(() => {
    if (!runId || status?.status === 'completed' || status?.status === 'failed') return
    const interval = setInterval(async () => {
      try {
        const s = await apiFetch(`/admin/evals/run-status/${runId}`, sessionId)
        setStatus(s)
        if (s.status === 'completed' || s.status === 'failed') clearInterval(interval)
      } catch { /* retry */ }
    }, 2000)
    return () => clearInterval(interval)
  }, [runId, status?.status, sessionId])

  async function handleStart() {
    if (!model.trim()) return
    setStarting(true)
    setStatus(null)
    try {
      const body: Record<string, unknown> = { model: model.trim() }
      if (baseUrl.trim()) body.base_url = baseUrl.trim()
      if (casesFile) body.cases_file = casesFile
      if (judgeModel.trim()) body.judge_model = judgeModel.trim()
      if (tag.trim()) body.tag = tag.trim()

      const r = await fetch('/api/admin/evals/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error(`${r.status}`)
      const data = await r.json()
      setRunId(data.run_id)
      setStatus({ status: 'running', progress: '0/0' })
    } catch (e) {
      setStatus({ status: 'failed', progress: '', error: String(e) })
    } finally {
      setStarting(false)
    }
  }

  const PRESETS = ['gpt-4.1-nano', 'gpt-4o-mini', 'qwen2.5:7b', 'llama3.1:8b']

  return (
    <div className="mx-auto max-w-3xl px-6 py-6 space-y-6">
      <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-6 space-y-4">
        <h2 className="text-sm font-semibold">Run Evaluation</h2>

        {/* Model */}
        <div className="space-y-2">
          <label className="text-xs text-[hsl(var(--muted-foreground))]">Model</label>
          <input
            value={model} onChange={e => setModel(e.target.value)}
            placeholder="e.g. gpt-4.1-nano or gemma3:4b"
            className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
          />
          <div className="flex flex-wrap gap-1">
            {PRESETS.map(p => (
              <button key={p} onClick={() => setModel(p)}
                className="rounded px-2 py-0.5 text-xs border border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))] transition-colors">
                {p}
              </button>
            ))}
          </div>
        </div>

        {/* Base URL */}
        <div className="space-y-1">
          <label className="text-xs text-[hsl(var(--muted-foreground))]">Base URL (optional)</label>
          <input
            value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
            placeholder="http://localhost:11434/v1 for Ollama"
            className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
          />
        </div>

        {/* Case file + tag */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs text-[hsl(var(--muted-foreground))]">Case File</label>
            <select value={casesFile} onChange={e => setCasesFile(e.target.value)}
              className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm">
              {caseFiles.map(f => (
                <option key={f.filename} value={`backend/evals/cases/${f.filename}`}>
                  {f.filename} ({f.case_count} cases)
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-[hsl(var(--muted-foreground))]">Tag filter (optional)</label>
            <input value={tag} onChange={e => setTag(e.target.value)}
              placeholder="e.g. voice_rules"
              className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
            />
          </div>
        </div>

        {/* Judge model */}
        <div className="space-y-1">
          <label className="text-xs text-[hsl(var(--muted-foreground))]">Judge model for accuracy (optional)</label>
          <input value={judgeModel} onChange={e => setJudgeModel(e.target.value)}
            placeholder="e.g. gpt-4.1-mini"
            className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
          />
        </div>

        {/* Start button */}
        <button
          onClick={handleStart}
          disabled={!model.trim() || starting || status?.status === 'running'}
          className={cn(
            'w-full rounded px-4 py-2 text-sm font-medium transition-colors',
            'bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))]',
            'hover:bg-[hsl(var(--primary)/0.9)]',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          {starting ? 'Starting...' : status?.status === 'running' ? 'Running...' : 'Start Eval Run'}
        </button>
      </div>

      {/* Progress / Result */}
      {status && (
        <div className={cn(
          'rounded-lg border p-4',
          status.status === 'completed' ? 'border-green-500/40 bg-green-500/5' :
          status.status === 'failed' ? 'border-red-500/40 bg-red-500/5' :
          'border-[hsl(var(--border))] bg-[hsl(var(--card))]'
        )}>
          {status.status === 'running' && (
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span>Running {model}...</span>
                <span className="font-mono">{status.progress}</span>
              </div>
              {(() => {
                const [done, total] = (status.progress || '0/0').split('/').map(Number)
                const pct = total > 0 ? (done / total * 100) : 0
                return (
                  <div className="h-2 rounded-full bg-[hsl(var(--muted))] overflow-hidden">
                    <div className="h-full bg-[hsl(var(--primary))] transition-all duration-300" style={{ width: `${pct}%` }} />
                  </div>
                )
              })()}
            </div>
          )}
          {status.status === 'completed' && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-green-400 font-medium">Eval complete!</span>
              <button onClick={onRunComplete}
                className="rounded px-3 py-1 text-sm bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] hover:bg-[hsl(var(--primary)/0.9)]">
                View Results
              </button>
            </div>
          )}
          {status.status === 'failed' && (
            <p className="text-sm text-red-400">{status.error || 'Run failed'}</p>
          )}
        </div>
      )}
    </div>
  )
}


// ═══════════════════════════════════════════════════════════════════════════════
// Training Data Tab
// ═══════════════════════════════════════════════════════════════════════════════

interface TrainingExample {
  example_id: string
  messages_preview: string
  message_count: number
  has_tools: boolean
  annotation: { rating: string; notes: string } | null
}

function TrainingDataTab({ sessionId }: { sessionId: string }) {
  const [file, setFile] = useState('training_openai.jsonl')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [examples, setExamples] = useState<TrainingExample[]>([])
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [annotating, setAnnotating] = useState(false)

  const LIMIT = 20
  const FILES = ['training_openai.jsonl', 'training_alpaca.jsonl', 'live_turns.jsonl']

  const fetchExamples = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = { file, offset: String(offset), limit: String(LIMIT) }
      if (search.trim()) params.search = search.trim()
      const data = await apiFetch('/admin/training/examples', sessionId, params)
      setExamples(data.examples || [])
      setTotal(data.total || 0)
    } catch { setExamples([]) }
    finally { setLoading(false) }
  }, [sessionId, file, offset, search])

  useEffect(() => { fetchExamples() }, [fetchExamples])

  // Reset offset when search/file changes
  useEffect(() => { setOffset(0) }, [file, search])

  async function handleAnnotate(exampleId: string, rating: string) {
    setAnnotating(true)
    try {
      await fetch('/api/admin/training/annotate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({ example_id: exampleId, rating }),
      })
      await fetchExamples()
    } catch { /* non-fatal */ }
    finally { setAnnotating(false) }
  }

  async function handleConvert(exampleId: string) {
    try {
      await fetch('/api/admin/training/convert-to-eval', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({ example_id: exampleId, tags: ['converted'] }),
      })
      alert('Converted to eval case!')
    } catch { alert('Failed to convert') }
  }

  const totalPages = Math.ceil(total / LIMIT)
  const currentPage = Math.floor(offset / LIMIT) + 1

  return (
    <div className="mx-auto max-w-4xl px-6 py-6 space-y-4">
      {/* Controls */}
      <div className="flex flex-wrap gap-3 items-end">
        <div className="space-y-1">
          <label className="text-xs text-[hsl(var(--muted-foreground))]">File</label>
          <select value={file} onChange={e => setFile(e.target.value)}
            className="rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm">
            {FILES.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
        <div className="flex-1 space-y-1 min-w-[200px]">
          <label className="text-xs text-[hsl(var(--muted-foreground))]">Search</label>
          <input value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Filter by content..."
            className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
          />
        </div>
        <p className="text-xs text-[hsl(var(--muted-foreground))] pb-1">{total} examples</p>
      </div>

      {/* Example list */}
      {loading ? (
        <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading...</p>
      ) : examples.length === 0 ? (
        <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-8 text-center">
          <p className="text-[hsl(var(--muted-foreground))]">No training examples found.</p>
          <p className="text-xs text-[hsl(var(--muted-foreground))] mt-1">
            Run the export script first: <code className="font-mono">uv run python -m backend.scripts.export_training_data</code>
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {examples.map(ex => (
            <div key={ex.example_id}
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] overflow-hidden">
              <button
                onClick={() => setExpanded(expanded === ex.example_id ? null : ex.example_id)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[hsl(var(--accent)/0.3)] transition-colors"
              >
                <span className="font-mono text-xs text-[hsl(var(--muted-foreground))] w-20 shrink-0">
                  {ex.example_id}
                </span>
                <span className="text-xs text-[hsl(var(--muted-foreground))] w-12 shrink-0">
                  {ex.message_count} msgs
                </span>
                {ex.has_tools && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-400">tools</span>
                )}
                {ex.annotation && (
                  <span className={cn(
                    'text-xs px-1.5 py-0.5 rounded',
                    ex.annotation.rating === 'good' ? 'bg-green-500/10 text-green-400' :
                    ex.annotation.rating === 'bad' ? 'bg-red-500/10 text-red-400' :
                    'bg-yellow-500/10 text-yellow-400'
                  )}>
                    {ex.annotation.rating}
                  </span>
                )}
                <span className="flex-1 text-xs text-[hsl(var(--muted-foreground))] truncate">
                  {ex.messages_preview}
                </span>
                <span className="text-xs text-[hsl(var(--muted-foreground))]">
                  {expanded === ex.example_id ? '\u25B2' : '\u25BC'}
                </span>
              </button>

              {expanded === ex.example_id && (
                <div className="border-t border-[hsl(var(--border))] px-4 py-3 space-y-3">
                  <div className="flex gap-2">
                    {(['good', 'bad', 'skip'] as const).map(rating => (
                      <button key={rating} onClick={() => handleAnnotate(ex.example_id, rating)}
                        disabled={annotating}
                        className={cn(
                          'rounded px-3 py-1 text-xs font-medium transition-colors border',
                          ex.annotation?.rating === rating
                            ? rating === 'good' ? 'bg-green-500/20 border-green-500/40 text-green-400'
                              : rating === 'bad' ? 'bg-red-500/20 border-red-500/40 text-red-400'
                              : 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400'
                            : 'border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))]'
                        )}
                      >
                        {rating}
                      </button>
                    ))}
                    <button onClick={() => handleConvert(ex.example_id)}
                      className="rounded px-3 py-1 text-xs border border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))] ml-auto">
                      Convert to Eval Case
                    </button>
                  </div>
                  <pre className="text-xs bg-[hsl(var(--background))] rounded p-3 overflow-x-auto max-h-80 overflow-y-auto whitespace-pre-wrap break-words">
                    {ex.messages_preview}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <button onClick={() => setOffset(Math.max(0, offset - LIMIT))} disabled={offset === 0}
            className="rounded px-3 py-1 text-sm border border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))] disabled:opacity-40">
            Prev
          </button>
          <span className="text-xs text-[hsl(var(--muted-foreground))]">
            Page {currentPage} of {totalPages}
          </span>
          <button onClick={() => setOffset(offset + LIMIT)} disabled={offset + LIMIT >= total}
            className="rounded px-3 py-1 text-sm border border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))] disabled:opacity-40">
            Next
          </button>
        </div>
      )}
    </div>
  )
}
