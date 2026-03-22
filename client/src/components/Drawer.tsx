/**
 * Simple slide-in panel from the right.
 * No external dependencies — just CSS transitions.
 */

import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import { useTheme, drawerPixelReveal, darkLightDrawerReveal } from '@/lib/theme'

interface DrawerProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  width?: string
}

export function Drawer({ open, onClose, title, children, width = 'w-96' }: DrawerProps) {
  const { theme } = useTheme()
  const panelRef  = useRef<HTMLDivElement>(null)

  // Close on Escape
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // Canvas reveal when drawer opens
  useEffect(() => {
    if (!open || !panelRef.current) return
    if (theme === 'synthwave') drawerPixelReveal(panelRef.current)
    else darkLightDrawerReveal(panelRef.current, theme === 'dark')
  }, [open, theme])

  return (
    <>
      {/* Backdrop */}
      <div
        className={cn(
          'fixed inset-0 z-40 bg-black/40 transition-opacity duration-200',
          open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none',
        )}
        onClick={onClose}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        className={cn(
          'fixed right-0 top-0 z-50 h-full bg-[hsl(var(--card))] shadow-xl',
          'flex flex-col',
          width,
          open ? 'translate-x-0' : 'translate-x-full',
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[hsl(var(--border))] px-5 py-4">
          <h2 className="text-base font-semibold">{title}</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))]"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {children}
        </div>
      </div>
    </>
  )
}
