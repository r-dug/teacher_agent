/**
 * Root application component.
 *
 * Auth flow:
 *  1. On mount, read session_id from localStorage.
 *  2. If found, call GET /api/auth/me to validate — if OK, enter app.
 *  3. If missing / invalid, show auth screens (login / register / verify).
 *
 * Routing (authenticated):
 *  /                  → HomePage (courses + individual lessons)
 *  /courses/:courseId → CoursePage
 *  /teach/:id         → TeachPage
 *  /admin/usage       → UsageDashboardPage (admin only)
 *
 * Routing (unauthenticated):
 *  /auth/verify → EmailVerifyPage  (handles email verification links)
 *  *            → LoginPage / RegisterPage / EmailPendingPage
 */

import { lazy, Suspense, useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'

import { HomePage } from './pages/HomePage'
import { CoursePage } from './pages/CoursePage'
import { TeachPage } from './pages/TeachPage'
const UsageDashboardPage = lazy(() =>
  import('./pages/UsageDashboardPage').then(m => ({ default: m.UsageDashboardPage }))
)
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { EmailPendingPage } from './pages/EmailPendingPage'
import { EmailVerifyPage } from './pages/EmailVerifyPage'
import { ForgotPasswordPage } from './pages/ForgotPasswordPage'
import { ResetPasswordPage } from './pages/ResetPasswordPage'

type AuthPage = 'login' | 'register' | 'pending' | 'forgot'

const SESSION_KEY = 'session_id'

export default function App() {
  const [authState, setAuthState] = useState<'loading' | 'authenticated' | 'unauthenticated'>('loading')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [isAdmin, setIsAdmin] = useState(false)
  const [authPage, setAuthPage] = useState<AuthPage>('login')
  const [pendingEmail, setPendingEmail] = useState('')

  // ── session bootstrap ────────────────────────────────────────────────────
  useEffect(() => {
    const stored = localStorage.getItem(SESSION_KEY)
    if (!stored) {
      setAuthState('unauthenticated')
      return
    }
    fetch('/api/auth/me', { headers: { 'X-Session-Id': stored } })
      .then((r) => {
        if (!r.ok) throw new Error('invalid')
        return r.json()
      })
      .then((data) => {
        setSessionId(stored)
        setIsAdmin(Boolean(data.is_admin))
        setAuthState('authenticated')
      })
      .catch(() => {
        localStorage.removeItem(SESSION_KEY)
        setAuthState('unauthenticated')
      })
  }, [])

  // ── auth callbacks ───────────────────────────────────────────────────────
  function onLogin(sid: string) {
    localStorage.setItem(SESSION_KEY, sid)
    setSessionId(sid)
    // Re-fetch /me to get is_admin
    fetch('/api/auth/me', { headers: { 'X-Session-Id': sid } })
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setIsAdmin(Boolean(data.is_admin)) })
      .catch(() => {})
    setAuthState('authenticated')
  }

  function onLogout() {
    const sid = sessionId
    localStorage.removeItem(SESSION_KEY)
    setSessionId(null)
    setIsAdmin(false)
    setAuthState('unauthenticated')
    setAuthPage('login')
    if (sid) {
      fetch('/api/auth/logout', { method: 'POST', headers: { 'X-Session-Id': sid } }).catch(() => {})
    }
  }

  function onRegisterSuccess(email: string) {
    setPendingEmail(email)
    setAuthPage('pending')
  }

  // ── loading ──────────────────────────────────────────────────────────────
  if (authState === 'loading') {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading…</p>
      </div>
    )
  }

  // ── authenticated app ────────────────────────────────────────────────────
  if (authState === 'authenticated' && sessionId) {
    return (
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomePage sessionId={sessionId} onLogout={onLogout} isAdmin={isAdmin} />} />
          <Route path="/courses/:courseId" element={<CoursePage sessionId={sessionId} isAdmin={isAdmin} />} />
          <Route path="/teach/:lessonId" element={<TeachPage sessionId={sessionId} isAdmin={isAdmin} />} />
          <Route path="/admin/usage" element={
            <Suspense fallback={<div className="flex h-screen items-center justify-center text-sm text-[hsl(var(--muted-foreground))]">Loading…</div>}>
              <UsageDashboardPage sessionId={sessionId} isAdmin={isAdmin} />
            </Suspense>
          } />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    )
  }

  // ── unauthenticated ──────────────────────────────────────────────────────
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/auth/verify"
          element={<EmailVerifyPage onLogin={onLogin} onGoLogin={() => setAuthPage('login')} />}
        />
        <Route
          path="/auth/reset-password"
          element={<ResetPasswordPage onGoLogin={() => setAuthPage('login')} />}
        />
        <Route
          path="*"
          element={
            authPage === 'pending' ? (
              <EmailPendingPage email={pendingEmail} onGoLogin={() => setAuthPage('login')} />
            ) : authPage === 'register' ? (
              <RegisterPage onPending={onRegisterSuccess} onGoLogin={() => setAuthPage('login')} />
            ) : authPage === 'forgot' ? (
              <ForgotPasswordPage onGoLogin={() => setAuthPage('login')} />
            ) : (
              <LoginPage onLogin={onLogin} onGoRegister={() => setAuthPage('register')} onForgotPassword={() => setAuthPage('forgot')} />
            )
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
