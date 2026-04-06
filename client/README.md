# Client

React + TypeScript frontend built with Vite. Communicates with the BFF over REST and WebSocket.

## Setup

```bash
source /home/appuser/.nvm/nvm.sh && nvm use 20
npm install
npm run dev          # dev server on :5173, proxies /api and /ws to :8000
npm run build        # production build to ../frontend/static/
```

## Structure

```
src/
  pages/                Page-level components (one per route)
    TeachPage.tsx       Main teaching UI — voice, slides, exercises
    HomePage.tsx        Landing / dashboard
    LessonPickerPage.tsx  Lesson browser and course catalog
    CoursePage.tsx      Course viewer with chapter navigation
    LoginPage.tsx       Auth pages (login, register, verify, reset)
    IamPage.tsx         Admin user management
    LeaderboardPage.tsx Gamification leaderboard
    UsageDashboardPage.tsx  Token and cost analytics

  components/           Reusable UI components
    ConversationView.tsx  Chat message display
    CurriculumPanel.tsx   Section sidebar with progress
    RecordButton.tsx      Push-to-talk / VAD recording
    SlideViewer.tsx       PDF page display with zoom
    Sketchpad.tsx         Drawing canvas (character practice, diagrams)
    CodeEditor.tsx        CodeMirror editor with execution
    HtmlCssEditor.tsx     Dual HTML/CSS editor with live preview
    TimerExercise.tsx     Countdown timer for drills
    CameraCapture.tsx     Photo capture
    VideoCapture.tsx      Video recording with frame sampling
    ImageViewer.tsx       AI-generated / searched image display
    InputBar.tsx          Text input with send
    StatusBar.tsx         Connection and session status
    SettingsDrawer.tsx    Voice, model, and UI preferences
    LessonDrawer.tsx      Lesson info panel
    CourseDrawer.tsx       Course management panel

  lib/
    ws.ts               WebSocket client (connect, reconnect, event dispatch)
    types.ts            ClientEvent / ServerEvent type definitions
    utils.ts            Shared utilities
    theme.tsx           Theme provider (light/dark)
    recency.ts          Recency tracking for UI state
    audio/
      recorder.ts       Microphone capture (PCM 16-bit, 16kHz)
      player.ts         Audio playback from PCM chunks
      vad.worklet.ts    Voice activity detection (AudioWorklet)

  App.tsx               Root component, routing
  main.tsx              Entry point
  globals.css           Base styles (Tailwind)
```

## WebSocket Events

The client communicates with the backend via a single WebSocket connection. Event types are defined in `lib/types.ts`.

**Client sends:** `audio_chunk`, `text_input`, `tool_result`, `start_turn`, `set_voice`, etc.

**Server sends:** `text_chunk`, `audio_chunk`, `section_advanced`, `task_progress`, `show_slide`, `open_sketchpad`, `open_code_editor`, `decompose_complete`, `history`, etc.

## Key Dependencies

- **React 19** with TypeScript
- **Vite 6** for dev server and builds
- **CodeMirror** for code/HTML editors
- **@ricky0123/vad-web** for client-side voice activity detection
- **Lucide React** for icons
- **Tailwind CSS** via `globals.css`

## Build Output

`npm run build` outputs to `../frontend/static/`. The BFF serves these files as the production frontend. The build includes a `copy-vad-assets` step that bundles the VAD WASM/ONNX files into `public/vad/`.
