/**
 * InputBar — unified input area with three modalities:
 *
 *  1. Text       — type directly, Enter sends, Shift+Enter newline
 *  2. STT→text   — left mic button, VAD-based, transcription fills text box for editing
 *  3. Voice msg  — right mic button, MediaRecorder (webm/opus), shows playable pending
 *                  chip; sends compressed audio directly to backend on ➤
 */

import { useState, useRef, useCallback, useEffect } from 'react'
import { Mic, Send, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Recorder } from '@/lib/audio/recorder'

interface InputBarProps {
  /** Disable all inputs (agent busy or disconnected). */
  disabled: boolean
  /** Controlled text value — parent can append transcription results. */
  inputText: string
  onTextChange: (text: string) => void
  /** Called when the user submits typed/STT-filled text. */
  onSendText: (text: string) => void
  /** Called when the user submits a recorded voice message. */
  onSendVoice: (b64: string, mimeType: string) => void
  /** Called when the VAD utterance fires — parent sends `transcribe_only` to backend. */
  onTranscribeAudio: (b64: string, sampleRate: number) => void
}

// Pick the best codec available in this browser
function getBestMimeType(): string {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
  ]
  return candidates.find((m) => MediaRecorder.isTypeSupported(m)) ?? ''
}

export function InputBar({
  disabled,
  inputText,
  onTextChange,
  onSendText,
  onSendVoice,
  onTranscribeAudio,
}: InputBarProps) {
  // ── STT mic state ──────────────────────────────────────────────────────────
  const [sttActive, setSttActive] = useState(false)
  const [sttSpeaking, setSttSpeaking] = useState(false)
  const sttRecorderRef = useRef<Recorder | null>(null)

  // ── Voice recording state ──────────────────────────────────────────────────
  const [audioRecording, setAudioRecording] = useState(false)
  const [audioSecs, setAudioSecs] = useState(0)
  const audioSecsRef = useRef(0)
  const [pendingAudio, setPendingAudio] = useState<{
    b64: string
    mime: string
    secs: number
    objectUrl: string
  } | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const audioTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Textarea auto-resize ───────────────────────────────────────────────────
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [inputText])

  // ── Cleanup on unmount ─────────────────────────────────────────────────────
  useEffect(() => () => {
    sttRecorderRef.current?.stop()
    if (audioTimerRef.current) clearInterval(audioTimerRef.current)
    try { mediaRecorderRef.current?.stop() } catch { /* ok */ }
  }, [])

  // ── STT mic ────────────────────────────────────────────────────────────────
  const startStt = useCallback(async () => {
    if (sttRecorderRef.current) return
    const rec = new Recorder({
      onUtterance: (b64, sampleRate) => {
        onTranscribeAudio(b64, sampleRate)
        rec.stop()
        sttRecorderRef.current = null
        setSttActive(false)
        setSttSpeaking(false)
      },
      onSpeaking: setSttSpeaking,
    })
    try {
      await rec.start()
      sttRecorderRef.current = rec
      setSttActive(true)
    } catch (err) {
      // Keep dictation failures non-fatal to the rest of the page (e.g. CSP or mic init issues).
      console.error('[STT] Failed to start dictation recorder:', err)
      setSttActive(false)
      setSttSpeaking(false)
    }
  }, [onTranscribeAudio])

  const stopStt = useCallback(() => {
    sttRecorderRef.current?.stop()
    sttRecorderRef.current = null
    setSttActive(false)
    setSttSpeaking(false)
  }, [])

  const toggleStt = useCallback(() => {
    if (sttActive) stopStt()
    else void startStt()
  }, [sttActive, startStt, stopStt])

  // ── Voice recording ────────────────────────────────────────────────────────
  const startAudioRecording = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
    const mimeType = getBestMimeType()
    audioChunksRef.current = []
    audioSecsRef.current = 0

    const mr = new MediaRecorder(stream, mimeType ? { mimeType } : {})
    mediaRecorderRef.current = mr

    mr.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunksRef.current.push(e.data)
    }

    mr.onstop = () => {
      stream.getTracks().forEach((t) => t.stop())
      const cleanMime = mimeType.split(';')[0] || 'audio/webm'
      const blob = new Blob(audioChunksRef.current, { type: cleanMime })
      const objectUrl = URL.createObjectURL(blob)
      const secs = audioSecsRef.current
      const reader = new FileReader()
      reader.onload = () => {
        const b64 = (reader.result as string).split(',')[1]!
        setPendingAudio({ b64, mime: cleanMime, secs, objectUrl })
      }
      reader.readAsDataURL(blob)
    }

    mr.start(100)
    setAudioRecording(true)
    setAudioSecs(0)
    audioTimerRef.current = setInterval(() => {
      audioSecsRef.current += 1
      setAudioSecs((s) => s + 1)
    }, 1000)
  }, [])

  const stopAudioRecording = useCallback(() => {
    if (audioTimerRef.current) { clearInterval(audioTimerRef.current); audioTimerRef.current = null }
    try { mediaRecorderRef.current?.stop() } catch { /* ok */ }
    mediaRecorderRef.current = null
    setAudioRecording(false)
  }, [])

  const toggleAudioRecording = useCallback(() => {
    if (audioRecording) stopAudioRecording()
    else void startAudioRecording()
  }, [audioRecording, startAudioRecording, stopAudioRecording])
  // Voice-message controls are temporarily hidden in the UI, but we keep this
  // callback wired for fast re-enable without rewriting recorder logic.
  // sure, that's fine.
  void toggleAudioRecording

  const discardPendingAudio = useCallback(() => {
    if (pendingAudio) URL.revokeObjectURL(pendingAudio.objectUrl)
    setPendingAudio(null)
    setAudioSecs(0)
    audioSecsRef.current = 0
  }, [pendingAudio])

  // ── Send ───────────────────────────────────────────────────────────────────
  const handleSend = useCallback(() => {
    if (pendingAudio) {
      onSendVoice(pendingAudio.b64, pendingAudio.mime)
      URL.revokeObjectURL(pendingAudio.objectUrl)
      setPendingAudio(null)
      setAudioSecs(0)
      audioSecsRef.current = 0
    } else if (inputText.trim()) {
      onSendText(inputText)
    }
  }, [pendingAudio, inputText, onSendVoice, onSendText])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend],
  )

  const canSend = !disabled && (!!pendingAudio || !!inputText.trim())
  const showTextArea = !pendingAudio && !audioRecording

  return (
    <div className="flex items-end gap-2 border-t border-[hsl(var(--border))] px-3 py-2">
      {/* STT mic button (left) — fills text box */}
      <Button
        variant="ghost"
        size="icon"
        onClick={toggleStt}
        disabled={disabled || !!pendingAudio || audioRecording}
        aria-label={sttActive ? 'Stop dictation' : 'Dictate to text box'}
        className={cn(
          'shrink-0 transition-colors',
          sttActive && sttSpeaking && 'text-[hsl(var(--primary))] animate-pulse',
          sttActive && !sttSpeaking && 'text-[hsl(var(--primary))]',
        )}
      >
        <Mic className="h-4 w-4" />
      </Button>

      {/* Middle area: textarea OR recording timer OR pending audio chip */}
      <div className="flex min-w-0 flex-1 items-end">
        {showTextArea ? (
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => onTextChange(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder="Message…"
            rows={1}
            className={cn(
              'w-full resize-none rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))]',
              'px-3 py-2 text-sm leading-relaxed outline-none',
              'placeholder:text-[hsl(var(--muted-foreground))]',
              'focus:ring-1 focus:ring-[hsl(var(--primary))]',
              'disabled:opacity-50',
            )}
            style={{ minHeight: '38px', maxHeight: '120px', overflow: 'hidden' }}
          />
        ) : audioRecording ? (
          <div className="flex h-[38px] flex-1 items-center gap-2 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
            <span className="font-mono text-sm tabular-nums text-[hsl(var(--foreground))]">
              {String(Math.floor(audioSecs / 60)).padStart(2, '0')}:{String(audioSecs % 60).padStart(2, '0')}
            </span>
          </div>
        ) : (
          /* Pending audio chip */
          <div className="flex h-[38px] flex-1 items-center gap-2 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3">
            <audio
              src={pendingAudio!.objectUrl}
              controls
              className="h-7 w-full max-w-[200px]"
              style={{ colorScheme: 'dark' }}
            />
            <span className="text-xs tabular-nums text-[hsl(var(--muted-foreground))]">
              {String(Math.floor(pendingAudio!.secs / 60)).padStart(2, '0')}:{String(pendingAudio!.secs % 60).padStart(2, '0')}
            </span>
            <button
              onClick={discardPendingAudio}
              aria-label="Discard voice message"
              className="ml-auto text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--destructive))]"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>

        {/* we do not want the voice recording button.... do not add it back in... */}

      {/* Send button */}
      <Button
        size="icon"
        onClick={handleSend}
        disabled={!canSend}
        aria-label="Send"
        className="shrink-0"
      >
        <Send className="h-4 w-4" />
      </Button>
    </div>
  )
}
