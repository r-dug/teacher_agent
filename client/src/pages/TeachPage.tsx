/**
 * TeachPage: main teaching interface.
 *
 * Layout (desktop):
 *  ┌────────────────────────────────────┬──────────────────┐
 *  │ StatusBar                          │                  │
 *  ├────────────────────────────────────┤  CurriculumPanel │
 *  │ ConversationView (flex-1, scroll)  │  (sidebar)       │
 *  ├────────────────────────────────────┤                  │
 *  │ RecordButton + cancel              │                  │
 *  └────────────────────────────────────┴──────────────────┘
 *
 * Overlays (conditional):
 *  - SlideViewer (when show_slide received)
 *  - Sketchpad (when open_sketchpad received)
 */

import { useCallback, useState, useRef, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, X } from 'lucide-react'

import { StatusBar } from '@/components/StatusBar'
import { ConversationView, type Turn, type Figure } from '@/components/ConversationView'
import { RecordButton } from '@/components/RecordButton'
import { CurriculumPanel } from '@/components/CurriculumPanel'
import { SlideViewer } from '@/components/SlideViewer'
import { ZoomableImage } from '@/components/ZoomableImage'
import { Sketchpad } from '@/components/Sketchpad'
import { CameraCapture } from '@/components/CameraCapture'
import { Button } from '@/components/ui/button'

import { useWebSocket } from '@/hooks/useWebSocket'
import { useRecorder } from '@/hooks/useRecorder'
import { useAudioPlayer } from '@/hooks/useAudioPlayer'

import type { ServerEvent, CurriculumData, CurriculumState, Persona, Voice, SttLanguage } from '@/lib/types'


interface TeachPageProps {
  sessionId: string
}

interface SketchpadState {
  prompt: string
  invocationId: string
  textBg?: string
  imBg?: string
}

interface SlideState {
  page: number
  caption?: string
}

