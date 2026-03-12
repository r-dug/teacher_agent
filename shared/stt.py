"""Speech-to-text backend wrappers (faster-whisper and whisperx)."""

from __future__ import annotations


def detect_available_backends() -> list[str]:
    """Return a list of installed STT backend names."""
    available = []
    try:
        import whisperx  # noqa: F401
        available.append("whisperx")
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel  # noqa: F401
        available.append("faster-whisper")
    except ImportError:
        pass
    return available


class FasterWhisperBackend:
    """STT backend using faster-whisper (recommended; supports CUDA and CPU)."""

    def __init__(self, model_size: str):
        from faster_whisper import WhisperModel
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        print(f"Loading Faster-Whisper {model_size} on {device}...")
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio_path: str, language: str | None = None) -> str:
        segments, _ = self.model.transcribe(audio_path, beam_size=5, language=language)
        return " ".join(seg.text.strip() for seg in segments)


class WhisperXBackend:
    """STT backend using whisperx (requires a specific torch version)."""

    def __init__(self, model_size: str):
        import torch
        import whisperx
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        print(f"Loading WhisperX {model_size} on {device}...")
        self.model = whisperx.load_model(model_size, device, compute_type=compute_type)
        self.device = device

    def transcribe(self, audio_path: str, language: str | None = None) -> str:
        import whisperx
        audio = whisperx.load_audio(audio_path)
        kwargs = {"batch_size": 16}
        if language:
            kwargs["language"] = language
        result = self.model.transcribe(audio, **kwargs)
        return " ".join(seg["text"].strip() for seg in result["segments"])
