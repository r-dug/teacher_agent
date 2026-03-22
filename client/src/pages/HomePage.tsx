/**
 * Home page — two collapsible sections:
 *   • Courses — grouped lessons
 *   • Individual Lessons — standalone lessons (no course)
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Drawer } from '@/components/Drawer'
import { LessonDrawer } from '@/components/LessonDrawer'
import { CourseDrawer } from '@/components/CourseDrawer'
import { ThemePicker } from '@/components/ThemePicker'
import type { Course, Lesson } from '@/lib/types'

interface HomePageProps {
  sessionId: string
  onLogout: () => void
  isAdmin?: boolean
}

// ── Collapsible section header ────────────────────────────────────────────────

function SectionHeader({
  title,
  open,
  onToggle,
  action,
}: {
  title: string
  open: boolean
  onToggle: () => void
  action?: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        onClick={onToggle}
        className="flex flex-1 items-center gap-2 text-left"
      >
        <span className="text-lg font-semibold">{title}</span>
        <span className="text-[hsl(var(--muted-foreground))] transition-transform duration-200" style={{ display: 'inline-block', transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }}>
          ▾
        </span>
      </button>
      {action}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function HomePage({ sessionId, onLogout, isAdmin }: HomePageProps) {
  const navigate = useNavigate()

  const [courses, setCourses] = useState<Course[]>([])
  const [standaloneL, setStandaloneL] = useState<Lesson[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // collapse state
  const [coursesOpen, setCoursesOpen] = useState(true)
  const [lessonsOpen, setLessonsOpen] = useState(true)

  // drawer state
  const [courseDrawer, setCourseDrawer] = useState<{ mode: 'create' | 'edit'; course?: Course } | null>(null)
  const [lessonDrawer, setLessonDrawer] = useState<{ mode: 'create' | 'edit'; lesson?: Lesson } | null>(null)

  useEffect(() => {
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const [coursesRes, lessonsRes] = await Promise.all([
          fetch('/api/courses', { headers: { 'X-Session-Id': sessionId } }),
          fetch('/api/lessons?standalone=true', { headers: { 'X-Session-Id': sessionId } }),
        ])
        if (!coursesRes.ok || !lessonsRes.ok) throw new Error('Failed to load')
        setCourses(await coursesRes.json())
        setStandaloneL(await lessonsRes.json())
      } catch {
        setError('Failed to load. Please refresh.')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [sessionId])

  // ── course handlers ───────────────────────────────────────────────────────

  function handleCourseCreated(course: Course, options?: { openChapters?: boolean }) {
    setCourses((prev) => [course, ...prev])
    if (options?.openChapters) {
      navigate(`/courses/${course.id}?chapters=1`)
    }
  }

  function handleCourseUpdated(course: Course) {
    setCourses((prev) => prev.map((c) => (c.id === course.id ? course : c)))
  }

  function handleCourseDeleted(courseId: string) {
    setCourses((prev) => prev.filter((c) => c.id !== courseId))
  }

  // ── lesson handlers ───────────────────────────────────────────────────────

  function handleLessonUpdated(lesson: Lesson) {
    if (lesson.course_id) {
      // moved to a course — remove from standalone
      setStandaloneL((prev) => prev.filter((l) => l.id !== lesson.id))
    } else {
      setStandaloneL((prev) => prev.map((l) => (l.id === lesson.id ? lesson : l)))
    }
  }

  function handleLessonDeleted(lessonId: string) {
    setStandaloneL((prev) => prev.filter((l) => l.id !== lessonId))
  }

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 space-y-8">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">My Learning</h1>
        <div className="flex items-center gap-2">
          <ThemePicker />
          {isAdmin && (
            <Button variant="ghost" size="sm" onClick={() => navigate('/admin/usage')} data-page-transition>
              Usage
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={onLogout} data-page-transition>
            Log out
          </Button>
        </div>
      </div>

      {error && <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>}

      {loading ? (
        <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading…</p>
      ) : (
        <>
          {/* ── Courses section ─────────────────────────────────────────── */}
          <div className="space-y-3">
            <SectionHeader
              title="Courses"
              open={coursesOpen}
              onToggle={() => setCoursesOpen((v) => !v)}
              action={isAdmin ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setCourseDrawer({ mode: 'create' })}
                >
                  + New Course
                </Button>
              ) : undefined}
            />

            {coursesOpen && (
              <div className="space-y-3">
                {courses.length === 0 ? (
                  <p className="text-sm text-[hsl(var(--muted-foreground))]">
                    No courses yet. Create one to group related lessons.
                  </p>
                ) : (
                  courses.map((course) => (
                    <Card
                      key={course.id}
                      className="cursor-pointer hover:border-[hsl(var(--primary))] transition-colors"
                      onClick={() => navigate(`/courses/${course.id}`)}
                    >
                      <CardHeader className="p-4 pb-2">
                        <CardTitle className="text-base flex items-center justify-between">
                          <span>{course.title}</span>
                          <button
                            className="rounded p-1 text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))]"
                            onClick={(e) => { e.stopPropagation(); setCourseDrawer({ mode: 'edit', course }) }}
                            aria-label="Course settings"
                          >
                            ⚙
                          </button>
                        </CardTitle>
                      </CardHeader>
                      {course.description && (
                        <CardContent className="p-4 pt-0">
                          <p className="text-xs text-[hsl(var(--muted-foreground))] line-clamp-2">
                            {course.description}
                          </p>
                        </CardContent>
                      )}
                    </Card>
                  ))
                )}
              </div>
            )}
          </div>

          {/* ── Individual Lessons section ──────────────────────────────── */}
          <div className="space-y-3">
            <SectionHeader
              title="Individual Lessons"
              open={lessonsOpen}
              onToggle={() => setLessonsOpen((v) => !v)}
              action={isAdmin ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setLessonDrawer({ mode: 'create' })}
                >
                  + Add Lesson
                </Button>
              ) : undefined}
            />

            {lessonsOpen && (
              <div className="space-y-3">
                {standaloneL.length === 0 ? (
                  <p className="text-sm text-[hsl(var(--muted-foreground))]">
                    No individual lessons yet. Upload a PDF to get started.
                  </p>
                ) : (
                  standaloneL.map((lesson) => (
                    <Card
                      key={lesson.id}
                      className="cursor-pointer hover:border-[hsl(var(--primary))] transition-colors"
                      onClick={() => navigate(`/teach/${lesson.id}`)}
                    >
                      <CardHeader className="p-4 pb-2">
                        <CardTitle className="text-base flex items-center justify-between">
                          <span>{lesson.title}</span>
                          <button
                            className="rounded p-1 text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))]"
                            onClick={(e) => { e.stopPropagation(); setLessonDrawer({ mode: 'edit', lesson }) }}
                            aria-label="Lesson settings"
                          >
                            ⚙
                          </button>
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="p-4 pt-0 flex items-center gap-2">
                        <span className="text-xs text-[hsl(var(--muted-foreground))] flex-1">
                          {new Date(lesson.created_at).toLocaleDateString()}
                          {lesson.section_count > 0 && (
                            <span className="ml-2">
                              {lesson.current_section_idx} / {lesson.section_count} sections
                            </span>
                          )}
                        </span>
                        {lesson.completed && <Badge variant="secondary">Complete</Badge>}
                      </CardContent>
                    </Card>
                  ))
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* ── Course drawer ──────────────────────────────────────────────── */}
      <Drawer
        open={courseDrawer !== null}
        onClose={() => setCourseDrawer(null)}
        title={courseDrawer?.mode === 'create' ? 'New Course' : 'Edit Course'}
      >
        {courseDrawer && (
          <CourseDrawer
            sessionId={sessionId}
            mode={courseDrawer.mode}
            course={courseDrawer.course}
            onClose={() => setCourseDrawer(null)}
            onCreated={handleCourseCreated}
            onUpdated={handleCourseUpdated}
            onDeleted={handleCourseDeleted}
          />
        )}
      </Drawer>

      {/* ── Lesson drawer ──────────────────────────────────────────────── */}
      <Drawer
        open={lessonDrawer !== null}
        onClose={() => setLessonDrawer(null)}
        title={lessonDrawer?.mode === 'create' ? 'Add Lesson' : 'Lesson Settings'}
      >
        {lessonDrawer && (
          <LessonDrawer
            sessionId={sessionId}
            mode={lessonDrawer.mode}
            lesson={lessonDrawer.lesson}
            courses={courses}
            onClose={() => setLessonDrawer(null)}
            onUpdated={handleLessonUpdated}
            onDeleted={handleLessonDeleted}
          />
        )}
      </Drawer>
    </div>
  )
}
