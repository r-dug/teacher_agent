# React Client — Design Notes

_Decision: React + TypeScript (Vite) + shadcn/ui + Tailwind CSS_

---

## Tailwind modularity principles

All design values live in `tailwind.config.ts` — no raw hex codes or magic
numbers scattered in component files.

```ts
// tailwind.config.ts
theme: {
  extend: {
    colors: {
      // shadcn/ui uses CSS variables; extend here for app-specific tokens
      brand: {
        DEFAULT: 'hsl(var(--brand))',
        foreground: 'hsl(var(--brand-foreground))',
      },
    },
    // Typography scale, spacing, border-radius defined here too
  }
}
```

CSS variables are declared in `globals.css` (one place to update the look):
```css
:root {
  --brand: 220 90% 56%;          /* HSL — easy to theme */
  --brand-foreground: 0 0% 100%;
}
.dark {
  --brand: 220 90% 65%;
}
```

Components use `cn()` (clsx + tailwind-merge) for conditional class composition.
No inline styles except for dynamic values (e.g. waveform bar heights).

---

## Key custom UI surfaces

These have no off-the-shelf component — we build them regardless of library:

| Surface | Implementation |
|---------|---------------|
| Real-time text stream | Append to a scrollable `<div>` as WS chunks arrive |
| Recording indicator | CSS animation (pulsing ring) or canvas waveform |
| Audio waveform visualizer | `<canvas>` + Web Audio API `AnalyserNode` |
| Sketchpad canvas | `<canvas>` with pointer events; submit as base64 PNG |
| PDF slide popup | `<dialog>` or shadcn `Sheet`; image served from backend |
| Curriculum progress | shadcn `Progress` + section list |
| Click-to-replay | Text spans tagged with `data-turn` + `data-chunk` attributes |

---

## Audio architecture (browser)

### Recording (user → server)

```
getUserMedia (16kHz mono)
  → AudioWorklet (VAD + PCM extraction)
  → Float32Array chunks
  → base64 encode
  → WebSocket send {event: "audio_input", data: ..., sample_rate: 16000}
```

`AudioWorklet` runs off the main thread — no dropped frames from React rendering.
The worklet implements the same simple RMS VAD that `VoicePipeline` uses:
accumulate samples, detect silence gap, emit complete utterance.

### Playback (server → user)

```
WebSocket receive {event: "audio_chunk", data: base64, sample_rate: 24000}
  → base64 decode → Float32Array
  → AudioContext.createBuffer(1, samples, 24000)
  → AudioBufferSourceNode.start()
  → store in audioTurns[turnIdx][chunkIdx] for click-to-replay
```

A playback queue serialises chunk playback so chunks don't overlap.
`audioTurns` LRU: retain last 10 turns (same policy as Tkinter design).

---

## Project structure (client/)

```
client/
├── public/
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── globals.css          ← CSS variables + Tailwind directives
│   ├── lib/
│   │   ├── utils.ts         ← cn() helper
│   │   ├── ws.ts            ← WebSocket connection + event dispatch
│   │   ├── audio/
│   │   │   ├── recorder.ts  ← getUserMedia + AudioWorklet controller
│   │   │   ├── player.ts    ← AudioContext playback queue
│   │   │   └── vad.worklet.ts  ← AudioWorklet VAD processor
│   │   └── types.ts         ← WS event types (mirrors backend protocol)
│   ├── hooks/
│   │   ├── useWebSocket.ts
│   │   ├── useRecorder.ts
│   │   └── useAudioPlayer.ts
│   ├── components/
│   │   ├── ui/              ← shadcn/ui generated components
│   │   ├── ConversationView.tsx
│   │   ├── RecordButton.tsx
│   │   ├── CurriculumPanel.tsx
│   │   ├── SlideViewer.tsx
│   │   ├── Sketchpad.tsx
│   │   └── StatusBar.tsx
│   └── pages/
│       ├── TeachPage.tsx    ← main teaching view
│       └── LessonPickerPage.tsx
├── package.json
├── tsconfig.json
├── vite.config.ts
└── tailwind.config.ts
```

---

## WS event types (TypeScript)

Defined once in `src/lib/types.ts`; used throughout the app for type-safe
event dispatch.

```ts
// Outbound (client → server)
type ClientEvent =
  | { event: 'audio_input'; data: string; sample_rate: number }
  | { event: 'tool_result'; invocation_id: string; result: { drawing: string } }
  | { event: 'set_instructions'; instructions: string }
  | { event: 'reconnect'; last_turn_id: string }
  | { event: 'cancel_turn' }
  | { event: 'ping' }

// Inbound (server → client)
type ServerEvent =
  | { event: 'transcription'; text: string; turn_id: string }
  | { event: 'text_chunk'; text: string; turn_idx: number }
  | { event: 'audio_chunk'; data: string; sample_rate: number; turn_idx: number; chunk_idx: number }
  | { event: 'chunk_complete'; turn_idx: number; chunk_idx: number }
  | { event: 'chunk_ready'; tag: string; turn_idx: number; chunk_idx: number }
  | { event: 'turn_complete'; turn_id: string }
  | { event: 'turn_interrupted' }
  | { event: 'show_slide'; page: number; caption: string }
  | { event: 'open_sketchpad'; prompt: string; invocation_id: string }
  | { event: 'section_advanced'; curriculum: CurriculumState }
  | { event: 'curriculum_complete' }
  | { event: 'decompose_complete'; lesson_id: string; curriculum: CurriculumData }
  | { event: 'tts_playing'; playing: boolean }
  | { event: 'status'; message: string }
  | { event: 'error'; message: string }
  | { event: 'response_end' }
  | { event: 'turn_start' }
  | { event: 'pong' }
```

---

## Development setup

```
# Terminal 1: backend
uv run python -m backend.main

# Terminal 2: frontend server (Phase 2)
uv run python -m frontend.main

# Terminal 3: React dev server
cd client && npm run dev    # Vite on :5173, proxies /api → :8000, /ws → :8000
```

In production: `npm run build` → static bundle served by FastAPI frontend server.

---

## Open items for React Phase

- [ ] Aesthetic direction (colors, type scale, spacing feel) — deferred
- [ ] PDF page image serving: backend needs a `GET /lessons/{id}/page/{n}` endpoint
      returning the page as PNG for `SlideViewer`
- [ ] Auth integration (login page, token storage) — future phase
- [ ] Mobile responsiveness — plan for it, don't optimise yet
