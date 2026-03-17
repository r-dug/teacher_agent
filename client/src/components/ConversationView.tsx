/**
 * Conversation view: scrollable list of user + assistant turns.
 *
 * - Text streams in as text_chunk events arrive.
 * - Each assistant chunk span is tagged with data-turn and data-chunk
 *   so click-to-replay can identify which audio to play.
 * - Auto-scrolls to bottom when new content arrives.
 */

import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

export type Figure =
  | { type: 'slide'; page: number; caption: string; lessonId: string }
  | { type: 'drawing'; dataUrl: string; prompt: string }

export interface Turn {
  role: 'user' | 'assistant'
  /** For user turns: the transcription. For assistant: accumulated text chunks. */
  text: string
  turnId?: string    // present for user turns (WS turn_id)
  turnIdx?: number   // present for assistant turns (for replay)
  complete?: boolean
  figures?: Figure[]
}

function figureLabel(fig: Figure): string {
  if (fig.type === 'slide') {
    const caption = fig.caption.trim()
    return caption ? `Page ${fig.page} · ${caption.slice(0, 32)}${caption.length > 32 ? '…' : ''}` : `Page ${fig.page}`
  }
  const p = fig.prompt.trim()
  return `Drawing · ${p.slice(0, 32)}${p.length > 32 ? '…' : ''}`
}

interface ConversationViewProps {
  turns: Turn[]
  onReplayTurn?: (turnIdx: number) => void
  onFigureClick?: (fig: Figure) => void
}

export function ConversationView({ turns, onReplayTurn, onFigureClick }: ConversationViewProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  return (
    <div className="flex-1 overflow-y-auto space-y-4 p-4">
      {turns.map((turn, i) => (
        <div
          key={i}
          className={cn(
            'flex',
            turn.role === 'user' ? 'justify-end' : 'justify-start'
          )}
        >
          <div
            className={cn(
              'max-w-[80%] rounded-2xl px-4 py-2 text-sm leading-relaxed whitespace-pre-wrap',
              turn.role === 'user'
                ? 'bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))]'
                : 'bg-[hsl(var(--muted))] text-[hsl(var(--foreground))]'
            )}
            onClick={() => {
              if (turn.role === 'assistant' && turn.turnIdx !== undefined) {
                onReplayTurn?.(turn.turnIdx)
              }
            }}
            style={{ cursor: turn.role === 'assistant' && turn.turnIdx !== undefined ? 'pointer' : undefined }}
            title={turn.role === 'assistant' && turn.turnIdx !== undefined ? 'Click to replay audio' : undefined}
          >
            {turn.text}
            {!turn.complete && turn.role === 'assistant' && (
              <span className="ml-1 inline-block h-3 w-0.5 bg-current animate-pulse" />
            )}
            {turn.figures && turn.figures.length > 0 && (
              <div className={cn('flex flex-wrap gap-1', turn.text && 'mt-2')}>
                {turn.figures.map((fig, fi) => (
                  <button
                    key={fi}
                    onClick={(e) => { e.stopPropagation(); onFigureClick?.(fig) }}
                    className="text-xs px-2 py-1 rounded bg-black/10 hover:bg-black/20 transition-colors cursor-pointer touch-manipulation"
                  >
                    {figureLabel(fig)}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
