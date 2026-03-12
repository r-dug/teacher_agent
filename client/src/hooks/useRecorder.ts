/**
 * Hook: manage the Recorder lifecycle.
 *
 * Returns { isRecording, isSpeaking, start, stop }.
 * Calls onUtterance when a VAD-segmented utterance is ready.
 */

import { useState, useRef, useCallback } from 'react'
import { Recorder } from '@/lib/audio/recorder'

interface UseRecorderOptions {
  onUtterance: (data: string, sampleRate: number) => void
}

export function useRecorder({ onUtterance }: UseRecorderOptions) {
  const [isRecording, setIsRecording] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const recorderRef = useRef<Recorder | null>(null)

  const start = useCallback(async () => {
    if (recorderRef.current) return
    const rec = new Recorder({
      onUtterance: (data, sampleRate) => {
        onUtterance(data, sampleRate)
        // Auto-stop after the utterance is emitted so the mic closes itself.
        rec.stop()
        recorderRef.current = null
        setIsRecording(false)
        setIsSpeaking(false)
      },
      onSpeaking: setIsSpeaking,
    })
    await rec.start()
    recorderRef.current = rec
    setIsRecording(true)
  }, [onUtterance])

  const stop = useCallback(() => {
    recorderRef.current?.stop()
    recorderRef.current = null
    setIsRecording(false)
    setIsSpeaking(false)
  }, [])

  return { isRecording, isSpeaking, start, stop }
}
