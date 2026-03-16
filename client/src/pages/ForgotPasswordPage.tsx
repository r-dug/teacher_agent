/**
 * Forgot password page: user enters email, we send a reset link.
 */

import { useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface ForgotPasswordPageProps {
  onGoLogin: () => void
}

export function ForgotPasswordPage({ onGoLogin }: ForgotPasswordPageProps) {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      if (!res.ok) {
        const data = await res.json()
        setError(data.detail ?? 'Something went wrong.')
        return
      }
      setSent(true)
    } catch {
      setError('Could not connect to the server.')
    } finally {
      setLoading(false)
    }
  }

  if (sent) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle className="text-xl">Check your inbox</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              If <strong>{email}</strong> is registered, you'll receive a reset link shortly.
            </p>
            <Button variant="outline" className="w-full" onClick={onGoLogin}>
              Back to sign in
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
          <CardTitle className="text-xl">Reset password</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label className="text-sm font-medium">Email</label>
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
              />
            </div>
            {error && (
              <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? 'Sending…' : 'Send reset link'}
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-[hsl(var(--muted-foreground))]">
            <button
              onClick={onGoLogin}
              className="text-[hsl(var(--primary))] underline-offset-2 hover:underline"
            >
              Back to sign in
            </button>
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
