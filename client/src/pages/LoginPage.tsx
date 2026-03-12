/**
 * Login page: email + password form.
 */

import { useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface LoginPageProps {
  onLogin: (sessionId: string) => void
  onGoRegister: () => void
}

export function LoginPage({ onLogin, onGoRegister }: LoginPageProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const data = await res.json()
      if (!res.ok) {
        if (data.detail === 'email_not_verified') {
          setError('Please verify your email before logging in. Check your inbox.')
        } else {
          setError(data.detail ?? 'Login failed')
        }
        return
      }
      onLogin(data.session_id)
    } catch {
      setError('Could not connect to the server.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">Sign in</CardTitle>
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
            <div className="space-y-1">
              <label className="text-sm font-medium">Password</label>
              <input
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
              />
            </div>
            {error && (
              <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-[hsl(var(--muted-foreground))]">
            Don&apos;t have an account?{' '}
            <button
              onClick={onGoRegister}
              className="text-[hsl(var(--primary))] underline-offset-2 hover:underline"
            >
              Create one
            </button>
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
