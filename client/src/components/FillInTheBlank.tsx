import { useState } from 'react'
import { Button } from '@/components/ui/button'

interface FillInTheBlankProps {
  prompt: string
  template: string
  answers: string[]
  invocationId: string
  onSubmit: (invocationId: string, studentAnswers: string[], correct: boolean[]) => void
  onCancel: () => void
}

export function FillInTheBlank({ prompt, template, answers, invocationId, onSubmit, onCancel }: FillInTheBlankProps) {
  const parts = template.split('___')
  const blankCount = parts.length - 1
  const [values, setValues] = useState<string[]>(Array(blankCount).fill(''))
  const [submitted, setSubmitted] = useState(false)
  const [correctFlags, setCorrectFlags] = useState<boolean[]>([])

  function handleChange(idx: number, val: string) {
    setValues((prev) => { const next = [...prev]; next[idx] = val; return next })
  }

  function handleSubmit() {
    const flags = values.map((v, i) =>
      v.trim().toLowerCase() === (answers[i] || '').trim().toLowerCase()
    )
    setCorrectFlags(flags)
    setSubmitted(true)
    setTimeout(() => onSubmit(invocationId, values, flags), 1500)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-lg rounded-lg bg-[hsl(var(--card))] shadow-xl flex flex-col">
        <div className="border-b p-4">
          <p className="text-sm font-medium">{prompt}</p>
        </div>

        <div className="p-4">
          <div className="text-sm leading-relaxed flex flex-wrap items-baseline gap-y-2">
            {parts.map((part, i) => (
              <span key={i}>
                {part}
                {i < blankCount && (
                  <input
                    type="text"
                    value={values[i]}
                    onChange={(e) => handleChange(i, e.target.value)}
                    disabled={submitted}
                    className={`inline-block w-28 mx-1 rounded border px-2 py-0.5 text-sm text-center focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] ${
                      submitted
                        ? correctFlags[i]
                          ? 'border-green-500 bg-green-500/10'
                          : 'border-red-500 bg-red-500/10'
                        : 'border-[hsl(var(--border))] bg-[hsl(var(--background))]'
                    }`}
                    autoFocus={i === 0}
                  />
                )}
              </span>
            ))}
          </div>
          {submitted && (
            <div className="mt-3 space-y-1">
              {correctFlags.map((ok, i) =>
                !ok && (
                  <p key={i} className="text-xs text-red-500">
                    Blank {i + 1}: expected "{answers[i]}"
                  </p>
                )
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t p-4">
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={submitted}>Skip</Button>
          <Button size="sm" onClick={handleSubmit} disabled={submitted || values.every((v) => !v.trim())}>
            {submitted ? 'Submitting...' : 'Submit'}
          </Button>
        </div>
      </div>
    </div>
  )
}
