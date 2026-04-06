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
 * Tools: pen, highlighter, dashed line, straight line, rectangle, circle,
 *        text, fill, eraser.
 * Features: brush size slider, color presets + custom picker, opacity control,
 *           pressure sensitivity, undo, clear, minimize/restore.
 * Preferences are loaded/saved via props.
 */

import { useRef, useEffect, useState, useCallback } from 'react'
import { Button } from './ui/button'
import { cn } from '@/lib/utils'

const W = 560
const H = 360

const PRESET_COLORS = ['#1a1a1a', '#e53e3e', '#3182ce', '#38a169', '#d69e2e', '#805ad5', '#dd6b20', '#d53f8c']

type Tool = 'pen' | 'highlighter' | 'dashed' | 'line' | 'rect' | 'circle' | 'text' | 'fill' | 'eraser'

const TOOL_LABELS: Record<Tool, { icon: string; label: string }> = {
  pen:         { icon: '✏️', label: 'Pen' },
  highlighter: { icon: '🖍️', label: 'Highlight' },
  dashed:      { icon: '┈',  label: 'Dashed' },
  line:        { icon: '╱',  label: 'Line' },
  rect:        { icon: '▭',  label: 'Rect' },
  circle:      { icon: '○',  label: 'Circle' },
  text:        { icon: 'T',  label: 'Text' },
  fill:        { icon: '🪣', label: 'Fill' },
  eraser:      { icon: '⌫',  label: 'Erase' },
}

export interface SketchpadPrefs {
  brushSize?: number
  color?: string
  tool?: Tool
  opacity?: number
}

interface SketchpadProps {
  prompt: string
  invocationId: string
  textBg?: string
  imBg?: string
  prefs?: SketchpadPrefs
  onPrefsChange?: (prefs: SketchpadPrefs) => void
  onSubmit: (invocationId: string, drawingB64: string) => void
  onCancel: () => void
}

// ── flood fill (4-connected) ────────────────────────────────────────────────

function floodFill(ctx: CanvasRenderingContext2D, x: number, y: number, fillColor: string, tolerance = 32) {
  const imageData = ctx.getImageData(0, 0, W, H)
  const data = imageData.data
  const startIdx = (y * W + x) * 4
  const startR = data[startIdx], startG = data[startIdx + 1], startB = data[startIdx + 2], startA = data[startIdx + 3]

  // Parse fill color
  const tmp = document.createElement('canvas')
  tmp.width = tmp.height = 1
  const tc = tmp.getContext('2d')!
  tc.fillStyle = fillColor
  tc.fillRect(0, 0, 1, 1)
  const fc = tc.getImageData(0, 0, 1, 1).data

  if (Math.abs(startR - fc[0]) + Math.abs(startG - fc[1]) + Math.abs(startB - fc[2]) + Math.abs(startA - fc[3]) < tolerance) return

  const stack = [x, y]
  const visited = new Uint8Array(W * H)

  function matches(i: number) {
    const idx = i * 4
    return (
      Math.abs(data[idx] - startR) +
      Math.abs(data[idx + 1] - startG) +
      Math.abs(data[idx + 2] - startB) +
      Math.abs(data[idx + 3] - startA)
    ) < tolerance
  }

  while (stack.length) {
    const cy = stack.pop()!
    const cx = stack.pop()!
    const pos = cy * W + cx
    if (cx < 0 || cx >= W || cy < 0 || cy >= H || visited[pos] || !matches(pos)) continue
    visited[pos] = 1
    const idx = pos * 4
    data[idx] = fc[0]; data[idx + 1] = fc[1]; data[idx + 2] = fc[2]; data[idx + 3] = fc[3]
    stack.push(cx + 1, cy, cx - 1, cy, cx, cy + 1, cx, cy - 1)
  }
  ctx.putImageData(imageData, 0, 0)
}


