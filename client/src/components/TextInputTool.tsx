import { useState } from 'react'
import { Button } from '@/components/ui/button'

interface TextInputToolProps {
  prompt: string
  placeholder?: string
  multiline?: boolean
  invocationId: string
  onSubmit: (invocationId: string, answer: string) => void
  onCancel: () => void
}

export function TextInputTool({ prompt, placeholder, multiline, invocationId, onSubmit, onCancel }: TextInputToolProps) {
  const [answer, setAnswer] = useState('')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-lg rounded-lg bg-[hsl(var(--card))] shadow-xl flex flex-col">
        <div className="border-b p-4">
          <p className="text-sm font-medium">{prompt}</p>
        </div>

        <div className="p-4">
          {multiline ? (
            <textarea
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder={placeholder || 'Type your answer...'}
              className="w-full min-h-[120px] rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm resize-y focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
              autoFocus
            />
          ) : (
            <input
              type="text"
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder={placeholder || 'Type your answer...'}
              className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') onSubmit(invocationId, answer) }}
            />
          )}
        </div>

        <div className="flex justify-end gap-2 border-t p-4">
          <Button variant="ghost" size="sm" onClick={onCancel}>Skip</Button>
          <Button size="sm" onClick={() => onSubmit(invocationId, answer)}>Submit</Button>
        </div>
      </div>
    </div>
  )
}
