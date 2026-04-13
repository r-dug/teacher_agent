/**
 * Slide-in drawer for creating or editing a lesson.
 *
 * Create mode: shows file picker + fields; navigates to /teach/:id on success.
 * Edit mode: shows editable fields (no file picker) + delete button.
 */

import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import type { Course, Lesson, VisualAidConfig, VisualAidSourceConfig } from '@/lib/types'

interface LessonDrawerProps {
  sessionId: string
  mode: 'create' | 'edit'
  /** Pre-fill and lock the course when creating within a course context. */
  courseId?: string | null
  lesson?: Lesson
  courses: Course[]
  onClose: () => void
  onCreated?: (lessonId: string) => void
  onUpdated?: (lesson: Lesson) => void
  onDeleted?: (lessonId: string) => void
}

export function LessonDrawer({
  sessionId,
  mode,
  courseId,
  lesson,
  courses,
  onClose,
  onCreated,
  onUpdated,
  onDeleted,
}: LessonDrawerProps) {
  const navigate = useNavigate()
  const fileRef = useRef<HTMLInputElement>(null)

  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState(lesson?.title ?? '')
  const [description, setDescription] = useState(lesson?.description ?? '')
  const [selectedCourseId, setSelectedCourseId] = useState<string>(
    lesson?.course_id ?? courseId ?? ''
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Visual aid config
  const defaultSrc: VisualAidSourceConfig = { enabled: false, persistence: 'persistent', timing: 'spontaneous' }
  const initVac = lesson?.visual_aid_config ?? {}
  const [vacPdfPages, setVacPdfPages] = useState(initVac.pdf_pages ?? true)
  const [vacGenerated, setVacGenerated] = useState<VisualAidSourceConfig>({ ...defaultSrc, ...initVac.generated_images })
  const [vacWeb, setVacWeb] = useState<VisualAidSourceConfig>({ ...defaultSrc, ...initVac.web_images })

  function buildVisualAidConfig(): VisualAidConfig {
    return {
      pdf_pages: vacPdfPages,
      generated_images: vacGenerated,
      web_images: vacWeb,
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null
    setFile(f)
    // Pre-fill name from filename if not already set
    if (f && !name) {
      setName(f.name.replace(/\.pdf$/i, ''))
    }
  }

  async function handleSave() {
    if (mode === 'create' && !file) {
      setError('Please select a PDF file.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (mode === 'create') {
        // 1. Get upload token
        const tokenRes = await fetch(`/api/sessions/${sessionId}/upload_token`, {
          headers: { 'X-Session-Id': sessionId },
        })
        if (!tokenRes.ok) throw new Error('Failed to get upload token')
        const { token } = (await tokenRes.json()) as { token: string }

        // 2. Upload with extra fields
        const form = new FormData()
        form.append('file', file!)
        form.append('session_id', sessionId)
        if (name.trim()) form.append('lesson_name', name.trim())
        if (description.trim()) form.append('description', description.trim())
        if (selectedCourseId) form.append('course_id', selectedCourseId)

        const uploadRes = await fetch('/api/lessons/decompose', {
          method: 'POST',
          headers: { 'X-Upload-Token': token, 'X-Session-Id': sessionId },
          body: form,
        })
        if (!uploadRes.ok) throw new Error('Upload failed')
        const { lesson_id } = (await uploadRes.json()) as { lesson_id: string }
        onCreated?.(lesson_id)
        navigate(`/teach/${lesson_id}`)
      } else {
        // Edit: PATCH lesson
        const body: Record<string, unknown> = {}
        if (name.trim() !== lesson?.title) body.title = name.trim() || lesson?.title
        if (description !== (lesson?.description ?? '')) body.description = description
        // Always send course_id so we can remove from course (null = standalone)
        body.course_id = selectedCourseId || null
        body.visual_aid_config = buildVisualAidConfig()

        const res = await fetch(`/api/lessons/${lesson!.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
          body: JSON.stringify(body),
        })
        if (!res.ok) throw new Error('Failed to save lesson')
        const updated = (await res.json()) as Lesson
        onUpdated?.(updated)
        onClose()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!lesson) return
    if (!confirm(`Delete "${lesson.title}"? This cannot be undone.`)) return
    setSaving(true)
    setError(null)
    try {
      const res = await fetch(`/api/lessons/${lesson.id}`, {
        method: 'DELETE',
        headers: { 'X-Session-Id': sessionId },
      })
      if (!res.ok) throw new Error('Failed to delete lesson')
      onDeleted?.(lesson.id)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  const lockedCourse = mode === 'create' && !!courseId

  return (
    <div className="space-y-4">
      {/* File picker — create mode only */}
      {mode === 'create' && (
        <div>
          <label className="mb-1 block text-sm font-medium">PDF file</label>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={handleFileChange}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => fileRef.current?.click()}
            disabled={saving}
          >
            {file ? file.name : 'Choose PDF…'}
          </Button>
        </div>
      )}

      {/* Lesson name */}
      <div>
        <label className="mb-1 block text-sm font-medium">Lesson name</label>
        <input
          type="text"
          className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
          placeholder="e.g. Chapter 3 — Making a Date"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={saving}
        />
      </div>

      {/* Description */}
      <div>
        <label className="mb-1 block text-sm font-medium">Description <span className="text-[hsl(var(--muted-foreground))]">(optional)</span></label>
        <textarea
          className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] resize-none"
          rows={3}
          placeholder="What will you learn in this lesson?"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={saving}
        />
      </div>

      {/* Course assignment */}
      <div>
        <label className="mb-1 block text-sm font-medium">Course</label>
        {lockedCourse ? (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            {courses.find((c) => c.id === courseId)?.title ?? 'Unknown course'}
          </p>
        ) : (
          <select
            className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            value={selectedCourseId}
            onChange={(e) => setSelectedCourseId(e.target.value)}
            disabled={saving}
          >
            <option value="">No course (individual lesson)</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>{c.title}</option>
            ))}
          </select>
        )}
      </div>

      {/* Visual aids config — edit mode only */}
      {mode === 'edit' && (
        <div className="space-y-3">
          <label className="block text-sm font-medium">Visual aids</label>

          {/* PDF pages */}
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={vacPdfPages} onChange={(e) => setVacPdfPages(e.target.checked)} />
            PDF pages (show slides from source document)
          </label>

          {/* Generated images */}
          <div className="space-y-1">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={vacGenerated.enabled} onChange={(e) => setVacGenerated((v) => ({ ...v, enabled: e.target.checked }))} />
              AI-generated images
            </label>
            {vacGenerated.enabled && (
              <div className="ml-6 space-y-1 text-xs text-[hsl(var(--muted-foreground))]">
                <div className="flex items-center gap-3">
                  <span>Storage:</span>
                  <label className="flex items-center gap-1"><input type="radio" name="gen-persist" checked={vacGenerated.persistence === 'persistent'} onChange={() => setVacGenerated((v) => ({ ...v, persistence: 'persistent' }))} /> Persistent</label>
                  <label className="flex items-center gap-1"><input type="radio" name="gen-persist" checked={vacGenerated.persistence === 'ephemeral'} onChange={() => setVacGenerated((v) => ({ ...v, persistence: 'ephemeral' }))} /> Ephemeral</label>
                </div>
                <div className="flex items-center gap-3">
                  <span>Timing:</span>
                  <label className="flex items-center gap-1"><input type="radio" name="gen-timing" checked={vacGenerated.timing === 'spontaneous'} onChange={() => setVacGenerated((v) => ({ ...v, timing: 'spontaneous' }))} /> Spontaneous</label>
                  <label className="flex items-center gap-1"><input type="radio" name="gen-timing" checked={vacGenerated.timing === 'prefetched'} onChange={() => setVacGenerated((v) => ({ ...v, timing: 'prefetched' }))} /> Prefetched</label>
                </div>
              </div>
            )}
          </div>

          {/* Web images */}
          <div className="space-y-1">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={vacWeb.enabled} onChange={(e) => setVacWeb((v) => ({ ...v, enabled: e.target.checked }))} />
              Web images (public domain search)
            </label>
            {vacWeb.enabled && (
              <div className="ml-6 space-y-1 text-xs text-[hsl(var(--muted-foreground))]">
                <div className="flex items-center gap-3">
                  <span>Storage:</span>
                  <label className="flex items-center gap-1"><input type="radio" name="web-persist" checked={vacWeb.persistence === 'persistent'} onChange={() => setVacWeb((v) => ({ ...v, persistence: 'persistent' }))} /> Persistent</label>
                  <label className="flex items-center gap-1"><input type="radio" name="web-persist" checked={vacWeb.persistence === 'ephemeral'} onChange={() => setVacWeb((v) => ({ ...v, persistence: 'ephemeral' }))} /> Ephemeral</label>
                </div>
                <div className="flex items-center gap-3">
                  <span>Timing:</span>
                  <label className="flex items-center gap-1"><input type="radio" name="web-timing" checked={vacWeb.timing === 'spontaneous'} onChange={() => setVacWeb((v) => ({ ...v, timing: 'spontaneous' }))} /> Spontaneous</label>
                  <label className="flex items-center gap-1"><input type="radio" name="web-timing" checked={vacWeb.timing === 'prefetched'} onChange={() => setVacWeb((v) => ({ ...v, timing: 'prefetched' }))} /> Prefetched</label>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {error && <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-2">
        <Button onClick={handleSave} disabled={saving} size="sm">
          {saving ? 'Saving…' : mode === 'create' ? 'Upload & Start' : 'Save'}
        </Button>
        <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        {mode === 'edit' && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto text-[hsl(var(--destructive))] hover:bg-[hsl(var(--destructive)/0.1)]"
            onClick={handleDelete}
            disabled={saving}
          >
            Delete
          </Button>
        )}
      </div>
    </div>
  )
}
