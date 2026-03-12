/**
 * Curriculum progress panel: section list with current-section highlight.
 */

import { Progress } from './ui/progress'
import { cn } from '@/lib/utils'
import type { CurriculumData, CurriculumState, Persona, Voice, SttLanguage } from '@/lib/types'

interface CurriculumPanelProps {
  curriculum: CurriculumData | null
  state: CurriculumState | null
  complete: boolean
  personas: Persona[]
  selectedPersonaId: string
  onPersonaChange: (id: string) => void
  voices: Voice[]
  selectedVoiceId: string
  onVoiceChange: (id: string) => void
  sttLanguages: SttLanguage[]
  selectedLangCode: string
  onLangChange: (code: string) => void
}

function SelectRow({ label, value, onChange, children }: {
  label: string
  value: string
  onChange: (v: string) => void
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-[hsl(var(--muted-foreground))]">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2 py-1 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-1 focus:ring-[hsl(var(--ring))]"
      >
        {children}
      </select>
    </div>
  )
}

export function CurriculumPanel({
  curriculum, state, complete,
  personas, selectedPersonaId, onPersonaChange,
  voices, selectedVoiceId, onVoiceChange,
  sttLanguages, selectedLangCode, onLangChange,
}: CurriculumPanelProps) {
  const controls = (
    <div className="flex flex-col gap-2">
      {personas.length > 0 && (
        <SelectRow label="Persona" value={selectedPersonaId} onChange={onPersonaChange}>
          <option value="">Default</option>
          {personas.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </SelectRow>
      )}
      {voices.length > 0 && (
        <SelectRow label="Voice" value={selectedVoiceId} onChange={onVoiceChange}>
          {voices.map((v) => (
            <option key={v.id} value={v.id}>{v.id}</option>
          ))}
        </SelectRow>
      )}
      {sttLanguages.length > 0 && (
        <SelectRow label="Speech language" value={selectedLangCode} onChange={onLangChange}>
          {sttLanguages.map((l) => (
            <option key={l.code ?? '__auto'} value={l.code ?? ''}>{l.name}</option>
          ))}
        </SelectRow>
      )}
    </div>
  )

  if (!curriculum) {
    return (
      <div className="flex flex-col gap-3 p-4">
        {controls}
        <p className="text-sm text-[hsl(var(--muted-foreground))]">No curriculum loaded.</p>
      </div>
    )
  }

  const total = curriculum.sections.length
  const current = state?.idx ?? 0
  const pct = complete ? 100 : total > 0 ? Math.round((current / total) * 100) : 0

  return (
    <div className="flex flex-col gap-3 p-4">
      {controls}
      <div className="flex items-center justify-between text-xs text-[hsl(var(--muted-foreground))]">
        <span>Progress</span>
        <span>{complete ? 'Complete!' : `${current + 1} / ${total}`}</span>
      </div>
      <Progress value={pct} />
      <ul className="mt-2 space-y-1">
        {curriculum.sections.map((sec, idx) => {
          const isCurrent = !complete && idx === current
          const isDone = complete || idx < current
          return (
            <li
              key={idx}
              className={cn(
                'rounded px-2 py-1 text-sm transition-colors',
                isCurrent && 'bg-[hsl(var(--primary)/0.1)] font-medium text-[hsl(var(--primary))]',
                isDone && !isCurrent && 'text-[hsl(var(--muted-foreground))] line-through',
                !isCurrent && !isDone && 'text-[hsl(var(--foreground))]'
              )}
            >
              {sec.title ?? `Section ${idx + 1}`}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
