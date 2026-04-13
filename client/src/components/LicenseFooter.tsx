/**
 * LicenseFooter — sits below the main content on every page.
 *
 * Uses the theme's CSS variables for colours so it adapts to dark/light mode.
 */

export function LicenseFooter() {
  return (
    <footer className="w-full border-t border-[hsl(var(--border))] py-2 text-center text-[11px] text-[hsl(var(--muted-foreground))]">
      &copy; 2026 TutorAIl &middot; Provided as-is, no warranty &middot;{' '}
      <a
        href="/terms"
        className="underline underline-offset-2 hover:text-[hsl(var(--foreground))]"
      >
        Terms &amp; License
      </a>
    </footer>
  )
}
