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

// ── Drawer pixel reveal ───────────────────────────────────────────────────────
// Called by Drawer when it opens in synthwave theme.
// Covers the panel with a neon pixel grid that dissolves to reveal the content.

const D_PIXEL   = 16
const D_GAP     = 2
const D_FADE_DUR = 0.42
const D_TOTAL_MS = 750

export function drawerPixelReveal(panel: HTMLElement) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const w  = panel.offsetWidth
  const h  = vh
  const left = vw - w

  const canvas = document.createElement('canvas')
  canvas.width  = w
  canvas.height = h
  Object.assign(canvas.style, {
    position: 'fixed',
    left: `${left}px`,
    top: '0',
    width: `${w}px`,
    height: `${h}px`,
    zIndex: '9999',
    pointerEvents: 'none',
  })
  document.body.appendChild(canvas)
  const ctx = canvas.getContext('2d')!

  const cols  = Math.ceil(w / D_PIXEL)
  const rows  = Math.ceil(h / D_PIXEL)
  const total = cols * rows

  const colors = new Array<string>(total)
  for (let i = 0; i < total; i++) {
    const col = i % cols
    const row = Math.floor(i / cols)
    const nx  = col / Math.max(cols - 1, 1)
    const ny  = row / Math.max(rows - 1, 1)
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
    fadeStart[order[i]] = (i / total) * (1 - D_FADE_DUR)
  }

  const start = performance.now()
  function frame(now: number) {
    const t    = Math.min((now - start) / D_TOTAL_MS, 1)
    const pixT = t
    ctx.clearRect(0, 0, w, h)
    for (let i = 0; i < total; i++) {
      const fs = fadeStart[i]
      if (pixT >= fs + D_FADE_DUR) continue
      const alpha = pixT <= fs ? 1 : 1 - (pixT - fs) / D_FADE_DUR
      const col = i % cols
      const row = Math.floor(i / cols)
      ctx.globalAlpha = alpha
      ctx.fillStyle = colors[i]
      ctx.fillRect(col * D_PIXEL + D_GAP, row * D_PIXEL + D_GAP, D_PIXEL - D_GAP, D_PIXEL - D_GAP)
    }
    ctx.globalAlpha = 1
    if (t < 1) requestAnimationFrame(frame)
    else canvas.remove()
  }
  requestAnimationFrame(frame)
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
    if (theme === 'light') return

    function onMouseDown(e: MouseEvent) {
      const btn = (e.target as Element).closest('button:not([disabled])')
      if (!btn) return

      if (theme === 'synthwave' && (btn as HTMLElement).dataset.pageTransition !== undefined) {
        synthwaveTransition(btn)
      }

      // Small button feedback pulse (all non-light themes)
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
