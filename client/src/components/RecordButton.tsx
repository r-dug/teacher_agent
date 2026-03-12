/**
 * Record button: push-to-talk or toggle-to-talk.
 *
 * Visual states:
 *  - idle: plain mic icon
 *  - recording + not speaking: mic icon with pulsing ring (waiting for speech)
 *  - recording + speaking: mic icon with solid ring (speech detected)
 */

import { Mic, MicOff } from 'lucide-react'
import { cn } from '@/lib/utils'

interface RecordButtonProps {
  isRecording: boolean
  isSpeaking: boolean
  disabled?: boolean
  onClick: () => void
}

export function RecordButton({ isRecording, isSpeaking, disabled, onClick }: RecordButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={isRecording ? 'Stop recording' : 'Start recording'}
      className={cn(
        'relative flex h-16 w-16 items-center justify-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))] disabled:opacity-50 touch-manipulation',
        isRecording
          ? 'bg-red-500 text-white hover:bg-red-600'
          : 'bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] hover:bg-[hsl(var(--primary)/0.9)]'
      )}
    >
      {/* Pulsing ring when recording but no speech yet */}
      {isRecording && !isSpeaking && (
        <span className="absolute inset-0 rounded-full bg-red-400 animate-ping opacity-50" />
      )}
      {/* Solid ring when speech is detected */}
      {isRecording && isSpeaking && (
        <span className="absolute inset-0 rounded-full ring-4 ring-red-300" />
      )}
      {isRecording ? <MicOff className="h-6 w-6" /> : <Mic className="h-6 w-6" />}
    </button>
  )
}
