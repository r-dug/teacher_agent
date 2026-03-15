/**
 * ZoomableImage: scroll-to-zoom + drag-to-pan image viewer.
 *
 * - Scroll wheel: zoom in/out anchored to cursor position (max 6×)
 * - Drag: pan when zoomed in
 * - Double-click: reset to 1×
 *
 * The wheel listener is registered as non-passive so that
 * e.preventDefault() actually suppresses page scroll in Chrome.
 */

import { useRef, useState, useEffect } from 'react'

interface ZoomableImageProps {
  src: string
  alt: string
  /** Applied to the <img> element — use for max-h / w-auto constraints. */
  className?: string
}

export function ZoomableImage({ src, alt, className }: ZoomableImageProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Use refs for values the native wheel handler reads, to avoid re-attaching.
  const zoomRef = useRef(1)
  const translateRef = useRef({ x: 0, y: 0 })

  const [zoom, setZoom] = useState(1)
  const [translate, setTranslate] = useState({ x: 0, y: 0 })

  // Keep refs in sync with state so the wheel handler always sees fresh values.
  zoomRef.current = zoom
  translateRef.current = translate

  // Non-passive wheel listener — must be native, not React synthetic.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    function onWheel(e: WheelEvent) {
      if (!e.ctrlKey && !e.metaKey) return  // plain scroll — let it pass through
      const z = zoomRef.current
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
      const newZoom = Math.max(1, Math.min(6, z * factor))
      if (newZoom === z) return
      e.preventDefault()
      const rect = el!.getBoundingClientRect()
      const cx = e.clientX - rect.left
      const cy = e.clientY - rect.top
      const t = translateRef.current
      // Keep the content point under the cursor stationary.
      // With transform: translate(tx,ty) scale(zoom) and transform-origin 0 0:
      //   screen_x = img_x * zoom + tx  →  img_x = (cx - tx) / zoom
      // After zoom: newTx = cx - img_x * newZoom
      const imgX = (cx - t.x) / z
      const imgY = (cy - t.y) / z
      const newTx = newZoom === 1 ? 0 : cx - imgX * newZoom
      const newTy = newZoom === 1 ? 0 : cy - imgY * newZoom
      zoomRef.current = newZoom
      translateRef.current = { x: newTx, y: newTy }
      setZoom(newZoom)
      setTranslate({ x: newTx, y: newTy })
    }

    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // Drag-to-pan
  const dragRef = useRef<{ startX: number; startY: number; tx: number; ty: number } | null>(null)

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (zoomRef.current <= 1) return
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      tx: translateRef.current.x,
      ty: translateRef.current.y,
    }
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!dragRef.current) return
    const newTranslate = {
      x: dragRef.current.tx + (e.clientX - dragRef.current.startX),
      y: dragRef.current.ty + (e.clientY - dragRef.current.startY),
    }
    translateRef.current = newTranslate
    setTranslate(newTranslate)
  }

  function onPointerUp() {
    dragRef.current = null
  }

  function onDoubleClick() {
    zoomRef.current = 1
    translateRef.current = { x: 0, y: 0 }
    setZoom(1)
    setTranslate({ x: 0, y: 0 })
  }

  return (
    <div
      ref={containerRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerUp}
      onDoubleClick={onDoubleClick}
      className="overflow-hidden select-none w-fit"
      style={{
        cursor: zoom > 1 ? 'grab' : 'default',
        width: '100%',
        padding: '5%',
        border: '8px solid #3b09f0c0',
      }}
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        className={className}
        style={{
          display: 'block',
          width: '100%',
          transform: `translate(${translate.x}px, ${translate.y}px) scale(${zoom})`,
          transformOrigin: '0 0',
        }}
      />
    </div>
  )
}
