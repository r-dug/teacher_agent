/**
 * User settings drawer — accessible from all pages.
 *
 * Sections:
 *   • Account  — shows email, change-password form
 *   • Appearance — theme picker
 *   • Sketchpad defaults — tool, color, brush size, opacity
 *   • Sign out
 */

import { useState, useEffect } from 'react'
import { Drawer } from './Drawer'
import { Button } from './ui/button'
import { useTheme, type Theme } from '@/lib/theme'
import { cn } from '@/lib/utils'

type SketchTool = 'pen' | 'highlighter' | 'dashed' | 'line' | 'rect' | 'circle' | 'text' | 'fill' | 'eraser'

const SKETCH_COLORS = ['#1a1a1a', '#e53e3e', '#3182ce', '#38a169', '#d69e2e', '#805ad5', '#dd6b20', '#d53f8c']
const SKETCH_TOOLS: { value: SketchTool; label: string }[] = [
  { value: 'pen', label: 'Pen' },
  { value: 'highlighter', label: 'Highlight' },
  { value: 'dashed', label: 'Dashed' },
  { value: 'line', label: 'Line' },
  { value: 'rect', label: 'Rect' },
  { value: 'circle', label: 'Circle' },
  { value: 'text', label: 'Text' },
  { value: 'fill', label: 'Fill' },
  { value: 'eraser', label: 'Eraser' },
]

interface SettingsDrawerProps {
  open: boolean
  onClose: () => void
  sessionId: string
  userEmail: string
  username?: string
  onUsernameChange?: (newUsername: string) => void
  onLogout: () => void
}

const THEMES: { value: Theme; icon: string; label: string }[] = [
  { value: 'light',     icon: '☀️',  label: 'Light' },
  { value: 'dark',      icon: '🌙',  label: 'Dark' },
  { value: 'synthwave', icon: '🌆',  label: 'Synthwave' },
]

