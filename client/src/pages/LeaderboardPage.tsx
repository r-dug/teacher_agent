/**
 * Leaderboard page — global rankings by total points.
 * Shows top 50 users with stats, highlights the current user's row.
 * My Stats card above the table shows the user's own points, streak, and rank.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import type { LeaderboardEntry, LeaderboardResponse, UserStats } from '@/lib/types'

interface LeaderboardPageProps {
  sessionId: string
  username: string
}

function StatPill({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-[hsl(var(--border))] px-4 py-3">
      <span className="text-xl font-bold">{value}</span>
      <span className="text-xs text-[hsl(var(--muted-foreground))]">{label}</span>
    </div>
  )
}

export function LeaderboardPage({ sessionId, username }: LeaderboardPageProps) {
  const navigate = useNavigate()
  const [entries, setEntries] = useState<LeaderboardEntry[]>([])
  const [myStats, setMyStats] = useState<UserStats | null>(null)
  const [selfRank, setSelfRank] = useState<number | null>(null)
  const [selfPoints, setSelfPoints] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const headers = { 'X-Session-Id': sessionId }

    Promise.all([
      fetch('/api/leaderboard', { headers }).then(r => r.json() as Promise<LeaderboardResponse>),
      fetch('/api/leaderboard/me', { headers }).then(r => r.json() as Promise<UserStats>),
    ])
      .then(([board, stats]) => {
        setEntries(board.entries)
        setSelfRank(board.self_rank)
        setSelfPoints(board.self_points)
        setMyStats(stats)
      })
      .catch(() => setError('Failed to load leaderboard.'))
      .finally(() => setLoading(false))
  }, [sessionId])

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 space-y-6">
      {/* Top bar */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate('/')}>← Back</Button>
        <h1 className="text-2xl font-bold">Leaderboard</h1>
      </div>

      {loading && <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading…</p>}
      {error && <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>}

      {/* My stats */}
      {myStats && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              {username || myStats.username}
              {selfRank != null && (
                <span className="ml-2 text-sm font-normal text-[hsl(var(--muted-foreground))]">
                  Rank #{selfRank}
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
              <StatPill label="Points" value={selfPoints ?? myStats.total_points} />
              <StatPill label="Lessons" value={myStats.lessons_completed} />
              <StatPill label="Sections" value={myStats.sections_advanced} />
              <StatPill label="Streak" value={`${myStats.current_streak}d`} />
              <StatPill label="Best streak" value={`${myStats.longest_streak}d`} />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Leaderboard table */}
      {!loading && entries.length === 0 && !error && (
        <p className="text-sm text-[hsl(var(--muted-foreground))]">No scores yet — complete a lesson to appear here!</p>
      )}

      {entries.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))]">
                  <th className="px-4 py-3 text-left w-10">#</th>
                  <th className="px-4 py-3 text-left">User</th>
                  <th className="px-4 py-3 text-right">Pts</th>
                  <th className="px-4 py-3 text-right hidden sm:table-cell">Lessons</th>
                  <th className="px-4 py-3 text-right hidden sm:table-cell">Streak</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr
                    key={entry.rank}
                    className={[
                      'border-b border-[hsl(var(--border))] last:border-0',
                      entry.is_self ? 'bg-[hsl(var(--accent))]' : 'hover:bg-[hsl(var(--muted)/0.4)]',
                    ].join(' ')}
                  >
                    <td className="px-4 py-3 text-[hsl(var(--muted-foreground))] font-mono">
                      {entry.rank === 1 ? '🥇' : entry.rank === 2 ? '🥈' : entry.rank === 3 ? '🥉' : entry.rank}
                    </td>
                    <td className="px-4 py-3 font-medium">
                      {entry.username}
                      {entry.is_self && (
                        <span className="ml-2 text-xs text-[hsl(var(--muted-foreground))]">(you)</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right font-semibold">{entry.total_points}</td>
                    <td className="px-4 py-3 text-right hidden sm:table-cell text-[hsl(var(--muted-foreground))]">
                      {entry.lessons_completed}
                    </td>
                    <td className="px-4 py-3 text-right hidden sm:table-cell text-[hsl(var(--muted-foreground))]">
                      {entry.current_streak > 0 ? `${entry.current_streak}d` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
