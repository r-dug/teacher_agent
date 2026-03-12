import * as React from 'react'
import { cn } from '@/lib/utils'

interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  value?: number   // 0–100
}

const Progress = React.forwardRef<HTMLDivElement, ProgressProps>(
  ({ className, value = 0, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('relative h-2 w-full overflow-hidden rounded-full bg-[hsl(var(--secondary))]', className)}
      {...props}
    >
      <div
        className="h-full w-full flex-1 bg-[hsl(var(--primary))] transition-all duration-300"
        style={{ transform: `translateX(-${100 - (value ?? 0)}%)` }}
      />
    </div>
  )
)
Progress.displayName = 'Progress'

export { Progress }