export function TeachPage({ sessionId }: TeachPageProps) {
  const { lessonId = '' } = useParams<{ lessonId: string }>()
  const navigate = useNavigate()

  // ── state ────────────────────────────────────────────────────────────────
  const [turns, setTurns] = useState<Turn[]>([])
  const [statusMsg, setStatusMsg] = useState('')
  const [curriculum, setCurriculum] = useState<CurriculumData | null>(null)
  const [currState, setCurrState] = useState<CurriculumState | null>(null)
  const [currComplete, setCurrComplete] = useState(false)
  const [slide, setSlide] = useState<SlideState | null>(null)
  const [sketchpad, setSketchpad] = useState<SketchpadState | null>(null)
  const [camera, setCamera] = useState<{ prompt: string; invocationId: string } | null>(null)
  const [drawingView, setDrawingView] = useState<{ dataUrl: string; prompt: string } | null>(null)
  const [agentBusy, setAgentBusy] = useState(false)
  const lastTurnIdRef = useRef<string | null>(null)

  // ── personas ─────────────────────────────────────────────────────────────
  const [personas, setPersonas] = useState<Persona[]>([])
  const [selectedPersonaId, setSelectedPersonaId] = useState<string>('')
  const [personasReady, setPersonasReady] = useState(false)
  const lessonStartedRef = useRef(false)

  useEffect(() => {
    fetch('/api/personas', { headers: { 'X-Session-Id': sessionId } })
      .then((r) => r.json())
      .then((data: Persona[]) => { setPersonas(data); setPersonasReady(true) })
      .catch(() => setPersonasReady(true))  // start even if fetch fails
  }, [sessionId])

  // ── voices ───────────────────────────────────────────────────────────────
  const [voices, setVoices] = useState<Voice[]>([])
  const [selectedVoiceId, setSelectedVoiceId] = useState<string>('')

  useEffect(() => {
    fetch('/api/voices')
      .then((r) => r.json())
      .then((data: Voice[]) => {
        setVoices(data)
        const def = data.find((v) => v.is_default)
        if (def) setSelectedVoiceId(def.id)
      })
      .catch(() => {/* non-fatal */})
  }, [])

  // ── STT languages ─────────────────────────────────────────────────────────
  const [sttLanguages, setSttLanguages] = useState<SttLanguage[]>([])
  const [selectedLangCode, setSelectedLangCode] = useState<string>('')  // '' = auto

  useEffect(() => {
    fetch('/api/stt-languages')
      .then((r) => r.json())
      .then((data: SttLanguage[]) => setSttLanguages(data))
      .catch(() => {/* non-fatal */})
  }, [])

  // ── audio ────────────────────────────────────────────────────────────────
  const { enqueue, replay } = useAudioPlayer()

  // ── WS event handler ─────────────────────────────────────────────────────
  const handleEvent = useCallback((ev: ServerEvent) => {
    switch (ev.event) {
      case 'transcription':
        lastTurnIdRef.current = ev.turn_id
        setTurns((prev) => [...prev, { role: 'user', text: ev.text, complete: true }])
        setAgentBusy(true)
        break

      case 'turn_start':
        setTurns((prev) => [...prev, { role: 'assistant', text: '', complete: false }])
        break

      case 'text_chunk':
        setTurns((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant') {
            next[next.length - 1] = { ...last, text: last.text + ev.text, turnIdx: ev.turn_idx }
          }
          return next
        })
        break

      case 'audio_chunk':
        enqueue(ev.data, ev.sample_rate, ev.turn_idx, ev.chunk_idx)
        break

      case 'turn_complete':
        lastTurnIdRef.current = ev.turn_id
        setTurns((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant') next[next.length - 1] = { ...last, complete: true }
          return next
        })
        setAgentBusy(false)
        break

      case 'turn_interrupted':
        setAgentBusy(false)
        setStatusMsg('Turn interrupted — tap mic to retry')
        break

      case 'show_slide':
        setSlide({ page: ev.page, caption: ev.caption })
        setTurns((prev) => {
          const next = [...prev]
          let idx = next.length - 1
          while (idx >= 0 && next[idx]!.role !== 'assistant') idx--
          if (idx >= 0) {
            const t = next[idx] as Turn
            next[idx] = { ...t, figures: [...(t.figures ?? []), { type: 'slide', page: ev.page, caption: ev.caption, lessonId }] }
          }
          return next
        })
        break

      case 'open_sketchpad':
        setSketchpad({
          prompt: ev.prompt,
          invocationId: ev.invocation_id,
          textBg: ev.text_bg,
          imBg: ev.im_bg,
        })
        break

      case 'take_photo':
        setCamera({ prompt: ev.prompt, invocationId: ev.invocation_id })
        break

      case 'section_advanced':
        setCurrState(ev.curriculum)
        break

      case 'curriculum_complete':
        setCurrComplete(true)
        setStatusMsg('Curriculum complete!')
        break

      case 'decompose_complete':
        setCurriculum(ev.curriculum)
        if (ev.curriculum.idx !== undefined && ev.curriculum.idx > 0) {
          setCurrState({
            title: '',
            idx: ev.curriculum.idx,
            total: ev.curriculum.sections.length,
          })
        }
        setStatusMsg('Lesson ready!')
        break

      case 'history':
        setTurns(ev.turns.map((t) => ({
          role: t.role,
          text: t.text,
          complete: true,
          figures: t.figures?.map((fig) =>
            fig.type === 'slide'
              ? { type: 'slide' as const, page: fig.page, caption: fig.caption, lessonId: lessonId! }
              : { type: 'drawing' as const, dataUrl: `data:image/png;base64,${fig.data}`, prompt: fig.prompt }
          ),
        })))
        break

      case 'status':
        setStatusMsg(ev.message)
        break

      case 'error':
        setStatusMsg(`Error: ${ev.message}`)
        setAgentBusy(false)
        break

      case 'tts_playing':
      case 'chunk_complete':
      case 'chunk_ready':
      case 'response_end':
      case 'pong':
        break
    }
  }, [enqueue])

  // ── WS connection ─────────────────────────────────────────────────────────
  const wsOpts = lessonId ? { sessionId, lessonId, onEvent: handleEvent } : null
  const { status: wsStatus, send } = useWebSocket(wsOpts)

  // Send reconnect event when connection re-establishes after a drop
  const prevStatus = useRef(wsStatus)
  useEffect(() => {
    if (prevStatus.current !== 'connected' && wsStatus === 'connected') {
      if (lastTurnIdRef.current) {
        send({ event: 'reconnect', last_turn_id: lastTurnIdRef.current })
      }
    }
    prevStatus.current = wsStatus
  }, [wsStatus, send])

  // Reset start flag when disconnected so reconnect re-sends start_lesson
  useEffect(() => {
    if (wsStatus !== 'connected') lessonStartedRef.current = false
  }, [wsStatus])

  // Send set_instructions once personasReady; also send start_lesson on first connect.
  // Waiting for personasReady ensures the correct persona is applied before the lesson begins.
  useEffect(() => {
    if (wsStatus !== 'connected' || !personasReady) return
    const persona = personas.find((p) => p.id === selectedPersonaId)
    send({ event: 'set_instructions', instructions: persona?.instructions ?? '' })
    if (!lessonStartedRef.current) {
      lessonStartedRef.current = true
      send({ event: 'start_lesson' })
    }
  }, [selectedPersonaId, wsStatus, personas, personasReady, send])

  // Send set_voice whenever voice selection changes
  useEffect(() => {
    if (wsStatus !== 'connected' || !selectedVoiceId) return
    send({ event: 'set_voice', voice: selectedVoiceId })
  }, [selectedVoiceId, wsStatus, send])

  // Send set_stt_language whenever language selection changes
  useEffect(() => {
    if (wsStatus !== 'connected') return
    send({ event: 'set_stt_language', language: selectedLangCode || null })
  }, [selectedLangCode, wsStatus, send])

  // ── recorder ─────────────────────────────────────────────────────────────
  const { isRecording, isSpeaking, start, stop } = useRecorder({
    onUtterance: useCallback((data: string, sampleRate: number) => {
      send({ event: 'audio_input', data, sample_rate: sampleRate })
    }, [send]),
  })

  function toggleRecord() {
    if (isRecording) stop()
    else void start()
  }

  function cancelTurn() {
    send({ event: 'cancel_turn' })
    setAgentBusy(false)
  }

  // ── sketchpad submit ─────────────────────────────────────────────────────
  function handleSketchSubmit(invocationId: string, drawing: string) {
    const prompt = sketchpad?.prompt ?? ''
    send({ event: 'tool_result', invocation_id: invocationId, result: { drawing } })
    setSketchpad(null)
    setTurns((prev) => [...prev, {
      role: 'user' as const,
      text: '',
      complete: true,
      figures: [{ type: 'drawing' as const, dataUrl: `data:image/png;base64,${drawing}`, prompt }],
    }])
  }

  // ── camera submit ─────────────────────────────────────────────────────────
  function handlePhotoSubmit(invocationId: string, photo: string) {
    const prompt = camera?.prompt ?? ''
    send({ event: 'tool_result', invocation_id: invocationId, result: { photo } })
    setCamera(null)
    setTurns((prev) => [...prev, {
      role: 'user' as const,
      text: '',
      complete: true,
      figures: [{ type: 'drawing' as const, dataUrl: `data:image/png;base64,${photo}`, prompt }],
    }])
  }

  // ── figure click ─────────────────────────────────────────────────────────
  function handleFigureClick(fig: Figure) {
    if (fig.type === 'slide') {
      setSlide({ page: fig.page, caption: fig.caption })
    } else {
      setDrawingView({ dataUrl: fig.dataUrl, prompt: fig.prompt })
    }
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {/* Top bar */}
      <StatusBar wsStatus={wsStatus} message={statusMsg} />

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Conversation area */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Nav */}
          <div className="flex items-center gap-2 border-b border-[hsl(var(--border))] px-4 py-2">
            <Button variant="ghost" size="icon" onClick={() => navigate('/')} aria-label="Back">
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <span className="text-sm font-medium truncate">{lessonId}</span>
          </div>

          <ConversationView turns={turns} onReplayTurn={replay} onFigureClick={handleFigureClick} />

          {/* Controls */}
          <div className="flex items-center justify-center gap-4 border-t border-[hsl(var(--border))] p-4">
            {agentBusy && (
              <Button variant="ghost" size="icon" onClick={cancelTurn} aria-label="Cancel turn">
                <X className="h-5 w-5" />
              </Button>
            )}
            <RecordButton
              isRecording={isRecording}
              isSpeaking={isSpeaking}
              disabled={wsStatus !== 'connected' || (agentBusy && !isRecording)}
              onClick={toggleRecord}
            />
          </div>
        </div>

        {/* Curriculum sidebar */}
        <aside className="hidden w-64 shrink-0 overflow-y-auto border-l border-[hsl(var(--border))] lg:block">
          <CurriculumPanel
            curriculum={curriculum}
            state={currState}
            complete={currComplete}
            personas={personas}
            selectedPersonaId={selectedPersonaId}
            onPersonaChange={setSelectedPersonaId}
            voices={voices}
            selectedVoiceId={selectedVoiceId}
            onVoiceChange={setSelectedVoiceId}
            sttLanguages={sttLanguages}
            selectedLangCode={selectedLangCode}
            onLangChange={setSelectedLangCode}
          />
        </aside>
      </div>

      {/* Overlays */}
      {slide && (
        <SlideViewer
          lessonId={lessonId}
          page={slide.page}
          caption={slide.caption}
          onClose={() => setSlide(null)}
          isRecording={isRecording}
          isSpeaking={isSpeaking}
          recordDisabled={wsStatus !== 'connected' || (agentBusy && !isRecording)}
          onRecord={toggleRecord}
        />
      )}
      {sketchpad && (
        <Sketchpad
          prompt={sketchpad.prompt}
          invocationId={sketchpad.invocationId}
          textBg={sketchpad.textBg}
          imBg={sketchpad.imBg}
          onSubmit={handleSketchSubmit}
          onCancel={() => setSketchpad(null)}
        />
      )}
      {camera && (
        <CameraCapture
          prompt={camera.prompt}
          invocationId={camera.invocationId}
          onSubmit={handlePhotoSubmit}
          onCancel={() => setCamera(null)}
        />
      )}
      {drawingView && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={() => setDrawingView(null)}
        >
          <div
            className="relative rounded-lg bg-[hsl(var(--card))] shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setDrawingView(null)}
              aria-label="Close drawing"
              className="absolute right-2 top-2 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-black/40 text-white hover:bg-black/60 touch-manipulation"
            >
              ✕
            </button>
            <ZoomableImage
              src={drawingView.dataUrl}
              alt={drawingView.prompt}
              className="block max-h-[85vh] max-w-[90vw] w-auto rounded-lg"
            />
            <p className="px-3 py-2 text-sm text-[hsl(var(--muted-foreground))]">{drawingView.prompt}</p>
          </div>
        </div>
      )}
    </div>
  )
}
