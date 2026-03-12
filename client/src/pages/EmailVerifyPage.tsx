/**
 * Email verification handler — rendered at /auth/verify?token=...
 *
 * On mount, reads the token from the URL, calls POST /api/auth/verify,
 * stores the returned session_id, then redirects to /.
 */

import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

interface EmailVerifyPageProps {
  onLogin: (sessionId: string) => void
  onGoLogin: () => void
}

export function EmailVerifyPage({ onLogin, onGoLogin }: EmailVerifyPageProps) {
  const [searchParams] = useSearchParams()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = searchParams.get('token')
    if (!token) {
      setError('Missing verification token.')
      return
    }
    fetch('/api/auth/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    })
      .then((r) => {
        if (!r.ok) return r.json().then((d) => Promise.reject(d.detail ?? 'Verification failed'))
        return r.json()
      })
      .then((data: { session_id: string }) => {
        onLogin(data.session_id)
      })
      .catch((msg: string) => {
        setError(typeof msg === 'string' ? msg : 'Invalid or expired verification link.')
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4 text-center">
        <div className="space-y-3">
          <p className="font-semibold text-[hsl(var(--destructive))]">Verification failed</p>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">{error}</p>
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

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-sm text-[hsl(var(--muted-foreground))]">Verifying your email…</p>
    </div>
  )
}