export function SettingsDrawer({ open, onClose, sessionId, userEmail, username = '', onUsernameChange, onLogout }: SettingsDrawerProps) {
  const { theme, setTheme } = useTheme()

  const [editUsername, setEditUsername] = useState(username)
  const [usernameSaving, setUsernameSaving] = useState(false)
  const [usernameMsg, setUsernameMsg] = useState<string | null>(null)

  // Sync local state when prop changes
  useEffect(() => { setEditUsername(username) }, [username])

  async function handleUsernameSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = editUsername.trim()
    if (!trimmed || trimmed === username) return
    setUsernameSaving(true)
    setUsernameMsg(null)
    try {
      const resp = await fetch('/api/auth/username', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({ username: trimmed }),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(data.detail || 'Failed to update username')
      onUsernameChange?.(data.username)
      setUsernameMsg('Username updated.')
    } catch (err) {
      setUsernameMsg(err instanceof Error ? err.message : 'Failed to update username')
    } finally {
      setUsernameSaving(false)
    }
  }

  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [pwLoading, setPwLoading] = useState(false)
  const [pwError, setPwError] = useState<string | null>(null)
  const [pwSuccess, setPwSuccess] = useState(false)

  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  // Sketchpad defaults
  const [sketchTool, setSketchTool] = useState<SketchTool>('pen')
  const [sketchColor, setSketchColor] = useState('#1a1a1a')
  const [sketchSize, setSketchSize] = useState(5)
  const [sketchOpacity, setSketchOpacity] = useState(100)
  const [sketchLoaded, setSketchLoaded] = useState(false)
  const [sketchSaving, setSketchSaving] = useState(false)

  // Training data consent
  const [trainingConsent, setTrainingConsent] = useState(false)
  const [trainingConsentSaving, setTrainingConsentSaving] = useState(false)

  useEffect(() => {
    if (!open || sketchLoaded) return
    fetch('/api/preferences', { headers: { 'X-Session-Id': sessionId } })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        const sp = data?.prefs?.sketchpad
        if (sp) {
          if (sp.tool) setSketchTool(sp.tool)
          if (sp.color) setSketchColor(sp.color)
          if (sp.brushSize != null) setSketchSize(sp.brushSize)
          if (sp.opacity != null) setSketchOpacity(Math.round(sp.opacity * 100))
        }
        if (data?.prefs?.training_consent) setTrainingConsent(true)
        setSketchLoaded(true)
      })
      .catch(() => setSketchLoaded(true))
  }, [open, sessionId, sketchLoaded])

  function saveSketchPrefs(overrides: Partial<{ tool: SketchTool; color: string; brushSize: number; opacity: number }>) {
    const prefs = {
      tool: overrides.tool ?? sketchTool,
      color: overrides.color ?? sketchColor,
      brushSize: overrides.brushSize ?? sketchSize,
      opacity: (overrides.opacity ?? sketchOpacity) / 100,
    }
    setSketchSaving(true)
    fetch('/api/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
      body: JSON.stringify({ prefs: { sketchpad: prefs } }),
    })
      .catch(() => {})
      .finally(() => setSketchSaving(false))
  }

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
    setSketchLoaded(false)
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

          <form onSubmit={handleUsernameSubmit} className="space-y-2 pt-1">
            <p className="text-xs font-medium text-[hsl(var(--muted-foreground))]">Username</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={editUsername}
                onChange={e => setEditUsername(e.target.value)}
                placeholder="username"
                className="flex-1 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
              />
              <Button type="submit" size="sm" disabled={usernameSaving || editUsername.trim() === username}>
                {usernameSaving ? 'Saving…' : 'Save'}
              </Button>
            </div>
            {usernameMsg && <p className="text-xs text-[hsl(var(--muted-foreground))]">{usernameMsg}</p>}
          </form>

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

        {/* ── Sketchpad Defaults ─────────────────────────────────────────── */}
        <section className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
            Sketchpad defaults
          </h3>

          {/* Default tool */}
          <div className="space-y-1">
            <p className="text-xs text-[hsl(var(--muted-foreground))]">Default tool</p>
            <div className="flex flex-wrap gap-1">
              {SKETCH_TOOLS.map(t => (
                <button
                  key={t.value}
                  onClick={() => { setSketchTool(t.value); saveSketchPrefs({ tool: t.value }) }}
                  className={cn(
                    'rounded px-2 py-1 text-xs border transition-colors',
                    sketchTool === t.value
                      ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.1)] text-[hsl(var(--primary))]'
                      : 'border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))]'
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Default color */}
          <div className="space-y-1">
            <p className="text-xs text-[hsl(var(--muted-foreground))]">Default color</p>
            <div className="flex items-center gap-1">
              {SKETCH_COLORS.map(c => (
                <button
                  key={c}
                  onClick={() => { setSketchColor(c); saveSketchPrefs({ color: c }) }}
                  className={cn(
                    'rounded-full w-6 h-6 border-2 transition-all',
                    sketchColor === c ? 'border-[hsl(var(--primary))] scale-110' : 'border-[hsl(var(--border))]'
                  )}
                  style={{ backgroundColor: c }}
                />
              ))}
              <label
                className="relative w-6 h-6 rounded-full border-2 border-[hsl(var(--border))] overflow-hidden cursor-pointer"
                style={{
                  background: 'conic-gradient(red, yellow, lime, cyan, blue, magenta, red)',
                  borderColor: !SKETCH_COLORS.includes(sketchColor) ? 'hsl(var(--primary))' : undefined,
                }}
              >
                <input
                  type="color"
                  value={sketchColor}
                  onChange={e => { setSketchColor(e.target.value); saveSketchPrefs({ color: e.target.value }) }}
                  className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
                />
              </label>
            </div>
          </div>

          {/* Default brush size */}
          <div className="space-y-1">
            <p className="text-xs text-[hsl(var(--muted-foreground))]">Default brush size</p>
            <div className="flex items-center gap-2">
              <input
                type="range" min={1} max={30} value={sketchSize}
                onChange={e => { const v = Number(e.target.value); setSketchSize(v); saveSketchPrefs({ brushSize: v }) }}
                className="w-32 h-1 accent-[hsl(var(--primary))]"
              />
              <span className="text-xs tabular-nums w-5 text-right">{sketchSize}</span>
            </div>
          </div>

          {/* Default opacity */}
          <div className="space-y-1">
            <p className="text-xs text-[hsl(var(--muted-foreground))]">Default opacity</p>
            <div className="flex items-center gap-2">
              <input
                type="range" min={10} max={100} value={sketchOpacity}
                onChange={e => { const v = Number(e.target.value); setSketchOpacity(v); saveSketchPrefs({ opacity: v }) }}
                className="w-32 h-1 accent-[hsl(var(--primary))]"
              />
              <span className="text-xs tabular-nums w-8 text-right">{sketchOpacity}%</span>
            </div>
          </div>

          {sketchSaving && (
            <p className="text-xs text-[hsl(var(--muted-foreground))]">Saving...</p>
          )}
        </section>

        <div className="border-t border-[hsl(var(--border))]" />

        {/* ── Data & Privacy ─────────────────────────────────────────────── */}
        <section className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
            Data &amp; Privacy
          </h3>
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={trainingConsent}
              onChange={e => {
                const checked = e.target.checked
                setTrainingConsent(checked)
                setTrainingConsentSaving(true)
                fetch('/api/preferences', {
                  method: 'PUT',
                  headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
                  body: JSON.stringify({
                    prefs: {
                      training_consent: checked,
                      training_consent_at: checked ? new Date().toISOString() : null,
                    },
                  }),
                })
                  .catch(() => setTrainingConsent(!checked))
                  .finally(() => setTrainingConsentSaving(false))
              }}
              className="mt-0.5 h-4 w-4 rounded border-[hsl(var(--border))] accent-[hsl(var(--primary))]"
            />
            <div className="space-y-1">
              <p className="text-sm text-[hsl(var(--foreground))]">
                Help improve our AI teacher
              </p>
              <p className="text-xs text-[hsl(var(--muted-foreground))] leading-relaxed">
                Allow your session conversations (text only, not audio or images) to be used
                for training smaller, more efficient AI models. Data is anonymized before use.
                You can opt out at any time.
              </p>
            </div>
          </label>
          {trainingConsentSaving && (
            <p className="text-xs text-[hsl(var(--muted-foreground))]">Saving...</p>
          )}
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
