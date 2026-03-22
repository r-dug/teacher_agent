import { createContext, useContext, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark' | 'synthwave'

const THEMES: Theme[] = ['light', 'dark', 'synthwave']
const STORAGE_KEY = 'theme'

interface ThemeContextValue {
  theme: Theme
  setTheme: (t: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue>({ theme: 'synthwave', setTheme: () => {} })

// ── Synthwave: page transition ────────────────────────────────────────────────
//
// Phase 1: button grows beyond the viewport (overshoots)
// Phase 2: shape slides to bottom-left corner and shrinks offscreen

const TRANS_MS  = 750
const GROW_END  = 0.42   // fraction at which max size is reached

function easeOut(t: number) { return 1 - Math.pow(1 - t, 3) }
function easeIn(t: number)  { return t * t * t }

function synthwaveTransition(btn: Element) {
  const rect = (btn as HTMLElement).getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight
  const cx = rect.left + rect.width  / 2
  const cy = rect.top  + rect.height / 2

  const canvas = document.createElement('canvas')
  canvas.width  = vw
  canvas.height = vh
  Object.assign(canvas.style, {
    position: 'fixed', inset: '0', zIndex: '9999', pointerEvents: 'none',
  })
  document.body.appendChild(canvas)
  const ctx = canvas.getContext('2d')!

  // Overshoot size: larger than the viewport diagonal so nothing peeks out
  const maxSize = Math.hypot(vw, vh) * 1.4

  // Destination: bottom-left, well offscreen
  const destX = -maxSize * 0.35
  const destY =  vh + maxSize * 0.35

  const start = performance.now()

  function drawShape(centerX: number, centerY: number, size: number) {
    const hw = size / 2
    const grad = ctx.createLinearGradient(centerX - hw, centerY - hw, centerX + hw, centerY + hw)
    grad.addColorStop(0,    'hsl(185 100% 55%)')
    grad.addColorStop(0.40, 'hsl(280  80% 38%)')
    grad.addColorStop(0.75, 'hsl(320 100% 60%)')
    grad.addColorStop(1,    'hsl(268  75% 22%)')
    ctx.fillStyle = grad
    ctx.beginPath()
    ctx.roundRect(centerX - hw, centerY - hw, size, size, 12)
    ctx.fill()
  }

  function frame(now: number) {
    const t = Math.min((now - start) / TRANS_MS, 1)
    ctx.clearRect(0, 0, vw, vh)

    if (t <= GROW_END) {
      // Phase 1: grow from button size to maxSize, anchored on button centre
      const p    = easeOut(t / GROW_END)
      const size = rect.width + (maxSize - rect.width) * p
      drawShape(cx, cy, size)
    } else {
      // Phase 2: slide to bottom-left + shrink to nothing
      const p     = easeIn((t - GROW_END) / (1 - GROW_END))
      const centerX = cx    + (destX - cx)    * p
      const centerY = cy    + (destY - cy)    * p
      const size    = maxSize * (1 - p)
      drawShape(centerX, centerY, size)
    }

    if (t < 1) requestAnimationFrame(frame)
    else canvas.remove()
  }

  requestAnimationFrame(frame)
}

// ── Shared pixel-grid dissolve ────────────────────────────────────────────────
// Used by both drawer reveal and full-screen lesson transition.

function pixelDissolve(
  left: number, top: number, w: number, h: number,
  pixelSize: number, fadeDur: number, totalMs: number,
) {
  const canvas = document.createElement('canvas')
  canvas.width  = Math.ceil(w)
  canvas.height = Math.ceil(h)
  Object.assign(canvas.style, {
    position: 'fixed',
    left: `${left}px`, top: `${top}px`,
    width: `${w}px`,   height: `${h}px`,
    zIndex: '9999', pointerEvents: 'none',
  })
  document.body.appendChild(canvas)
  const ctx = canvas.getContext('2d')!

  const GAP   = 2
  const cols  = Math.ceil(w / pixelSize)
  const rows  = Math.ceil(h / pixelSize)
  const total = cols * rows

  const colors = new Array<string>(total)
  for (let i = 0; i < total; i++) {
    const nx = (i % cols) / Math.max(cols - 1, 1)
    const ny = Math.floor(i / cols) / Math.max(rows - 1, 1)
    const hue = 185 + (320 - 185) * nx + (Math.random() * 18 - 9)
    const sat = 90  + Math.random() * 10
    const lit = 52  + (1 - ny) * 12 + Math.random() * 8
    colors[i] = `hsl(${hue} ${sat}% ${lit}%)`
  }

  const order = Uint16Array.from({ length: total }, (_, i) => i)
  for (let i = total - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[order[i], order[j]] = [order[j], order[i]]
  }
  const fadeStart = new Float32Array(total)
  for (let i = 0; i < total; i++) {
    fadeStart[order[i]] = (i / total) * (1 - fadeDur)
  }

  const start = performance.now()
  function frame(now: number) {
    const pixT = Math.min((now - start) / totalMs, 1)
    ctx.clearRect(0, 0, w, h)
    for (let i = 0; i < total; i++) {
      const fs = fadeStart[i]
      if (pixT >= fs + fadeDur) continue
      const alpha = pixT <= fs ? 1 : 1 - (pixT - fs) / fadeDur
      const col = i % cols
      const row = Math.floor(i / cols)
      ctx.globalAlpha = alpha
      ctx.fillStyle = colors[i]
      ctx.fillRect(col * pixelSize + GAP, row * pixelSize + GAP, pixelSize - GAP, pixelSize - GAP)
    }
    ctx.globalAlpha = 1
    if (pixT < 1) requestAnimationFrame(frame)
    else canvas.remove()
  }
  requestAnimationFrame(frame)
}

// ── Dark/Light: page transition ───────────────────────────────────────────────
//
// Phase 1 (easeOut): clicked element expands as solid black/white to fill viewport
// Phase 2 (easeIn):  block slides off toward bottom-left with gradient wipe edge

const DL_TRANS_MS = 700
const DL_GROW_END = 0.40

function darkLightPageTransition(el: Element, isDark: boolean) {
  const rect = (el as HTMLElement).getBoundingClientRect()
  const vw   = window.innerWidth
  const vh   = window.innerHeight
  const solid = isDark ? '#000' : '#fff'
  const r = isDark ? 0 : 255

  const canvas = document.createElement('canvas')
  canvas.width = vw; canvas.height = vh
  Object.assign(canvas.style, { position: 'fixed', inset: '0', zIndex: '9999', pointerEvents: 'none' })
  document.body.appendChild(canvas)
  const ctx = canvas.getContext('2d')!
  const start = performance.now()

  function frame(now: number) {
    const t = Math.min((now - start) / DL_TRANS_MS, 1)
    ctx.clearRect(0, 0, vw, vh)

    if (t <= DL_GROW_END) {
      // Phase 1: lerp from element rect to full viewport
      const p = easeOut(t / DL_GROW_END)
      const x = rect.left   * (1 - p)
      const y = rect.top    * (1 - p)
      const w = rect.width  + (vw - rect.width)  * p
      const h = rect.height + (vh - rect.height) * p
      ctx.fillStyle = solid
      ctx.fillRect(x, y, w, h)
    } else {
      // Phase 2: slide toward bottom-left; gradient fades transparent at top-right edge
      const p  = easeIn((t - DL_GROW_END) / (1 - DL_GROW_END))
      const tx = -p * vw
      const ty =  p * vh
      ctx.save()
      ctx.translate(tx, ty)
      const grad = ctx.createLinearGradient(vw, 0, vw * 0.5, vh * 0.5)
      grad.addColorStop(0,    `rgba(${r},${r},${r},0)`)
      grad.addColorStop(0.45, `rgba(${r},${r},${r},1)`)
      grad.addColorStop(1,    `rgba(${r},${r},${r},1)`)
      ctx.fillStyle = grad
      ctx.fillRect(0, 0, vw, vh)
      ctx.restore()
    }

    if (t < 1) requestAnimationFrame(frame)
    else canvas.remove()
  }

  requestAnimationFrame(frame)
}

// ── Dark/Light: drawer reveal ─────────────────────────────────────────────────
//
// Phase 1 (easeOut): solid fill sweeps in from right edge to cover drawer
// Phase 2 (easeIn):  block slides off toward bottom-right with gradient wipe edge

export function darkLightDrawerReveal(panel: HTMLElement, isDark: boolean) {
  const vh   = window.innerHeight
  const dw   = panel.offsetWidth
  const left = window.innerWidth - dw
  const solid = isDark ? '#000' : '#fff'
  const r = isDark ? 0 : 255

  const canvas = document.createElement('canvas')
  canvas.width = dw; canvas.height = vh
  Object.assign(canvas.style, {
    position: 'fixed', left: `${left}px`, top: '0',
    width: `${dw}px`, height: `${vh}px`,
    zIndex: '9999', pointerEvents: 'none',
  })
  document.body.appendChild(canvas)
  const ctx = canvas.getContext('2d')!

  const TOTAL = 700, SPLIT = 0.42
  const start = performance.now()

  function frame(now: number) {
    const t = Math.min((now - start) / TOTAL, 1)
    ctx.clearRect(0, 0, dw, vh)

    if (t <= SPLIT) {
      // Phase 1: fill sweeps in from right edge leftward
      const p     = easeOut(t / SPLIT)
      const fillW = dw * p
      const x     = dw - fillW
      const soft  = fillW * 0.25
      // gradient on leading (left) edge, solid behind it
      const grad = ctx.createLinearGradient(x, 0, x + soft, 0)
      grad.addColorStop(0, `rgba(${r},${r},${r},0)`)
      grad.addColorStop(1, `rgba(${r},${r},${r},1)`)
      ctx.fillStyle = grad
      ctx.fillRect(x, 0, soft, vh)
      ctx.fillStyle = solid
      if (fillW > soft) ctx.fillRect(x + soft, 0, fillW - soft, vh)
    } else {
      // Phase 2: slide toward bottom-right; gradient fades transparent at top-left edge
      const p  = easeIn((t - SPLIT) / (1 - SPLIT))
      const tx = p * dw
      const ty = p * vh
      ctx.save()
      ctx.translate(tx, ty)
      const soft = Math.min(dw, vh) * 0.4
      const grad = ctx.createLinearGradient(0, 0, soft, soft)
      grad.addColorStop(0,   `rgba(${r},${r},${r},0)`)
      grad.addColorStop(0.5, `rgba(${r},${r},${r},1)`)
      grad.addColorStop(1,   `rgba(${r},${r},${r},1)`)
      ctx.fillStyle = grad
      ctx.fillRect(-dw, -vh, dw * 3, vh * 3)
      ctx.restore()
    }

    if (t < 1) requestAnimationFrame(frame)
    else canvas.remove()
  }

  requestAnimationFrame(frame)
}

// ── Drawer pixel reveal ───────────────────────────────────────────────────────

export function drawerPixelReveal(panel: HTMLElement) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const w  = panel.offsetWidth
  pixelDissolve(vw - w, 0, w, vh, 16, 0.42, 750)
}

// ── Full-screen pixel reveal (lesson transition) ───────────────────────────────

export function screenPixelReveal() {
  pixelDissolve(0, 0, window.innerWidth, window.innerHeight, 22, 0.48, 1100)
}

// ── ThemeProvider ─────────────────────────────────────────────────────────────

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const stored = localStorage.getItem(STORAGE_KEY) as Theme | null
    return stored && THEMES.includes(stored) ? stored : 'synthwave'
  })

  // Apply theme class to <html>
  useEffect(() => {
    const html = document.documentElement
    html.classList.remove('dark', 'synthwave')
    if (theme !== 'light') html.classList.add(theme)
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  // Click animations
  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      const target = e.target as Element

      if (theme === 'synthwave') {
        const transEl  = target.closest('[data-page-transition]')
        const pixelEl  = target.closest('[data-pixel-transition]')
        if (transEl)  synthwaveTransition(transEl)
        else if (pixelEl) screenPixelReveal()
      } else {
        const transEl  = target.closest('[data-page-transition]')
        const pixelEl  = target.closest('[data-pixel-transition]')
        if (transEl)  darkLightPageTransition(transEl, theme === 'dark')
        else if (pixelEl) darkLightPageTransition(pixelEl, theme === 'dark')
      }

      // Small pulse feedback — buttons only
      const btn = target.closest('button:not([disabled])')
      if (!btn) return
      btn.classList.remove('btn-clicked')
      void (btn as HTMLElement).offsetWidth
      btn.classList.add('btn-clicked')
      setTimeout(() => btn.classList.remove('btn-clicked'), 520)
    }

    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [theme])

  return (
    <ThemeContext.Provider value={{ theme, setTheme: setThemeState }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  return useContext(ThemeContext)
}
