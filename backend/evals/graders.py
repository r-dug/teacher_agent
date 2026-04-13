"""Graders for the teaching agent eval harness.

Each grader returns a GradeResult indicating pass/fail, a 0-1 score,
and a human-readable detail string.

Deterministic graders (voice_rules, conciseness, engagement, tool_use)
are free and fast.  The accuracy grader requires an LLM judge call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class GradeResult:
    name: str
    passed: bool
    score: float  # 0.0 – 1.0
    detail: str


# ── Deterministic graders ─────────────────────────────────────────────────────


def grade_voice_rules(text: str) -> GradeResult:
    """Check for forbidden formatting in voice output."""
    violations: list[str] = []
    if re.search(r"\*\*", text):
        violations.append("markdown bold (**)")
    if re.search(r"^[\s]*[-*]\s", text, re.MULTILINE):
        violations.append("bullet list")
    if re.search(r"^\s*\d+\.\s", text, re.MULTILINE):
        violations.append("numbered list")
    if "—" in text:
        violations.append("em-dash")
    if re.search(r"```", text):
        violations.append("code block")
    if re.search(r"\[.*?\]\(.*?\)", text):
        violations.append("markdown link")
    if re.search(r"^#+\s", text, re.MULTILINE):
        violations.append("markdown heading")

    passed = len(violations) == 0
    return GradeResult(
        "voice_rules",
        passed,
        1.0 if passed else 0.0,
        f"Violations: {', '.join(violations)}" if violations else "Clean",
    )


def grade_conciseness(text: str) -> GradeResult:
    """Check that the response is 2-4 sentences (before a question or tool call)."""
    sentences = [s.strip() for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()]
    n = len(sentences)
    if n <= 4:
        return GradeResult("conciseness", True, 1.0, f"{n} sentences")
    elif n <= 6:
        return GradeResult("conciseness", False, 0.5, f"{n} sentences (target: 2-4)")
    else:
        return GradeResult("conciseness", False, 0.0, f"{n} sentences (target: 2-4, got {n})")


def grade_engagement(text: str, tool_calls: list[dict]) -> GradeResult:
    """Check that the response asks a question or uses a tool (not pure lecture)."""
    has_question = "?" in text
    has_tool = len(tool_calls) > 0
    engaged = has_question or has_tool

    detail_parts: list[str] = []
    if has_question:
        detail_parts.append("asks question")
    if has_tool:
        names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
        detail_parts.append(f"uses tool ({', '.join(names)})")
    if not engaged:
        detail_parts.append("pure lecture — no question or tool call")

    return GradeResult("engagement", engaged, 1.0 if engaged else 0.0, "; ".join(detail_parts))


def grade_tool_use(tool_calls: list[dict], expected: str | None) -> GradeResult:
    """Check if the model called the expected tool (if one is specified)."""
    if expected is None:
        return GradeResult("tool_use", True, 1.0, "no expectation set")

    called = [tc.get("function", {}).get("name", "") for tc in tool_calls]
    if expected in called:
        return GradeResult("tool_use", True, 1.0, f"correctly called {expected}")
    elif called:
        return GradeResult("tool_use", False, 0.3, f"called {', '.join(called)} instead of {expected}")
    else:
        return GradeResult("tool_use", False, 0.0, f"no tool call — expected {expected}")


# ── LLM judge grader ──────────────────────────────────────────────────────────

ACCURACY_JUDGE_PROMPT = """\
You are evaluating a teaching agent's response for factual accuracy.

SOURCE MATERIAL:
{section_content}

TEACHER'S RESPONSE:
{response}

Rate the factual accuracy on this scale:
- "accurate": All claims are supported by the source material
- "minor_error": Small inaccuracy that wouldn't mislead a student
- "major_error": Significant factual mistake or fabrication

Respond with ONLY one of: accurate, minor_error, major_error"""

ACCURACY_LABELS = {"accurate": 1.0, "minor_error": 0.5, "major_error": 0.0}


def grade_accuracy(
    text: str,
    section_content: str,
    judge_client,
    judge_model: str,
) -> GradeResult:
    """Use an LLM judge to evaluate factual accuracy against source material."""
    prompt = ACCURACY_JUDGE_PROMPT.format(section_content=section_content, response=text)
    try:
        response = judge_client.chat.completions.create(
            model=judge_model,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=16,
            temperature=0,
        )
        label = response.choices[0].message.content.strip().lower()
        # Fuzzy match in case the model is wordy
        matched_label = None
        for key in ACCURACY_LABELS:
            if key in label:
                matched_label = key
                break
        if matched_label is None:
            return GradeResult("accuracy", False, 0.0, f"judge returned unparseable: {label!r}")

        score = ACCURACY_LABELS[matched_label]
        passed = matched_label == "accurate"
        return GradeResult("accuracy", passed, score, matched_label)

    except Exception as e:
        return GradeResult("accuracy", False, 0.0, f"judge error: {e}")
