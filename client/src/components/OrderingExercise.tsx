import { useState, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { GripVertical, ChevronUp, ChevronDown, Check, X } from 'lucide-react'

interface OrderingExerciseProps {
  prompt: string
  items: string[]
  correctOrder: number[]
  invocationId: string
  onSubmit: (invocationId: string, studentOrder: number[], correct: boolean) => void
  onCancel: () => void
}

export function OrderingExercise({ prompt, items, correctOrder, invocationId, onSubmit, onCancel }: OrderingExerciseProps) {
  // order[i] is the index into `items` for position i
  const [order, setOrder] = useState<number[]>(items.map((_, i) => i))
  const [submitted, setSubmitted] = useState(false)
  const [isCorrect, setIsCorrect] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)

  const moveItem = useCallback((from: number, to: number) => {
    setOrder((prev) => {
      const next = [...prev]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next
    })
  }, [])

  function handleSubmit() {
    // Check if student's order matches correct order.
    // correctOrder maps shuffled position → original index.
    // The correct sequence is: correctOrder.indexOf(0), correctOrder.indexOf(1), ...
    // i.e., position of original item 0 should come first, etc.
    // Simpler: items were shuffled by backend. correctOrder[i] = original index of items[i].
    // Correct answer = sorted by correctOrder ascending.
    const correctSequence = [...Array(items.length).keys()].sort((a, b) => correctOrder[a] - correctOrder[b])
    const correct = order.every((val, i) => val === correctSequence[i])
    setIsCorrect(correct)
    setSubmitted(true)
    setTimeout(() => onSubmit(invocationId, order, correct), 1200)
  }

  function handleDragStart(idx: number) {
    setDragIdx(idx)
  }

  function handleDragOver(e: React.DragEvent, idx: number) {
    e.preventDefault()
    if (dragIdx !== null && dragIdx !== idx) {
      moveItem(dragIdx, idx)
      setDragIdx(idx)
    }
  }

  function handleDragEnd() {
    setDragIdx(null)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg bg-[hsl(var(--card))] shadow-xl flex flex-col max-h-[80vh]">
        <div className="border-b p-4">
          <p className="text-sm font-medium">{prompt}</p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-1.5">
          {order.map((itemIdx, pos) => {
            let borderClass = 'border-[hsl(var(--border))]'
            if (submitted) {
              // Show correct position feedback
              const correctSequence = [...Array(items.length).keys()].sort((a, b) => correctOrder[a] - correctOrder[b])
              const inRightSpot = itemIdx === correctSequence[pos]
              borderClass = inRightSpot ? 'border-green-500 bg-green-500/5' : 'border-red-500 bg-red-500/5'
            }

            return (
              <div
                key={itemIdx}
                draggable={!submitted}
                onDragStart={() => handleDragStart(pos)}
                onDragOver={(e) => handleDragOver(e, pos)}
                onDragEnd={handleDragEnd}
                className={`flex items-center gap-2 rounded-md border ${borderClass} px-3 py-2 text-sm cursor-grab active:cursor-grabbing transition-colors ${
                  dragIdx === pos ? 'opacity-50' : ''
                }`}
              >
                <GripVertical className="h-4 w-4 text-[hsl(var(--muted-foreground))] flex-shrink-0" />
                <span className="flex-1">{items[itemIdx]}</span>
                {!submitted && (
                  <div className="flex flex-col -my-1">
                    <button
                      onClick={() => pos > 0 && moveItem(pos, pos - 1)}
                      disabled={pos === 0}
                      className="p-0.5 hover:text-[hsl(var(--primary))] disabled:opacity-30"
                    >
                      <ChevronUp className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => pos < order.length - 1 && moveItem(pos, pos + 1)}
                      disabled={pos === order.length - 1}
                      className="p-0.5 hover:text-[hsl(var(--primary))] disabled:opacity-30"
                    >
                      <ChevronDown className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}
                {submitted && (
                  (() => {
                    const correctSequence = [...Array(items.length).keys()].sort((a, b) => correctOrder[a] - correctOrder[b])
                    return itemIdx === correctSequence[pos]
                      ? <Check className="h-4 w-4 text-green-500" />
                      : <X className="h-4 w-4 text-red-500" />
                  })()
                )}
              </div>
            )
          })}
        </div>

        <div className="flex items-center justify-between border-t p-4">
          {submitted && (
            <span className={`text-sm font-medium ${isCorrect ? 'text-green-600' : 'text-red-600'}`}>
              {isCorrect ? 'Correct!' : 'Not quite right'}
            </span>
          )}
          <div className="flex gap-2 ml-auto">
            <Button variant="ghost" size="sm" onClick={onCancel} disabled={submitted}>Skip</Button>
            <Button size="sm" onClick={handleSubmit} disabled={submitted}>
              {submitted ? 'Submitting...' : 'Submit'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
