import { useEffect, useRef, useState } from 'react'
import { useTheme, type Theme } from '@/lib/theme'
import { cn } from '@/lib/utils'

const OPTIONS: { value: Theme; icon: string; label: string }[] = [
  { value: 'light',     icon: '☀️',  label: 'Light' },
  { value: 'dark',      icon: '🌙',  label: 'Dark' },
  { value: 'synthwave', icon: '🌆',  label: 'Synthwave' },
]

export function ThemePicker() {
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const current = OPTIONS.find(o => o.value === theme)!

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm hover:bg-[hsl(var(--muted))] transition-colors"
      >
        <span>{current.icon}</span>
        <span className="text-[hsl(var(--foreground))]">{current.label}</span>
        <span className="text-[hsl(var(--muted-foreground))] text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-36 rounded-md border bg-[hsl(var(--popover))] shadow-lg z-50 overflow-hidden">
          {OPTIONS.map(o => (
            <button
              key={o.value}
              onClick={() => { setTheme(o.value); setOpen(false) }}
              className={cn(
                'flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors',
                'hover:bg-[hsl(var(--muted))]',
                o.value === theme
                  ? 'text-[hsl(var(--primary))] font-medium'
                  : 'text-[hsl(var(--foreground))]',
              )}
            >
              <span>{o.icon}</span>
              <span>{o.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
