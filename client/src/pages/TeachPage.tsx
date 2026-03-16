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
import { ArrowLeft, X, Menu, Loader2, VolumeX } from 'lucide-react'

import { StatusBar } from '@/components/StatusBar'
import { ConversationView, type Turn, type Figure } from '@/components/ConversationView'
import { CurriculumPanel } from '@/components/CurriculumPanel'
import { SlideViewer } from '@/components/SlideViewer'
import { ZoomableImage } from '@/components/ZoomableImage'
import { Sketchpad } from '@/components/Sketchpad'
import { CameraCapture } from '@/components/CameraCapture'
import { VideoCapture } from '@/components/VideoCapture'
import { CodeEditor, type CodeOutput } from '@/components/CodeEditor'
import { HtmlCssEditor } from '@/components/HtmlCssEditor'
import { TimerExercise } from '@/components/TimerExercise'
import { InputBar } from '@/components/InputBar'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { TokenUsageDisplay } from '@/components/TokenUsageDisplay'

import { useWebSocket } from '@/hooks/useWebSocket'
import { useRecorder } from '@/hooks/useRecorder'
import { useAudioPlayer } from '@/hooks/useAudioPlayer'

import type { ServerEvent, CurriculumData, CurriculumState, Persona, Voice, SttLanguage, SttModel } from '@/lib/types'


interface TeachPageProps {
  sessionId: string
  isAdmin?: boolean
}

interface SketchpadState {
  prompt: string
  invocationId: string
  textBg?: string
  imBg?: string
}

interface SlideState {
  pageStart: number
  pageEnd: number
  caption?: string
}

