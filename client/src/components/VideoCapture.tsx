/**
 * VideoCapture: record a short video clip and return evenly-sampled JPEG frames.
 *
 * Flow:
 *   requesting → recording (live viewfinder + timer) → previewing → submitting
 *
 * On submit, N_FRAMES evenly-spaced frames are extracted via <canvas> and returned
 * to the parent as an array of base64 JPEG strings (no "data:..." prefix).
 * The agent receives these as a sequence of images.
 */

import { useRef, useEffect, useState, useCallback } from 'react'
import { Button } from './ui/button'

const N_FRAMES = 10       // frames sampled from the recording
const MAX_SECONDS = 30    // hard cap on recording length

interface VideoCaptureProps {
  prompt: string
  invocationId: string
  onSubmit: (invocationId: string, frames: string[]) => void
  onCancel: () => void
}

type Phase = 'requesting' | 'recording' | 'previewing' | 'extracting' | 'error'

/** Seek through a video element and capture one JPEG frame per target time. */
async function extractFrames(blob: Blob, count: number): Promise<string[]> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(blob)
    const video = document.createElement('video')
    video.src = url
    video.muted = true
    video.preload = 'metadata'

    const canvas = document.createElement('canvas')
    const frames: string[] = []
    let i = 0

    video.onloadedmetadata = () => {
      const dur = Math.max(video.duration, 0.01)
      canvas.width = video.videoWidth || 640
      canvas.height = video.videoHeight || 480

      function next() {
        if (i >= count) {
          URL.revokeObjectURL(url)
          resolve(frames)
          return
        }
        // Place seek point in the middle of each equal-width interval
        video.currentTime = (i + 0.5) * (dur / count)
        i++
      }

      video.onseeked = () => {
        const ctx = canvas.getContext('2d')!
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        const b64 = canvas.toDataURL('image/jpeg', 0.82).replace(/^data:image\/jpeg;base64,/, '')
        frames.push(b64)
        next()
      }

      next()
    }

    video.onerror = () => { URL.revokeObjectURL(url); resolve(frames) }
    video.load()
  })
}

export function VideoCapture({ prompt, invocationId, onSubmit, onCancel }: VideoCaptureProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const [phase, setPhase] = useState<Phase>('requesting')
  const [errorMsg, setErrorMsg] = useState('')
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
  }, [])

  // Clean up on unmount or new invocationId
  useEffect(() => {
    return () => {
      stopStream()
      recorderRef.current?.stop()
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [invocationId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: 'user' }, audio: true })
      .then((stream) => {
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.play()
        }
        setPhase('recording')
      })
      .catch((err) => {
        setErrorMsg(err instanceof Error ? err.message : 'Camera/mic access denied')
        setPhase('error')
      })
  }, [invocationId])

  function startRecording() {
    const stream = streamRef.current
    if (!stream) return
    chunksRef.current = []
    setElapsed(0)

    const recorder = new MediaRecorder(stream)
    recorderRef.current = recorder

    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
    recorder.onstop = () => {
      stopStream()
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType })
      const url = URL.createObjectURL(blob)
      setBlobUrl(url)
      if (videoRef.current) {
        videoRef.current.srcObject = null
        videoRef.current.src = url
        videoRef.current.muted = false
        videoRef.current.loop = true
        videoRef.current.play()
      }
      setPhase('previewing')
    }

    recorder.start()

    timerRef.current = setInterval(() => {
      setElapsed((s) => {
        if (s + 1 >= MAX_SECONDS) stopRecording()
        return s + 1
      })
    }, 1000)
  }

  function stopRecording() {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    recorderRef.current?.stop()
  }

  async function handleSubmit() {
    if (!blobUrl) return
    setPhase('extracting')
    try {
      const resp = await fetch(blobUrl)
      const blob = await resp.blob()
      const frames = await extractFrames(blob, N_FRAMES)
      onSubmit(invocationId, frames)
    } catch {
      setErrorMsg('Failed to process video')
      setPhase('error')
    }
  }

  function handleCancel() {
    stopStream()
    recorderRef.current?.stop()
    if (blobUrl) URL.revokeObjectURL(blobUrl)
    onCancel()
  }

  const isRecordingActive = phase === 'recording' && recorderRef.current?.state === 'recording'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex w-full max-w-[560px] flex-col gap-4 rounded-lg bg-[hsl(var(--card))] p-5 shadow-xl">
        <p className="text-sm font-medium">{prompt}</p>

        {/* Viewfinder / preview */}
        <div
          className="relative overflow-hidden rounded border border-[hsl(var(--border))] bg-black"
          style={{ aspectRatio: '4/3' }}
        >
          {phase === 'requesting' && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-[hsl(var(--muted-foreground))]">
              Requesting camera…
            </div>
          )}
          {phase === 'error' && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center">
              <p className="text-sm text-[hsl(var(--destructive))]">Camera unavailable</p>
              <p className="text-xs text-[hsl(var(--muted-foreground))]">{errorMsg}</p>
            </div>
          )}
          {phase === 'extracting' && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-[hsl(var(--muted-foreground))]">
              Processing…
            </div>
          )}

          <video
            ref={videoRef}
            className="absolute inset-0 h-full w-full object-cover"
            style={{ display: (phase === 'recording' || phase === 'previewing') ? 'block' : 'none' }}
            playsInline
            muted={phase === 'recording'}
          />

          {/* Recording indicator */}
          {isRecordingActive && (
            <div className="absolute left-2 top-2 flex items-center gap-1.5 rounded-full bg-black/60 px-2 py-1">
              <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
              <span className="text-xs font-medium text-white">
                {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')} / {MAX_SECONDS}s
              </span>
            </div>
          )}
        </div>

        {/* Controls */}
        <div className="flex justify-end gap-2">
          {phase === 'recording' && !isRecordingActive && (
            <Button size="sm" onClick={startRecording}>Start Recording</Button>
          )}
          {isRecordingActive && (
            <Button size="sm" variant="destructive" onClick={stopRecording}>Stop</Button>
          )}
          {phase === 'previewing' && (
            <>
              <Button variant="outline" size="sm" onClick={() => {
                if (blobUrl) URL.revokeObjectURL(blobUrl)
                setBlobUrl(null)
                setPhase('requesting')
                navigator.mediaDevices
                  .getUserMedia({ video: { facingMode: 'user' }, audio: true })
                  .then((stream) => {
                    streamRef.current = stream
                    if (videoRef.current) {
                      videoRef.current.src = ''
                      videoRef.current.srcObject = stream
                      videoRef.current.muted = true
                      videoRef.current.play()
                    }
                    setPhase('recording')
                  })
                  .catch((err) => {
                    setErrorMsg(err instanceof Error ? err.message : 'Camera/mic access denied')
                    setPhase('error')
                  })
              }}>Re-record</Button>
              <Button size="sm" onClick={handleSubmit}>Submit</Button>
            </>
          )}
          <Button variant="ghost" size="sm" onClick={handleCancel}>Cancel</Button>
        </div>
      </div>
    </div>
  )
}
