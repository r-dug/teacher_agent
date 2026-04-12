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
 *  - Sketchpad (when open_sketchpad received)
 */

import { useCallback, useState, useRef, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, X, Menu, Loader2, VolumeX } from 'lucide-react'

import { StatusBar } from '@/components/StatusBar'
import { ConversationView, type Turn, type Figure } from '@/components/ConversationView'
import { CurriculumPanel } from '@/components/CurriculumPanel'
import { ImageViewer } from '@/components/ImageViewer'
import { Celebration } from '@/components/Celebration'
import { ZoomableImage } from '@/components/ZoomableImage'
import { Sketchpad, type SketchpadPrefs } from '@/components/Sketchpad'
import { CameraCapture } from '@/components/CameraCapture'
import { VideoCapture } from '@/components/VideoCapture'
import { CodeEditor, type CodeOutput } from '@/components/CodeEditor'
import { HtmlCssEditor } from '@/components/HtmlCssEditor'
import { TimerExercise } from '@/components/TimerExercise'
import { ProgressView } from '@/components/ProgressView'
import { TextInputTool } from '@/components/TextInputTool'
import { Quiz } from '@/components/Quiz'
import { FillInTheBlank } from '@/components/FillInTheBlank'
import { FlashcardDeck } from '@/components/FlashcardDeck'
import { OrderingExercise } from '@/components/OrderingExercise'
import { InputBar } from '@/components/InputBar'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { TokenUsageDisplay } from '@/components/TokenUsageDisplay'
import { SettingsDrawer } from '@/components/SettingsDrawer'

import { useWebSocket } from '@/hooks/useWebSocket'
import { useRecorder } from '@/hooks/useRecorder'
import { useAudioPlayer } from '@/hooks/useAudioPlayer'
import { useRealtimeStreamRecorder } from '@/hooks/useRealtimeStreamRecorder'

import type {
  ServerEvent,
  CurriculumData,
  CurriculumState,
  Persona,
  Voice,
  VoiceArch,
  SttLanguage,
  SttModel,
  SttProvider,
} from '@/lib/types'


interface TeachPageProps {
  sessionId: string
  isAdmin?: boolean
  onLogout?: () => void
  userEmail?: string
}

interface SketchpadState {
  prompt: string
  invocationId: string
  textBg?: string
  imBg?: string
}

