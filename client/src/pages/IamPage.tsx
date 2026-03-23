import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { IamUser } from '@/lib/types'

interface IamPageProps {
  sessionId: string
  isAdmin: boolean
  currentUserId: string
}

interface IamUsersResponse {
  users: IamUser[]
}

interface IamSetAdminResponse {
  user: IamUser
  warning?: string | null
}

function extractDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  return null
}

export function IamPage({ sessionId, isAdmin, currentUserId }: IamPageProps) {
  const navigate = useNavigate()
  const [users, setUsers] = useState<IamUser[]>([])
  const [loading, setLoading] = useState(true)
  const [savingUserId, setSavingUserId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [warningBanner, setWarningBanner] = useState<string | null>(null)

  const loadUsers = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await fetch('/api/admin/iam/users', {
        headers: { 'X-Session-Id': sessionId },
      })
      const payload: IamUsersResponse | { detail?: string } = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        throw new Error(extractDetail(payload) || `Failed to load IAM users (${resp.status})`)
      }
      setUsers(Array.isArray((payload as IamUsersResponse).users) ? (payload as IamUsersResponse).users : [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load IAM users')
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    if (!isAdmin) {
      navigate('/', { replace: true })
      return
    }
    void loadUsers()
  }, [isAdmin, navigate, loadUsers])

  async function handleSetAdmin(user: IamUser, nextIsAdmin: boolean) {
    if (!nextIsAdmin && user.id === currentUserId) return
    if (!nextIsAdmin) {
      const confirmed = window.confirm(`Demote ${user.email || user.id} from admin access?`)
      if (!confirmed) return
    }

    setSavingUserId(user.id)
    setError(null)
    setSuccess(null)
    setWarningBanner(null)

    try {
      const resp = await fetch(`/api/admin/iam/users/${user.id}/admin`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-Id': sessionId,
        },
        body: JSON.stringify({ is_admin: nextIsAdmin }),
      })
      const payload: IamSetAdminResponse | { detail?: string } = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        throw new Error(extractDetail(payload) || `Update failed (${resp.status})`)
      }

      if ((payload as IamSetAdminResponse).warning) {
        setWarningBanner((payload as IamSetAdminResponse).warning || null)
      }
      setSuccess(nextIsAdmin ? 'Admin granted successfully.' : 'Admin removed successfully.')
      await loadUsers()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update admin access')
    } finally {
      setSavingUserId(null)
    }
  }

  if (!isAdmin) return null

  return (
    <div className="min-h-screen bg-[hsl(var(--background))] text-[hsl(var(--foreground))]">
      <div className="sticky top-0 z-10 border-b border-[hsl(var(--border))] bg-[hsl(var(--background)/0.95)] backdrop-blur px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/')}
            className="text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
          >
            ← Back
          </button>
          <span className="font-semibold text-sm">IAM Management</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[hsl(var(--destructive)/0.15)] text-[hsl(var(--destructive))]">Admin</span>
        </div>
        <Button variant="outline" size="sm" onClick={() => void loadUsers()} disabled={loading || savingUserId !== null}>
          Refresh
        </Button>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-6 space-y-4">
        {error && (
          <div className="rounded-md border border-[hsl(var(--destructive)/0.45)] bg-[hsl(var(--destructive)/0.1)] px-3 py-2 text-sm text-[hsl(var(--destructive))]">
            {error}
          </div>
        )}
        {success && (
          <div className="rounded-md border border-[hsl(var(--primary)/0.4)] bg-[hsl(var(--primary)/0.12)] px-3 py-2 text-sm">
            {success}
          </div>
        )}
        {warningBanner && (
          <div className="rounded-md border border-amber-400/60 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            {warningBanner}
          </div>
        )}

        <div className="overflow-x-auto rounded-lg border border-[hsl(var(--border))]">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[hsl(var(--border))] bg-[hsl(var(--muted))]">
                <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Email</th>
                <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Verified</th>
                <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Admin</th>
                <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Bootstrap</th>
                <th className="px-3 py-2 text-right font-medium text-[hsl(var(--muted-foreground))]">Action</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-center text-[hsl(var(--muted-foreground))]">
                    Loading IAM users…
                  </td>
                </tr>
              )}
              {!loading && users.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-center text-[hsl(var(--muted-foreground))]">
                    No users found.
                  </td>
                </tr>
              )}
              {!loading && users.map((user) => {
                const isCurrentAdmin = user.id === currentUserId && user.is_admin
                const actionDisabled = savingUserId !== null || isCurrentAdmin
                return (
                  <tr key={user.id} className="border-b border-[hsl(var(--border))] last:border-0">
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span>{user.email || user.id}</span>
                        {user.id === currentUserId && <Badge variant="outline">You</Badge>}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <Badge variant={user.email_verified ? 'secondary' : 'outline'}>
                        {user.email_verified ? 'Verified' : 'Unverified'}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      <Badge variant={user.is_admin ? 'default' : 'outline'}>
                        {user.is_admin ? 'Admin' : 'Member'}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      {user.bootstrap_managed ? (
                        <Badge variant="outline">ADMIN_EMAILS</Badge>
                      ) : (
                        <span className="text-[hsl(var(--muted-foreground))]">No</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        size="sm"
                        variant={user.is_admin ? 'destructive' : 'default'}
                        onClick={() => void handleSetAdmin(user, !user.is_admin)}
                        disabled={actionDisabled}
                      >
                        {savingUserId === user.id ? 'Saving…' : user.is_admin ? 'Demote' : 'Promote'}
                      </Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
