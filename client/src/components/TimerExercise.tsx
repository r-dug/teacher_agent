/**
 * TimerExercise: timed exercise overlay.
 *
 * Shows a countdown timer, an optional text input for the student's answer,
 * and a Submit button. Auto-submits when the timer reaches zero.
 * Supports minimize so the student can refer to the slide while working.
 */

import { useEffect, useRef, useState } from 'react'
import { Button } from './ui/button'
import { cn } from '@/lib/utils'

interface TimerExerciseProps {
  prompt: string
  invocationId: string
  durationSeconds: number
  onSubmit: (invocationId: string, timedOut: boolean, answer: string, elapsedSeconds: number) => void
  onCancel: (invocationId: string) => void
}

export function TimerExercise({ prompt, invocationId, durationSeconds, onSubmit, onCancel }: TimerExerciseProps) {
  const [remaining, setRemaining] = useState(durationSeconds)
  const [answer, setAnswer] = useState('')
  const [minimized, setMinimized] = useState(false)
  const [expired, setExpired] = useState(false)
  const startRef = useRef(Date.now())
  const submittedRef = useRef(false)
  // Keep a ref in sync with answer state so auto-submit closure always sees current value
  const answerRef = useRef('')

  function updateAnswer(v: string) {
    answerRef.current = v
    setAnswer(v)
  }

  useEffect(() => {
    const interval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startRef.current) / 1000)
      const left = Math.max(0, durationSeconds - elapsed)
      setRemaining(left)
      if (left === 0) {
        clearInterval(interval)
        setExpired(true)
      }
    }, 250)
    return () => clearInterval(interval)
  }, [durationSeconds])

  // Auto-submit on expiry — use ref so we always get the latest answer
  useEffect(() => {
    if (expired && !submittedRef.current) {
      submittedRef.current = true
      const elapsed = Math.floor((Date.now() - startRef.current) / 1000)
      onSubmit(invocationId, true, answerRef.current, elapsed)
    }
  }, [expired])

  function handleSubmit() {
    if (submittedRef.current) return
    submittedRef.current = true
    const elapsed = Math.floor((Date.now() - startRef.current) / 1000)
    onSubmit(invocationId, false, answerRef.current, elapsed)
  }

  function handleCancel() {
    if (submittedRef.current) return
    submittedRef.current = true
    onCancel(invocationId)
  }

  const mins = Math.floor(remaining / 60)
  const secs = remaining % 60
  const timeStr = mins > 0
    ? `${mins}:${String(secs).padStart(2, '0')}`
    : `${secs}s`

  const urgency = remaining <= 10 ? 'text-red-500' : remaining <= 30 ? 'text-amber-500' : 'text-[hsl(var(--foreground))]'

  if (minimized) {
    return (
      <button
        onClick={() => setMinimized(false)}
        className={cn(
          'fixed bottom-24 right-4 z-50 flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium shadow-lg hover:opacity-90 transition-opacity',
          remaining <= 10
            ? 'bg-red-500 text-white'
            : 'bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))]'
        )}
      >
        ⏱ {timeStr}
      </button>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex flex-col gap-4 rounded-lg bg-[hsl(var(--card))] p-6 shadow-xl w-full max-w-md">

        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <p className="text-sm font-medium flex-1">{prompt}</p>
          <button
            onClick={() => setMinimized(true)}
            className="shrink-0 rounded px-2 py-0.5 text-xs text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))] transition-colors"
            title="Minimize"
          >
            ─
          </button>
        </div>

        {/* Countdown */}
        <div className="flex items-center justify-center py-2">
          <span className={cn('text-5xl font-mono font-bold tabular-nums', urgency)}>
            {timeStr}
          </span>
        </div>

        {/* Answer input */}
        <textarea
          value={answer}
          onChange={(e) => updateAnswer(e.target.value)}
          placeholder="Type your answer here…"
          disabled={expired}
          rows={3}
          className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))] disabled:opacity-50"
        />

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={handleCancel} disabled={expired}>
            Skip
          </Button>
          <Button size="sm" onClick={handleSubmit} disabled={expired}>
            {expired ? 'Time up!' : 'Submit'}
          </Button>
        </div>
      </div>
    </div>
  )
}
