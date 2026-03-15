# UI Wireframes — pdf_to_audio

---

## 1. Login Page `/`

```
┌─────────────────────────────────────────┐
│                                         │
│           ┌─────────────────┐           │
│           │   Sign In       │           │
│           ├─────────────────┤           │
│           │ Email           │           │
│           │ [___________]   │           │
│           │ Password        │           │
│           │ [___________]   │           │
│           │                 │           │
│           │ [   Log In   ]  │           │
│           │                 │           │
│           │ Don't have an   │           │
│           │ account?        │           │
│           │ [Register]      │           │
│           └─────────────────┘           │
│                                         │
└─────────────────────────────────────────┘
```

---

## 2. Register Page `/register`

```
┌─────────────────────────────────────────┐
│                                         │
│           ┌─────────────────┐           │
│           │   Register      │           │
│           ├─────────────────┤           │
│           │ Email           │           │
│           │ [___________]   │           │
│           │ Password        │           │
│           │ [___________]   │           │
│           │                 │           │
│           │ [ Register  ]   │           │
│           │                 │           │
│           │ Already have    │           │
│           │ an account?     │           │
│           │ [Log in]        │           │
│           └─────────────────┘           │
│                                         │
└─────────────────────────────────────────┘
```

---

## 3. Email Pending Page

```
┌─────────────────────────────────────────┐
│                                         │
│           ┌─────────────────┐           │
│           │  Check your     │           │
│           │  email          │           │
│           ├─────────────────┤           │
│           │  We sent a      │           │
│           │  verification   │           │
│           │  link to        │           │
│           │  user@...       │           │
│           └─────────────────┘           │
│                                         │
└─────────────────────────────────────────┘
```

---

## 4. Lesson Picker `/` (authenticated)

```
┌─────────────────────────────────────────┐
│  Your Lessons          [Upload PDF] [Log out] │
├─────────────────────────────────────────┤
│                                         │
│  ┌───────────────────────────────────┐  │
│  │ Introduction to Thermodynamics    │  │
│  │ Mar 5 2026               Complete │  │
│  └───────────────────────────────────┘  │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │ Linear Algebra Chapter 3          │  │
│  │ Mar 10 2026                       │  │
│  └───────────────────────────────────┘  │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │ ...                               │  │
│  └───────────────────────────────────┘  │
│                                         │
│  (empty state)                          │
│  No lessons yet. Upload a PDF to        │
│  get started.                           │
│                                         │
└─────────────────────────────────────────┘
```

---

## 5. Teaching Page `/teach/:lessonId`

### 5a. Normal state

```
┌──────────────────────────────────┬──────────────────────┐
│  ← lesson-id        [status msg] │  CURRICULUM          │
├──────────────────────────────────┤  ──────────────────  │
│                                  │  Section 2 / 8       │
│  [assistant bubble]              │  ████████░░░░ 25%    │
│  Hello! Today we'll cover...     │                      │
│                                  │  Sections            │
│  [slide thumbnail]  [drawing]    │  ○ Intro             │
│                                  │  ● Foundations  ←    │
│  [user bubble]                   │  ○ Applications      │
│  Can you explain this part?      │  ○ ...               │
│                                  │                      │
│  [assistant bubble]              │  ──────────────────  │
│  Sure! Let me show you...        │  Persona             │
│  ▌ (streaming)                   │  [Friendly ▾]        │
│                                  │                      │
│  [slide thumbnail]               │  Voice               │
│                                  │  [af_bella ▾]        │
│                                  │                      │
│                                  │  Language            │
│                                  │  [Auto ▾]            │
│                                  │                      │
├──────────────────────────────────┤                      │
│         [●] Record               │                      │
│                                  │                      │
└──────────────────────────────────┴──────────────────────┘
```

### 5b. Recording state

```
├──────────────────────────────────┤
│         [■] Stop    [✕ Cancel]   │
└──────────────────────────────────┘
```

### 5c. Agent busy (speaking / processing)

