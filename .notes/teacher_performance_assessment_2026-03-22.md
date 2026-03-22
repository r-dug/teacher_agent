# Teacher Agent Performance Assessment
**Date:** 2026-03-22
**Lesson:** A Practical Guide to Building Agents
**Section:** 2–3 / 20
**Modes tested:** Chained (STT→LLM→TTS) and Realtime (OpenAI audio in/out)
**Turns:** 10

---

## What Worked Well

### Chained mode (turns 1–2) — strong
- Teacher opened with a tight, page-anchored question and a clear constraint ("in one sentence")
- Followed the student profile accurately — challenged directly rather than re-explaining
- Page citations were precise and useful
- Curriculum advanced correctly (section 2 → 3)
- Appropriate difficulty calibration for a fast-moving student

### Student profile handoff
- The incoming teacher note was well-structured and actually influenced early pacing
- The "probe for completeness rather than re-teach" instruction was visibly applied

---

## Problems Observed

### 1. STT fails badly under speaker-playback conditions (critical)
When TTS audio plays through speakers and the mic picks it up (speaker-to-mic feedback), Whisper produces heavily corrupted transcriptions:
- "optimistic execution model" → "octopus execution model"
- "handing off to a human" → "handing out"
- "Among the computational of the orchestra" (for "orchestrator")
- Final turn produced "They're really rude." from ambient/garbled audio

**The teacher never flagged any of these as unclear.** It just responded to nonsense as if it were a legitimate student answer.

### 2. Teacher too agreeable when input is garbled
Responses like "Exactly!", "Great topic!", "Absolutely!" even when the student input was incoherent. The teacher should detect low-confidence or nonsensical input and ask for clarification rather than validate it.

### 3. Curriculum completely abandoned in realtime mode
In chained mode the teacher followed the curriculum section-by-section with page references. In realtime mode the teacher drifted into generic agent design discussion with no page citations, no section progression, and no reference to the actual source document. The lesson stuck at section 3/20 for the entire realtime portion.

### 4. Realtime responses too long for audio
Teacher responses in realtime were 3–5 sentences. For voice interaction, 1–2 sentences maximum is more natural. Long audio responses are hard to interrupt and feel like a monologue.

### 5. "Active response" race condition in realtime
Hit `Error: Conversation already has an active response in progress` — the student spoke before the teacher finished. The system should queue or debounce rather than error.

### 6. Human handoff question mishandled
When asked "when should you hand off to a human?" the teacher answered about handing off to *tools* — a completely wrong interpretation. This is a factual error tied to poor STT, but also shows the teacher doesn't sanity-check its own answer against the curriculum.

---

## Improvement Recommendations

### High priority
1. **Add STT confidence gating in realtime mode**: If the transcription looks semantically incoherent or very short (< 4 words, or perplexity too high), respond with "Sorry, I didn't catch that — could you say that again?" rather than answering.

2. **Curriculum anchoring in realtime**: The realtime teacher should still track which section it's on and reference source pages. Currently the section context is passed at session start but seems to be lost or ignored during realtime turns.

3. **Shorten realtime responses**: Add an instruction to the realtime system prompt to keep voice responses to 1–2 sentences. Longer explanations should prompt the student first ("want me to go deeper on that?").

### Medium priority
4. **Race condition handling**: Queue student audio if a response is already in progress rather than throwing an error. The error shows in the UI status bar which is distracting.

5. **Factual self-check**: When the teacher's response doesn't match the curriculum section's key concepts, it should be caught. A simple post-generation check against expected section content would help.

### Low priority
6. **Speaker-mic feedback**: This is partly a test-environment artifact (TTS audio feeding back into the mic). In real use the student uses headphones. But the system should still be robust to garbled input rather than confidently answering nonsense.

---

## Summary Score

| Dimension | Chained mode | Realtime mode |
|---|---|---|
| Question quality | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| Curriculum adherence | ⭐⭐⭐⭐⭐ | ⭐ |
| Handling unclear input | N/A | ⭐ |
| Response length for voice | N/A | ⭐⭐ |
| Student profile use | ⭐⭐⭐⭐ | ⭐⭐ |

**Overall: Chained mode is production-ready. Realtime mode needs significant work before it's usable.**
