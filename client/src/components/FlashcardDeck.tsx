import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Check, X, RotateCcw } from 'lucide-react'

interface FlashcardDeckProps {
  prompt: string
  cards: Array<{ front: string; back: string }>
  invocationId: string
  onSubmit: (invocationId: string, results: Array<{ card_index: number; self_grade: 'correct' | 'incorrect' }>) => void
  onCancel: () => void
}

export function FlashcardDeck({ prompt, cards, invocationId, onSubmit, onCancel }: FlashcardDeckProps) {
  const [currentIdx, setCurrentIdx] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [results, setResults] = useState<Array<{ card_index: number; self_grade: 'correct' | 'incorrect' }>>([])
  const [done, setDone] = useState(false)

  const card = cards[currentIdx]
  const progress = `${currentIdx + 1} / ${cards.length}`

  function handleGrade(grade: 'correct' | 'incorrect') {
    const newResults = [...results, { card_index: currentIdx, self_grade: grade }]
    setResults(newResults)

    if (currentIdx + 1 >= cards.length) {
      setDone(true)
      setTimeout(() => onSubmit(invocationId, newResults), 800)
    } else {
      setFlipped(false)
      setCurrentIdx(currentIdx + 1)
    }
  }

  if (done) {
    const correct = results.filter((r) => r.self_grade === 'correct').length
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
        <div className="w-full max-w-sm rounded-lg bg-[hsl(var(--card))] shadow-xl p-6 text-center">
          <p className="text-lg font-semibold mb-2">{correct} / {cards.length} correct</p>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">Submitting results...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-sm rounded-lg bg-[hsl(var(--card))] shadow-xl flex flex-col">
        <div className="flex items-center justify-between border-b p-4">
          <p className="text-sm font-medium">{prompt}</p>
          <span className="text-xs text-[hsl(var(--muted-foreground))]">{progress}</span>
        </div>

        {/* Card */}
        <div className="p-4">
          <button
            onClick={() => setFlipped(!flipped)}
            className="w-full min-h-[160px] rounded-lg border border-[hsl(var(--border))] flex items-center justify-center p-6 cursor-pointer transition-all hover:shadow-md"
            style={{ perspective: '800px' }}
          >
            <div className="text-center">
              <p className="text-base">{flipped ? card.back : card.front}</p>
              {!flipped && (
                <p className="text-xs text-[hsl(var(--muted-foreground))] mt-3 flex items-center justify-center gap-1">
                  <RotateCcw className="h-3 w-3" /> Tap to flip
                </p>
              )}
            </div>
          </button>
        </div>

        {/* Grade buttons (only visible when flipped) */}
        <div className="flex justify-center gap-3 border-t p-4">
          {flipped ? (
            <>
              <Button variant="outline" size="sm" onClick={() => handleGrade('incorrect')} className="gap-1 border-red-300 text-red-600 hover:bg-red-50">
                <X className="h-4 w-4" /> Missed it
              </Button>
              <Button variant="outline" size="sm" onClick={() => handleGrade('correct')} className="gap-1 border-green-300 text-green-600 hover:bg-green-50">
                <Check className="h-4 w-4" /> Got it
              </Button>
            </>
          ) : (
            <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          )}
        </div>
      </div>
    </div>
  )
}