```
├──────────────────────────────────┤
│    [✕]  [● disabled]             │
└──────────────────────────────────┘
  ↑ Cancel turn button appears
```

---

## 6. Slide Viewer (overlay on TeachPage)

```
┌─────────────────────────────────────────┐  ← full-screen overlay
│  [✕ Close]                              │
├─────────────────────────────────────────┤
│                                         │
│  ┌─────────────────────────────────┐    │
│  │                                 │    │
│  │        PDF page N               │    │
│  │        (zoomable)               │    │  ← page_start
│  │                                 │    │
│  └─────────────────────────────────┘    │
│  [ Annotate ]  ← hover-revealed         │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │                                 │    │
│  │        PDF page N+1             │    │  ← page_end (if range)
│  │                                 │    │
│  └─────────────────────────────────┘    │
│  [ Annotate ]                           │
│                                         │
│  Caption text here                      │
│                                         │
├─────────────────────────────────────────┤
│            [● Record]                   │
└─────────────────────────────────────────┘
```

---

## 7. Annotation Overlay (overlay on SlideViewer)

```
┌─────────────────────────────────────────┐  ← full-screen
│                                         │
│  ┌─────────────────────────────────┐    │
│  │  [PDF page image as background] │    │
│  │                                 │    │
│  │  ╔═══════════════════════════╗  │    │
│  │  ║  canvas (transparent)     ║  │    │
│  │  ║  draw with red strokes    ║  │    │
│  │  ╚═══════════════════════════╝  │    │
│  └─────────────────────────────────┘    │
│                                         │
│       [Cancel]   [Submit ✓]             │
└─────────────────────────────────────────┘
  Submit composites canvas onto image and
  sends image_input event to backend.
```

---

## 8. Sketchpad (overlay on TeachPage)

```
┌─────────────────────────────────────────┐
│  Draw: [agent prompt text here]         │
├─────────────────────────────────────────┤
│                                         │
│  ┌─────────────────────────────────┐    │
│  │  (optional bg image or text)    │    │
│  │  ╔═══════════════════════════╗  │    │
│  │  ║  free-draw canvas         ║  │    │
│  │  ╚═══════════════════════════╝  │    │
│  └─────────────────────────────────┘    │
│                                         │
│       [Cancel]   [Submit ✓]             │
└─────────────────────────────────────────┘
```

---

## 9. Camera Capture (overlay on TeachPage)

```
┌─────────────────────────────────────────┐
│  Photo: [agent prompt text here]        │
├─────────────────────────────────────────┤
│                                         │
│  ┌─────────────────────────────────┐    │
│  │      [live camera feed]         │    │
│  └─────────────────────────────────┘    │
│                                         │
│       [Cancel]   [📷 Capture]           │
└─────────────────────────────────────────┘
  After capture: shows still preview
  with [Retake] and [Send] buttons.
```

---

## 10. Drawing Viewer (overlay on TeachPage)

```
┌─────────────────────────────────────────┐  ← click-away to dismiss
│                                    [✕]  │
│  ┌─────────────────────────────────┐    │
│  │  [drawing / annotated image]    │    │
│  │  (zoomable)                     │    │
│  └─────────────────────────────────┘    │
│  prompt / caption text                  │
└─────────────────────────────────────────┘
```

---

## User Flow Summary

```
/login ──→ /register ──→ email pending
   │
   └──→ / (lesson picker)
              │
              ├── [Upload PDF] ──→ /teach/:id  (decompose in background)
              │
              └── [click lesson] ──→ /teach/:id
                                         │
                                    WS connects
                                    intro phase:
                                      agent gives overview
                                      agent asks goal
                                      student responds (audio)
                                    teaching phase:
                                      agent teaches
                                      ├─ show_slide ──→ SlideViewer overlay
                                      │                   └─ annotate ──→ AnnotationOverlay
                                      ├─ open_sketchpad ──→ Sketchpad overlay
                                      └─ take_photo ──→ CameraCapture overlay
```
