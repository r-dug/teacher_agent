/**
 * Thin status bar: shows WS connection state + latest status message.
 */

import { cn } from '@/lib/utils'

interface StatusBarProps {
  wsStatus: 'connecting' | 'connected' | 'disconnected'
  message: string
}

const WS_LABEL: Record<StatusBarProps['wsStatus'], string> = {
  connecting: 'Connecting…',
  connected: 'Connected',
  disconnected: 'Disconnected',
}

const WS_DOT: Record<StatusBarProps['wsStatus'], string> = {
  connecting: 'bg-yellow-400 animate-pulse',
  connected: 'bg-green-500',
  disconnected: 'bg-red-500',
}

export function StatusBar({ wsStatus, message }: StatusBarProps) {
  return (
    <div className="flex items-center gap-3 px-4 py-1 text-xs text-[hsl(var(--muted-foreground))] border-b border-[hsl(var(--border))]">
      <span className={cn('h-2 w-2 rounded-full', WS_DOT[wsStatus])} />
      <span>{WS_LABEL[wsStatus]}</span>
      {message && <span className="ml-2 truncate">{message}</span>}
    </div>
  )
}
