/**
 * Password reset handler — rendered at /auth/reset-password?token=...
 *
 * On submit, calls POST /api/auth/reset-password with the token and new password,
 * then redirects to login.
 */

import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface ResetPasswordPageProps {
  onGoLogin: () => void
}

export function ResetPasswordPage({ onGoLogin }: ResetPasswordPageProps) {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [loading, setLoading] = useState(false)

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4 text-center">
        <div className="space-y-3">
          <p className="font-semibold text-[hsl(var(--destructive))]">Invalid reset link</p>
          <button
            onClick={onGoLogin}
            className="text-sm text-[hsl(var(--primary))] underline-offset-2 hover:underline"
          >
            Back to sign in
          </button>
        </div>
      </div>
    )
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError('Passwords do not match')
      return
    }
    setLoading(true)
    try {
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail ?? 'Reset failed')
        return
      }
      setDone(true)
    } catch {
      setError('Could not connect to the server.')
    } finally {
      setLoading(false)
    }
  }

  if (done) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle className="text-xl">Password updated</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              Your password has been reset. You can now sign in with your new password.
            </p>
            <Button className="w-full" onClick={onGoLogin}>
              Sign in
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">Set new password</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label className="text-sm font-medium">New password</label>
              <input
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
              />
              <p className="text-xs text-[hsl(var(--muted-foreground))]">Minimum 8 characters</p>
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium">Confirm password</label>
              <input
                type="password"
                required
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
              />
            </div>
            {error && (
              <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? 'Updating…' : 'Update password'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
