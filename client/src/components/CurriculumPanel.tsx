/**
 * Curriculum progress panel: section list with current-section highlight.
 */

import { Progress } from './ui/progress'
import { cn } from '@/lib/utils'
import { sortByRecency, recordRecent } from '@/lib/recency'
import type { CurriculumData, CurriculumState, Persona, Voice, SttLanguage, SttModel } from '@/lib/types'

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
  sttModels: SttModel[]
  selectedSttModelId: string
  onSttModelChange: (id: string) => void
  onViewPage?: (pageStart: number, pageEnd: number) => void
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
  sttModels, selectedSttModelId, onSttModelChange,
  onViewPage,
}: CurriculumPanelProps) {
  const sortedPersonas   = sortByRecency(personas,     (p) => p.id,           (p) => p.name, 'persona')
  const sortedVoices     = sortByRecency(voices,       (v) => v.id,           (v) => v.id,   'voice')
  const sortedLanguages  = sortByRecency(sttLanguages, (l) => l.code ?? '',   (l) => l.name, 'stt_lang')
  const sortedSttModels  = sortByRecency(sttModels,    (m) => m.id,           (m) => m.id,   'stt_model')

  const controls = (
    <div className="flex flex-col gap-2">
      {sortedPersonas.length > 0 && (
        <SelectRow label="Persona" value={selectedPersonaId} onChange={(id) => { recordRecent('persona', id); onPersonaChange(id) }}>
          <option value="">Default</option>
          {sortedPersonas.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </SelectRow>
      )}
      {sortedVoices.length > 0 && (
        <SelectRow label="Voice" value={selectedVoiceId} onChange={(id) => { recordRecent('voice', id); onVoiceChange(id) }}>
          {sortedVoices.map((v) => (
            <option key={v.id} value={v.id}>{v.id}</option>
          ))}
        </SelectRow>
      )}
      {sortedLanguages.length > 0 && (
        <SelectRow label="Speech language" value={selectedLangCode} onChange={(code) => { recordRecent('stt_lang', code); onLangChange(code) }}>
          {sortedLanguages.map((l) => (
            <option key={l.code ?? '__auto'} value={l.code ?? ''}>{l.name}</option>
          ))}
        </SelectRow>
      )}
      {sortedSttModels.length > 0 && (
        <SelectRow label="STT model" value={selectedSttModelId} onChange={(id) => { recordRecent('stt_model', id); onSttModelChange(id) }}>
          {sortedSttModels.map((m) => (
            <option key={m.id} value={m.id}>{m.id}{m.is_default ? ' (default)' : ''}</option>
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
          const hasPages = sec.page_start != null
          const pageLabel = hasPages
            ? sec.page_end && sec.page_end !== sec.page_start
              ? `pp. ${sec.page_start}–${sec.page_end}`
              : `p. ${sec.page_start}`
            : null
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
              <div className="flex items-center justify-between gap-1">
                <span className="flex-1 truncate">{sec.title ?? `Section ${idx + 1}`}</span>
                {pageLabel && onViewPage && (
                  <button
                    onClick={() => onViewPage(sec.page_start!, sec.page_end ?? sec.page_start!)}
                    className="shrink-0 rounded px-1 text-xs text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))] transition-colors"
                    title="View pages"
                  >
                    {pageLabel}
                  </button>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
