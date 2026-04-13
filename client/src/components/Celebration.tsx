/**
 * Celebration overlay: confetti burst + points toast.
 * Auto-dismisses after the animation completes.
 */

import { useEffect, useState, useRef } from 'react'

interface Particle {
  id: number
  x: number
  y: number
  angle: number
  speed: number
  spin: number
  size: number
  color: string
  shape: 'rect' | 'circle'
}

const COLORS = [
  '#ff6b9d', '#c44dff', '#ff4ecb', '#51e5ff',
  '#ffed4e', '#ff9148', '#6bff6b', '#4e8cff',
]

function makeParticles(count: number): Particle[] {
  return Array.from({ length: count }, (_, i) => ({
    id: i,
    x: 40 + Math.random() * 20,  // cluster around center
    y: 30 + Math.random() * 10,
    angle: Math.random() * 360,
    speed: 2 + Math.random() * 4,
    spin: (Math.random() - 0.5) * 720,
    size: 6 + Math.random() * 6,
    color: COLORS[Math.floor(Math.random() * COLORS.length)],
    shape: Math.random() > 0.5 ? 'rect' : 'circle',
  }))
}

interface CelebrationProps {
  points: number
  reason: 'section_advance' | 'lesson_complete' | string
  onDone: () => void
}

export function Celebration({ points, reason, onDone }: CelebrationProps) {
  const [particles] = useState(() => makeParticles(reason === 'lesson_complete' ? 80 : 40))
  const [visible, setVisible] = useState(true)
  const timerRef = useRef<ReturnType<typeof setTimeout>>(null)

  useEffect(() => {
    timerRef.current = setTimeout(() => {
      setVisible(false)
      setTimeout(onDone, 400)  // wait for fade-out
    }, reason === 'lesson_complete' ? 3500 : 2500)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [onDone, reason])

  const label = reason === 'lesson_complete' ? 'Lesson Complete!' : 'Section Complete!'

  return (
    <div
      className="fixed inset-0 z-[60] pointer-events-none"
      style={{
        opacity: visible ? 1 : 0,
        transition: 'opacity 400ms ease-out',
      }}
    >
      {/* Confetti */}
      <svg className="absolute inset-0 w-full h-full overflow-visible">
        {particles.map((p) => {
          const rad = (p.angle * Math.PI) / 180
          const tx = Math.cos(rad) * p.speed * 15
          const ty = Math.sin(rad) * p.speed * 15 + 40  // gravity bias
          return (
            <g key={p.id}>
              {p.shape === 'rect' ? (
                <rect
                  x={`${p.x}%`}
                  y={`${p.y}%`}
                  width={p.size}
                  height={p.size * 0.6}
                  rx={1}
                  fill={p.color}
                  style={{
                    animation: `confetti-fall ${1.2 + Math.random() * 1.2}s ease-out forwards`,
                    '--tx': `${tx}vw`,
                    '--ty': `${ty}vh`,
                    '--spin': `${p.spin}deg`,
                    transformOrigin: 'center',
                  } as React.CSSProperties}
                />
              ) : (
                <circle
                  cx={`${p.x}%`}
                  cy={`${p.y}%`}
                  r={p.size / 2}
                  fill={p.color}
                  style={{
                    animation: `confetti-fall ${1.2 + Math.random() * 1.2}s ease-out forwards`,
                    '--tx': `${tx}vw`,
                    '--ty': `${ty}vh`,
                    '--spin': `${p.spin}deg`,
                    transformOrigin: 'center',
                  } as React.CSSProperties}
                />
              )}
            </g>
          )
        })}
      </svg>

      {/* Points toast */}
      <div
        className="absolute left-1/2 top-1/3 -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-2"
        style={{
          animation: 'toast-rise 0.6s ease-out forwards',
        }}
      >
        <div className="text-2xl font-bold text-white drop-shadow-lg">
          {label}
        </div>
        <div
          className="rounded-full px-5 py-2 text-lg font-bold shadow-xl"
          style={{
            background: 'linear-gradient(135deg, #ff6b9d, #c44dff)',
            color: 'white',
            animation: 'points-pop 0.4s ease-out 0.3s both',
          }}
        >
          +{points} pts
        </div>
      </div>

      <style>{`
        @keyframes confetti-fall {
          0% {
            transform: translate(0, 0) rotate(0deg) scale(1);
            opacity: 1;
          }
          100% {
            transform: translate(var(--tx), var(--ty)) rotate(var(--spin)) scale(0.3);
            opacity: 0;
          }
        }
        @keyframes toast-rise {
          0% {
            opacity: 0;
            transform: translate(-50%, -30%) scale(0.5);
          }
          100% {
            opacity: 1;
            transform: translate(-50%, -50%) scale(1);
          }
        }
        @keyframes points-pop {
          0% {
            transform: scale(0);
            opacity: 0;
          }
          60% {
            transform: scale(1.2);
          }
          100% {
            transform: scale(1);
            opacity: 1;
          }
        }
      `}</style>
    </div>
  )
}
