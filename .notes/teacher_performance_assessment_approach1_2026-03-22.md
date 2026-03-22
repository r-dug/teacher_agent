# Teacher Agent Performance Assessment — Approach 1 (Realtime Relay)
**Date:** 2026-03-22
**Architecture:** Realtime as VAD+STT relay only (`create_response: False`), chained Anthropic/Sonnet handles all reasoning/TTS/curriculum
**Lesson:** A Practical Guide to Building Agents
**Section:** 3 / 20 (Agent Design Foundations: Core Components)
**Turns observed:** ~8

---

## Massive Improvements vs Previous Assessment

### 1. Curriculum adherence — fixed
Section 3 content is now consistently referenced. Teacher cited "page seven" in the opening question and stayed anchored to core components throughout. No drifting into generic agent discussion.

### 2. Question quality — fixed
Tight, page-referenced, structured Socratic questions:
- "What are the three core components, and what is the role of each in one concise sentence each?"
- Followed up with a concrete application (quiz scenario mapping)
- Then escalated to guardrail encoding

This is the same quality as chained mode — because it IS the chained teacher.

### 3. No "active response in progress" errors
The race condition is eliminated. `create_response: False` means the realtime model never generates responses, so the error can't occur.

### 4. Transcription quality — dramatically better
Clean student audio → clean transcription. The first student turn came back verbatim. The Approach 1 architecture doesn't fix STT quality per se, but it gives a better model (Sonnet) to handle any imperfect transcripts.

---

## Remaining Problem: Teacher Self-Interruption

### What happens
1. Teacher responds → TTS audio plays through laptop speakers
2. The open microphone picks up the speaker audio
3. Realtime VAD fires on it, producing a garbled transcript ("Danes.", "Ok.", "Mon")
4. This gets dispatched to the chained teacher as a new user turn
5. Teacher processes the garbage while the real student has no opportunity to respond
6. Wastes one turn per teacher response, derails conversation flow

### Example sequence seen
```
Teacher: "...Would you like to try a couple of example student answers...?"
[TTS plays through speakers]
Microphone captures: "Danes."
Teacher processes: [Thinking...] → responds to "Danes." nonsensically
```

### Root cause
The realtime session microphone is active with VAD enabled at all times — even while TTS is playing. Browser echo cancellation may not be activated.

### Fix options (in priority order)

**Option A — Enable browser echo cancellation (easiest, low risk)**
In `recorder.ts`, the `getUserMedia` call should request `echoCancellation: true`, `noiseSuppression: true`, and `autoGainControl: true`. Check if these are already set. If not, adding them may reduce or eliminate the feedback loop.

**Option B — Mute mic while TTS is playing (robust fix)**
- Client-side: when `audio_chunk` events are received (TTS playing), pause sending audio to the realtime stream
- Or: send a `input_audio_buffer.clear` to the realtime session when TTS starts playing, and a signal when it ends
- This requires client-server coordination: the client needs to know when TTS is done

**Option C — Minimum-length filter (easy guard)**
In `_dispatch_realtime_transcript_to_teacher`, skip transcripts shorter than N words (e.g., < 3 words). "Danes.", "Ok.", "Mon" would be filtered. This won't catch longer garbage but handles single-word bleed-through.

**Option D — Turn-guard: discard transcript if teacher turn is too recent (medium)**
If the last teacher response was less than ~2 seconds ago, the transcript is likely feedback — discard it.

---

## Section Advancement

Section did NOT advance to 4/20 during this session. The teacher kept probing the same topic in depth (which is correct behavior — it should only advance when key concepts are confirmed). The self-interrupt problem consumed turns that should have been the student demonstrating mastery.

If Option A/B is implemented, section advancement should occur naturally once the student can actually respond cleanly.

---

## Summary Score

| Dimension | Previous Realtime | Approach 1 (this session) |
|---|---|---|
| Question quality | ⭐ | ⭐⭐⭐⭐⭐ |
| Curriculum adherence | ⭐ | ⭐⭐⭐⭐⭐ |
| Handling garbled input | ⭐ | ⭐⭐⭐ (better model, still no filtering) |
| Response length for voice | ⭐⭐ | ⭐⭐⭐⭐ |
| Self-interruption (new issue) | N/A | ⭐⭐ |
| Race condition errors | ⭐⭐ | ⭐⭐⭐⭐⭐ |

**Overall: Approach 1 is a major step forward. The one remaining issue is mic/speaker feedback causing phantom user turns. Fix: echo cancellation or mic muting during TTS playback.**

---

## Recommended Next Fix

Check `recorder.ts` — if echo cancellation is not already enabled in `getUserMedia`, add it. If it is and still bleeding through, implement Option B (pause mic input while TTS plays).
