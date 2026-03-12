# Client-Server Architecture: Open Design Questions

This file tracks questions I need Richard to answer before we finalize the design
and begin implementation. Richard will answer inline; I'll update the design doc once
we've resolved everything here.

---

## Q1: Frontend Server Role

Is the frontend server purely a **pass-through proxy / BFF** (routing, session tokens,
response formatting), or should it carry any domain logic of its own?

Examples of domain logic it *could* own:
- Deciding which backend endpoint to call based on client state
- Composing responses from multiple backend calls before sending to client
- Rate limiting per user

> **Richard's answer:**
> We should be planning for security and portability. If handling front end domain logic doesn't pose risks in either of those areas of concern, it seems "simpler" (shorter chain). If the pass-through / BFF proxy will be sufficiently manageable now and provide material advantages, we should move in that direction. 

---

## Q2: PDF Upload Path

When the user selects a PDF to decompose into a lesson, where does the file go?

Options:
- **A**: Client → Frontend Server → Backend (frontend proxies the upload)
- **B**: Client uploads directly to Backend (frontend server just authorizes the request)

Option A keeps the backend hidden from clients entirely.
Option B is simpler and avoids large file proxying overhead.

> **Richard's answer:**
> Option B. The overhead isn't very problematic because of the infrequency of this operation.

---

## Q3: Lesson / User Schema Scope

Once we add user accounts, lessons will belong to a user. Should we:

- **A**: Design the DB schema with `user_id` foreign keys from the start (even though
  auth isn't implemented yet), so we don't have to migrate later?
- **B**: Stub user ownership and add it when we implement auth?

I lean toward A — it's a small upfront cost and avoids a painful migration.

> **Richard's answer:**
> A, without a doubt

---

## Q4: Port Assignments

Any preferences, or shall I use these defaults?

| Interface | Default port |
|-----------|-------------|
| Client → Frontend Server | `8000` |
| Frontend Server → Backend | `8001` |

> **Richard's answer:**
> Defaults are absolutely fine for now. Down the road we may want more dynamic solutions but for now, simple is best.

---

## Q5: TTS on Client vs Server — Confirming Split

Your notes lean toward keeping STT (whisper-base) and TTS (kokoro) on the client,
transmitting only text. Confirming this is the intended approach for the prototype,
with the understanding that we can migrate TTS/STT to the server later if needed?

Side effect: the client still needs Python + CUDA (or CPU inference) dependencies.
A future "thin client" (browser/mobile) would require server-side TTS.

> **Richard's answer:**
> Two options: edge inference (e.g. whisper-cpp) or (and I'm now leaning in this direction) migrate to server-side to avoid side effect. Send audio data to clients via stream or otherwise.

---

## Q6: Streaming Protocol

For real-time LLM text streaming and tool-call events (show_slide, open_sketchpad,
section_advanced, etc.), I'm proposing:

- **WebSocket** between client ↔ frontend server, and between frontend ↔ backend
- **REST** for stateless operations (upload, load lesson, settings)

Any objection to WebSocket as the streaming transport? Alternatives would be
Server-Sent Events (SSE, server→client only, simpler) or long-polling (legacy).

> **Richard's answer:**
> No immediate objections. Consider whether SSE offers benefit (security? UX?). I'm open to follow-up discussion if necessary 

---

## Q7: Sketchpad Interaction — Async Resume

Currently the agent thread blocks on a `threading.Event` while the user draws.
Across a network, the backend agent turn must park and resume when the client finishes.

Proposed approach:
1. Backend sends `{"event": "open_sketchpad", "prompt": "...", "invocation_id": "uuid"}` over WebSocket
2. Client opens canvas, user draws, client POSTs result to `POST /tool_result/{invocation_id}`
3. Backend resumes the suspended agent coroutine via an `asyncio.Event`

Does this approach make sense to you, or do you have a different mental model?

> **Richard's answer:**
> That makes complete sense.

---

## Q8: Separate AI and DB Backend Processes?

Your notes mention this may eventually merit separate servers. For the prototype,
I'd keep them in one FastAPI process but in separate routers (`routers/ai.py`,
`routers/lessons.py`) so splitting later is just a deploy change, not a refactor.

Agree?

> **Richard's answer:**
> Agree.
