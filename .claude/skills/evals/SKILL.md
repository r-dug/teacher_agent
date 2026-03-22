---
name: evals
description: Load eval reference context and help design or implement evals for this application. Use when building eval harnesses, graders, or measurement tooling.
allowed-tools: Read, Bash, Write, Edit
---

Before proceeding with $ARGUMENTS, read the following reference docs:

- `.notes/model_optimization/evals/Working_with_evals.md`
- `.notes/model_optimization/evals/API_reference.md`

## Context for this application

The goal of evals here is to measure agent behavior quality — not just correctness of output, but:

- **Teacher agent**: Did the student engage? Did the section advance appropriately? Were tool choices (sketchpad, code editor, etc.) well-matched to the content?
- **Teacher builder**: Did the generated system prompt accurately reflect the session? Does it improve subsequent teaching turns?
- **Lesson planner**: Does the lesson plan cover key concepts from the source document? Are sections well-scoped?

## Key signals already available

- Teacher builder summaries — implicit encoding of whether a session went well
- Section advancement events (`section_advanced`, `curriculum_complete`) — measurable outcomes
- Tool use events — can be compared against "ideal" tool choices for given content types

## Eval approach for this app

Prefer **model-graded evals** (LLM-as-judge) over exact match for teaching quality.
Use **stored completions** to capture real production turns as eval inputs.
Write graders that score on rubrics (e.g. "did the agent check for understanding before advancing?").

If $ARGUMENTS is empty, ask the user what agent or behavior they want to evaluate before proceeding.
