/**
 * Course page — lesson picker for a specific course.
 * Shows all lessons in the course with progress indicators.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Drawer } from '@/components/Drawer'
import { LessonDrawer } from '@/components/LessonDrawer'
import { CourseChaptersEditor } from '@/components/CourseChaptersEditor'
import type { Course, Lesson } from '@/lib/types'

interface CoursePageProps {
  sessionId: string
  isAdmin?: boolean
}

export function CoursePage({ sessionId, isAdmin = false }: CoursePageProps) {
  const { courseId } = useParams<{ courseId: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [course, setCourse] = useState<Course | null>(null)
  const [lessons, setLessons] = useState<Lesson[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [lessonDrawer, setLessonDrawer] = useState<{ mode: 'create' | 'edit'; lesson?: Lesson } | null>(null)
  const [chaptersDrawerOpen, setChaptersDrawerOpen] = useState(false)
  const [publishing, setPublishing] = useState(false)

  useEffect(() => {
    if (!courseId) return
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const [courseRes, lessonsRes] = await Promise.all([
          fetch(`/api/courses/${courseId}`, { headers: { 'X-Session-Id': sessionId } }),
          fetch(`/api/lessons?course_id=${courseId}`, { headers: { 'X-Session-Id': sessionId } }),
        ])
        if (!courseRes.ok) throw new Error('Course not found')
        if (!lessonsRes.ok) throw new Error('Failed to load lessons')
        setCourse(await courseRes.json())
        setLessons(await lessonsRes.json())
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [courseId, sessionId])

  useEffect(() => {
    if (!isAdmin) return
    if (searchParams.get('chapters') !== '1') return
    setChaptersDrawerOpen(true)
    const next = new URLSearchParams(searchParams)
    next.delete('chapters')
    setSearchParams(next, { replace: true })
  }, [isAdmin, searchParams, setSearchParams])

  function handleLessonUpdated(lesson: Lesson) {
    if (lesson.course_id !== courseId) {
      // moved out of this course
      setLessons((prev) => prev.filter((l) => l.id !== lesson.id))
    } else {
      setLessons((prev) => prev.map((l) => (l.id === lesson.id ? lesson : l)))
    }
  }

  function handleLessonDeleted(lessonId: string) {
    setLessons((prev) => prev.filter((l) => l.id !== lessonId))
  }

  async function handlePublishToAllUsers() {
    if (!course || publishing) return
    const ok = confirm(
      `Publish "${course.title}" to all users?\n\n` +
      "Only lessons that already finished decomposition will be included.\n" +
      "Existing published copies will be reset to the starting point."
    )
    if (!ok) return

    setPublishing(true)
    setError(null)
    try {
      const res = await fetch(`/api/courses/${course.id}/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({}),
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(payload?.detail || 'Failed to publish course')

      const skipped = Number(payload.skipped_lessons || 0)
      const base = `Published to ${payload.target_users ?? 0} users.`
      const extra = skipped > 0 ? ` ${skipped} undecomposed lesson(s) were skipped.` : ''
      alert(base + extra)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to publish')
    } finally {
      setPublishing(false)
    }
  }

  // Progress bar component
  function ProgressBar({ value, max }: { value: number; max: number }) {
    const pct = max > 0 ? Math.round((value / max) * 100) : 0
    return (
      <div className="h-1.5 w-full rounded-full bg-[hsl(var(--muted))] overflow-hidden">
        <div
          className="h-full rounded-full bg-[hsl(var(--primary))] transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate('/')}>
          ← Back
        </Button>
        <div className="flex-1 min-w-0">
          {course ? (
            <>
              <h1 className="text-2xl font-bold truncate">{course.title}</h1>
              {course.description && (
                <p className="text-sm text-[hsl(var(--muted-foreground))] mt-0.5">{course.description}</p>
              )}
            </>
          ) : (
            <div className="h-7 w-48 rounded bg-[hsl(var(--muted))] animate-pulse" />
          )}
        </div>
        {isAdmin && (
          <Button
            size="sm"
            onClick={() => setLessonDrawer({ mode: 'create' })}
          >
            + Add Lesson
          </Button>
        )}
        {isAdmin && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setChaptersDrawerOpen(true)}
            disabled={!course}
          >
            Edit Chapters
          </Button>
        )}
        {isAdmin && (
          <Button
            size="sm"
            variant="secondary"
            onClick={handlePublishToAllUsers}
            disabled={publishing || !course}
          >
            {publishing ? 'Publishing…' : 'Publish To All Users'}
          </Button>
        )}
      </div>

      {error && <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>}

      {loading ? (
        <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading…</p>
      ) : (
        <div className="space-y-3">
          {lessons.length === 0 ? (
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              No lessons yet. Add the first lesson to this course.
            </p>
          ) : (
            lessons.map((lesson, idx) => (
              <Card
                key={lesson.id}
                className="cursor-pointer hover:border-[hsl(var(--primary))] transition-colors"
                onClick={() => navigate(`/teach/${lesson.id}`)}
              >
                <CardHeader className="p-4 pb-2">
                  <CardTitle className="text-base flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2 min-w-0">
                      <span className="text-xs text-[hsl(var(--muted-foreground))] shrink-0">
                        {idx + 1}.
                      </span>
                      <span className="truncate">{lesson.title}</span>
                    </span>
                    <button
                      className="shrink-0 rounded p-1 text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))]"
                      onClick={(e) => { e.stopPropagation(); setLessonDrawer({ mode: 'edit', lesson }) }}
                      aria-label="Lesson settings"
                    >
                      ⚙
                    </button>
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-4 pt-0 space-y-2">
                  {lesson.description && (
                    <p className="text-xs text-[hsl(var(--muted-foreground))] line-clamp-2">
                      {lesson.description}
                    </p>
                  )}
                  <div className="flex items-center gap-3">
                    {lesson.section_count > 0 ? (
                      <>
                        <div className="flex-1">
                          <ProgressBar value={lesson.current_section_idx} max={lesson.section_count} />
                        </div>
                        <span className="text-xs text-[hsl(var(--muted-foreground))] shrink-0 tabular-nums">
                          {lesson.current_section_idx} / {lesson.section_count} sections
                        </span>
                      </>
                    ) : (
                      <span className="text-xs text-[hsl(var(--muted-foreground))]">
                        {new Date(lesson.created_at).toLocaleDateString()}
                      </span>
                    )}
                    {lesson.completed && <Badge variant="secondary">Complete</Badge>}
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </div>
      )}

      {/* Lesson drawer */}
      <Drawer
        open={lessonDrawer !== null}
        onClose={() => setLessonDrawer(null)}
        title={lessonDrawer?.mode === 'create' ? 'Add Lesson' : 'Lesson Settings'}
      >
        {lessonDrawer && course && (
          <LessonDrawer
            sessionId={sessionId}
            mode={lessonDrawer.mode}
            courseId={courseId}
            lesson={lessonDrawer.lesson}
            courses={[course]}
            onClose={() => setLessonDrawer(null)}
            onUpdated={handleLessonUpdated}
            onDeleted={handleLessonDeleted}
          />
        )}
      </Drawer>

      {/* Chapter draft editor */}
      <Drawer
        open={chaptersDrawerOpen}
        onClose={() => setChaptersDrawerOpen(false)}
        title="Chapter Drafts"
      >
        {courseId ? (
          <CourseChaptersEditor
            sessionId={sessionId}
            courseId={courseId}
          />
        ) : (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">Course not found.</p>
        )}
      </Drawer>
    </div>
  )
}
