/**
 * Lesson picker: list existing lessons or upload a new PDF.
 *
 * Flow:
 *  1. GET /api/lessons → display list
 *  2. "Upload PDF" → POST /api/sessions/{id}/upload_token → POST /lessons/decompose (direct to backend)
 *  3. Clicking a lesson → navigate to /teach/:lessonId
 */

import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { Lesson } from '@/lib/types'

interface LessonPickerPageProps {
  sessionId: string
  onLogout: () => void
}

export function LessonPickerPage({ sessionId, onLogout }: LessonPickerPageProps) {
  const [lessons, setLessons] = useState<Lesson[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    fetch('/api/lessons', { headers: { 'X-Session-Id': sessionId } })
      .then((r) => r.json())
      .then(setLessons)
      .catch(() => setError('Failed to load lessons'))
  }, [sessionId])

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      // 1. Get upload token from frontend server
      const tokenRes = await fetch(`/api/sessions/${sessionId}/upload_token`, {
        headers: { 'X-Session-Id': sessionId },
      })
      if (!tokenRes.ok) throw new Error('Failed to get upload token')
      const { token } = (await tokenRes.json()) as { token: string }

      // 2. Upload directly to backend (via Vite proxy /api → :8000 → :8001)
      const form = new FormData()
      form.append('file', file)
      form.append('session_id', sessionId)
      const uploadRes = await fetch('/api/lessons/decompose', {
        method: 'POST',
        headers: { 'X-Upload-Token': token },
        body: form,
      })
      if (!uploadRes.ok) throw new Error('Upload failed')
      const { lesson_id } = (await uploadRes.json()) as { lesson_id: string }

      // Navigate to teach page immediately; decompose_complete arrives over WS
      navigate(`/teach/${lesson_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Your Lessons</h1>
        <div className="flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={handleFileChange}
          />
          <Button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            size="sm"
          >
            {uploading ? 'Uploading…' : 'Upload PDF'}
          </Button>
          <Button variant="ghost" size="sm" onClick={onLogout}>
            Log out
          </Button>
        </div>
      </div>

      {error && (
        <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>
      )}

      {lessons.length === 0 && !error && (
        <p className="text-sm text-[hsl(var(--muted-foreground))]">
          No lessons yet. Upload a PDF to get started.
        </p>
      )}

      <div className="space-y-3">
        {lessons.map((lesson) => (
          <Card
            key={lesson.id}
            className="cursor-pointer hover:border-[hsl(var(--primary))] transition-colors"
            onClick={() => navigate(`/teach/${lesson.id}`)}
          >
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-base">{lesson.title}</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0 flex items-center gap-2">
              <span className="text-xs text-[hsl(var(--muted-foreground))]">
                {new Date(lesson.created_at).toLocaleDateString()}
              </span>
              {lesson.completed && <Badge variant="secondary">Complete</Badge>}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