export function Sketchpad({ prompt, invocationId, textBg, imBg, prefs, onPrefsChange, onSubmit, onCancel }: SketchpadProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const [isDrawing, setIsDrawing] = useState(false)
  const lastPos = useRef<{ x: number; y: number } | null>(null)
  const startPos = useRef<{ x: number; y: number } | null>(null)
  const undoStack = useRef<ImageData[]>([])

  const [tool, setTool] = useState<Tool>(prefs?.tool ?? 'pen')
  const [brushSize, setBrushSize] = useState(prefs?.brushSize ?? 5)
  const [color, setColor] = useState(prefs?.color ?? '#1a1a1a')
  const [opacity, setOpacity] = useState(prefs?.opacity ?? 1)
  const [minimized, setMinimized] = useState(false)
  const [hasStrokes, setHasStrokes] = useState(false)
  const [textInput, setTextInput] = useState('')
  const [textPos, setTextPos] = useState<{ x: number; y: number } | null>(null)

  // Persist preference changes
  const emitPrefs = useCallback((partial: Partial<SketchpadPrefs>) => {
    onPrefsChange?.({ brushSize, color, tool, opacity, ...partial })
  }, [brushSize, color, tool, opacity, onPrefsChange])

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

  function pushUndo() {
    const ctx = canvasRef.current?.getContext('2d')
    if (ctx) {
      undoStack.current.push(ctx.getImageData(0, 0, W, H))
      if (undoStack.current.length > 50) undoStack.current.shift()
    }
  }

  function applyBrush(ctx: CanvasRenderingContext2D, pressure: number) {
    const pressureScale = pressure > 0 ? 0.5 + pressure * 0.5 : 1
    if (tool === 'eraser') {
      ctx.globalCompositeOperation = 'destination-out'
      ctx.strokeStyle = 'rgba(0,0,0,1)'
      ctx.lineWidth = brushSize * 4 * pressureScale
      ctx.globalAlpha = 1
      ctx.setLineDash([])
    } else if (tool === 'highlighter') {
      ctx.globalCompositeOperation = 'source-over'
      ctx.strokeStyle = color
      ctx.lineWidth = brushSize * 3 * pressureScale
      ctx.globalAlpha = 0.3 * opacity
      ctx.setLineDash([])
    } else if (tool === 'dashed') {
      ctx.globalCompositeOperation = 'source-over'
      ctx.strokeStyle = color
      ctx.lineWidth = brushSize * pressureScale
      ctx.globalAlpha = opacity
      ctx.setLineDash([brushSize * 3, brushSize * 2])
    } else {
      ctx.globalCompositeOperation = 'source-over'
      ctx.strokeStyle = color
      ctx.lineWidth = brushSize * pressureScale
      ctx.globalAlpha = opacity
      ctx.setLineDash([])
    }
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
  }

  function clearOverlay() {
    const oc = overlayRef.current?.getContext('2d')
    if (oc) oc.clearRect(0, 0, W, H)
  }

  // ── pointer handlers ─────────────────────────────────────────────────────

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    const pos = getPos(e)

    // Text tool: place cursor
    if (tool === 'text') {
      setTextPos(pos)
      setTextInput('')
      return
    }

    // Fill tool: immediate action
    if (tool === 'fill') {
      const ctx = canvasRef.current?.getContext('2d')
      if (!ctx) return
      pushUndo()
      floodFill(ctx, Math.round(pos.x), Math.round(pos.y), color)
      setHasStrokes(true)
      return
    }

    pushUndo()
    setIsDrawing(true)
    setHasStrokes(true)
    lastPos.current = pos
    startPos.current = pos
    canvasRef.current?.setPointerCapture(e.pointerId)
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!isDrawing || !lastPos.current || !startPos.current) return
    const pos = getPos(e)
    const pressure = e.pressure

    // Shape tools: draw preview on overlay
    if (tool === 'line' || tool === 'rect' || tool === 'circle') {
      const oc = overlayRef.current?.getContext('2d')
      if (!oc) return
      oc.clearRect(0, 0, W, H)
      applyBrush(oc, pressure)
      oc.beginPath()
      if (tool === 'line') {
        oc.moveTo(startPos.current.x, startPos.current.y)
        oc.lineTo(pos.x, pos.y)
      } else if (tool === 'rect') {
        oc.rect(startPos.current.x, startPos.current.y, pos.x - startPos.current.x, pos.y - startPos.current.y)
      } else {
        const rx = Math.abs(pos.x - startPos.current.x) / 2
        const ry = Math.abs(pos.y - startPos.current.y) / 2
        const cx = (startPos.current.x + pos.x) / 2
        const cy = (startPos.current.y + pos.y) / 2
        oc.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2)
      }
      oc.stroke()
      oc.globalAlpha = 1
      oc.globalCompositeOperation = 'source-over'
      oc.setLineDash([])
      return
    }

    // Freehand tools: draw directly on canvas
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    applyBrush(ctx, pressure)
    ctx.beginPath()
    ctx.moveTo(lastPos.current.x, lastPos.current.y)
    ctx.lineTo(pos.x, pos.y)
    ctx.stroke()
    ctx.globalAlpha = 1
    ctx.globalCompositeOperation = 'source-over'
    ctx.setLineDash([])
    lastPos.current = pos
  }

  function onPointerUp(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!isDrawing || !startPos.current) {
      setIsDrawing(false)
      lastPos.current = null
      startPos.current = null
      return
    }

    // Shape tools: commit overlay to main canvas
    if (tool === 'line' || tool === 'rect' || tool === 'circle') {
      const ctx = canvasRef.current?.getContext('2d')
      const oc = overlayRef.current?.getContext('2d')
      if (ctx && oc) {
        const pos = getPos(e)
        applyBrush(ctx, e.pressure || 1)
        ctx.beginPath()
        if (tool === 'line') {
          ctx.moveTo(startPos.current.x, startPos.current.y)
          ctx.lineTo(pos.x, pos.y)
        } else if (tool === 'rect') {
          ctx.rect(startPos.current.x, startPos.current.y, pos.x - startPos.current.x, pos.y - startPos.current.y)
        } else {
          const rx = Math.abs(pos.x - startPos.current.x) / 2
          const ry = Math.abs(pos.y - startPos.current.y) / 2
          const cx = (startPos.current.x + pos.x) / 2
          const cy = (startPos.current.y + pos.y) / 2
          ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2)
        }
        ctx.stroke()
        ctx.globalAlpha = 1
        ctx.globalCompositeOperation = 'source-over'
        ctx.setLineDash([])
        clearOverlay()
      }
    }

    setIsDrawing(false)
    lastPos.current = null
    startPos.current = null
  }

  // ── text commit ──────────────────────────────────────────────────────────

  function commitText() {
    if (!textPos || !textInput.trim()) {
      setTextPos(null)
      return
    }
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    pushUndo()
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = opacity
    ctx.fillStyle = color
    const fontSize = Math.max(brushSize * 3, 12)
    ctx.font = `${fontSize}px sans-serif`
    ctx.textBaseline = 'top'
    ctx.fillText(textInput, textPos.x, textPos.y)
    ctx.globalAlpha = 1
    setHasStrokes(true)
    setTextPos(null)
    setTextInput('')
  }

  // ── undo / clear ─────────────────────────────────────────────────────────

  function handleUndo() {
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx || undoStack.current.length === 0) return
    ctx.putImageData(undoStack.current.pop()!, 0, 0)
  }

  function handleClear() {
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    pushUndo()
    ctx.clearRect(0, 0, W, H)
    setHasStrokes(false)
  }

  // ── submit (composite background + strokes) ──────────────────────────────

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

  // ── tool/pref change helpers ─────────────────────────────────────────────

  function changeTool(t: Tool) {
    if (textPos) commitText()
    setTool(t)
    emitPrefs({ tool: t })
  }

  function changeColor(c: string) {
    setColor(c)
    if (tool === 'eraser') setTool('pen')
    emitPrefs({ color: c })
  }

  function changeBrushSize(s: number) {
    setBrushSize(s)
    emitPrefs({ brushSize: s })
  }

  function changeOpacity(o: number) {
    setOpacity(o)
    emitPrefs({ opacity: o })
  }

  // ── cursor style ──────────────────────────────────────────────────────────

  const cursor = tool === 'eraser' ? 'cursor-cell'
    : tool === 'fill' ? 'cursor-crosshair'
    : tool === 'text' ? 'cursor-text'
    : 'cursor-crosshair'

  // ── minimized pill ────────────────────────────────────────────────────────
  if (minimized) {
    return (
      <>
        <div className="sr-only" aria-hidden>
          <canvas ref={canvasRef} width={W} height={H} />
          <canvas ref={overlayRef} width={W} height={H} />
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
        style={{ width: 640, minWidth: 320, maxWidth: '95vw', resize: 'both', overflow: 'hidden' }}
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

        {/* Toolbar row 1: tools */}
        <div className="flex items-center gap-1 flex-wrap">
          {(Object.keys(TOOL_LABELS) as Tool[]).map((t) => (
            <button
              key={t}
              onClick={() => changeTool(t)}
              className={cn(
                'flex items-center gap-1 rounded px-2 py-1 text-xs border transition-colors',
                tool === t
                  ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.1)] text-[hsl(var(--primary))]'
                  : 'border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))]'
              )}
              title={TOOL_LABELS[t].label}
            >
              <span>{TOOL_LABELS[t].icon}</span>
              <span className="hidden sm:inline">{TOOL_LABELS[t].label}</span>
            </button>
          ))}
          <div className="w-px h-6 bg-[hsl(var(--border))]" />
          <button
            onClick={handleUndo}
            className="rounded px-2 py-1 text-xs border border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))] transition-colors"
            title="Undo"
          >
            ↩ Undo
          </button>
        </div>

        {/* Toolbar row 2: colors + size + opacity */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Color presets */}
          <div className="flex items-center gap-1">
            {PRESET_COLORS.map((c) => (
              <button
                key={c}
                onClick={() => changeColor(c)}
                className={cn(
                  'rounded-full w-6 h-6 border-2 transition-all',
                  color === c && tool !== 'eraser'
                    ? 'border-[hsl(var(--primary))] scale-110'
                    : 'border-[hsl(var(--border))]'
                )}
                style={{ backgroundColor: c }}
                title={c}
              />
            ))}
            <label
              className="relative w-6 h-6 rounded-full border-2 border-[hsl(var(--border))] overflow-hidden cursor-pointer"
              title="Custom color"
              style={{
                background: 'conic-gradient(red, yellow, lime, cyan, blue, magenta, red)',
                borderColor: !PRESET_COLORS.includes(color) && tool !== 'eraser' ? 'hsl(var(--primary))' : undefined,
              }}
            >
              <input
                type="color"
                value={color}
                onChange={(e) => changeColor(e.target.value)}
                className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
              />
            </label>
          </div>

          <div className="w-px h-6 bg-[hsl(var(--border))]" />

          {/* Brush size slider */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-[hsl(var(--muted-foreground))]">Size</span>
            <input
              type="range"
              min={1}
              max={30}
              value={brushSize}
              onChange={(e) => changeBrushSize(Number(e.target.value))}
              className="w-20 h-1 accent-[hsl(var(--primary))]"
            />
            <span className="text-xs tabular-nums w-5 text-right">{brushSize}</span>
          </div>

          <div className="w-px h-6 bg-[hsl(var(--border))]" />

          {/* Opacity slider */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-[hsl(var(--muted-foreground))]">Opacity</span>
            <input
              type="range"
              min={10}
              max={100}
              value={Math.round(opacity * 100)}
              onChange={(e) => changeOpacity(Number(e.target.value) / 100)}
              className="w-16 h-1 accent-[hsl(var(--primary))]"
            />
            <span className="text-xs tabular-nums w-8 text-right">{Math.round(opacity * 100)}%</span>
          </div>
        </div>

        {/* Canvas stack */}
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
              'absolute inset-0 touch-none rounded border border-[hsl(var(--border))] bg-transparent',
              cursor
            )}
            style={{ width: '100%', height: '100%' }}
            onPointerDown={(e) => { e.preventDefault(); onPointerDown(e) }}
            onPointerMove={(e) => { e.preventDefault(); onPointerMove(e) }}
            onPointerUp={(e) => { e.preventDefault(); onPointerUp(e) }}
            onPointerLeave={(e) => { e.preventDefault(); if (isDrawing) onPointerUp(e) }}
            onTouchStart={(e) => e.preventDefault()}
            onTouchMove={(e) => e.preventDefault()}
            onTouchEnd={(e) => e.preventDefault()}
          />

          {/* Shape preview overlay */}
          <canvas
            ref={overlayRef}
            width={W}
            height={H}
            className="absolute inset-0 pointer-events-none rounded"
            style={{ width: '100%', height: '100%' }}
          />

          {/* Text input overlay */}
          {textPos && (
            <div
              className="absolute"
              style={{
                left: `${(textPos.x / W) * 100}%`,
                top: `${(textPos.y / H) * 100}%`,
              }}
            >
              <input
                autoFocus
                value={textInput}
                onChange={(e) => setTextInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitText(); if (e.key === 'Escape') setTextPos(null) }}
                onBlur={commitText}
                className="border border-[hsl(var(--primary))] bg-[hsl(var(--background))] px-1 py-0.5 text-sm rounded outline-none min-w-[80px]"
                style={{ color, fontSize: Math.max(brushSize * 3, 12) * (H / canvasRef.current!.getBoundingClientRect().height) * 0.3 }}
                placeholder="Type text…"
              />
            </div>
          )}
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
