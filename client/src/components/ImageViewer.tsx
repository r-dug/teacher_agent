/**
 * ImageViewer: displays an AI-generated image from the teaching agent.
 *
 * The image URL requires the session auth header, so we fetch it once and
 * render via a blob URL (same pattern as AuthPageImage in SlideViewer).
 */

import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { ZoomableImage } from './ZoomableImage'
import { RecordButton } from './RecordButton'

interface ImageViewerProps {
  imageUrl: string    // backend-relative URL, e.g. /api/lessons/assets/...
  caption: string
  sessionId: string
  onClose: () => void
  isRecording: boolean
  isSpeaking: boolean
  recordDisabled?: boolean
  onRecord: () => void
}

export function ImageViewer({
  imageUrl, caption, sessionId, onClose,
  isRecording, isSpeaking, recordDisabled, onRecord,
}: ImageViewerProps) {
  const [blobSrc, setBlobSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    const apiUrl = imageUrl.startsWith('/api') ? imageUrl : `/api${imageUrl}`
    fetch(apiUrl, { headers: { 'X-Session-Id': sessionId } })
      .then(r => r.ok ? r.blob() : Promise.reject(r.status))
      .then(blob => {
        if (cancelled) return
        setBlobSrc(URL.createObjectURL(blob))
      })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => {
      cancelled = true
      if (blobSrc) URL.revokeObjectURL(blobSrc)
    }
  }, [imageUrl, sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
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
          aria-label="Close image"
          className="absolute right-2 top-2 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-black/40 text-white hover:bg-black/60 touch-manipulation"
        >
          ✕
        </button>

        {/* Image */}
        <div className="flex-1 overflow-y-auto flex items-center justify-center p-2">
          {failed ? (
            <p className="text-sm text-[hsl(var(--muted-foreground))]">Image could not be loaded.</p>
          ) : blobSrc ? (
            <ZoomableImage src={blobSrc} alt={caption} className="block w-auto max-h-full" />
          ) : (
            <Loader2 className="animate-spin text-[hsl(var(--muted-foreground))]" size={32} />
          )}
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