export function TeachPage({ sessionId, isAdmin = false }: TeachPageProps) {
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
  const [videoCapture, setVideoCapture] = useState<{ prompt: string; invocationId: string } | null>(null)
  const [codeEditor, setCodeEditor] = useState<{ prompt: string; language: string; starterCode?: string; invocationId: string } | null>(null)
  const [codeOutput, setCodeOutput] = useState<CodeOutput>({ stdout: '', stderr: '', exitCode: null, elapsedMs: null, running: false })
  const activeCodeInvId = useRef<string | null>(null)
  const [htmlEditor, setHtmlEditor] = useState<{ prompt: string; starterHtml?: string; starterCss?: string; invocationId: string } | null>(null)
  const [drawingView, setDrawingView] = useState<{ dataUrl: string; prompt: string } | null>(null)
  const [timerExercise, setTimerExercise] = useState<{ prompt: string; invocationId: string; durationSeconds: number } | null>(null)
  const [decomposing, setDecomposing] = useState(false)
  const [agentBusy, setAgentBusy] = useState(false)
  const [inputText, setInputText] = useState('')
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
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

  // ── STT models ────────────────────────────────────────────────────────────
  const [sttModels, setSttModels] = useState<SttModel[]>([])
  const [selectedSttModelId, setSelectedSttModelId] = useState<string>('')

  useEffect(() => {
    fetch('/api/stt-models')
      .then((r) => r.json())
      .then((data: SttModel[]) => {
        setSttModels(data)
        const def = data.find((m) => m.is_default)
        if (def) setSelectedSttModelId(def.id)
      })
      .catch(() => {/* non-fatal */})
  }, [])

  // ── audio ────────────────────────────────────────────────────────────────
  const { enqueue, replay, stop: stopAudio } = useAudioPlayer()
  const [ttsPlaying, setTtsPlaying] = useState(false)

  // Stop audio playback when navigating away
  useEffect(() => () => { stopAudio() }, [stopAudio])

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
        setTtsPlaying(false)
        break

      case 'turn_interrupted':
        setAgentBusy(false)
        setTtsPlaying(false)
        setStatusMsg('Turn interrupted — tap mic to retry')
        break

      case 'show_slide':
        setSlide({ pageStart: ev.page_start, pageEnd: ev.page_end, caption: ev.caption })
        setTurns((prev) => {
          const next = [...prev]
          let idx = next.length - 1
          while (idx >= 0 && next[idx]!.role !== 'assistant') idx--
          if (idx >= 0) {
            const t = next[idx] as Turn
            next[idx] = { ...t, figures: [...(t.figures ?? []), { type: 'slide', page: ev.page_start, caption: ev.caption, lessonId }] }
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

      case 'record_video':
        setVideoCapture({ prompt: ev.prompt, invocationId: ev.invocation_id })
        break

      case 'open_code_editor':
        activeCodeInvId.current = ev.invocation_id
        setCodeOutput({ stdout: '', stderr: '', exitCode: null, elapsedMs: null, running: false })
        setCodeEditor({ prompt: ev.prompt, language: ev.language, starterCode: ev.starter_code, invocationId: ev.invocation_id })
        break

      case 'open_html_editor':
        setHtmlEditor({ prompt: ev.prompt, starterHtml: ev.starter_html, starterCss: ev.starter_css, invocationId: ev.invocation_id })
        break

      case 'start_timer':
        setTimerExercise({ prompt: ev.prompt, invocationId: ev.invocation_id, durationSeconds: ev.duration_seconds })
        break

      case 'code_stdout':
        if (ev.invocation_id === activeCodeInvId.current) {
          setCodeOutput((prev) => ({ ...prev, stdout: prev.stdout + ev.data }))
        }
        break

      case 'code_stderr':
        if (ev.invocation_id === activeCodeInvId.current) {
          setCodeOutput((prev) => ({ ...prev, stderr: prev.stderr + ev.data }))
        }
        break

      case 'code_done':
        if (ev.invocation_id === activeCodeInvId.current) {
          setCodeOutput((prev) => ({ ...prev, running: false, exitCode: ev.exit_code, elapsedMs: ev.elapsed_ms }))
        }
        break

      case 'code_error':
        if (ev.invocation_id === activeCodeInvId.current) {
          setCodeOutput((prev) => ({ ...prev, running: false, stderr: prev.stderr + `\nError: ${ev.message}` }))
        }
        break

      case 'section_advanced':
        setCurrState(ev.curriculum)
        break

      case 'curriculum_complete':
        setCurrComplete(true)
        setStatusMsg('Curriculum complete!')
        break

      case 'decompose_start':
        setDecomposing(true)
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
        setDecomposing(false)
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
        setErrorBanner(JSON.stringify(ev))
        setAgentBusy(false)
        setDecomposing(false)
        break

      case 'transcription_only':
        setInputText((prev) => prev ? prev + ' ' + ev.text : ev.text)
        break

      case 'tts_playing':
        setTtsPlaying(ev.playing)
        break

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

  // Send set_stt_model whenever model selection changes
  useEffect(() => {
    if (wsStatus !== 'connected' || !selectedSttModelId) return
    send({ event: 'set_stt_model', model_size: selectedSttModelId })
  }, [selectedSttModelId, wsStatus, send])

  // ── InputBar handlers ────────────────────────────────────────────────────
  const handleSendText = useCallback((text: string) => {
    send({ event: 'text_message', text })
    setInputText('')
  }, [send])

  const handleSendVoice = useCallback((b64: string, mimeType: string) => {
    send({ event: 'voice_message', data: b64, mime_type: mimeType })
  }, [send])

  const handleTranscribeAudio = useCallback((b64: string, sampleRate: number) => {
    send({ event: 'transcribe_only', data: b64, sample_rate: sampleRate })
  }, [send])

  // ── Recorder for SlideViewer mic (sends raw PCM via audio_input) ─────────
  const { isRecording, isSpeaking, start: startRec, stop: stopRec } = useRecorder({
    onUtterance: useCallback((data: string, sampleRate: number) => {
      send({ event: 'audio_input', data, sample_rate: sampleRate })
    }, [send]),
  })

  function toggleRecord() {
    if (isRecording) stopRec()
    else void startRec()
  }

  function cancelTurn() {
    send({ event: 'cancel_turn' })
    setAgentBusy(false)
  }

  function stopTalking() {
    stopAudio()
    send({ event: 'cancel_turn' })
    setTtsPlaying(false)
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

  // ── code editor ───────────────────────────────────────────────────────────
  function handleCodeRun(code: string, runtime: string) {
    if (!codeEditor) return
    setCodeOutput({ stdout: '', stderr: '', exitCode: null, elapsedMs: null, running: true })
    send({ event: 'run_code', invocation_id: codeEditor.invocationId, code, runtime })
  }

  function handleCodeSubmit(invocationId: string, code: string) {
    send({
      event: 'tool_result',
      invocation_id: invocationId,
      result: {
        code,
        stdout: codeOutput.stdout,
        stderr: codeOutput.stderr,
        exit_code: codeOutput.exitCode ?? -1,
      },
    })
    setCodeEditor(null)
    activeCodeInvId.current = null
  }

  // ── html/css editor ───────────────────────────────────────────────────────
  function handleHtmlSubmit(invocationId: string, html: string, css: string) {
    send({ event: 'tool_result', invocation_id: invocationId, result: { html, css } })
    setHtmlEditor(null)
  }

  // ── timer submit / cancel ─────────────────────────────────────────────────
  function handleTimerSubmit(invocationId: string, timedOut: boolean, answer: string, elapsedSeconds: number) {
    send({ event: 'tool_result', invocation_id: invocationId, result: { timed_out: timedOut, answer, elapsed_seconds: elapsedSeconds } })
    setTimerExercise(null)
  }

  function handleTimerCancel(invocationId: string) {
    send({ event: 'tool_result', invocation_id: invocationId, result: { timed_out: false, answer: '', elapsed_seconds: 0 } })
    setTimerExercise(null)
  }

  // ── video submit ──────────────────────────────────────────────────────────
  function handleVideoSubmit(invocationId: string, frames: string[]) {
    const prompt = videoCapture?.prompt ?? ''
    send({ event: 'tool_result', invocation_id: invocationId, result: { video_frames: frames } })
    setVideoCapture(null)
    // Show the first frame as a thumbnail in the conversation
    if (frames.length > 0) {
      setTurns((prev) => [...prev, {
        role: 'user' as const,
        text: '',
        complete: true,
        figures: [{ type: 'drawing' as const, dataUrl: `data:image/jpeg;base64,${frames[0]}`, prompt: `Video: ${prompt}` }],
      }])
    }
  }

  // ── figure click ─────────────────────────────────────────────────────────
  function handleAnnotate(compositeB64: string) {
    send({ event: 'image_input', data: compositeB64 })
    setTurns((prev) => [...prev, {
      role: 'user' as const,
      text: '',
      complete: true,
      figures: [{ type: 'drawing' as const, dataUrl: `data:image/png;base64,${compositeB64}`, prompt: 'Annotated slide' }],
    }])
  }

  function handleFigureClick(fig: Figure) {
    if (fig.type === 'slide') {
      setSlide({ pageStart: fig.page, pageEnd: fig.page, caption: fig.caption })
    } else {
      setDrawingView({ dataUrl: fig.dataUrl, prompt: fig.prompt })
    }
  }

  // Progress values for floating indicator and sidebar
  const progressTotal = curriculum?.sections.length ?? 0
  const progressCurrent = currState?.idx ?? 0
  const progressPct = currComplete ? 100 : progressTotal > 0 ? Math.round((progressCurrent / progressTotal) * 100) : 0

  // Shared CurriculumPanel props
  const curriculumPanelProps = {
    curriculum,
    state: currState,
    complete: currComplete,
    personas,
    selectedPersonaId,
    onPersonaChange: setSelectedPersonaId,
    voices,
    selectedVoiceId,
    onVoiceChange: setSelectedVoiceId,
    sttLanguages,
    selectedLangCode,
    onLangChange: setSelectedLangCode,
    sttModels,
    selectedSttModelId,
    onSttModelChange: setSelectedSttModelId,
    isAdmin,
    onViewPage: (pageStart: number, pageEnd: number) => {
      setSlide({ pageStart, pageEnd })
      setMobileSidebarOpen(false)
    },
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {/* Top bar */}
      <StatusBar wsStatus={wsStatus} message={statusMsg} />

      {/* Error banner */}
      {errorBanner && (
        <div className="flex items-start gap-2 border-b border-red-500/30 bg-red-500/10 px-4 py-2">
          <pre className="flex-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs text-red-400">{errorBanner}</pre>
          <button
            onClick={() => setErrorBanner(null)}
            aria-label="Dismiss error"
            className="shrink-0 text-red-400 hover:text-red-300"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Conversation area */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Nav */}
          <div className="flex items-center gap-2 border-b border-[hsl(var(--border))] px-4 py-2">
            <Button variant="ghost" size="icon" onClick={() => navigate('/')} aria-label="Back">
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <span className="flex-1 text-sm font-medium truncate">{lessonId}</span>
            {/* Hamburger — mobile only */}
            <Button
              variant="ghost"
              size="icon"
              className="lg:hidden"
              onClick={() => setMobileSidebarOpen(true)}
              aria-label="Open sidebar"
            >
              <Menu className="h-5 w-5" />
            </Button>
          </div>

          {/* Floating mini progress — mobile only, hidden when sidebar is open */}
          {curriculum && !mobileSidebarOpen && (
            <div className="fixed right-3 top-20 z-30 flex flex-col items-end gap-1 lg:hidden">
              <span className="rounded-full bg-[hsl(var(--card))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))] shadow-sm border border-[hsl(var(--border))]">
                {currComplete ? 'Done' : `${progressCurrent + 1} / ${progressTotal}`}
              </span>
              <Progress value={progressPct} className="w-20 shadow-sm" />
            </div>
          )}

          <ConversationView turns={turns} onReplayTurn={replay} onFigureClick={handleFigureClick} />

          {/* Bottom area: stop/cancel banner + InputBar */}
          <div className="flex flex-col">
            {(ttsPlaying || (agentBusy && !ttsPlaying)) && (
              <div className="flex justify-center border-t border-[hsl(var(--border))] py-1.5">
                {ttsPlaying ? (
                  <Button variant="ghost" size="sm" onClick={stopTalking} className="gap-1.5 text-xs">
                    <VolumeX className="h-3.5 w-3.5" />
                    Stop talking
                  </Button>
                ) : (
                  <Button variant="ghost" size="sm" onClick={cancelTurn} className="gap-1.5 text-xs">
                    <X className="h-3.5 w-3.5" />
                    Cancel
                  </Button>
                )}
              </div>
            )}
            <InputBar
              disabled={wsStatus !== 'connected' || agentBusy}
              inputText={inputText}
              onTextChange={setInputText}
              onSendText={handleSendText}
              onSendVoice={handleSendVoice}
              onTranscribeAudio={handleTranscribeAudio}
            />
          </div>
        </div>

        {/* Curriculum sidebar — desktop */}
        <aside className="hidden w-64 shrink-0 flex-col border-l border-[hsl(var(--border))] lg:flex">
          <div className="flex-1 overflow-y-auto">
            <CurriculumPanel {...curriculumPanelProps} />
          </div>
          <TokenUsageDisplay />
        </aside>
      </div>

      {/* Mobile sidebar drawer */}
      {mobileSidebarOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileSidebarOpen(false)}
          />
          {/* Panel */}
          <aside className="absolute right-0 top-0 flex h-full w-72 flex-col bg-[hsl(var(--card))] shadow-xl">
            <div className="flex items-center justify-between border-b border-[hsl(var(--border))] px-4 py-3">
              <span className="text-sm font-semibold">Curriculum</span>
              <button
                onClick={() => setMobileSidebarOpen(false)}
                aria-label="Close sidebar"
                className="flex h-7 w-7 items-center justify-center rounded-full hover:bg-[hsl(var(--accent))]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <CurriculumPanel {...curriculumPanelProps} />
            </div>
            <TokenUsageDisplay />
          </aside>
        </div>
      )}

      {/* Overlays */}
      {slide && (
        <SlideViewer
          lessonId={lessonId}
          sessionId={sessionId}
          pageStart={slide.pageStart}
          pageEnd={slide.pageEnd}
          caption={slide.caption}
          onClose={() => setSlide(null)}
          isRecording={isRecording}
          isSpeaking={isSpeaking}
          recordDisabled={wsStatus !== 'connected' || (agentBusy && !isRecording)}
          onRecord={toggleRecord}
          onAnnotate={handleAnnotate}
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
      {videoCapture && (
        <VideoCapture
          prompt={videoCapture.prompt}
          invocationId={videoCapture.invocationId}
          onSubmit={handleVideoSubmit}
          onCancel={() => setVideoCapture(null)}
        />
      )}
      {codeEditor && (
        <CodeEditor
          prompt={codeEditor.prompt}
          language={codeEditor.language}
          starterCode={codeEditor.starterCode}
          invocationId={codeEditor.invocationId}
          output={codeOutput}
          onRun={handleCodeRun}
          onSubmit={handleCodeSubmit}
          onCancel={() => { setCodeEditor(null); activeCodeInvId.current = null }}
        />
      )}
      {htmlEditor && (
        <HtmlCssEditor
          prompt={htmlEditor.prompt}
          starterHtml={htmlEditor.starterHtml}
          starterCss={htmlEditor.starterCss}
          invocationId={htmlEditor.invocationId}
          onSubmit={handleHtmlSubmit}
          onCancel={() => setHtmlEditor(null)}
        />
      )}
      {timerExercise && (
        <TimerExercise
          prompt={timerExercise.prompt}
          invocationId={timerExercise.invocationId}
          durationSeconds={timerExercise.durationSeconds}
          onSubmit={handleTimerSubmit}
          onCancel={handleTimerCancel}
        />
      )}

      {/* Decomposition loading overlay */}
      {decomposing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[hsl(var(--background))]/80 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-4 rounded-xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-10 py-8 shadow-2xl">
            <Loader2 className="h-10 w-10 animate-spin text-[hsl(var(--primary))]" />
            <div className="flex flex-col items-center gap-1 text-center">
              <span className="text-sm font-semibold">Preparing your lesson</span>
              {statusMsg && (
                <span className="text-xs text-[hsl(var(--muted-foreground))]">{statusMsg}</span>
              )}
            </div>
          </div>
        </div>
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
