/**
 * Shown after registration: tells user to check email.
 */

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface EmailPendingPageProps {
  email: string
  onGoLogin: () => void
}

export function EmailPendingPage({ email, onGoLogin }: EmailPendingPageProps) {
  const [resent, setResent] = useState(false)
  const [resending, setResending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleResend() {
    setResending(true)
    setError(null)
    try {
      await fetch('/api/auth/resend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      setResent(true)
    } catch {
      setError('Could not resend. Please try again.')
    } finally {
      setResending(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">Check your email</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            We sent a verification link to <strong>{email}</strong>. Click it to activate your
            account. The link expires in 24 hours.
          </p>
          {error && <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>}
          {resent ? (
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              New link sent — check your inbox.
            </p>
          ) : (
            <Button variant="ghost" size="sm" onClick={handleResend} disabled={resending}>
              {resending ? 'Sending…' : 'Resend verification email'}
            </Button>
          )}
          <p className="text-center text-sm text-[hsl(var(--muted-foreground))]">
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
