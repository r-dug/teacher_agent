"""TeachingCallbacks — groups all event hooks passed to TeachingAgent."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .curriculum import Curriculum


@dataclass
class TeachingCallbacks:
    """All optional event callbacks for a teaching session.

    Pass an instance to TeachingAgent instead of 20+ individual keyword args.
    Every field defaults to None (no-op).
    """

    on_status: Callable[[str], None] | None = None
    on_turn_start: Callable[[], None] | None = None
    on_text_chunk: Callable[[str], None] | None = None
    on_chunk_ready: Callable[[str, int, int], None] | None = None
    on_audio_chunk: Callable[[np.ndarray, int, int], None] | None = None
    on_show_slide: Callable[[int, int, str], None] | None = None
    on_open_sketchpad: Callable[[str, list, threading.Event, str | None, int | None], None] | None = None
    on_take_photo: Callable[[str, list, threading.Event], None] | None = None
    on_record_video: Callable[[str, list, threading.Event], None] | None = None
    on_open_code_editor: Callable[[str, str, str | None, list, threading.Event], None] | None = None
    on_open_html_editor: Callable[[str, str | None, str | None, list, threading.Event], None] | None = None
    on_start_timer: Callable[[str, int, list, threading.Event], None] | None = None
    on_token_usage: Callable[[str, str, object], None] | None = None
    on_section_advanced: Callable[[Curriculum], None] | None = None
    on_curriculum_complete: Callable[[], None] | None = None
    on_turn_complete: Callable[[np.ndarray | None], None] | None = None
    on_response_end: Callable[[], None] | None = None
    on_tts_playing: Callable[[bool], None] | None = None
    on_tts_done: Callable[..., None] | None = None
    on_error: Callable[[str], None] | None = None