export function TeachPage({ sessionId, isAdmin = false, onLogout, userEmail = '' }: TeachPageProps) {
  const { lessonId = '' } = useParams<{ lessonId: string }>()
  const navigate = useNavigate()

  // ── state ────────────────────────────────────────────────────────────────
  const [turns, setTurns] = useState<Turn[]>([])
  const [statusMsg, setStatusMsg] = useState('')
  const [curriculum, setCurriculum] = useState<CurriculumData | null>(null)
  const [currState, setCurrState] = useState<CurriculumState | null>(null)
  const [currComplete, setCurrComplete] = useState(false)
  const [celebration, setCelebration] = useState<{ points: number; reason: string } | null>(null)
  const [generatedImage, setGeneratedImage] = useState<{ imageUrl: string; caption: string } | null>(null)
  const [generatingImage, setGeneratingImage] = useState(false)
  const [sketchpad, setSketchpad] = useState<SketchpadState | null>(null)
  const [camera, setCamera] = useState<{ prompt: string; invocationId: string } | null>(null)
  const [videoCapture, setVideoCapture] = useState<{ prompt: string; invocationId: string } | null>(null)
  const [codeEditor, setCodeEditor] = useState<{ prompt: string; language: string; starterCode?: string; invocationId: string } | null>(null)
  const [codeOutput, setCodeOutput] = useState<CodeOutput>({ stdout: '', stderr: '', exitCode: null, elapsedMs: null, running: false })
  const activeCodeInvId = useRef<string | null>(null)
  const [htmlEditor, setHtmlEditor] = useState<{ prompt: string; starterHtml?: string; starterCss?: string; invocationId: string } | null>(null)
  const [drawingView, setDrawingView] = useState<{ dataUrl: string; prompt: string } | null>(null)
  const [timerExercise, setTimerExercise] = useState<{ prompt: string; invocationId: string; durationSeconds: number } | null>(null)
  const [progressView, setProgressView] = useState<{ sections: Array<{ title: string; idx: number; completed: boolean }>; currentIdx: number; tasks: Array<{ concept: string; status: string }> } | null>(null)
  const [textInput, setTextInput] = useState<{ prompt: string; placeholder?: string; multiline?: boolean; invocationId: string } | null>(null)
  const [quiz, setQuiz] = useState<{ prompt: string; choices: Array<{ label: string; text: string }>; correctIndex: number; invocationId: string } | null>(null)
  const [fillBlank, setFillBlank] = useState<{ prompt: string; template: string; answers: string[]; invocationId: string } | null>(null)
  const [flashcards, setFlashcards] = useState<{ prompt: string; cards: Array<{ front: string; back: string }>; invocationId: string } | null>(null)
  const [ordering, setOrdering] = useState<{ prompt: string; items: string[]; correctOrder: number[]; invocationId: string } | null>(null)
  const [imageGenAvailable, setImageGenAvailable] = useState(false)
  const [imageGenEnabled, setImageGenEnabled] = useState(false)
  const [lessonTitle, setLessonTitle] = useState<string>('')
  const [decomposing, setDecomposing] = useState(false)
  const [agentBusy, setAgentBusy] = useState(false)
  const [inputText, setInputText] = useState('')

  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const lastTurnIdRef = useRef<string | null>(null)

  // ── sketchpad preferences ───────────────────────────────────────────────
  const [sketchPrefs, setSketchPrefs] = useState<SketchpadPrefs>({})
  const sketchPrefsSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    fetch('/api/preferences', { headers: { 'X-Session-Id': sessionId } })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.prefs?.sketchpad) setSketchPrefs(data.prefs.sketchpad)
      })
      .catch(() => {/* non-fatal */})
  }, [sessionId])

  const handleSketchPrefsChange = useCallback((prefs: SketchpadPrefs) => {
    setSketchPrefs(prefs)
    // Debounce save to avoid spamming API on slider drags
    if (sketchPrefsSaveTimer.current) clearTimeout(sketchPrefsSaveTimer.current)
    sketchPrefsSaveTimer.current = setTimeout(() => {
      fetch('/api/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({ prefs: { sketchpad: prefs } }),
      }).catch(() => {/* non-fatal */})
    }, 800)
  }, [sessionId])

  // ── personas ─────────────────────────────────────────────────────────────
  const [personas, setPersonas] = useState<Persona[]>([])
  const [selectedPersonaId, setSelectedPersonaId] = useState<string>('')
  const [personasReady, setPersonasReady] = useState(false)
  const personaInitRef = useRef(false)
  const lessonStartedRef = useRef(false)

  useEffect(() => {
    fetch('/api/personas', { headers: { 'X-Session-Id': sessionId } })
      .then((r) => r.json())
      .then((data: Persona[]) => { setPersonas(data); setPersonasReady(true) })
      .catch(() => setPersonasReady(true))  // start even if fetch fails
  }, [sessionId])

  useEffect(() => {
    if (!lessonId) return
    const h = { 'X-Session-Id': sessionId }
    fetch(`/api/lessons/${lessonId}`, { headers: h })
      .then((r) => r.ok ? r.json() : null)
      .then(async (data) => {
        if (!data) return
        if (data.title) setLessonTitle(data.title)
        // Resolve persona: enrollment > course default > "default"
        if (!personaInitRef.current) {
          personaInitRef.current = true
          if (data.persona_id) {
            setSelectedPersonaId(data.persona_id)
          } else if (data.course_id) {
            try {
              const cResp = await fetch(`/api/courses/${data.course_id}`, { headers: h })
              if (cResp.ok) {
                const course = await cResp.json()
                if (course.default_persona_id) {
                  setSelectedPersonaId(course.default_persona_id)
                  return
                }
              }
            } catch { /* non-fatal */ }
            setSelectedPersonaId('default')
          } else {
            setSelectedPersonaId('default')
          }
        }
      })
      .catch(() => {/* non-fatal */})
  }, [lessonId, sessionId])

  // Persist persona selection to enrollment
  const handlePersonaChange = useCallback((personaId: string) => {
    setSelectedPersonaId(personaId)
    if (lessonId) {
      fetch(`/api/lessons/${lessonId}/enrollment/persona`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
        body: JSON.stringify({ persona_id: personaId || null }),
      }).catch(() => {/* non-fatal */})
    }
  }, [lessonId, sessionId])

  // ── voices ───────────────────────────────────────────────────────────────
  const [voices, setVoices] = useState<Voice[]>([])
  const [selectedVoiceId, setSelectedVoiceId] = useState<string>('')
  const [voiceArches, setVoiceArches] = useState<VoiceArch[]>([])
  const [selectedVoiceArchId, setSelectedVoiceArchId] = useState<string>('')

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

  useEffect(() => {
    fetch('/api/voice-arches')
      .then((r) => r.json())
      .then((data: VoiceArch[]) => {
        const options = data.length > 0
          ? data
          : [
            { id: 'chained', label: 'Chained (STT -> LLM -> TTS)', is_default: true },
            { id: 'realtime', label: 'Realtime (OpenAI audio in/out)', is_default: false },
          ]
        setVoiceArches(options)
        const def = options.find((opt) => opt.is_default)?.id ?? options[0]?.id ?? 'chained'
        setSelectedVoiceArchId(def)
      })
      .catch(() => {
        const options = [
          { id: 'chained', label: 'Chained (STT -> LLM -> TTS)', is_default: true },
          { id: 'realtime', label: 'Realtime (OpenAI audio in/out)', is_default: false },
        ]
        setVoiceArches(options)
        setSelectedVoiceArchId('chained')
      })
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
  const [sttProviders, setSttProviders] = useState<SttProvider[]>([])
  const [selectedSttProviderId, setSelectedSttProviderId] = useState<string>('')
  const [sttModels, setSttModels] = useState<SttModel[]>([])
  const [selectedSttModelId, setSelectedSttModelId] = useState<string>('')

  const loadSttModels = useCallback((providerId: string) => {
    const qs = providerId ? `?provider=${encodeURIComponent(providerId)}` : ''
    fetch(`/api/stt-models${qs}`)
      .then((r) => r.json())
      .then((data: SttModel[]) => {
        setSttModels(data)
        setSelectedSttModelId((prev) => {
          if (prev && data.some((m) => m.id === prev)) return prev
          const def = data.find((m) => m.is_default)
          return def?.id ?? data[0]?.id ?? ''
        })
      })
      .catch(() => {/* non-fatal */})
  }, [])

  useEffect(() => {
    fetch('/api/stt-providers')
      .then((r) => r.json())
      .then((data: SttProvider[]) => {
        const providers = data.length > 0 ? data : [{ id: 'local', is_default: true }, { id: 'openai', is_default: false }]
        setSttProviders(providers)
        const def = providers.find((p) => p.is_default)?.id ?? providers[0]?.id ?? 'local'
        setSelectedSttProviderId(def)
        loadSttModels(def)
      })
      .catch(() => {
        const providers = [{ id: 'local', is_default: true }, { id: 'openai', is_default: false }]
        setSttProviders(providers)
        setSelectedSttProviderId('local')
        loadSttModels('local')
      })
  }, [loadSttModels])

  useEffect(() => {
    if (!selectedSttProviderId) return
    loadSttModels(selectedSttProviderId)
  }, [selectedSttProviderId, loadSttModels])

  // ── audio ────────────────────────────────────────────────────────────────
  const { enqueue, replay, stop: stopAudio } = useAudioPlayer()
  const [ttsPlaying, setTtsPlaying] = useState(false)

  // Mute the realtime mic while TTS is playing + 1.5 s cooldown after it stops.
  // This prevents the teacher's own TTS audio from bleeding into the mic and
  // producing phantom "user" transcriptions (self-interruption).
  const [micMuted, setMicMuted] = useState(false)
  const muteCooldownRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (ttsPlaying) {
      if (muteCooldownRef.current) clearTimeout(muteCooldownRef.current)
      setMicMuted(true)
    } else {
      muteCooldownRef.current = setTimeout(() => setMicMuted(false), 500)
    }
    return () => {
      if (muteCooldownRef.current) clearTimeout(muteCooldownRef.current)
    }
  }, [ttsPlaying])

  // Stop audio playback when navigating away
  useEffect(() => () => { stopAudio() }, [stopAudio])

  // ── WS event handler ─────────────────────────────────────────────────────
  const handleEvent = useCallback((ev: ServerEvent) => {
    switch (ev.event) {
      case 'transcription':
        lastTurnIdRef.current = ev.turn_id
        setTurns((prev) => {
          const next = [...prev]

          // De-dupe/update if this user turn already exists.
          const existingIdx = next.findIndex((t) => t.role === 'user' && t.turnId === ev.turn_id)
          if (existingIdx >= 0) {
            next[existingIdx] = { ...next[existingIdx]!, text: ev.text, complete: true, turnId: ev.turn_id }
            return next
          }

          const userTurn: Turn = { role: 'user', text: ev.text, complete: true, turnId: ev.turn_id }
          const last = next[next.length - 1]

          // If assistant has already started streaming, insert user text before
          // the active assistant bubble so chunk routing remains stable.
          if (last?.role === 'assistant' && !last.complete) {
            next.splice(next.length - 1, 0, userTurn)
          } else {
            next.push(userTurn)
          }
          return next
        })
        setAgentBusy(true)
        break

      case 'turn_start':
        setTurns((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant' && !last.complete) return next
          next.push({ role: 'assistant', text: '', complete: false })
          return next
        })
        break

      case 'text_chunk':
        setTurns((prev) => {
          const next = [...prev]

          // Prefer exact turn_idx match for out-of-order event safety.
          let targetIdx = -1
          for (let i = next.length - 1; i >= 0; i -= 1) {
            const t = next[i]
            if (t?.role === 'assistant' && t.turnIdx === ev.turn_idx) {
              targetIdx = i
              break
            }
          }

          // Fallback: attach to latest in-progress assistant without a turnIdx.
          if (targetIdx === -1) {
            for (let i = next.length - 1; i >= 0; i -= 1) {
              const t = next[i]
              if (t?.role === 'assistant' && !t.complete && t.turnIdx === undefined) {
                targetIdx = i
                break
              }
            }
          }

          if (targetIdx === -1) {
            next.push({ role: 'assistant', text: ev.text, turnIdx: ev.turn_idx, complete: false })
            return next
          }

          const target = next[targetIdx] as Turn
          next[targetIdx] = {
            ...target,
            text: (target.text ?? '') + ev.text,
            turnIdx: ev.turn_idx,
            complete: false,
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
          let idx = -1
          for (let i = next.length - 1; i >= 0; i -= 1) {
            const t = next[i]
            if (t?.role === 'assistant' && !t.complete) {
              idx = i
              break
            }
          }
          if (idx === -1) {
            for (let i = next.length - 1; i >= 0; i -= 1) {
              if (next[i]?.role === 'assistant') {
                idx = i
                break
              }
            }
          }
          if (idx >= 0) {
            const t = next[idx] as Turn
            // Safety net: if the LLM repeated itself after a tool call,
            // the text may be the same block pasted twice. Strip the duplicate.
            let text = t.text ?? ''
            const len = text.length
            if (len >= 60) {
              // Try halves first (exact repeat of full text)
              const half = len >> 1
              if (text.slice(0, half) === text.slice(half)) {
                text = text.slice(0, half)
              } else {
                // Try detecting a repeated block that doesn't split evenly:
                // search for the first char sequence (min 30 chars) that
                // appears again later and runs to the end.
                const probe = text.slice(0, 40)
                const secondOccurrence = text.indexOf(probe, 30)
                if (secondOccurrence > 0 && text.slice(0, len - secondOccurrence) === text.slice(secondOccurrence)) {
                  text = text.slice(0, secondOccurrence)
                }
              }
            }
            next[idx] = { ...t, text, complete: true }
          }
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

      case 'show_progress':
        setProgressView({ sections: ev.sections, currentIdx: ev.current_idx, tasks: ev.tasks })
        break

      case 'play_audio_clip':
        // Audio clip is synthesized by backend TTS and arrives as audio_chunk events.
        // No UI needed — just a brief status indicator.
        setStatusMsg('Playing audio clip...')
        break

      case 'text_input':
        setTextInput({ prompt: ev.prompt, placeholder: ev.placeholder, multiline: ev.multiline, invocationId: ev.invocation_id })
        break

      case 'show_quiz':
        setQuiz({ prompt: ev.prompt, choices: ev.choices, correctIndex: ev.correct_index, invocationId: ev.invocation_id })
        break

      case 'fill_in_the_blank':
        setFillBlank({ prompt: ev.prompt, template: ev.template, answers: ev.answers, invocationId: ev.invocation_id })
        break

      case 'show_flashcard_deck':
        setFlashcards({ prompt: ev.prompt, cards: ev.cards, invocationId: ev.invocation_id })
        break

      case 'ordering_exercise':
        setOrdering({ prompt: ev.prompt, items: ev.items, correctOrder: ev.correct_order, invocationId: ev.invocation_id })
        break

      case 'generating_image':
        setGeneratingImage(true)
        setStatusMsg('Generating image…')
        break

      case 'show_image':
        setGeneratingImage(false)
        setStatusMsg('')
        setGeneratedImage({ imageUrl: ev.image_url, caption: ev.caption ?? '' })
        setTurns((prev) => {
          const next = [...prev]
          let idx = next.length - 1
          while (idx >= 0 && next[idx]!.role !== 'assistant') idx--
          if (idx >= 0) {
            const t = next[idx] as Turn
            next[idx] = {
              ...t,
              figures: [...(t.figures ?? []), {
                type: 'generated_image' as const,
                imageUrl: ev.image_url,
                caption: ev.caption ?? '',
                prompt: ev.prompt ?? '',
              }],
            }
          }
          return next
        })
        break

      case 'generation_failed':
        setGeneratingImage(false)
        setStatusMsg(`Image generation failed: ${ev.reason ?? 'unknown error'}`)
        break

      case 'capabilities':
        setImageGenAvailable(ev.image_gen_available)
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

      case 'points_awarded':
        setCelebration({ points: ev.points, reason: ev.reason })
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
          figures: t.figures?.map((fig) => {
            if (fig.type === 'generated_image')
              return { type: 'generated_image' as const, imageUrl: fig.image_url, caption: fig.caption, prompt: fig.prompt }
            return { type: 'drawing' as const, dataUrl: `data:image/png;base64,${fig.data}`, prompt: fig.prompt }
          }),
        })))
        break

      case 'status':
        setStatusMsg(ev.message)
        break

      case 'error':
        setStatusMsg(`Error: ${ev.message}`)

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

  // Send set_voice_arch whenever conversation mode selection changes
  useEffect(() => {
    if (wsStatus !== 'connected' || !selectedVoiceArchId) return
    send({ event: 'set_voice_arch', voice_arch: selectedVoiceArchId })
  }, [selectedVoiceArchId, wsStatus, send])

  // Send set_instructions once personasReady; also send start_lesson on first connect.
  // Waiting for personasReady ensures the correct persona is applied before the lesson begins.
  useEffect(() => {
    if (wsStatus !== 'connected' || !personasReady) return
    const persona = personas.find((p) => p.id === selectedPersonaId)
    send({ event: 'set_instructions', instructions: persona?.instructions ?? '', voice_instructions: persona?.voice_instructions ?? '' })
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

  // Send set_stt_provider whenever provider selection changes
  useEffect(() => {
    if (wsStatus !== 'connected' || !selectedSttProviderId) return
    send({ event: 'set_stt_provider', provider: selectedSttProviderId })
  }, [selectedSttProviderId, wsStatus, send])

  // Send set_stt_model whenever model selection changes
  useEffect(() => {
    if (wsStatus !== 'connected' || !selectedSttModelId) return
    send({ event: 'set_stt_model', model_size: selectedSttModelId })
  }, [selectedSttModelId, wsStatus, send])

  // ── image gen toggle ──────────────────────────────────────────────────────
  const handleImageGenChange = useCallback((enabled: boolean) => {
    setImageGenEnabled(enabled)
    send({ event: 'set_image_gen', enabled })
  }, [send])

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

  // ── Recorder (sends raw PCM via audio_input) ─────────
  const { isRecording, isSpeaking, start: startRec, stop: stopRec } = useRecorder({
    onUtterance: useCallback((data: string, sampleRate: number) => {
      send({ event: 'audio_input', data, sample_rate: sampleRate })
    }, [send]),
  })
  const {
    isRecording: isRealtimeRecording,
    isSpeaking: isRealtimeSpeaking,
    start: startRealtimeRec,
    stop: stopRealtimeRec,
  } = useRealtimeStreamRecorder({
    onChunk: useCallback((data: string, sampleRate: number) => {
      send({ event: 'realtime_stream_chunk', data, sample_rate: sampleRate })
    }, [send]),
    muted: micMuted,
  })

  const realtimeMode = selectedVoiceArchId === 'realtime'
  const recordIsActive = realtimeMode ? isRealtimeRecording : isRecording
  const recordIsSpeaking = realtimeMode ? isRealtimeSpeaking : isSpeaking

  function toggleRecord() {
    if (realtimeMode) {
      if (isRealtimeRecording) {
        void stopRealtimeRec()
        send({ event: 'realtime_stream_stop' })
      } else {
        send({ event: 'realtime_stream_start' })
        void startRealtimeRec().catch((err) => {
          setStatusMsg(`Realtime mic error: ${String(err)}`)
          send({ event: 'realtime_stream_stop' })
        })
      }
      return
    }
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

  useEffect(() => {
    if (selectedVoiceArchId === 'realtime') return
    if (isRealtimeRecording) {
      void stopRealtimeRec()
      send({ event: 'realtime_stream_stop' })
    }
  }, [selectedVoiceArchId, isRealtimeRecording, send, stopRealtimeRec])

  useEffect(() => {
    if (wsStatus === 'connected') return
    if (isRealtimeRecording) {
      void stopRealtimeRec()
    }
  }, [wsStatus, isRealtimeRecording, stopRealtimeRec])

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

  // ── new tool submit handlers ────────────────────────────────────────────
  function handleTextInputSubmit(invocationId: string, answer: string) {
    send({ event: 'tool_result', invocation_id: invocationId, result: { answer } })
    setTextInput(null)
  }

  function handleQuizSubmit(invocationId: string, selectedIndex: number, correct: boolean) {
    send({ event: 'tool_result', invocation_id: invocationId, result: { selected_index: selectedIndex, correct } })
    setQuiz(null)
  }

  function handleFillBlankSubmit(invocationId: string, studentAnswers: string[], correct: boolean[]) {
    send({ event: 'tool_result', invocation_id: invocationId, result: { student_answers: studentAnswers, correct } })
    setFillBlank(null)
  }

  function handleFlashcardSubmit(invocationId: string, results: Array<{ card_index: number; self_grade: 'correct' | 'incorrect' }>) {
    send({ event: 'tool_result', invocation_id: invocationId, result: { results } })
    setFlashcards(null)
  }

  function handleOrderingSubmit(invocationId: string, studentOrder: number[], correct: boolean) {
    send({ event: 'tool_result', invocation_id: invocationId, result: { student_order: studentOrder, correct } })
    setOrdering(null)
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
  function handleFigureClick(fig: Figure) {
    if (fig.type === 'generated_image') {
      setGeneratedImage({ imageUrl: fig.imageUrl, caption: fig.caption })
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
    onPersonaChange: handlePersonaChange,
    voices,
    selectedVoiceId,
    onVoiceChange: setSelectedVoiceId,
    voiceArches,
    selectedVoiceArchId,
    onVoiceArchChange: setSelectedVoiceArchId,
    sttLanguages,
    selectedLangCode,
    onLangChange: setSelectedLangCode,
    sttProviders,
    selectedSttProviderId,
    onSttProviderChange: setSelectedSttProviderId,
    sttModels,
    selectedSttModelId,
    onSttModelChange: setSelectedSttModelId,
    isAdmin,
    imageGenAvailable,
    imageGenEnabled,
    onImageGenChange: handleImageGenChange,
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
            <Button variant="ghost" size="icon" onClick={() => navigate('/')} aria-label="Back" data-page-transition>
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <span className="flex-1 text-sm font-medium truncate">{lessonTitle || lessonId}</span>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSettingsOpen(true)}
              aria-label="Settings"
            >
              <span className="text-base leading-none">⚙</span>
            </Button>
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

          <SettingsDrawer
            open={settingsOpen}
            onClose={() => setSettingsOpen(false)}
            sessionId={sessionId}
            userEmail={userEmail}
            onLogout={onLogout ?? (() => {})}
          />

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
            {realtimeMode && (
              <div className="flex justify-center border-t border-[hsl(var(--border))] py-1.5">
                <Button
                  variant={recordIsActive ? 'destructive' : 'secondary'}
                  size="sm"
                  onClick={toggleRecord}
                  disabled={wsStatus !== 'connected'}
                  className="text-xs"
                >
                  {recordIsActive ? 'Stop Live Mic' : 'Start Live Mic'}
                </Button>
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
      {celebration && (
        <Celebration
          points={celebration.points}
          reason={celebration.reason}
          onDone={() => setCelebration(null)}
        />
      )}
      {generatedImage && (
        <ImageViewer
          imageUrl={generatedImage.imageUrl}
          caption={generatedImage.caption}
          sessionId={sessionId}
          onClose={() => setGeneratedImage(null)}
          isRecording={recordIsActive}
          isSpeaking={recordIsSpeaking}
          recordDisabled={wsStatus !== 'connected' || (!realtimeMode && agentBusy && !recordIsActive)}
          onRecord={toggleRecord}
        />
      )}
      {sketchpad && (
        <Sketchpad
          prompt={sketchpad.prompt}
          invocationId={sketchpad.invocationId}
          textBg={sketchpad.textBg}
          imBg={sketchpad.imBg}
          prefs={sketchPrefs}
          onPrefsChange={handleSketchPrefsChange}
          onSubmit={handleSketchSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: sketchpad.invocationId, result: {} }); setSketchpad(null) }}
        />
      )}
      {camera && (
        <CameraCapture
          prompt={camera.prompt}
          invocationId={camera.invocationId}
          onSubmit={handlePhotoSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: camera.invocationId, result: {} }); setCamera(null) }}
        />
      )}
      {videoCapture && (
        <VideoCapture
          prompt={videoCapture.prompt}
          invocationId={videoCapture.invocationId}
          onSubmit={handleVideoSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: videoCapture.invocationId, result: {} }); setVideoCapture(null) }}
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
          onCancel={() => { send({ event: 'tool_result', invocation_id: codeEditor.invocationId, result: { code: '' } }); setCodeEditor(null); activeCodeInvId.current = null }}
        />
      )}
      {htmlEditor && (
        <HtmlCssEditor
          prompt={htmlEditor.prompt}
          starterHtml={htmlEditor.starterHtml}
          starterCss={htmlEditor.starterCss}
          invocationId={htmlEditor.invocationId}
          onSubmit={handleHtmlSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: htmlEditor.invocationId, result: { html: '', css: '' } }); setHtmlEditor(null) }}
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
      {progressView && (
        <ProgressView
          sections={progressView.sections}
          currentIdx={progressView.currentIdx}
          tasks={progressView.tasks}
          onClose={() => setProgressView(null)}
        />
      )}
      {textInput && (
        <TextInputTool
          prompt={textInput.prompt}
          placeholder={textInput.placeholder}
          multiline={textInput.multiline}
          invocationId={textInput.invocationId}
          onSubmit={handleTextInputSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: textInput.invocationId, result: {} }); setTextInput(null) }}
        />
      )}
      {quiz && (
        <Quiz
          prompt={quiz.prompt}
          choices={quiz.choices}
          correctIndex={quiz.correctIndex}
          invocationId={quiz.invocationId}
          onSubmit={handleQuizSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: quiz.invocationId, result: {} }); setQuiz(null) }}
        />
      )}
      {fillBlank && (
        <FillInTheBlank
          prompt={fillBlank.prompt}
          template={fillBlank.template}
          answers={fillBlank.answers}
          invocationId={fillBlank.invocationId}
          onSubmit={handleFillBlankSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: fillBlank.invocationId, result: {} }); setFillBlank(null) }}
        />
      )}
      {flashcards && (
        <FlashcardDeck
          prompt={flashcards.prompt}
          cards={flashcards.cards}
          invocationId={flashcards.invocationId}
          onSubmit={handleFlashcardSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: flashcards.invocationId, result: {} }); setFlashcards(null) }}
        />
      )}
      {ordering && (
        <OrderingExercise
          prompt={ordering.prompt}
          items={ordering.items}
          correctOrder={ordering.correctOrder}
          invocationId={ordering.invocationId}
          onSubmit={handleOrderingSubmit}
          onCancel={() => { send({ event: 'tool_result', invocation_id: ordering.invocationId, result: {} }); setOrdering(null) }}
        />
      )}

      {/* Image generation spinner — small non-blocking indicator */}
      {generatingImage && (
        <div className="fixed bottom-24 left-1/2 z-40 -translate-x-1/2 flex items-center gap-2 rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-4 py-2 shadow-lg text-sm">
          <Loader2 className="h-4 w-4 animate-spin text-[hsl(var(--primary))]" />
          Generating image…
        </div>
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
