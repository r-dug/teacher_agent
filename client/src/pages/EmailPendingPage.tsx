/**
 * Shown after registration: tells user to check email.
 * Includes spam-folder guidance, multi-resend with cooldown, and fallback contacts.
 */

import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

const RESEND_COOLDOWN = 30 // seconds before user can resend again

interface EmailPendingPageProps {
  email: string
  onGoLogin: () => void
}

export function EmailPendingPage({ email, onGoLogin }: EmailPendingPageProps) {
  const [sendCount, setSendCount] = useState(0)
  const [resending, setResending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(0) // seconds remaining

  // Tick down the cooldown
  useEffect(() => {
    if (cooldown <= 0) return
    const t = setTimeout(() => setCooldown(c => c - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])

  async function handleResend() {
    setResending(true)
    setError(null)
    try {
      const res = await fetch('/api/auth/resend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data.detail || 'Could not resend. Please try again.')
        return
      }
      setSendCount(n => n + 1)
      setCooldown(RESEND_COOLDOWN)
    } catch {
      setError('Network error. Please try again.')
    } finally {
      setResending(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-xl">Check your email</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">

          <p className="text-sm text-[hsl(var(--muted-foreground))] leading-relaxed">
            We sent a verification link to <strong className="text-[hsl(var(--foreground))]">{email}</strong>.
            Click it to activate your account — the link expires in 24 hours.
          </p>

          {/* Spam hint — always visible */}
          <div className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.4)] px-4 py-3 text-sm space-y-1">
            <p className="font-medium text-[hsl(var(--foreground))]">Can't find it?</p>
            <ul className="list-disc list-inside space-y-1 text-[hsl(var(--muted-foreground))]">
              <li>Check your <strong>spam or junk folder</strong> — it sometimes lands there</li>
              <li>Search for <em>noreply@tutorail.app</em></li>
              <li>Wait a minute and check again — delivery can be slow</li>
            </ul>
          </div>

          {/* Keep page open note */}
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Keep this page open — you can resend the email from here if needed.
          </p>

          {error && <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>}

          {sendCount > 0 && (
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              {sendCount === 1 ? 'New link sent.' : `Link resent ${sendCount} times.`}{' '}
              Check your inbox (and spam folder).
            </p>
          )}

          {/* Resend button with cooldown */}
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={handleResend}
            disabled={resending || cooldown > 0}
          >
            {resending
              ? 'Sending…'
              : cooldown > 0
              ? `Resend in ${cooldown}s`
              : sendCount === 0
              ? 'Resend verification email'
              : 'Resend again'}
          </Button>

          <div className="border-t border-[hsl(var(--border))] pt-3 space-y-2">
            {/* Wrong address? */}
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Wrong email address?{' '}
              <button
                onClick={onGoLogin}
                className="text-[hsl(var(--primary))] underline-offset-2 hover:underline"
              >
                Go back and register again
              </button>
            </p>

            {/* Last resort: sign-in link in case they verified another way */}
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Already verified?{' '}
              <button
                onClick={onGoLogin}
                className="text-[hsl(var(--primary))] underline-offset-2 hover:underline"
              >
                Sign in
              </button>
            </p>
          </div>

        </CardContent>
      </Card>
    </div>
  )
}
