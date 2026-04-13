/**
 * TermsModal — blocking full-screen modal shown when the signed-in user has
 * not accepted the current Terms of Service version.
 *
 * Cannot be dismissed without either (a) clicking "I Agree" (which records
 * acceptance server-side and closes) or (b) signing out.
 */

import { useEffect, useState } from 'react'

interface TermsModalProps {
  sessionId: string
  onAccepted: () => void
  onLogout: () => void
}

interface TermsPayload {
  version: string
  text: string
  license_name: string
  license_url: string
  copyright_holder: string
  copyright_year: string
}

export function TermsModal({ sessionId, onAccepted, onLogout }: TermsModalProps) {
  const [terms, setTerms] = useState<TermsPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/terms')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('fetch failed'))))
      .then((data: TermsPayload) => {
        if (!cancelled) setTerms(data)
      })
      .catch(() => {
        if (!cancelled) setError('Could not load the Terms of Use. Please try again.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleAccept() {
    setSubmitting(true)
    setError(null)
    try {
      const res = await fetch('/api/terms/accept', {
        method: 'POST',
        headers: { 'X-Session-Id': sessionId },
      })
      if (!res.ok && res.status !== 204) {
        throw new Error('accept failed')
      }
      onAccepted()
    } catch {
      setError('Could not record your acceptance. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="terms-modal-title"
    >
      <div className="flex h-full max-h-[90vh] w-full max-w-2xl flex-col rounded-lg bg-[hsl(var(--background))] shadow-xl">
        <div className="border-b border-[hsl(var(--border))] px-5 py-3">
          <h2 id="terms-modal-title" className="text-base font-semibold">
            Terms of Use & Liability Waiver
          </h2>
          <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
            Please review and accept before continuing.
          </p>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 text-sm leading-relaxed">
          {loading && (
            <p className="text-[hsl(var(--muted-foreground))]">Loading…</p>
          )}
          {error && !terms && (
            <p className="text-[hsl(var(--destructive))]">{error}</p>
          )}
          {terms && (
            <pre className="whitespace-pre-wrap break-words font-sans text-[hsl(var(--foreground))]">
              {terms.text}
            </pre>
          )}
        </div>

        <div className="border-t border-[hsl(var(--border))] px-5 py-3">
          {error && terms && (
            <p className="mb-2 text-xs text-[hsl(var(--destructive))]">{error}</p>
          )}
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={onLogout}
              className="text-xs text-[hsl(var(--muted-foreground))] underline-offset-2 hover:underline"
            >
              Decline and sign out
            </button>
            <button
              type="button"
              onClick={handleAccept}
              disabled={!terms || submitting}
              className="rounded bg-[hsl(var(--primary))] px-4 py-1.5 text-sm font-medium text-[hsl(var(--primary-foreground))] transition hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? 'Saving…' : 'I Agree'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
