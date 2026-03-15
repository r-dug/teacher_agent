/**
 * SlideViewer: shows one or more PDF page images from the backend.
 *
 * Image URL: /api/lessons/{lessonId}/page/{page}
 * Renders all pages in [pageStart..pageEnd] in a vertically scrollable list.
 *
 * Per-page "Annotate" button opens an AnnotationOverlay (canvas over the page).
 * The composited image is returned via onAnnotate as a base64 PNG string.
 *
 * Footer contains a RecordButton so the learner can respond while viewing slides.
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { ZoomableImage } from './ZoomableImage'
import { RecordButton } from './RecordButton'
import { Button } from './ui/button'
import { cn } from '@/lib/utils'

// ── AuthPageImage ─────────────────────────────────────────────────────────────
// Fetches a lesson page image with the session auth header and renders it via
// a blob URL so the browser never makes an unauthenticated image request.

interface AuthPageImageProps {
  lessonId: string
  page: number
  sessionId: string
  alt: string
  className?: string
  onLoad?: (page: number, blobUrl: string) => void
}

function AuthPageImage({ lessonId, page, sessionId, alt, className, onLoad }: AuthPageImageProps) {
  const [blobSrc, setBlobSrc] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch(`/api/lessons/${lessonId}/page/${page}`, {
      headers: { 'X-Session-Id': sessionId },
    })
      .then(r => r.ok ? r.blob() : Promise.reject(r.status))
      .then(blob => {
        if (cancelled) return
        const url = URL.createObjectURL(blob)
        setBlobSrc(url)
        onLoad?.(page, url)
      })
      .catch(() => {}) // non-fatal — page stays blank on error
    return () => { cancelled = true }
    // Blob URL lifecycle is managed by the parent SlideViewer
  }, [lessonId, page, sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!blobSrc) {
    return <div className="animate-pulse bg-[hsl(var(--muted))] h-48 w-full rounded" />
  }
  return <ZoomableImage src={blobSrc} alt={alt} className={className} />
}

// ── AnnotationOverlay ────────────────────────────────────────────────────────

interface AnnotationOverlayProps {
  src: string
  onSubmit: (compositeB64: string) => void
  onCancel: () => void
}

function AnnotationOverlay({ src, onSubmit, onCancel }: AnnotationOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const drawing = useRef(false)
  const lastPos = useRef<{ x: number; y: number } | null>(null)

  useEffect(() => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.src = src
    imgRef.current = img
  }, [src])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    canvas.width = canvas.offsetWidth
    canvas.height = canvas.offsetHeight
  }, [])

  function getPos(e: React.PointerEvent<HTMLCanvasElement>) {
    const rect = canvasRef.current!.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    drawing.current = true
    lastPos.current = getPos(e)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawing.current || !canvasRef.current || !lastPos.current) return
    const ctx = canvasRef.current.getContext('2d')!
    const pos = getPos(e)
    ctx.beginPath()
    ctx.moveTo(lastPos.current.x, lastPos.current.y)
    ctx.lineTo(pos.x, pos.y)
    ctx.strokeStyle = '#ef4444'
    ctx.lineWidth = 3
    ctx.lineCap = 'round'
    ctx.stroke()
    lastPos.current = pos
  }

  function onPointerUp() {
    drawing.current = false
    lastPos.current = null
  }

  function handleSubmit() {
    const canvas = canvasRef.current
    const img = imgRef.current
    if (!canvas || !img) return

    // Composite: page image first, annotation strokes on top.
    const out = document.createElement('canvas')
    out.width = img.naturalWidth || canvas.width
    out.height = img.naturalHeight || canvas.height
    const ctx = out.getContext('2d')!
    ctx.drawImage(img, 0, 0, out.width, out.height)
    ctx.drawImage(canvas, 0, 0, out.width, out.height)

    const b64 = out.toDataURL('image/png').replace(/^data:image\/png;base64,/, '')
    onSubmit(b64)
  }

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-black/80">
      <div className="flex items-center justify-between px-4 py-2 text-white text-sm">
        <span>Draw on the slide, then submit</span>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={onCancel}>Cancel</Button>
          <Button size="sm" onClick={handleSubmit}>Submit</Button>
        </div>
      </div>
      <div className="relative flex-1 overflow-hidden">
        <img
          src={src}
          alt=""
          className="absolute inset-0 w-full h-full object-contain pointer-events-none select-none"
        />
        <canvas
          ref={canvasRef}
          className="absolute inset-0 w-full h-full cursor-crosshair touch-none"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
        />
      </div>
    </div>
  )
}

// ── SlideViewer ──────────────────────────────────────────────────────────────

interface SlideViewerProps {
  lessonId: string | null
  sessionId: string
  pageStart: number
  pageEnd: number
  caption?: string
  onClose: () => void
  isRecording: boolean
  isSpeaking: boolean
  recordDisabled?: boolean
  onRecord: () => void
  onAnnotate?: (compositeB64: string) => void
}

export function SlideViewer({
  lessonId, sessionId, pageStart, pageEnd, caption, onClose,
  isRecording, isSpeaking, recordDisabled, onRecord, onAnnotate,
}: SlideViewerProps) {
  const [annotatingPage, setAnnotatingPage] = useState<number | null>(null)
  const [hoveredPage, setHoveredPage] = useState<number | null>(null)
  // Blob URLs keyed by page number — managed here so they survive annotation mode toggle.
  const [pageBlobUrls, setPageBlobUrls] = useState<Record<number, string>>({})
  const blobUrlsRef = useRef<Record<number, string>>({})

  // Revoke all blob URLs when the viewer unmounts.
  useEffect(() => {
    return () => {
      Object.values(blobUrlsRef.current).forEach(URL.revokeObjectURL)
    }
  }, [])

  const handlePageLoad = useCallback((page: number, url: string) => {
    blobUrlsRef.current[page] = url
    setPageBlobUrls(prev => ({ ...prev, [page]: url }))
  }, [])

  const handleAnnotateSubmit = useCallback((b64: string) => {
    setAnnotatingPage(null)
    onAnnotate?.(b64)
    onClose()
  }, [onAnnotate, onClose])

  if (!lessonId || pageStart <= 0) return null

  const pages = Array.from(
    { length: Math.max(1, pageEnd - pageStart + 1) },
    (_, i) => pageStart + i
  )

  if (annotatingPage !== null) {
    return (
      <AnnotationOverlay
        src={pageBlobUrls[annotatingPage] ?? ''}
        onSubmit={handleAnnotateSubmit}
        onCancel={() => setAnnotatingPage(null)}
      />
    )
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div
        className="relative flex flex-col rounded-lg bg-[hsl(var(--card))] shadow-xl"
        style={{
          width: 'min(700px, 90vw)',
          height: 'min(85vh, 800px)',
          resize: 'both',
          overflow: 'hidden',
          minWidth: 280,
          minHeight: 200,
        }}
      >
        <button
          onClick={onClose}
          aria-label="Close slide"
          className="absolute right-2 top-2 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-black/40 text-white hover:bg-black/60 touch-manipulation"
        >
          ✕
        </button>

        {/* Scrollable page list */}
        <div className="flex-1 overflow-y-auto">
          {pages.map((page) => (
            <div
              key={page}
              className="relative"
              onMouseEnter={() => setHoveredPage(page)}
              onMouseLeave={() => setHoveredPage(null)}
            >
              <AuthPageImage
                lessonId={lessonId}
                page={page}
                sessionId={sessionId}
                alt={caption ?? `Slide page ${page}`}
                className="block w-auto"
                onLoad={handlePageLoad}
              />
              {onAnnotate && (
                <button
                  className={cn(
                    'absolute bottom-2 right-2 px-3 py-1 rounded text-xs font-medium bg-black/60 text-white transition-opacity touch-manipulation',
                    hoveredPage === page ? 'opacity-100' : 'opacity-0'
                  )}
                  onClick={(e) => { e.stopPropagation(); setAnnotatingPage(page) }}
                >
                  Annotate
                </button>
              )}
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-3 border-t border-[hsl(var(--border))] px-3 py-2">
          {caption && (
            <p className="flex-1 text-sm text-[hsl(var(--muted-foreground))] truncate">{caption}</p>
          )}
          <div className="ml-auto">
            <RecordButton
              isRecording={isRecording}
              isSpeaking={isSpeaking}
              disabled={recordDisabled}
              onClick={onRecord}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
