"""Tool schemas for the teaching and decomposition agents."""

from __future__ import annotations

# Shared optional property added to every interactive tool.
_OPTIONAL_TIMER = {
    "duration_seconds": {
        "type": "integer",
        "description": (
            "Optional time limit in seconds. A countdown timer is shown "
            "and the exercise auto-submits when time expires."
        ),
    },
}

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

SEARCH_IMAGE_TOOL = {
    "name": "search_image",
    "description": (
        "Search the web for an image to show the student. Use when a real-world photo, "
        "chart, reference image, or existing diagram would help — e.g. a hiragana chart, "
        "a map, a photo of a cultural item, or a technical diagram. "
        "The image is displayed to the student automatically."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for the image, e.g. 'hiragana chart with stroke order'.",
            },
            "caption": {
                "type": "string",
                "description": "Short caption shown below the image (1 sentence).",
            },
        },
        "required": ["query", "caption"],
    },
}

# ── RAG search tool ───────────────────────────────────────────────────────────

SEARCH_CONTENT_TOOL = {
    "name": "search_content",
    "description": (
        "Search other sections of the lesson for relevant content. Use when the student asks "
        "about a topic outside the current section, references something covered earlier, "
        "or when you need additional context to answer accurately. Returns excerpts from "
        "matching sections."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — use key terms from the student's question.",
            },
        },
        "required": ["query"],
    },
}

# ── teaching tools ─────────────────────────────────────────────────────────────

GENERATE_VISUAL_AID_TOOL = {
    "name": "generate_visual_aid",
    "description": (
        "Generate a custom AI image to visually illustrate a concept for the student. "
        "Use this when the student asks for a diagram, illustration, or visual explanation, "
        "or when you judge that a visual would significantly aid understanding of an abstract "
        "or complex idea. Describe exactly what should appear in the image — the server will "
        "generate and display it. The student will see the image automatically. "
        "Do not use for showing PDF pages (use show_slide for that)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Description of the image to generate. Be specific and educational: "
                    "name the objects, labels, and layout. "
                    "Example: 'A labelled diagram of a neuron showing dendrites, axon, "
                    "myelin sheath, and synaptic terminals on a white background.'"
                ),
            },
            "caption": {
                "type": "string",
                "description": "Short caption shown below the image (1 sentence).",
            },
        },
        "required": ["prompt", "caption"],
    },
}

