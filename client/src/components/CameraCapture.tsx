/**
 * CameraCapture: request camera access, show a live viewfinder, let the user
 * capture a frame, preview it, and submit as a base64 PNG.
 *
 * Flow: idle → streaming → captured → (submit | retake → streaming)
 */

import { useRef, useEffect, useState, useCallback } from 'react'
import { Button } from './ui/button'

interface CameraCaptureProps {
  prompt: string
  invocationId: string
  onSubmit: (invocationId: string, photoB64: string) => void
  onCancel: () => void
}

type Phase = 'requesting' | 'streaming' | 'captured' | 'error'

const W = 560
const H = 420

export function CameraCapture({ prompt, invocationId, onSubmit, onCancel }: CameraCaptureProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [phase, setPhase] = useState<Phase>('requesting')
  const [errorMsg, setErrorMsg] = useState('')
  const [capturedUrl, setCapturedUrl] = useState<string | null>(null)

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }, [])

  useEffect(() => {
    let cancelled = false

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      .then((stream) => {
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.play()
        }
        setPhase('streaming')
      })
      .catch((err) => {
        if (cancelled) return
        setErrorMsg(err instanceof Error ? err.message : 'Camera access denied')
        setPhase('error')
      })

    return () => {
      cancelled = true
      stopStream()
    }
  }, [invocationId, stopStream])

  function handleCapture() {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas) return

    canvas.width = video.videoWidth || W
    canvas.height = video.videoHeight || H
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(video, 0, 0)

    setCapturedUrl(canvas.toDataURL('image/png'))
    stopStream()
    setPhase('captured')
  }

  function handleRetake() {
    setCapturedUrl(null)
    setPhase('requesting')

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      .then((stream) => {
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.play()
        }
        setPhase('streaming')
      })
      .catch((err) => {
        setErrorMsg(err instanceof Error ? err.message : 'Camera access denied')
        setPhase('error')
      })
  }

  function handleSubmit() {
    if (!capturedUrl) return
    const b64 = capturedUrl.replace(/^data:image\/png;base64,/, '')
    stopStream()
    onSubmit(invocationId, b64)
  }

  function handleCancel() {
    stopStream()
    onCancel()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex flex-col gap-4 rounded-lg bg-[hsl(var(--card))] p-6 shadow-xl">
        <p className="text-sm font-medium">{prompt}</p>

        {/* viewfinder / preview area */}
        <div className="relative overflow-hidden rounded border border-[hsl(var(--border))]"
             style={{ width: W, height: H, background: '#000' }}>

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

          {/* live viewfinder — hidden once captured */}
          <video
            ref={videoRef}
            className="absolute inset-0 w-full h-full object-cover"
            style={{ display: phase === 'streaming' ? 'block' : 'none' }}
            playsInline
            muted
          />

          {/* captured still */}
          {phase === 'captured' && capturedUrl && (
            <img src={capturedUrl} className="absolute inset-0 w-full h-full object-cover" />
          )}
        </div>

        {/* hidden canvas used for capture */}
        <canvas ref={canvasRef} className="hidden" />

        <div className="flex justify-end gap-2">
          {phase === 'streaming' && (
            <Button size="sm" onClick={handleCapture}>Take Photo</Button>
          )}
          {phase === 'captured' && (
            <>
              <Button variant="outline" size="sm" onClick={handleRetake}>Retake</Button>
              <Button size="sm" onClick={handleSubmit}>Submit</Button>
            </>
          )}
          <Button variant="ghost" size="sm" onClick={handleCancel}>Cancel</Button>
        </div>
      </div>
    </div>
  )
}
