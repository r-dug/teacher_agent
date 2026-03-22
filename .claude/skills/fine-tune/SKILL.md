---
name: fine-tune
description: Load fine-tuning reference context and help design or implement fine-tuning pipelines for this application. Use when building training datasets, running fine-tuning jobs, or implementing distillation.
allowed-tools: Read, Bash, Write, Edit
---

Before proceeding with $ARGUMENTS, read the following reference docs:

- `.notes/model_optimization/fine_tuning/overview.md`
- `.notes/model_optimization/fine_tuning/supervised.md`
- `.notes/model_optimization/fine_tuning/reinforcement.md`
- `.notes/model_optimization/fine_tuning/direct_preference.md`

## Fine-tuning targets in this application (priority order)

### 1. Tool use selection (teacher agent) — highest value
The teacher agent currently selects tools (sketchpad, code editor, camera, etc.) based on
description alone. This is noisy. Fine-tuning would make choices consistent and content-aware.
- Method: **DPO or RFT** — needs preference signal (good/bad tool choice pairs)
- Data source: log tool use events + manual or LLM-graded labels

### 2. Distillation — cost reduction
Run Sonnet/Opus to generate high-quality teaching turns, distill into Haiku or fine-tuned
gpt-4o-mini. Use OpenAI stored completions to capture production turns as training data.
- Method: **SFT** on Sonnet-generated outputs
- Target model: gpt-4o-mini or Haiku

### 3. Teacher builder — episodic context quality
Teacher builder summaries directly affect all subsequent teaching sessions. Bad summaries
compound. Fine-tuning here has high leverage.
- Method: **SFT** on good summaries vs bad summaries
- Data source: manually curated or LLM-graded summary pairs

## General rules

- Always build evals (`/evals`) before fine-tuning — you need a baseline to know if it worked.
- Training data format for OpenAI fine-tuning: JSONL with `{"messages": [...]}` structure.
- Gate Anthropic fine-tuning features on model name (Haiku ≠ Sonnet capabilities).

If $ARGUMENTS is empty, ask the user which agent or behavior they want to fine-tune before proceeding.