TEACHING_TOOLS = [
    {
        "name": "open_sketchpad",
        "description": (
            "Open a drawing canvas for freehand input (characters, diagrams, notation). "
            "Returns the drawing as an image for evaluation. "
            "Optionally set a background: text_bg for reference characters to trace, "
            "or bg_image_url for a web image. "
            "When testing recall, omit text_bg so the student draws from memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Instruction shown above the canvas. NEVER include the answer "
                        "in this field when testing recall. "
                        "Wrong: 'Write the character ち'. "
                        "Right: 'Write the hiragana for the chi sound.'"
                    ),
                },
                "text_bg": {
                    "type": "string",
                    "description": (
                        "Optional reference text to display as a faint guide behind the canvas "
                        "(e.g. 'さ', 'あ', '猫'). The student traces or copies it."
                    ),
                },
                "bg_image_url": {
                    "type": "string",
                    "description": (
                        "Optional URL of a web image to display as a faint background "
                        "(e.g. a stroke order chart or reference diagram). "
                        "Use search_image first to find the URL, then pass it here."
                    ),
                },
                **_OPTIONAL_TIMER,
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
                **_OPTIONAL_TIMER,
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
                **_OPTIONAL_TIMER,
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
        "name": "mark_task_complete",
        "description": (
            "Mark a specific concept-check task as complete for the current section. "
            "Call this after the student demonstrates understanding of an individual key concept. "
            "You MUST call this for each key concept (and the quiz) to complete the section."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_idx": {
                    "type": "integer",
                    "description": "Zero-based index of the task in the current section's checklist.",
                },
                "evidence": {
                    "type": "string",
                    "description": "What the student said or did that demonstrates understanding of this concept.",
                },
            },
            "required": ["task_idx", "evidence"],
        },
    },
    {
        "name": "unmark_task",
        "description": (
            "Reset a previously-completed task back to pending. "
            "Call this when the student reveals confusion or gives an incorrect answer "
            "about a concept that was previously marked complete — for example, "
            "during the section quiz. The teaching loop continues so you can "
            "re-teach and re-assess the concept."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_idx": {
                    "type": "integer",
                    "description": "Zero-based index of the task to reset.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the task is being unmarked (what the student got wrong).",
                },
            },
            "required": ["task_idx", "reason"],
        },
    },
    SEARCH_CONTENT_TOOL,
    {
        "name": "show_progress",
        "description": (
            "Display the student's progress through the curriculum — sections completed, "
            "current section tasks, and overall status. Use when the student asks about "
            "their progress or at natural transition points."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "play_audio_clip",
        "description": (
            "Play a specific text as a standalone audio clip for the student. Use when "
            "pronunciation, intonation, or listening comprehension matters — e.g. a foreign "
            "word, a phrase, or a sentence the student should hear clearly. The clip plays "
            "separately from your normal speech."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to synthesize and play as audio.",
                },
                "speed": {
                    "type": "number",
                    "description": "Playback speed multiplier (0.5 = half speed, 1.0 = normal, 2.0 = double). Default 1.0.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "text_input",
        "description": (
            "Open a text input for the student to type a written answer. Use for free-response "
            "questions where voice is insufficient — translations, formulas, written explanations, "
            "or any time you need the student's answer in text form."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The question or instruction shown above the text field.",
                },
                "placeholder": {
                    "type": "string",
                    "description": "Optional placeholder text in the input field.",
                },
                "multiline": {
                    "type": "boolean",
                    "description": "If true, show a multi-line textarea. Default false.",
                },
                **_OPTIONAL_TIMER,
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "show_quiz",
        "description": (
            "Display a multiple-choice question. Use for comprehension checks where you want "
            "the student to select from specific options. The student sees immediate feedback "
            "(correct/incorrect) before the result is sent back to you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The question text.",
                },
                "choices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "Short label, e.g. 'A', 'B', 'C'."},
                            "text": {"type": "string", "description": "The choice text."},
                        },
                        "required": ["label", "text"],
                    },
                    "description": "The answer choices (2-6 options).",
                },
                "correct_index": {
                    "type": "integer",
                    "description": "Zero-based index of the correct choice.",
                },
                **_OPTIONAL_TIMER,
            },
            "required": ["prompt", "choices", "correct_index"],
        },
    },
    {
        "name": "fill_in_the_blank",
        "description": (
            "Display a fill-in-the-blank exercise. The template uses ___ (three underscores) to "
            "mark each blank. The student types answers into inline fields. Use for vocabulary "
            "recall, grammar drills, or any exercise where specific words must be produced."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instructions shown above the exercise.",
                },
                "template": {
                    "type": "string",
                    "description": "Text with ___ marking each blank. E.g. 'The capital of France is ___.'",
                },
                "answers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Correct answer for each blank, in order.",
                },
                **_OPTIONAL_TIMER,
            },
            "required": ["prompt", "template", "answers"],
        },
    },
    {
        "name": "show_flashcard_deck",
        "description": (
            "Present a deck of flashcards for review. Each card has a front (prompt) and back "
            "(answer). The student flips each card, self-grades as 'Got it' or 'Missed it', "
            "and submits results for all cards. Use for vocabulary review, term definitions, "
            "or spaced-repetition drills."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instructions shown above the deck.",
                },
                "cards": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "front": {"type": "string", "description": "The front of the card (question/term)."},
                            "back": {"type": "string", "description": "The back of the card (answer/definition)."},
                        },
                        "required": ["front", "back"],
                    },
                    "description": "The flashcards (3-20 cards recommended).",
                },
                **_OPTIONAL_TIMER,
            },
            "required": ["prompt", "cards"],
        },
    },
    {
        "name": "ordering_exercise",
        "description": (
            "Present items that the student must put in the correct order by dragging. "
            "Use for sequences, processes, timelines, ranked lists, or any concept where "
            "ordering matters. Items are shuffled before display."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instructions shown above the exercise.",
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Items in their CORRECT order. They will be shuffled for the student.",
                },
                **_OPTIONAL_TIMER,
            },
            "required": ["prompt", "items"],
        },
    },
]
