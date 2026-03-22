import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

interface ChapterDraft {
  id: string
  idx: number
  title: string
  page_start: number
  page_end: number
  included: boolean
}

interface ChapterListResponse {
  course_id: string
  pdf_hash: string
  page_count: number
  chapters: ChapterDraft[]
}

interface AdvisorTurn {
  role: string
  content: string
}

interface AdvisorResponse {
  course_id: string
  status: string
  transcript: AdvisorTurn[]
  objectives_prompt?: string | null
  chapters?: ChapterDraft[] | null
}

interface DecomposeJob {
  id: string
  status: string
  total_items: number
  completed_items: number
  failed_items: number
  progress_pct: number
  error?: string | null
}

interface DecomposeJobItem {
  id: string
  chapter_id: string
  idx: number
  title: string
  status: string
  error?: string | null
}

interface DecomposeStatusResponse {
  job: DecomposeJob | null
  items: DecomposeJobItem[]
}

interface CourseChaptersEditorProps {
  sessionId: string
  courseId: string
}

async function parsePayload<T>(res: Response): Promise<(Partial<T> & { detail?: string })> {
  return (await res.json().catch(() => ({}))) as Partial<T> & { detail?: string }
}

export function CourseChaptersEditor({ sessionId, courseId }: CourseChaptersEditorProps) {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pageCount, setPageCount] = useState(0)
  const [pdfHash, setPdfHash] = useState('')
  const [chapters, setChapters] = useState<ChapterDraft[]>([])

  const [advisorLoading, setAdvisorLoading] = useState(true)
  const [advisorBusy, setAdvisorBusy] = useState(false)
  const [advisorError, setAdvisorError] = useState<string | null>(null)
  const [advisorStatus, setAdvisorStatus] = useState('draft')
  const [advisorTranscript, setAdvisorTranscript] = useState<AdvisorTurn[]>([])
  const [advisorInput, setAdvisorInput] = useState('')
  const [objectivesPrompt, setObjectivesPrompt] = useState<string | null>(null)

  const [decomposeLoading, setDecomposeLoading] = useState(true)
  const [decomposeBusy, setDecomposeBusy] = useState(false)
  const [decomposeError, setDecomposeError] = useState<string | null>(null)
  const [decomposeState, setDecomposeState] = useState<DecomposeStatusResponse>({ job: null, items: [] })
  const [lastNotifiedJobId, setLastNotifiedJobId] = useState<string | null>(null)
  const [decomposeNotice, setDecomposeNotice] = useState<string | null>(null)

  useEffect(() => {
    async function loadChapters(): Promise<boolean> {
      setLoading(true)
      setError(null)
      try {
        const res = await fetch(`/api/courses/${courseId}/chapters`, {
          headers: { 'X-Session-Id': sessionId },
        })
        const payload = await parsePayload<ChapterListResponse>(res)
        if (!res.ok) throw new Error(payload.detail || 'Failed to load chapter drafts')
        const hash = String(payload.pdf_hash || '')
        setPageCount(Number(payload.page_count || 0))
        setPdfHash(hash)
        setChapters((payload.chapters || []) as ChapterDraft[])
        return Boolean(hash)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load chapter drafts')
        return false
      } finally {
        setLoading(false)
      }
    }

    async function loadAdvisor() {
      setAdvisorLoading(true)
      setAdvisorError(null)
      try {
        const res = await fetch(`/api/courses/${courseId}/advisor/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
          body: JSON.stringify({ reset: false }),
        })
        const payload = await parsePayload<AdvisorResponse>(res)
        if (!res.ok) throw new Error(payload.detail || 'Failed to load advisor conversation')
        setAdvisorStatus(String(payload.status || 'draft'))
        setAdvisorTranscript((payload.transcript || []) as AdvisorTurn[])
        setObjectivesPrompt((payload.objectives_prompt || null) as string | null)
      } catch (err) {
        setAdvisorError(err instanceof Error ? err.message : 'Failed to load advisor conversation')
      } finally {
        setAdvisorLoading(false)
      }
    }

    async function loadDecomposeStatus() {
      setDecomposeLoading(true)
      setDecomposeError(null)
      try {
        const res = await fetch(`/api/courses/${courseId}/decompose/status`, {
          headers: { 'X-Session-Id': sessionId },
        })
        const payload = await parsePayload<DecomposeStatusResponse>(res)
        if (!res.ok) throw new Error(payload.detail || 'Failed to load decomposition status')
        setDecomposeState({
          job: (payload.job || null) as DecomposeJob | null,
          items: (payload.items || []) as DecomposeJobItem[],
        })
      } catch (err) {
        setDecomposeError(err instanceof Error ? err.message : 'Failed to load decomposition status')
      } finally {
        setDecomposeLoading(false)
      }
    }

    void loadChapters().then((hasPdf) => {
      if (hasPdf) void Promise.all([loadAdvisor(), loadDecomposeStatus()])
      else { setAdvisorLoading(false); setDecomposeLoading(false) }
    })
  }, [courseId, sessionId])

  useEffect(() => {
    const job = decomposeState.job
    if (!job) return
    if (job.status !== 'queued' && job.status !== 'running') return

    const timer = window.setInterval(async () => {
      try {
        const q = new URLSearchParams({ job_id: job.id })
        const res = await fetch(`/api/courses/${courseId}/decompose/status?${q.toString()}`, {
          headers: { 'X-Session-Id': sessionId },
        })
        const payload = await parsePayload<DecomposeStatusResponse>(res)
        if (!res.ok) throw new Error(payload.detail || 'Failed to refresh decomposition status')
        setDecomposeState({
          job: (payload.job || null) as DecomposeJob | null,
          items: (payload.items || []) as DecomposeJobItem[],
        })
      } catch (err) {
        setDecomposeError(err instanceof Error ? err.message : 'Failed to refresh decomposition status')
      }
    }, 3000)

    return () => window.clearInterval(timer)
  }, [courseId, decomposeState.job, sessionId])

  useEffect(() => {
    const job = decomposeState.job
    if (!job) return
    if (job.status !== 'completed' && job.status !== 'failed') return
    if (lastNotifiedJobId === job.id) return
    setLastNotifiedJobId(job.id)
    if (job.status === 'completed') {
      setDecomposeNotice('Decomposition finished — generated lessons are now available in this course.')
      return
    }
    setDecomposeNotice(`Decomposition finished with errors. Failed chapters: ${job.failed_items}.`)
  }, [decomposeState.job, lastNotifiedJobId])

  function updateChapter(index: number, patch: Partial<ChapterDraft>) {
    setChapters((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)))
  }

  function addChapter() {
    const highestEnd = chapters.reduce((m, c) => Math.max(m, Number(c.page_end) || 0), 0)
    const start = Math.min(Math.max(1, highestEnd + 1), Math.max(1, pageCount || 1))
    const end = Math.min(pageCount || start, start + 9)
    setChapters((prev) => [
      ...prev,
      {
        id: `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        idx: prev.length,
        title: `Chapter ${prev.length + 1}`,
        page_start: start,
        page_end: Math.max(start, end),
        included: true,
      },
    ])
  }

  function removeChapter(index: number) {
    setChapters((prev) => prev.filter((_, i) => i !== index))
  }

  async function saveChapters() {
    if (chapters.length === 0) {
      setError('At least one chapter is required.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const body = {
        chapters: chapters.map((c) => ({
          id: c.id.startsWith('tmp_') ? null : c.id,
          title: c.title,
          page_start: Number(c.page_start),
          page_end: Number(c.page_end),
          included: Boolean(c.included),
        })),
      }
      const res = await fetch(`/api/courses/${courseId}/chapters`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify(body),
      })
      const payload = await parsePayload<ChapterListResponse>(res)
      if (!res.ok) throw new Error(payload.detail || 'Failed to save chapter drafts')
      setPageCount(Number(payload.page_count || 0))
      setPdfHash(String(payload.pdf_hash || ''))
      setChapters((payload.chapters || []) as ChapterDraft[])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save chapter drafts')
    } finally {
      setSaving(false)
    }
  }

  async function resetAdvisorConversation() {
    setAdvisorBusy(true)
    setAdvisorError(null)
    try {
      const res = await fetch(`/api/courses/${courseId}/advisor/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({ reset: true }),
      })
      const payload = await parsePayload<AdvisorResponse>(res)
      if (!res.ok) throw new Error(payload.detail || 'Failed to reset advisor conversation')
      setAdvisorStatus(String(payload.status || 'draft'))
      setAdvisorTranscript((payload.transcript || []) as AdvisorTurn[])
      setObjectivesPrompt((payload.objectives_prompt || null) as string | null)
    } catch (err) {
      setAdvisorError(err instanceof Error ? err.message : 'Failed to reset advisor conversation')
    } finally {
      setAdvisorBusy(false)
    }
  }

  async function sendAdvisorMessage() {
    const text = advisorInput.trim()
    if (!text) return
    setAdvisorBusy(true)
    setAdvisorError(null)
    try {
      const res = await fetch(`/api/courses/${courseId}/advisor/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({ text }),
      })
      const payload = await parsePayload<AdvisorResponse>(res)
      if (!res.ok) throw new Error(payload.detail || 'Failed to send advisor message')
      setAdvisorInput('')
      setAdvisorStatus(String(payload.status || 'draft'))
      setAdvisorTranscript((payload.transcript || []) as AdvisorTurn[])
      setObjectivesPrompt((payload.objectives_prompt || null) as string | null)
    } catch (err) {
      setAdvisorError(err instanceof Error ? err.message : 'Failed to send advisor message')
    } finally {
      setAdvisorBusy(false)
    }
  }

  async function finalizeAdvisor() {
    setAdvisorBusy(true)
    setAdvisorError(null)
    try {
      const res = await fetch(`/api/courses/${courseId}/advisor/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({}),
      })
      const payload = await parsePayload<AdvisorResponse>(res)
      if (!res.ok) throw new Error(payload.detail || 'Failed to finalize advisor objectives')
      setAdvisorStatus(String(payload.status || 'draft'))
      setAdvisorTranscript((payload.transcript || []) as AdvisorTurn[])
      setObjectivesPrompt((payload.objectives_prompt || null) as string | null)
      if (payload.chapters && payload.chapters.length > 0) {
        setChapters(payload.chapters as ChapterDraft[])
      }
    } catch (err) {
      setAdvisorError(err instanceof Error ? err.message : 'Failed to finalize advisor objectives')
    } finally {
      setAdvisorBusy(false)
    }
  }

  async function startDecompose(decompose_mode: 'pdf' | 'text') {
    setDecomposeBusy(true)
    setDecomposeError(null)
    try {
      const res = await fetch(`/api/courses/${courseId}/decompose/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({
          objectives_prompt: (objectivesPrompt || '').trim() || undefined,
          decompose_mode,
        }),
      })
      const payload = await parsePayload<DecomposeStatusResponse>(res)
      if (!res.ok) throw new Error(payload.detail || 'Failed to start decomposition')
      setDecomposeState({
        job: (payload.job || null) as DecomposeJob | null,
        items: (payload.items || []) as DecomposeJobItem[],
      })
    } catch (err) {
      setDecomposeError(err instanceof Error ? err.message : 'Failed to start decomposition')
    } finally {
      setDecomposeBusy(false)
    }
  }

  if (loading) {
    return <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading chapter drafts…</p>
  }

  const runningJob = decomposeState.job && (decomposeState.job.status === 'queued' || decomposeState.job.status === 'running')

  return (
    <div className="space-y-6">
      <div className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.3)] p-3 text-xs text-[hsl(var(--muted-foreground))]">
        <p>Total pages: {pageCount}</p>
        <p className="truncate">PDF hash: {pdfHash || 'n/a'}</p>
      </div>

      <div className="space-y-3">
        {chapters.map((chapter, idx) => (
          <div key={chapter.id} className="rounded-md border border-[hsl(var(--border))] p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-[hsl(var(--muted-foreground))]">Chapter {idx + 1}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-[hsl(var(--destructive))] hover:bg-[hsl(var(--destructive)/0.1)]"
                onClick={() => removeChapter(idx)}
                disabled={saving || chapters.length <= 1 || Boolean(runningJob)}
              >
                Remove
              </Button>
            </div>
            <input
              type="text"
              className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
              value={chapter.title}
              onChange={(e) => updateChapter(idx, { title: e.target.value })}
              disabled={saving || Boolean(runningJob)}
            />
            <div className="grid grid-cols-2 gap-2">
              <input
                type="number"
                min={1}
                max={Math.max(1, pageCount)}
                className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
                value={chapter.page_start}
                onChange={(e) => updateChapter(idx, { page_start: Number(e.target.value) || 1 })}
                disabled={saving || Boolean(runningJob)}
              />
              <input
                type="number"
                min={1}
                max={Math.max(1, pageCount)}
                className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
                value={chapter.page_end}
                onChange={(e) => updateChapter(idx, { page_end: Number(e.target.value) || 1 })}
                disabled={saving || Boolean(runningJob)}
              />
            </div>
            <label className="inline-flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={chapter.included}
                onChange={(e) => updateChapter(idx, { included: e.target.checked })}
                disabled={saving || Boolean(runningJob)}
              />
              Include in decomposition
            </label>
          </div>
        ))}
      </div>

      {error && <p className="text-sm text-[hsl(var(--destructive))]">{error}</p>}

      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={addChapter} disabled={saving || Boolean(runningJob)}>
          + Add Chapter
        </Button>
        <Button size="sm" onClick={saveChapters} disabled={saving || Boolean(runningJob)}>
          {saving ? 'Saving…' : 'Save Chapter Drafts'}
        </Button>
      </div>

      <div className="rounded-md border border-[hsl(var(--border))] p-3 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Advisor Conversation</h3>
          <Button variant="outline" size="sm" onClick={resetAdvisorConversation} disabled={advisorBusy || advisorLoading}>
            Reset
          </Button>
        </div>

        {advisorLoading ? (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading advisor state…</p>
        ) : (
          <>
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Status: <span className="font-medium">{advisorStatus}</span>
            </p>
            <div className="max-h-56 space-y-2 overflow-auto rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.2)] p-2">
              {advisorTranscript.length === 0 ? (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">No advisor messages yet.</p>
              ) : (
                advisorTranscript.map((turn, idx) => (
                  <div key={`${idx}_${turn.role}`} className="text-xs leading-relaxed">
                    <span className="font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">{turn.role}:</span>{' '}
                    {turn.content}
                  </div>
                ))
              )}
            </div>

            <div className="flex gap-2">
              <input
                type="text"
                value={advisorInput}
                onChange={(e) => setAdvisorInput(e.target.value)}
                placeholder="Share your teaching goals and constraints..."
                className="w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
                disabled={advisorBusy}
              />
              <Button size="sm" onClick={sendAdvisorMessage} disabled={advisorBusy || !advisorInput.trim() || advisorStatus === 'finalized'}>
                Send
              </Button>
            </div>

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={finalizeAdvisor}
                disabled={advisorBusy || advisorTranscript.length < 2 || advisorStatus === 'finalized'}
              >
                Finalize Objectives
              </Button>
            </div>

            {advisorError && <p className="text-sm text-[hsl(var(--destructive))]">{advisorError}</p>}
          </>
        )}
      </div>

      <div className="rounded-md border border-[hsl(var(--border))] p-3 space-y-3">
        <h3 className="text-sm font-semibold">Decomposition Job</h3>

        {objectivesPrompt ? (
          <div className="space-y-2">
            <p className="text-xs text-[hsl(var(--muted-foreground))]">Finalized objective prompt</p>
            <textarea
              value={objectivesPrompt}
              onChange={(e) => setObjectivesPrompt(e.target.value)}
              className="min-h-28 w-full rounded-md border border-[hsl(var(--input))] bg-[hsl(var(--muted)/0.2)] px-3 py-2 text-xs leading-relaxed"
              disabled={decomposeBusy || Boolean(runningJob)}
            />
          </div>
        ) : (
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Finalize the advisor conversation before starting decomposition.
          </p>
        )}

        {decomposeLoading ? (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">Loading decomposition state…</p>
        ) : decomposeState.job ? (
          <div className="space-y-2 text-xs">
            <p>
              Status: <span className="font-medium">{decomposeState.job.status}</span> ({decomposeState.job.progress_pct}%)
            </p>
            <p>
              Completed {decomposeState.job.completed_items} / {decomposeState.job.total_items}; failed {decomposeState.job.failed_items}
            </p>
            {decomposeState.job.error && <p className="text-[hsl(var(--destructive))]">{decomposeState.job.error}</p>}
            {decomposeState.items.length > 0 && (
              <div className="max-h-36 space-y-1 overflow-auto rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.2)] p-2">
                {decomposeState.items.map((item) => (
                  <p key={item.id}>
                    {item.idx + 1}. {item.title} <span className="text-[hsl(var(--muted-foreground))]">({item.status})</span>
                  </p>
                ))}
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">No decomposition job started yet.</p>
        )}

        {decomposeError && <p className="text-sm text-[hsl(var(--destructive))]">{decomposeError}</p>}
        {decomposeNotice && <p className="text-sm text-[hsl(var(--primary))]">{decomposeNotice}</p>}

        <div className="flex items-center gap-2 flex-wrap">
          <Button
            size="sm"
            onClick={() => startDecompose('pdf')}
            disabled={decomposeBusy || advisorStatus !== 'finalized' || Boolean(runningJob)}
          >
            {decomposeBusy ? 'Starting…' : decomposeState.job?.status === 'completed' ? 'Re-run (PDF Pages)' : 'Decompose with PDF Pages'}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => startDecompose('text')}
            disabled={decomposeBusy || advisorStatus !== 'finalized' || Boolean(runningJob)}
          >
            {decomposeBusy ? 'Starting…' : decomposeState.job?.status === 'completed' ? 'Re-run (Extracted Text)' : 'Decompose with Extracted Text'}
          </Button>
        </div>
      </div>
    </div>
  )
}
