/**
 * User settings drawer — accessible from all pages.
 *
 * Sections:
 *   • Account  — shows email, change-password form
 *   • Appearance — theme picker
 *   • Sign out
 */

import { useState } from 'react'
import { Drawer } from './Drawer'
import { Button } from './ui/button'
import { useTheme, type Theme } from '@/lib/theme'
import { cn } from '@/lib/utils'

interface SettingsDrawerProps {
  open: boolean
  onClose: () => void
  sessionId: string
  userEmail: string
  onLogout: () => void
}

const THEMES: { value: Theme; icon: string; label: string }[] = [
  { value: 'light',     icon: '☀️',  label: 'Light' },
  { value: 'dark',      icon: '🌙',  label: 'Dark' },
  { value: 'synthwave', icon: '🌆',  label: 'Synthwave' },
]

export function SettingsDrawer({ open, onClose, sessionId, userEmail, onLogout }: SettingsDrawerProps) {
  const { theme, setTheme } = useTheme()

  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [pwLoading, setPwLoading] = useState(false)
  const [pwError, setPwError] = useState<string | null>(null)
  const [pwSuccess, setPwSuccess] = useState(false)

  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  function resetPwForm() {
    setCurrentPw('')
    setNewPw('')
    setConfirmPw('')
    setPwError(null)
    setPwSuccess(false)
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault()
    setPwError(null)
    setPwSuccess(false)
    if (newPw !== confirmPw) {
      setPwError('New passwords do not match')
      return
    }
    if (newPw.length < 8) {
      setPwError('New password must be at least 8 characters')
      return
    }
    setPwLoading(true)
    try {
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setPwError(data.detail || 'Failed to change password')
        return
      }
      setPwSuccess(true)
      setCurrentPw('')
      setNewPw('')
      setConfirmPw('')
    } catch {
      setPwError('Network error. Please try again.')
    } finally {
      setPwLoading(false)
    }
  }

  async function handleDeleteAccount() {
    setDeleteLoading(true)
    setDeleteError(null)
    try {
      const res = await fetch('/api/auth/delete-account', {
        method: 'POST',
        headers: { 'X-Session-Id': sessionId },
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setDeleteError(data.detail || 'Failed to delete account')
        return
      }
      onLogout()
    } catch {
      setDeleteError('Network error. Please try again.')
    } finally {
      setDeleteLoading(false)
    }
  }

  function handleClose() {
    resetPwForm()
    setDeleteConfirm(false)
    setDeleteError(null)
    onClose()
  }

  return (
    <Drawer open={open} onClose={handleClose} title="Settings">
      <div className="space-y-7">

        {/* ── Account ─────────────────────────────────────────────────────── */}
        <section className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
            Account
          </h3>
          <p className="text-sm text-[hsl(var(--foreground))]">{userEmail}</p>

          <form onSubmit={handleChangePassword} className="space-y-2 pt-1">
            <p className="text-xs font-medium text-[hsl(var(--muted-foreground))]">Change password</p>
            <input
              type="password"
              placeholder="Current password"
              value={currentPw}
              onChange={e => setCurrentPw(e.target.value)}
              required
              className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
            />
            <input
              type="password"
              placeholder="New password"
              value={newPw}
              onChange={e => setNewPw(e.target.value)}
              required
              className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
            />
            <input
              type="password"
              placeholder="Confirm new password"
              value={confirmPw}
              onChange={e => setConfirmPw(e.target.value)}
              required
              className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
            />
            {pwError && (
              <p className="text-xs text-[hsl(var(--destructive))]">{pwError}</p>
            )}
            {pwSuccess && (
              <p className="text-xs text-[hsl(var(--primary))]">Password updated.</p>
            )}
            <Button type="submit" size="sm" disabled={pwLoading} className="w-full">
              {pwLoading ? 'Updating…' : 'Update password'}
            </Button>
          </form>
        </section>

        <div className="border-t border-[hsl(var(--border))]" />

        {/* ── Appearance ──────────────────────────────────────────────────── */}
        <section className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
            Appearance
          </h3>
          <div className="flex gap-2">
            {THEMES.map(t => (
              <button
                key={t.value}
                onClick={() => setTheme(t.value)}
                className={cn(
                  'flex flex-1 flex-col items-center gap-1 rounded-md border px-2 py-2 text-xs transition-colors',
                  theme === t.value
                    ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.08)] text-[hsl(var(--primary))] font-semibold'
                    : 'border-[hsl(var(--border))] text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))]',
                )}
              >
                <span className="text-base">{t.icon}</span>
                <span>{t.label}</span>
              </button>
            ))}
          </div>
        </section>

        <div className="border-t border-[hsl(var(--border))]" />

        {/* ── Sign out ────────────────────────────────────────────────────── */}
        <section>
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => { handleClose(); onLogout() }}
          >
            Sign out
          </Button>
        </section>

        <div className="border-t border-[hsl(var(--border))]" />

        {/* ── Danger zone ─────────────────────────────────────────────────── */}
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-[hsl(var(--destructive))]">
            Danger zone
          </h3>
          {!deleteConfirm ? (
            <Button
              variant="outline"
              size="sm"
              className="w-full border-[hsl(var(--destructive)/0.4)] text-[hsl(var(--destructive))] hover:bg-[hsl(var(--destructive)/0.08)]"
              onClick={() => setDeleteConfirm(true)}
            >
              Delete account
            </Button>
          ) : (
            <div className="space-y-2 rounded-md border border-[hsl(var(--destructive)/0.4)] p-3">
              <p className="text-xs text-[hsl(var(--foreground))]">
                This will permanently delete your account and all data. This cannot be undone.
              </p>
              {deleteError && (
                <p className="text-xs text-[hsl(var(--destructive))]">{deleteError}</p>
              )}
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="flex-1"
                  onClick={() => { setDeleteConfirm(false); setDeleteError(null) }}
                  disabled={deleteLoading}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  className="flex-1 bg-[hsl(var(--destructive))] text-white hover:bg-[hsl(var(--destructive)/0.9)]"
                  onClick={handleDeleteAccount}
                  disabled={deleteLoading}
                >
                  {deleteLoading ? 'Deleting…' : 'Yes, delete'}
                </Button>
              </div>
            </div>
          )}
        </section>

      </div>
    </Drawer>
  )
}
