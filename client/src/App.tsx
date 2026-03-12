/**
 * Root application component.
 *
 * Auth flow:
 *  1. On mount, read session_id from localStorage.
 *  2. If found, call GET /api/auth/me to validate — if OK, enter app.
 *  3. If missing / invalid, show auth screens (login / register / verify).
 *
 * Routing (authenticated):
 *  /            → LessonPickerPage
 *  /teach/:id   → TeachPage
 *
 * Routing (unauthenticated):
 *  /auth/verify → EmailVerifyPage  (handles email verification links)
 *  *            → LoginPage / RegisterPage / EmailPendingPage
 */

import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'

import { LessonPickerPage } from './pages/LessonPickerPage'
import { TeachPage } from './pages/TeachPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { EmailPendingPage } from './pages/EmailPendingPage'
import { EmailVerifyPage } from './pages/EmailVerifyPage'

type AuthPage = 'login' | 'register' | 'pending'

const SESSION_KEY = 'session_id'

export default function App() {
  const [authState, setAuthState] = useState<'loading' | 'authenticated' | 'unauthenticated'>('loading')
  const [sessionId, setSessionId] = useState<string | null>(null)
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
      .then(() => {
        setSessionId(stored)
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
    setAuthState('authenticated')
  }

  function onLogout() {
    const sid = sessionId
    localStorage.removeItem(SESSION_KEY)
    setSessionId(null)
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
          <Route path="/" element={<LessonPickerPage sessionId={sessionId} onLogout={onLogout} />} />
          <Route path="/teach/:lessonId" element={<TeachPage sessionId={sessionId} />} />
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
          path="*"
          element={
            authPage === 'pending' ? (
              <EmailPendingPage email={pendingEmail} onGoLogin={() => setAuthPage('login')} />
            ) : authPage === 'register' ? (
              <RegisterPage onPending={onRegisterSuccess} onGoLogin={() => setAuthPage('login')} />
            ) : (
              <LoginPage onLogin={onLogin} onGoRegister={() => setAuthPage('register')} />
            )
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
