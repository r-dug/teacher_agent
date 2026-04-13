/**
 * Terms of Use page — public, no auth required.
 *
 * Rendered as a short-circuit route from App.tsx so the footer link works
 * regardless of whether the visitor is signed in.
 */

import { useEffect, useState } from 'react'

interface TermsPayload {
  version: string
  text: string
  license_name: string
  license_url: string
  copyright_holder: string
  copyright_year: string
}

export function TermsPage() {
  const [terms, setTerms] = useState<TermsPayload | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/terms')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('fetch failed'))))
      .then(setTerms)
      .catch(() => setError('Could not load the Terms of Use.'))
  }, [])

  return (
    <div className="min-h-screen bg-[hsl(var(--background))] text-[hsl(var(--foreground))]">
      <div className="mx-auto max-w-3xl px-4 py-10">
        <a
          href="/"
          className="text-sm text-[hsl(var(--muted-foreground))] underline-offset-2 hover:underline"
        >
          ← Back
        </a>
        <h1 className="mt-4 text-2xl font-semibold">Terms of Use & License</h1>
        {error && (
          <p className="mt-6 text-sm text-[hsl(var(--destructive))]">{error}</p>
        )}
        {!terms && !error && (
          <p className="mt-6 text-sm text-[hsl(var(--muted-foreground))]">Loading…</p>
        )}
        {terms && (
          <>
            <p className="mt-2 text-xs text-[hsl(var(--muted-foreground))]">
              Version {terms.version}
            </p>
            <pre className="mt-6 whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">
              {terms.text}
            </pre>
            <div className="mt-8 border-t border-[hsl(var(--border))] pt-4 text-xs text-[hsl(var(--muted-foreground))]">
              © {terms.copyright_year} {terms.copyright_holder} — Source code
              available under the{' '}
              <a
                href={terms.license_url}
                target="_blank"
                rel="noopener noreferrer"
                className="underline-offset-2 hover:underline"
              >
                {terms.license_name}
              </a>
              .
            </div>
          </>
        )}
      </div>
    </div>
  )
}
