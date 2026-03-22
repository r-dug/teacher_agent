/**
 * Sketchpad: transparent drawing canvas overlaid on an optional background.
 *
 * Backgrounds:
 *   textBg  — Unicode reference characters displayed as a faint SVG guide
 *   imBg    — base64 data-URL image (e.g. a PDF page) shown behind the canvas
 *
 * On submit, the background and strokes are composited onto an offscreen canvas
 * so the agent receives the full picture.
 *
 * Features: brush size, color, eraser, undo, minimize/restore.
 */

import { useRef, useEffect, useState } from 'react'
import { Button } from './ui/button'
import { cn } from '@/lib/utils'

const W = 560
const H = 360

const BRUSH_SIZES = [2, 5, 12] as const
const PRESET_COLORS = ['#1a1a1a', '#e53e3e', '#3182ce', '#38a169', '#d69e2e', '#ffffff']

interface SketchpadProps {
  prompt: string
  invocationId: string
  textBg?: string
  imBg?: string
  onSubmit: (invocationId: string, drawingB64: string) => void
  onCancel: () => void
}

export function Sketchpad({ prompt, invocationId, textBg, imBg, onSubmit, onCancel }: SketchpadProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [isDrawing, setIsDrawing] = useState(false)
  const lastPos = useRef<{ x: number; y: number } | null>(null)
  const undoStack = useRef<ImageData[]>([])

  const [brushSize, setBrushSize] = useState<number>(2)
  const [color, setColor] = useState('#1a1a1a')
  const [isErasing, setIsErasing] = useState(false)
  const [minimized, setMinimized] = useState(false)
  const [hasStrokes, setHasStrokes] = useState(false)

  useEffect(() => {
    const ctx = canvasRef.current?.getContext('2d')
    if (ctx) {
      ctx.clearRect(0, 0, W, H)
      undoStack.current = []
    }
  }, [invocationId])

  function getPos(e: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current!
    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    }
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    const ctx = canvasRef.current?.getContext('2d')
    if (ctx) {
      undoStack.current.push(ctx.getImageData(0, 0, W, H))
      if (undoStack.current.length > 50) undoStack.current.shift()
    }
    setIsDrawing(true)
    setHasStrokes(true)
    lastPos.current = getPos(e)
    canvasRef.current?.setPointerCapture(e.pointerId)
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!isDrawing || !lastPos.current) return
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    const pos = getPos(e)
    if (isErasing) {
      ctx.globalCompositeOperation = 'destination-out'
      ctx.strokeStyle = 'rgba(0,0,0,1)'
      ctx.lineWidth = brushSize * 4
    } else {
      ctx.globalCompositeOperation = 'source-over'
      ctx.strokeStyle = color
      ctx.lineWidth = brushSize
    }
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.beginPath()
    ctx.moveTo(lastPos.current.x, lastPos.current.y)
    ctx.lineTo(pos.x, pos.y)
    ctx.stroke()
    ctx.globalCompositeOperation = 'source-over'
    lastPos.current = pos
  }

  function onPointerUp() {
    setIsDrawing(false)
    lastPos.current = null
  }

  function handleUndo() {
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx || undoStack.current.length === 0) return
    ctx.putImageData(undoStack.current.pop()!, 0, 0)
  }

  function handleClear() {
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    undoStack.current.push(ctx.getImageData(0, 0, W, H))
    ctx.clearRect(0, 0, W, H)
    setHasStrokes(false)
  }

  async function handleSubmit() {
    const canvas = canvasRef.current!
    const off = document.createElement('canvas')
    off.width = W
    off.height = H
    const ctx = off.getContext('2d')!

    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, W, H)

    if (imBg) {
      await new Promise<void>((resolve) => {
        const img = new Image()
        img.onload = () => {
          ctx.globalAlpha = 0.35
          ctx.drawImage(img, 0, 0, W, H)
          ctx.globalAlpha = 1
          resolve()
        }
        img.onerror = () => resolve()
        img.src = imBg
      })
    } else if (textBg && textBg.length <= 5) {
      ctx.globalAlpha = 0.15
      ctx.fillStyle = '#000000'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      const maxW = W * 0.9
      let fontSize = H * 0.78
      ctx.font = `${fontSize}px sans-serif`
      const measured = ctx.measureText(textBg).width
      if (measured > maxW) fontSize *= maxW / measured
      ctx.font = `${fontSize}px sans-serif`
      ctx.fillText(textBg, W / 2, H / 2)
      ctx.globalAlpha = 1
    }

    ctx.drawImage(canvas, 0, 0)
    const b64 = off.toDataURL('image/png').replace(/^data:image\/png;base64,/, '')
    onSubmit(invocationId, b64)
  }

  // ── minimized pill ────────────────────────────────────────────────────────
  if (minimized) {
    return (
      <>
        {/* Canvas kept in DOM (hidden) so pixel data is preserved */}
        <div className="sr-only" aria-hidden>
          <canvas ref={canvasRef} width={W} height={H} />
        </div>
        <button
          onClick={() => setMinimized(false)}
          className="fixed bottom-24 right-4 z-50 flex items-center gap-2 rounded-full bg-[hsl(var(--primary))] px-4 py-2 text-sm font-medium text-[hsl(var(--primary-foreground))] shadow-lg hover:opacity-90 transition-opacity"
        >
          ✏️ Resume drawing
        </button>
      </>
    )
  }

  // ── full modal ────────────────────────────────────────────────────────────
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div
        className="flex flex-col gap-3 rounded-lg bg-[hsl(var(--card))] p-4 shadow-xl"
        style={{ width: 600, minWidth: 320, maxWidth: '95vw', resize: 'both', overflow: 'hidden' }}
      >

        {/* Header: prompt + minimize */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 flex flex-col gap-1">
            <p className="text-sm font-medium">{prompt}</p>
            {textBg && textBg.length > 5 && (
              <p className="text-xs text-[hsl(var(--muted-foreground))] italic border-l-2 border-[hsl(var(--border))] pl-2">{textBg}</p>
            )}
          </div>
          <button
            onClick={() => setMinimized(true)}
            className="shrink-0 rounded px-2 py-0.5 text-xs text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))] transition-colors"
            title="Minimize"
          >
            ─
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Brush sizes */}
          <div className="flex items-center gap-1">
            {BRUSH_SIZES.map((sz) => (
              <button
                key={sz}
                onClick={() => { setBrushSize(sz); setIsErasing(false) }}
                className={cn(
                  'flex items-center justify-center rounded w-7 h-7 border transition-colors',
                  brushSize === sz && !isErasing
                    ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.1)]'
                    : 'border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))]'
                )}
                title={`Brush size ${sz}`}
              >
                <span
                  className="rounded-full bg-[hsl(var(--foreground))]"
                  style={{ width: Math.min(sz * 2 + 2, 20), height: Math.min(sz * 2 + 2, 20) }}
                />
              </button>
            ))}
          </div>

          {/* Divider */}
          <div className="w-px h-6 bg-[hsl(var(--border))]" />

          {/* Color presets */}
          <div className="flex items-center gap-1">
            {PRESET_COLORS.map((c) => (
              <button
                key={c}
                onClick={() => { setColor(c); setIsErasing(false) }}
                className={cn(
                  'rounded-full w-6 h-6 border-2 transition-all',
                  color === c && !isErasing
                    ? 'border-[hsl(var(--primary))] scale-110'
                    : 'border-[hsl(var(--border))]'
                )}
                style={{ backgroundColor: c }}
                title={c}
              />
            ))}
            {/* Custom color picker */}
            <label
              className="relative w-6 h-6 rounded-full border-2 border-[hsl(var(--border))] overflow-hidden cursor-pointer"
              title="Custom color"
              style={{
                background: 'conic-gradient(red, yellow, lime, cyan, blue, magenta, red)',
                borderColor: !PRESET_COLORS.includes(color) && !isErasing ? 'hsl(var(--primary))' : undefined,
              }}
            >
              <input
                type="color"
                value={color}
                onChange={(e) => { setColor(e.target.value); setIsErasing(false) }}
                className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
              />
            </label>
          </div>

          {/* Divider */}
          <div className="w-px h-6 bg-[hsl(var(--border))]" />

          {/* Eraser */}
          <button
            onClick={() => setIsErasing((v) => !v)}
            className={cn(
              'rounded px-2 py-1 text-xs border transition-colors',
              isErasing
                ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.1)] text-[hsl(var(--primary))]'
                : 'border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))]'
            )}
            title="Eraser"
          >
            ⌫ Erase
          </button>

          {/* Undo */}
          <button
            onClick={handleUndo}
            className="rounded px-2 py-1 text-xs border border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))] transition-colors"
            title="Undo"
          >
            ↩ Undo
          </button>
        </div>

        {/* Canvas stack — resize: both lets the user drag the corner to resize.
            The canvas attributes (W×H) stay fixed; only the CSS display size changes.
            getPos() already compensates via canvas.width / rect.width scaling. */}
        <div
          className="relative"
          style={{ width: '100%', aspectRatio: `${W} / ${H}`, overflow: 'hidden' }}
        >
          {imBg && (
            <img
              src={imBg}
              className="absolute inset-0 w-full h-full object-contain rounded pointer-events-none select-none opacity-35"
            />
          )}

          {textBg && textBg.length <= 5 && !imBg && (() => {
            const fontSize = H * 0.78
            const maxW = W * 0.9
            const estimatedW = textBg.length * fontSize
            const constrain = estimatedW > maxW
            return (
              <svg
                className="absolute inset-0 pointer-events-none select-none"
                viewBox={`0 0 ${W} ${H}`}
                width="100%"
                height="100%"
                preserveAspectRatio="none"
              >
                <text
                  x={W / 2}
                  y={H * 0.82}
                  textAnchor="middle"
                  fontSize={fontSize}
                  opacity={0.15}
                  fontFamily="sans-serif"
                  {...(constrain ? { textLength: maxW, lengthAdjust: 'spacingAndGlyphs' } : {})}
                >
                  {textBg}
                </text>
              </svg>
            )
          })()}

          <canvas
            ref={canvasRef}
            width={W}
            height={H}
            className={cn(
              'touch-none rounded border border-[hsl(var(--border))] bg-transparent',
              isErasing ? 'cursor-cell' : 'cursor-crosshair'
            )}
            style={{ width: '100%', height: '100%' }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerLeave={onPointerUp}
          />
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={handleClear}>Clear</Button>
          <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          <Button size="sm" onClick={handleSubmit} disabled={!hasStrokes}>Submit</Button>
        </div>
      </div>
    </div>
  )
}
