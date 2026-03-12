"""Shared Tkinter / UI utilities."""

from __future__ import annotations

import io
import re


class StderrInterceptor(io.TextIOBase):
    """Intercepts tqdm download progress from stderr and forwards it to a callback.

    Useful for surfacing model-download progress in a GUI status label while
    still forwarding all output to the original stderr stream.
    """

    _PROGRESS_RE = re.compile(
        r"(\S+):\s+\d+%\S*\s+([\d.]+\s*\S+/[\d.]+\s*\S+)\s+\[.*?,\s*([\d.]+\s*\S+/s)"
    )

    def __init__(self, callback, original):
        self._callback = callback
        self._original = original
        self._buf = ""

    def isatty(self) -> bool:
        return self._original.isatty()

    def fileno(self) -> int:
        return self._original.fileno()

    @property
    def encoding(self):
        return self._original.encoding

    @property
    def errors(self):
        return getattr(self._original, "errors", "strict")

    def write(self, s: str) -> int:
        self._original.write(s)
        self._buf += s
        parts = re.split(r"[\r\n]", self._buf)
        for part in reversed(parts):
            part = part.strip()
            if part:
                m = self._PROGRESS_RE.search(part)
                if m:
                    filename, progress, speed = m.groups()
                    self._callback(f"Downloading {filename}: {progress} at {speed}")
                break
        if "\n" in self._buf:
            self._buf = self._buf.rsplit("\n", 1)[-1]
        return len(s)

    def flush(self):
        self._original.flush()
