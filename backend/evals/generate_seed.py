#!/usr/bin/env python3
"""Generate seed eval cases using make_teaching_prompt().

Run once to create backend/evals/cases/seed.jsonl:
  uv run python -m backend.evals.generate_seed
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.services.agents.prompts.teaching import make_teaching_prompt

OUT = Path(__file__).parent / "cases" / "seed.jsonl"

# ── Realistic section data ────────────────────────────────────────────────────

JAPANESE_SECTIONS = [
    {
        "title": "Greetings and Self-Introduction",
        "content": (
            "Japanese greetings vary by time of day and formality. おはようございます (ohayou gozaimasu) "
            "is used in the morning, こんにちは (konnichiwa) during the day, and こんばんは (konbanwa) "
            "in the evening. Self-introductions follow a set pattern: はじめまして (hajimemashite, "
            "'nice to meet you'), then your name with です (desu), and よろしくおねがいします "
            "(yoroshiku onegaishimasu, 'please treat me well'). Bowing accompanies greetings; "
            "the deeper the bow, the more formal the situation."
        ),
        "key_concepts": [
            "Use time-appropriate greetings",
            "Self-introduction pattern (hajimemashite → name desu → yoroshiku)",
            "Formal vs informal register",
        ],
        "page_start": 1,
        "page_end": 3,
    },
    {
        "title": "Particles (は, が, を, に, で)",
        "content": (
            "Japanese particles are postpositional markers that indicate grammatical relationships. "
            "は (wa) marks the topic of the sentence — what you are talking about. "
            "が (ga) marks the grammatical subject — who or what performs the action. "
            "を (wo/o) marks the direct object of a transitive verb. "
            "に (ni) indicates direction, time, location of existence, or indirect object. "
            "で (de) marks the location where an action takes place, or the means/instrument used. "
            "Example: 私は学校で本を読みます (Watashi wa gakkou de hon wo yomimasu) — "
            "'I read a book at school.' Here, 私 is the topic, 学校 is the location of the action, "
            "and 本 is the direct object."
        ),
        "key_concepts": [
            "Use は as topic marker",
            "Distinguish は (topic) from が (subject)",
            "Use を for direct objects",
            "Distinguish に (existence) vs で (action location)",
            "Use の to connect nouns",
        ],
        "page_start": 4,
        "page_end": 7,
    },
    {
        "title": "Verb Conjugation (ます form)",
        "content": (
            "Japanese verbs in polite speech use the ます (masu) form. Verbs are divided into "
            "three groups: Group 1 (godan/u-verbs), Group 2 (ichidan/ru-verbs), and Group 3 "
            "(irregular: する and 来る). To conjugate Group 2 verbs, drop る and add ます: "
            "食べる → 食べます (tabemasu, 'to eat'). Group 1 verbs change the final vowel sound "
            "to い (i) before adding ます: 書く → 書きます (kakimasu, 'to write'). "
            "Negative form: replace ます with ません. Past tense: replace ます with ました. "
            "Past negative: ませんでした."
        ),
        "key_concepts": [
            "Identify verb groups (Group 1, 2, 3)",
            "Conjugate Group 2 verbs to ます form",
            "Conjugate Group 1 verbs to ます form",
            "Form negative, past, and past negative",
        ],
        "page_start": 8,
        "page_end": 12,
    },
]

PYTHON_SECTIONS = [
    {
        "title": "Lists and List Comprehensions",
        "content": (
            "Python lists are ordered, mutable sequences. Create with square brackets: "
            "numbers = [1, 2, 3]. Access elements by index (zero-based): numbers[0] returns 1. "
            "List comprehensions provide a concise way to create lists: "
            "[x**2 for x in range(5)] produces [0, 1, 4, 9, 16]. "
            "Add conditions: [x for x in range(10) if x % 2 == 0] gives even numbers. "
            "Nested comprehensions: [[row[i] for row in matrix] for i in range(3)] transposes "
            "a matrix. Common methods: append(), extend(), insert(), pop(), sort(), reverse()."
        ),
        "key_concepts": [
            "Create and index lists",
            "Write basic list comprehensions",
            "Add conditions to list comprehensions",
            "Use common list methods",
        ],
        "page_start": 22,
        "page_end": 26,
    },
]

MUSIC_SECTIONS = [
    {
        "title": "Major and Minor Scales",
        "content": (
            "A major scale follows the pattern: whole, whole, half, whole, whole, whole, half "
            "(W-W-H-W-W-W-H). Starting from C: C-D-E-F-G-A-B-C. The natural minor scale "
            "pattern is: W-H-W-W-H-W-W. Starting from A: A-B-C-D-E-F-G-A. Every major key "
            "has a relative minor that shares the same key signature, found a minor third below "
            "the major tonic. C major's relative minor is A minor. The harmonic minor raises "
            "the seventh degree by a half step (G becomes G# in A harmonic minor), creating "
            "the leading tone needed for a strong V-I cadence."
        ),
        "key_concepts": [
            "Build a major scale from the W-W-H-W-W-W-H pattern",
            "Build a natural minor scale",
            "Find relative minor from any major key",
            "Understand harmonic minor and the raised seventh",
        ],
        "page_start": 8,
        "page_end": 11,
    },
]

TITLE = "Basic Japanese Grammar"
ALL_JP_SECTIONS = JAPANESE_SECTIONS


def _make_system(sections, idx, title=TITLE, goal=None, tasks=None):
    return make_teaching_prompt(title, sections, idx, lesson_goal=goal, current_tasks=tasks)


def _case(id, tags, system_prompt, messages, section_content=None, expected_behaviors=None):
    c = {
        "id": id,
        "tags": tags,
        "system_prompt": system_prompt,
        "messages": messages,
    }
    if section_content:
        c["section_content"] = section_content
    if expected_behaviors:
        c["expected_behaviors"] = expected_behaviors
    return c


def generate_cases() -> list[dict]:
    cases = []

    # ── Voice rules cases (5) ─────────────────────────────────────────────
    # These use section content that tempts the model into markdown

    # 1. Particle list — tempts bullet points
    cases.append(_case(
        "voice-rules-01", ["voice_rules"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn basic particles"),
        [{"role": "user", "content": "Can you list all the particles and what they do?"}],
        section_content=ALL_JP_SECTIONS[1]["content"],
    ))

    # 2. Code content — tempts code blocks
    cases.append(_case(
        "voice-rules-02", ["voice_rules"],
        _make_system(PYTHON_SECTIONS, 0, title="Introduction to Python", goal="Learn Python basics"),
        [{"role": "user", "content": "Show me how list comprehensions work with some examples."}],
        section_content=PYTHON_SECTIONS[0]["content"],
    ))

    # 3. Step-by-step process — tempts numbered lists
    cases.append(_case(
        "voice-rules-03", ["voice_rules"],
        _make_system(ALL_JP_SECTIONS, 2, goal="Learn verb conjugation"),
        [{"role": "user", "content": "Walk me through the steps to conjugate a Group 1 verb."}],
        section_content=ALL_JP_SECTIONS[2]["content"],
    ))

    # 4. Comparison — tempts tables/bold
    cases.append(_case(
        "voice-rules-04", ["voice_rules"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Understand particles"),
        [{"role": "user", "content": "What's the difference between に and で? Can you compare them?"}],
        section_content=ALL_JP_SECTIONS[1]["content"],
    ))

    # 5. Music theory — tempts notation formatting
    cases.append(_case(
        "voice-rules-05", ["voice_rules"],
        _make_system(MUSIC_SECTIONS, 0, title="Music Theory Fundamentals", goal="Learn scales"),
        [{"role": "user", "content": "What's the pattern for building a major scale? List the intervals."}],
        section_content=MUSIC_SECTIONS[0]["content"],
    ))

    # ── Conciseness cases (3) ─────────────────────────────────────────────
    # Broad questions that tempt long responses

    cases.append(_case(
        "concise-01", ["conciseness"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles"),
        [{"role": "user", "content": "Explain everything about Japanese particles to me."}],
    ))

    cases.append(_case(
        "concise-02", ["conciseness"],
        _make_system(ALL_JP_SECTIONS, 2, goal="Master verb forms"),
        [{"role": "user", "content": "I don't understand any of the verb conjugation rules. Can you go over all of them?"}],
    ))

    cases.append(_case(
        "concise-03", ["conciseness"],
        _make_system(ALL_JP_SECTIONS, 0, goal="Learn Japanese"),
        [{"role": "user", "content": "Tell me about Japanese greetings, self-introductions, and all the formality levels."}],
    ))

    # ── Tool use cases (5) ─────────────────────────────────────────────────

    # Should show slides for figures
    cases.append(_case(
        "tool-show-slide-01", ["tool_use"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles"),
        [{"role": "user", "content": "Is there a chart or diagram in the textbook showing the particles?"}],
        expected_behaviors={"should_use_tool": "show_slide"},
    ))

    # Should use sketchpad for writing practice
    cases.append(_case(
        "tool-sketchpad-01", ["tool_use"],
        _make_system(ALL_JP_SECTIONS, 0, goal="Learn greetings"),
        [
            {"role": "user", "content": "Please begin teaching."},
            {"role": "assistant", "content": "Welcome! Let's start with Japanese greetings. The most common daytime greeting is konnichiwa, written as こんにちは. Can you try writing it?"},
            {"role": "user", "content": "Sure, I'd like to try writing it."},
        ],
        expected_behaviors={"should_use_tool": "open_sketchpad"},
    ))

    # Should mark task complete when student demonstrates understanding
    tasks_particles = [
        {"concept": "Use は as topic marker", "status": "pending"},
        {"concept": "Distinguish は (topic) from が (subject)", "status": "pending"},
        {"concept": "Use を for direct objects", "status": "pending"},
        {"concept": "Distinguish に (existence) vs で (action location)", "status": "pending"},
        {"concept": "Use の to connect nouns", "status": "pending"},
    ]
    cases.append(_case(
        "tool-mark-task-01", ["tool_use"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles", tasks=tasks_particles),
        [
            {"role": "assistant", "content": "Let's talk about the particle は. It marks the topic of a sentence. For example, 私は学生です means 'I am a student,' where 私 is the topic. Can you make a sentence using は?"},
            {"role": "user", "content": "猫は大きいです。 The cat is big."},
        ],
        expected_behaviors={"should_use_tool": "mark_task_complete"},
    ))

    # Should use start_timer for a drill
    cases.append(_case(
        "tool-timer-01", ["tool_use"],
        _make_system(ALL_JP_SECTIONS, 2, goal="Build fluency with verbs"),
        [
            {"role": "assistant", "content": "You've got the conjugation patterns down. Let's see how quickly you can apply them. I'll give you a timed drill."},
            {"role": "user", "content": "Yes, let's do a speed round!"},
        ],
        expected_behaviors={"should_use_tool": "start_timer"},
    ))

    # Should NOT use tools — just respond conversationally
    cases.append(_case(
        "tool-none-01", ["tool_use", "engagement"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles"),
        [{"role": "user", "content": "I'm a bit confused about は. Can you explain it one more time?"}],
        expected_behaviors={"should_use_tool": None},
    ))

    # ── Accuracy cases (5) ────────────────────────────────────────────────

    cases.append(_case(
        "accuracy-01", ["accuracy"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles"),
        [{"role": "user", "content": "What does the particle を do?"}],
        section_content=ALL_JP_SECTIONS[1]["content"],
    ))

    cases.append(_case(
        "accuracy-02", ["accuracy"],
        _make_system(ALL_JP_SECTIONS, 2, goal="Learn verbs"),
        [{"role": "user", "content": "How do I make the past tense of a ます verb?"}],
        section_content=ALL_JP_SECTIONS[2]["content"],
    ))

    cases.append(_case(
        "accuracy-03", ["accuracy"],
        _make_system(ALL_JP_SECTIONS, 0, goal="Learn greetings"),
        [{"role": "user", "content": "What do I say when I meet someone for the first time?"}],
        section_content=ALL_JP_SECTIONS[0]["content"],
    ))

    cases.append(_case(
        "accuracy-04", ["accuracy"],
        _make_system(MUSIC_SECTIONS, 0, title="Music Theory Fundamentals", goal="Learn scales"),
        [{"role": "user", "content": "What is the relative minor of C major?"}],
        section_content=MUSIC_SECTIONS[0]["content"],
    ))

    cases.append(_case(
        "accuracy-05", ["accuracy"],
        _make_system(PYTHON_SECTIONS, 0, title="Introduction to Python", goal="Learn Python"),
        [{"role": "user", "content": "How do I get just the even numbers from a range using a list comprehension?"}],
        section_content=PYTHON_SECTIONS[0]["content"],
    ))

    # ── Multi-turn cases (5) ──────────────────────────────────────────────

    tasks_partial = [
        {"concept": "Use は as topic marker", "status": "passed"},
        {"concept": "Distinguish は (topic) from が (subject)", "status": "passed"},
        {"concept": "Use を for direct objects", "status": "pending"},
        {"concept": "Distinguish に (existence) vs で (action location)", "status": "pending"},
        {"concept": "Use の to connect nouns", "status": "pending"},
    ]

    cases.append(_case(
        "multi-01", ["multi_turn", "engagement"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles", tasks=tasks_partial),
        [
            {"role": "assistant", "content": "Great work with は and が! Now let's move on to を. This particle marks the direct object of a verb. For example, 水を飲みます means 'I drink water,' where 水 is the object. What do you think を does in りんごを食べます?"},
            {"role": "user", "content": "を marks the apple as the thing being eaten?"},
            {"role": "assistant", "content": "Exactly right! りんご is the direct object, and を connects it to the verb 食べます. Can you make your own sentence using を?"},
            {"role": "user", "content": "本を読みます。 I read a book."},
        ],
        section_content=ALL_JP_SECTIONS[1]["content"],
    ))

    cases.append(_case(
        "multi-02", ["multi_turn", "conciseness"],
        _make_system(ALL_JP_SECTIONS, 2, goal="Learn verbs"),
        [
            {"role": "assistant", "content": "Let's learn verb conjugation. Japanese has three verb groups. Group 2 verbs end in る and are the easiest to conjugate. To make the polite form, just drop る and add ます. So 食べる becomes 食べます. Can you try with 見る, which means 'to see'?"},
            {"role": "user", "content": "見ます?"},
            {"role": "assistant", "content": "Perfect! Now let's try Group 1 verbs. These are trickier. For 書く, meaning 'to write,' you change the く to き and add ます, giving you 書きます. Try conjugating 読む, 'to read.'"},
            {"role": "user", "content": "I'm not sure. 読む... 読みます?"},
        ],
        section_content=ALL_JP_SECTIONS[2]["content"],
    ))

    cases.append(_case(
        "multi-03", ["multi_turn"],
        _make_system(ALL_JP_SECTIONS, 0, goal="Learn basic Japanese"),
        [
            {"role": "assistant", "content": "Let's practice self-introductions. The pattern is hajimemashite, then your name desu, then yoroshiku onegaishimasu. Can you introduce yourself?"},
            {"role": "user", "content": "はじめまして、ジョンです。よろしくおねがいします。"},
            {"role": "assistant", "content": "Wonderful! Your pronunciation pattern is great. Now, how would you greet someone in the morning?"},
            {"role": "user", "content": "おはようございます!"},
        ],
        section_content=ALL_JP_SECTIONS[0]["content"],
    ))

    # Student is confused — model should re-explain, not advance
    cases.append(_case(
        "multi-04", ["multi_turn", "engagement"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles", tasks=tasks_partial),
        [
            {"role": "assistant", "content": "Now let's look at を. It marks the direct object. In ジュースを飲みます, the juice is what's being drunk. Can you identify the direct object in パンを食べます?"},
            {"role": "user", "content": "I don't understand what a direct object is. This is confusing."},
        ],
    ))

    # Student goes off-topic
    cases.append(_case(
        "multi-05", ["multi_turn", "engagement"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles"),
        [
            {"role": "assistant", "content": "Let's practice with は. Can you make a sentence with a topic marker?"},
            {"role": "user", "content": "Actually, can we talk about kanji instead? I want to learn to read."},
        ],
    ))

    # ── Edge cases (4) ────────────────────────────────────────────────────

    # Empty/minimal student response
    cases.append(_case(
        "edge-01", ["edge_case", "engagement"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles"),
        [
            {"role": "assistant", "content": "Let's start with particles. The particle は marks the topic. Can you try using it in a sentence?"},
            {"role": "user", "content": "I don't know."},
        ],
    ))

    # Student says something incorrect
    cases.append(_case(
        "edge-02", ["edge_case", "accuracy"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles"),
        [{"role": "user", "content": "は is the subject marker, right? Like in English 'I' is the subject?"}],
        section_content=ALL_JP_SECTIONS[1]["content"],
    ))

    # Very first turn — model needs to introduce the section
    cases.append(_case(
        "edge-03", ["edge_case", "conciseness", "engagement"],
        _make_system(ALL_JP_SECTIONS, 1, goal="Learn particles"),
        [{"role": "user", "content": "Please begin teaching."}],
    ))

    # Student wants to skip ahead
    cases.append(_case(
        "edge-04", ["edge_case"],
        _make_system(ALL_JP_SECTIONS, 0, goal="Learn Japanese fast"),
        [
            {"role": "assistant", "content": "Let's start with Japanese greetings. おはようございます is the morning greeting. Can you repeat it?"},
            {"role": "user", "content": "I already know all the greetings. Can we skip to particles?"},
        ],
    ))

    return cases


def main():
    cases = generate_cases()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"Wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
