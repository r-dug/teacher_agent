/**
 * Slide-in drawer for creating or editing a course.
 */

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import type { Course } from '@/lib/types'

interface CourseDrawerProps {
  sessionId: string
  mode: 'create' | 'edit'
  course?: Course
  onClose: () => void
  onCreated?: (course: Course) => void
  onUpdated?: (course: Course) => void
  onDeleted?: (courseId: string) => void
}

export function CourseDrawer({
  sessionId,
  mode,
  course,
  onClose,
  onCreated,
  onUpdated,
  onDeleted,
}: CourseDrawerProps) {
  const [title, setTitle] = useState(course?.title ?? '')
  const [description, setDescription] = useState(course?.description ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSave() {
    if (!title.trim()) {
      setError('Course name is required.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (mode === 'create') {
        const res = await fetch('/api/courses', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
          body: JSON.stringify({ title: title.trim(), description: description.trim() || null }),
        })
        if (!res.ok) throw new Error('Failed to create course')
        const created = (await res.json()) as Course
        onCreated?.(created)
        onClose()
      } else {
        const res = await fetch(`/api/courses/${course!.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
          body: JSON.stringify({ title: title.trim(), description: description.trim() || null }),
        })
        if (!res.ok) throw new Error('Failed to update course')
        const updated = (await res.json()) as Course
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
    if (!course) return
    if (!confirm(`Delete course "${course.title}"? Lessons in this course will become individual lessons.`)) return
    setSaving(true)
    setError(null)
    try {
      const res = await fetch(`/api/courses/${course.id}`, {
        method: 'DELETE',
        headers: { 'X-Session-Id': sessionId },
      })
      if (!res.ok) throw new Error('Failed to delete course')
      onDeleted?.(course.id)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Course name */}
      <div>
        <label className="mb-1 block text-sm font-medium">Course name</label>
        <input
          type="text"
          className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
          placeholder="e.g. Genki I — Japanese for Beginners"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={saving}
          autoFocus
        />
      </div>

      {/* Description */}
      <div>
        <label className="mb-1 block text-sm font-medium">Description <span className="text-[hsl(var(--muted-foreground))]">(optional)</span></label>
        <textarea
          className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] resize-none"
          rows={3}
          placeholder="What is this course about?"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={saving}
        />
      </div>

      {error && <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-2">
        <Button onClick={handleSave} disabled={saving} size="sm">
          {saving ? 'Saving…' : mode === 'create' ? 'Create Course' : 'Save'}
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
