# Tool Use: Interactive Code Editor & HTML/CSS Editor

_Status: Designed, not yet implemented._

---

## Overview

Two new agent tools allow the teacher to assign interactive coding challenges:

| Tool | Purpose |
|---|---|
| `open_code_editor` | Compiled/interpreted code challenge with sandboxed execution |
| `open_html_editor` | HTML + CSS challenge with live client-side iframe preview |

---

## `open_code_editor`

### Agent input schema
```json
{
  "prompt":       "Write a function that returns the nth Fibonacci number.",
  "language":     "python",
  "starter_code": "def fib(n):\n    pass  # your code here"
}
```

**`language` values**: `python` | `python-ml` | `javascript` | `typescript` | `c` | `cpp` | `rust`

### Runtime registry (backend)

Each runtime maps to an execution strategy:

| Runtime | Run command | Compiled? | Notes |
|---|---|---|---|
| `python` | `python3 <file>` | No | Standard stdlib only |
| `python-ml` | `uv run --with numpy,pandas,scikit-learn,matplotlib,torch <file>` | No | uv caches packages in `~/.cache/uv`; first run may be slow |
| `javascript` | `node <file>` | No | |
| `typescript` | `tsc` → `node <js>` | Yes (tsc) | Compile outside bwrap, run binary inside |
| `c` | `gcc -O2 -o <bin> <file>` → `<bin>` | Yes | |
| `cpp` | `g++ -O2 -std=c++17 -o <bin> <file>` → `<bin>` | Yes | |
| `rust` | `rustc -o <bin> <file>` → `<bin>` | Yes | |

### What the agent receives on Submit
```
Student submitted python code (runtime: python-ml):

```python
import numpy as np
arr = np.array([1, 2, 3])
print(arr.mean())
```

stdout:
2.0

stderr: (none)
exit code: 0  |  elapsed: 312ms
```

If the code failed to compile or crashed, the agent sees the stderr/exit code and can help debug.

---

## `open_html_editor`

### Agent input schema
```json
{
  "prompt":       "Style the heading so it's centered and red.",
  "starter_html": "<h1 class=\"title\">Hello</h1>",
  "starter_css":  ".title { }"
}
```

No backend execution — the iframe preview is entirely client-side (`srcdoc`).

### What the agent receives on Submit
```
Student submitted HTML/CSS:

HTML:
<h1 class="title">Hello</h1>

CSS:
.title { color: red; text-align: center; }
```

---

## UI Layout

### Code editor overlay
```
┌─────────────────────────────────────────────────────────┐
│  Challenge prompt (read-only)                           │
├──────────────────────────┬──────────────────────────────┤
│                          │  OUTPUT                      │
│   CodeMirror editor      │  stdout  (white)             │
│                          │  stderr  (amber)             │
│   (syntax highlighting   │  compile errors (amber)      │
│    for selected lang)    │  ✓ exit:0  312ms             │
├──────────────────────────┴──────────────────────────────┤
│  [Run ▶]  [Reset ↺]  [Submit ✓]  [Cancel ✗]  lang      │
└─────────────────────────────────────────────────────────┘
```
Mobile: editor stacked above output, controls fixed at bottom.

**Reset button**: reverts editor content to `starter_code` (or empty string if none was provided). Clears the output panel. Does not require confirmation.

**Submit**: locked until at least one `code_done` event has been received (any exit code — even compile failure counts, so the agent can help debug). Submit sends `{ code, stdout, stderr, exit_code }`.

### HTML/CSS editor overlay
```
┌─────────────────────────────────────────────────────────┐
│  Challenge prompt (read-only)                           │
├──────────────────────────┬──────────────────────────────┤
│  [HTML] [CSS]  tabs      │                              │
│  ─────────────────────  │   <iframe srcdoc preview>   │
│   CodeMirror pane        │   sandbox="allow-scripts"   │
│                          │                              │
├──────────────────────────┴──────────────────────────────┤
│  [Run ▶]  [Reset ↺]  [Submit ✓]  [Cancel ✗]           │
└─────────────────────────────────────────────────────────┘
```
Preview updates **only on Run click** (not live). This avoids breaking a partially-typed document mid-render.

**Reset**: reverts both HTML and CSS editors to their starter values.

---

## Streaming architecture

### Decision: stream execution output via existing WebSocket

Execution stdout/stderr is streamed to the client through the **existing lesson WebSocket** as new event types:

| Event | Direction | Payload |
|---|---|---|
| `run_code` | Client → Server | `{ invocation_id, code, runtime }` |
| `code_stdout` | Server → Client | `{ invocation_id, data }` |
| `code_stderr` | Server → Client | `{ invocation_id, data }` |
| `code_done` | Server → Client | `{ invocation_id, exit_code, elapsed_ms }` |
| `code_error` | Server → Client | `{ invocation_id, message }` (bwrap/compile failed to start) |

`invocation_id` is the same ID issued with `open_code_editor`, tying output to the correct editor overlay.

The agent thread is blocked on `threading.Event.wait()` in the thread pool while the event loop remains free to handle `run_code` and stream responses. This is the same pattern used by the sketchpad tool.

### Alternative considered: SSE (Server-Sent Events)

A dedicated `POST /api/execute/stream` endpoint returning `StreamingResponse` with `text/event-stream` would also work. The client would open a `fetch()` with `ReadableStream` for each run.

**Reasons we chose WS instead:**
- The lesson session is already WS; avoiding a second connection simplifies auth and session context.
- `invocation_id` routing is natural in the existing event dispatch loop.
- SSE requires either CORS handling or a separate proxy route.

**When to revisit**: if the WS event loop becomes congested with audio + execution events simultaneously (e.g., streaming large stdout while TTS audio is also in flight), an isolated SSE channel would decouple the streams cleanly. SSE also supports `Last-Event-ID` for resumption.

### Alternative considered: client-side execution (JS only)

For `javascript` language, `new Function(code)()` in a try/catch can run code entirely in-browser with no backend roundtrip. Pros: zero latency, no sandbox setup. Cons: no stdout capture (would need `console.log` override), same origin as the app (XSS risk), inconsistent with other languages. We chose uniform server-side execution for all `open_code_editor` languages to keep the mental model simple.

---

## Sandboxing

### Decision: AST check + rlimit + bubblewrap (three independent layers)

1. **AST check (Python only)**: Walk the AST before execution; block `import os`, `import sys`, `import subprocess`, `import socket`, `open()`, `eval`, `exec`, `__import__`. Raises a `SecurityError` before any code runs.

2. **`resource` limits**: Set `RLIMIT_CPU` (5 s) and `RLIMIT_AS` (256 MB) on the subprocess via `preexec_fn`.

3. **bubblewrap** (`bwrap`): Namespace sandbox. Core mounts:
   - `--ro-bind /usr /usr`, `/lib`, `/lib64`, `/bin` — interpreter/stdlib
   - `--tmpfs /tmp` — isolated scratch space
   - `--proc /proc`, `--dev /dev` — required by Python
   - `--unshare-all` — no network, no IPC, no user ns leakage
   - `--die-with-parent` — child dies if server crashes

   For `python-ml`: additionally `--ro-bind ~/.cache/uv ~/.cache/uv` and `--ro-bind ~/.local/bin/uv ~/.local/bin/uv` so uv can resolve cached packages. First-use warm-up (package download) runs **outside** bubblewrap with a separate timeout (60 s), then the actual execution runs inside.

   For compiled binaries (C/C++/Rust/TypeScript): compile outside bubblewrap, then bind-mount only the resulting binary into an otherwise empty sandbox.

### Wall-clock timeouts

| Phase | Timeout |
|---|---|
| Compilation (gcc, g++, rustc, tsc) | 30 s |
| Package download (python-ml first use) | 60 s |
| Execution inside bwrap | 10 s |

### Alternative considered: Docker

Full container isolation. Pros: strongest isolation, reproducible environments. Cons: requires Docker daemon, much higher spawn latency (~500 ms+), complex image management. Overkill for a single-user teaching app.

### Alternative considered: Piston API

Open-source code execution engine (self-hostable or public at `emkc.org`). Pros: zero server-side setup, 100+ languages. Cons: external network dependency, harder to customise runtime environments (e.g. `python-ml` packages). Worth revisiting if the app becomes multi-user or if bubblewrap setup proves fragile.

---

## Editor library

**Decision**: `@uiw/react-codemirror` (React wrapper for CodeMirror 6) + per-language packs.

Packages to install:
```
@uiw/react-codemirror
@codemirror/lang-python
@codemirror/lang-javascript   # covers JS and TS
@codemirror/lang-cpp          # covers C and C++
@codemirror/lang-rust
@codemirror/lang-html
@codemirror/lang-css
@uiw/codemirror-theme-vscode  # or one-dark
```

**Alternative considered**: Monaco Editor (`@monaco-editor/react`). Full VS Code engine — IntelliSense, multi-cursor, etc. Bundle cost ~2 MB gzipped. Ruled out due to mobile performance and load time. Worth revisiting if the app evolves toward a desktop-first experience.
