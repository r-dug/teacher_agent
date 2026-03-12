/**
 * Sketchpad: transparent drawing canvas overlaid on an optional background.
 *
 * Backgrounds:
 *   textBg  — Unicode reference characters displayed as a faint SVG guide
 *   imBg    — base64 data-URL image (e.g. a PDF page) shown behind the canvas
 *
 * On submit, the background and strokes are composited onto an offscreen canvas
 * so the agent receives the full picture.
 */

import { useRef, useEffect, useState } from 'react'
import { Button } from './ui/button'

const W = 560
const H = 360

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

  useEffect(() => {
    // Clear canvas (transparent) when a new prompt arrives
    const ctx = canvasRef.current?.getContext('2d')
    if (ctx) ctx.clearRect(0, 0, W, H)
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
    setIsDrawing(true)
    lastPos.current = getPos(e)
    canvasRef.current?.setPointerCapture(e.pointerId)
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!isDrawing || !lastPos.current) return
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    const pos = getPos(e)
    ctx.strokeStyle = '#1a1a1a'
    ctx.lineWidth = 2
    ctx.lineCap = 'round'
    ctx.beginPath()
    ctx.moveTo(lastPos.current.x, lastPos.current.y)
    ctx.lineTo(pos.x, pos.y)
    ctx.stroke()
    lastPos.current = pos
  }

  function onPointerUp() {
    setIsDrawing(false)
    lastPos.current = null
  }

  function handleClear() {
    const ctx = canvasRef.current?.getContext('2d')
    if (ctx) ctx.clearRect(0, 0, W, H)
  }

  async function handleSubmit() {
    const canvas = canvasRef.current!

    // Composite: white base → faint background → user strokes
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
    } else if (textBg) {
      ctx.globalAlpha = 0.15
      ctx.fillStyle = '#000000'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.font = `${H * 0.78}px sans-serif`
      ctx.fillText(textBg, W / 2, H / 2)
      ctx.globalAlpha = 1
    }

    ctx.drawImage(canvas, 0, 0)

    const b64 = off.toDataURL('image/png').replace(/^data:image\/png;base64,/, '')
    onSubmit(invocationId, b64)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex flex-col gap-4 rounded-lg bg-[hsl(var(--card))] p-6 shadow-xl">
        <p className="text-sm font-medium">{prompt}</p>

        {/* Canvas stack: background layer + transparent drawing canvas */}
        <div className="relative" style={{ width: W, height: H }}>

          {/* Image background (e.g. PDF page) */}
          {imBg && (
            <img
              src={imBg}
              className="absolute inset-0 w-full h-full object-contain rounded pointer-events-none select-none opacity-35"
            />
          )}

          {/* Text / character guide rendered as SVG */}
          {textBg && !imBg && (
            <svg
              className="absolute inset-0 pointer-events-none select-none"
              viewBox={`0 0 ${W} ${H}`}
              width={W}
              height={H}
            >
              <text
                x={W / 2}
                y={H * 0.82}
                textAnchor="middle"
                fontSize={H * 0.78}
                opacity={0.15}
                fontFamily="sans-serif"
              >
                {textBg}
              </text>
            </svg>
          )}

          <canvas
            ref={canvasRef}
            width={W}
            height={H}
            className="touch-none rounded border border-[hsl(var(--border))] bg-transparent cursor-crosshair"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerLeave={onPointerUp}
          />
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={handleClear}>Clear</Button>
          <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          <Button size="sm" onClick={handleSubmit}>Submit</Button>
        </div>
      </div>
    </div>
  )
}
