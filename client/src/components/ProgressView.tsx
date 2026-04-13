import { X, Check, Circle } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ProgressViewProps {
  sections: Array<{ title: string; idx: number; completed: boolean }>
  currentIdx: number
  tasks: Array<{ concept: string; status: string }>
  onClose: () => void
}

export function ProgressView({ sections, currentIdx, tasks, onClose }: ProgressViewProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="relative w-full max-w-md rounded-lg bg-[hsl(var(--card))] shadow-xl max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between border-b p-4">
          <h3 className="text-sm font-semibold">Lesson Progress</h3>
          <Button variant="ghost" size="sm" onClick={onClose} className="h-7 w-7 p-0">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {/* Section stepper */}
          <div className="space-y-1">
            {sections.map((sec) => {
              const isCurrent = sec.idx === currentIdx
              const isDone = sec.completed
              return (
                <div key={sec.idx} className="flex items-center gap-3">
                  <div className="flex-shrink-0">
                    {isDone ? (
                      <div className="h-6 w-6 rounded-full bg-green-500 flex items-center justify-center">
                        <Check className="h-3.5 w-3.5 text-white" />
                      </div>
                    ) : isCurrent ? (
                      <div className="h-6 w-6 rounded-full bg-blue-500 flex items-center justify-center">
                        <Circle className="h-3 w-3 text-white fill-white" />
                      </div>
                    ) : (
                      <div className="h-6 w-6 rounded-full border-2 border-[hsl(var(--muted-foreground))]" />
                    )}
                  </div>
                  <span className={`text-sm ${isCurrent ? 'font-medium' : isDone ? 'text-[hsl(var(--muted-foreground))]' : ''}`}>
                    {sec.title}
                  </span>
                </div>
              )
            })}
          </div>

          {/* Current section tasks */}
          {tasks.length > 0 && (
            <div className="mt-4 border-t pt-3">
              <p className="text-xs font-medium text-[hsl(var(--muted-foreground))] mb-2">Current section tasks</p>
              <div className="space-y-1">
                {tasks.map((task, i) => (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    {task.status === 'passed' || task.status === 'skipped' ? (
                      <Check className="h-3.5 w-3.5 text-green-500 flex-shrink-0" />
                    ) : (
                      <Circle className="h-3.5 w-3.5 text-[hsl(var(--muted-foreground))] flex-shrink-0" />
                    )}
                    <span className={task.status === 'passed' || task.status === 'skipped' ? 'line-through text-[hsl(var(--muted-foreground))]' : ''}>
                      {task.concept}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
