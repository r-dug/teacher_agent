"""Tool schemas for the teaching and decomposition agents."""

from __future__ import annotations

# ── intro goal-gathering tool ──────────────────────────────────────────────────

CAPTURE_GOAL_TOOL = {
    "name": "capture_lesson_goal",
    "description": (
        "Call this once you clearly understand what the student wants to learn from this lesson. "
        "Ends goal-gathering and begins tailored lesson preparation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "Clear, concise summary of the student's learning goal (1-3 sentences).",
            },
            "depth": {
                "type": "string",
                "enum": ["beginner", "intermediate", "advanced"],
                "description": "Inferred expertise level of the student for this topic.",
            },
        },
        "required": ["goal"],
    },
}

# ── decomposition search tool ──────────────────────────────────────────────────

SEARCH_WEB_TOOL = {
    "name": "search_web",
    "description": (
        "Search the web for factual information when the document is ambiguous, uses uncommon "
        "terminology, or covers a topic that needs supplementary context for lesson planning. "
        "Use sparingly — only when the document text alone is clearly insufficient."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Specific search query to clarify ambiguous terms or concepts.",
            },
            "reason": {
                "type": "string",
                "description": "Why this search is needed for the decomposition.",
            },
        },
        "required": ["query", "reason"],
    },
}

# ── teaching tools ─────────────────────────────────────────────────────────────

TEACHING_TOOLS = [
    {
        "name": "advance_to_next_section",
        "description": (
            "Call this ONLY after the student has answered at least one comprehension question "
            "and their answer demonstrates genuine understanding of the current section's key concepts. "
            "Do NOT call this preemptively or before asking any questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "evidence": {
                    "type": "string",
                    "description": "What the student said that demonstrates they understood.",
                }
            },
            "required": ["evidence"],
        },
    },
    {
        "name": "show_slide",
        "description": (
            "Display one or more pages from the PDF as a visual aid. "
            "Use this when a diagram, figure, table, or layout on a page would help the student. "
            "Use page_end to show a continuous range (e.g. a two-page spread or a sequence of diagrams). "
            "Provide a brief caption summarising what to focus on. "
            "The student can annotate any shown page and send it back to you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_start": {
                    "type": "integer",
                    "description": "1-based first page to display.",
                },
                "page_end": {
                    "type": "integer",
                    "description": "1-based last page to display (inclusive). Omit or set equal to page_start for a single page.",
                },
                "caption": {
                    "type": "string",
                    "description": "One sentence describing what to focus on.",
                },
            },
            "required": ["page_start", "caption"],
        },
    },
    {
        "name": "open_sketchpad",
        "description": (
            "Open a drawing canvas for the student to practise writing characters, diagrams, "
            "or anything else that benefits from freehand input (e.g. Japanese kana, kanji, "
            "mathematical notation, diagrams). The canvas is returned to you as an image so "
            "you can evaluate what the student drew and give feedback. "
            "Optionally supply a background: text_bg for a faint reference character the "
            "student should trace or copy, or bg_page to show a PDF slide behind the canvas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instruction shown above the canvas, e.g. 'Please write the character さ'.",
                },
                "text_bg": {
                    "type": "string",
                    "description": (
                        "Optional reference text to display as a faint guide behind the canvas "
                        "(e.g. 'さ', 'あ', '猫'). The student traces or copies it. "
                        "Use for character-writing practice."
                    ),
                },
                "bg_page": {
                    "type": "integer",
                    "description": (
                        "Optional 1-based PDF page number to display as a faint image behind "
                        "the canvas. Use when the student should annotate or reproduce a diagram."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "take_photo",
        "description": (
            "Ask the student to take a photo with their camera. Use this when you want to see "
            "something physical — their handwriting on paper, a real-world object, a physical "
            "diagram they drew, or their surroundings. The photo is returned as an image so you "
            "can observe, evaluate, or respond to what is shown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instruction shown to the student, e.g. 'Please show me what you wrote.'",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "record_video",
        "description": (
            "Ask the student to record a short video with their camera. Use this when you need "
            "to observe motion or a sequence of actions — for example, signing in ASL, "
            "demonstrating a physical technique, or showing a process step by step. "
            "The video is sampled into frames which you can observe and evaluate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instruction shown to the student, e.g. 'Please sign the word HELLO in ASL.'",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "open_code_editor",
        "description": (
            "Open an interactive code editor for the student to complete a coding challenge. "
            "The student writes code, runs it to see output, and submits when satisfied. "
            "You receive both the final code and its execution output. "
            "Use for any exercise requiring the student to write, debug, or modify code. "
            "Use python-ml for exercises involving NumPy, Pandas, scikit-learn, matplotlib, or PyTorch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The coding challenge or instructions shown above the editor.",
                },
                "language": {
                    "type": "string",
                    "enum": ["python", "python-ml", "javascript", "typescript", "c", "cpp", "rust"],
                    "description": "Programming language / runtime environment.",
                },
                "starter_code": {
                    "type": "string",
                    "description": (
                        "Optional code pre-filled in the editor. "
                        "Use for bug-fixing exercises or to provide a function scaffold."
                    ),
                },
            },
            "required": ["prompt", "language"],
        },
    },
    {
        "name": "open_html_editor",
        "description": (
            "Open a dual HTML + CSS editor with a live iframe preview. "
            "Use for web development exercises where the student writes or modifies HTML and CSS. "
            "The student clicks Run to update the preview, then submits. "
            "You receive the final HTML and CSS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The exercise instructions shown above the editor.",
                },
                "starter_html": {
                    "type": "string",
                    "description": "Optional HTML starter code.",
                },
                "starter_css": {
                    "type": "string",
                    "description": "Optional CSS starter code.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "start_timer",
        "description": (
            "Give the student a timed exercise. A countdown timer is displayed on screen. "
            "The student can type an answer and submit early, or wait for time to expire. "
            "You receive whether time expired, how long they took, and their written answer. "
            "Use for recall drills, timed translation challenges, vocabulary sprints, or any "
            "exercise where time pressure reinforces fluency."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The exercise instruction shown above the timer, e.g. 'Write the hiragana for all five vowels.'",
                },
                "duration_seconds": {
                    "type": "integer",
                    "description": "How long the student has, in seconds (e.g. 30, 60, 120).",
                },
            },
            "required": ["prompt", "duration_seconds"],
        },
    },
    {
        "name": "mark_curriculum_complete",
        "description": (
            "Call this ONLY after the student has demonstrated thorough understanding of ALL "
            "sections, including the final section. Only valid after advance_to_next_section "
            "has been called for all preceding sections."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "evidence": {
                    "type": "string",
                    "description": "Summary of demonstrated understanding across the curriculum.",
                }
            },
            "required": ["evidence"],
        },
    },
]
