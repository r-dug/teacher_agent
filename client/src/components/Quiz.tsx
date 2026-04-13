import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Check, X } from 'lucide-react'

interface QuizProps {
  prompt: string
  choices: Array<{ label: string; text: string }>
  correctIndex: number
  invocationId: string
  onSubmit: (invocationId: string, selectedIndex: number, correct: boolean) => void
  onCancel: () => void
}

export function Quiz({ prompt, choices, correctIndex, invocationId, onSubmit, onCancel }: QuizProps) {
  const [selected, setSelected] = useState<number | null>(null)
  const [submitted, setSubmitted] = useState(false)

  function handleSubmit() {
    if (selected === null) return
    setSubmitted(true)
    const correct = selected === correctIndex
    // Brief delay so the student sees feedback before the overlay closes
    setTimeout(() => onSubmit(invocationId, selected, correct), 1200)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-lg rounded-lg bg-[hsl(var(--card))] shadow-xl flex flex-col">
        <div className="border-b p-4">
          <p className="text-sm font-medium">{prompt}</p>
        </div>

        <div className="p-4 space-y-2">
          {choices.map((choice, i) => {
            const isSelected = selected === i
            const isCorrect = i === correctIndex
            let borderClass = 'border-[hsl(var(--border))]'
            let bgClass = ''
            if (submitted && isCorrect) {
              borderClass = 'border-green-500'
              bgClass = 'bg-green-500/10'
            } else if (submitted && isSelected && !isCorrect) {
              borderClass = 'border-red-500'
              bgClass = 'bg-red-500/10'
            } else if (isSelected) {
              borderClass = 'border-[hsl(var(--primary))]'
              bgClass = 'bg-[hsl(var(--primary))]/5'
            }

            return (
              <button
                key={i}
                disabled={submitted}
                onClick={() => setSelected(i)}
                className={`w-full flex items-center gap-3 rounded-md border ${borderClass} ${bgClass} px-3 py-2.5 text-left text-sm transition-colors hover:bg-[hsl(var(--muted))]/50 disabled:cursor-default`}
              >
                <span className="flex-shrink-0 font-medium text-[hsl(var(--muted-foreground))]">{choice.label}</span>
                <span className="flex-1">{choice.text}</span>
                {submitted && isCorrect && <Check className="h-4 w-4 text-green-500 flex-shrink-0" />}
                {submitted && isSelected && !isCorrect && <X className="h-4 w-4 text-red-500 flex-shrink-0" />}
              </button>
            )
          })}
        </div>

        <div className="flex justify-end gap-2 border-t p-4">
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={submitted}>Skip</Button>
          <Button size="sm" onClick={handleSubmit} disabled={selected === null || submitted}>
            {submitted ? 'Submitting...' : 'Submit'}
          </Button>
        </div>
      </div>
    </div>
  )
}
