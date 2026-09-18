"""
voice.py - Local Speech-to-Text Module (Phase 4)
Optimized for English using the 'small.en' model.
"""

from __future__ import annotations

import logging
from typing import Literal

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

log = logging.getLogger(__name__)

# ── Types ────────────────────────────────────────────────────────────────────

ModelSize = Literal["tiny", "tiny.en", "base", "base.en", "small", "small.en"]

# ── Default configuration ────────────────────────────────────────────────────

SAMPLE_RATE: int = 16_000      # Whisper's native sample rate
CHANNELS: int = 1              # Mono
DEFAULT_DURATION: float = 4.0  # Seconds to record per trigger


# ── VoiceEngine ──────────────────────────────────────────────────────────────

class VoiceEngine:
    def __init__(
        self,
        model_size: ModelSize = "small.en",
        device: str = "cpu",
        compute_type: str = "int8",
        duration: float = DEFAULT_DURATION,
    ) -> None:
        self.duration = duration
        log.info(
            "Loading Whisper model '%s' on %s (%s)...",
            model_size, device, compute_type,
        )
        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=None,
            local_files_only=False,
        )
        log.info("Whisper model loaded.")

    def record(self, duration: float | None = None) -> np.ndarray:
        secs = duration if duration is not None else self.duration
        log.info("Recording %.1f seconds of audio...", secs)
        audio: np.ndarray = sd.rec(
            int(secs * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
        )
        sd.wait()
        return audio.flatten()

    def transcribe(self, audio: np.ndarray, language: str = "en") -> str:
        # Force English and disable VAD so short or quiet audio is not clipped.
        segments, info = self._model.transcribe(
            audio,
            language="en",
            beam_size=5,
            vad_filter=False,
        )
        log.debug(
            "Detected language '%s' (prob=%.2f)", info.language, info.language_probability
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        log.info("Transcribed: %r", text)
        return text

    def listen_and_transcribe(
        self,
        duration: float | None = None,
        language: str = "en",
    ) -> str:
        try:
            audio = self.record(duration=duration)
            return self.transcribe(audio, language=language)
        except Exception as exc:
            log.error("listen_and_transcribe failed: %s", exc)
            return ""

# ── Module-level convenience ──────────────────────────────────────────────────

_engine: VoiceEngine | None = None


def get_engine(
    model_size: ModelSize = "small.en",
    device: str = "cpu",
    compute_type: str = "int8",
) -> VoiceEngine:
    """Returns a shared (lazy-loaded) singleton VoiceEngine."""
    global _engine
    if _engine is None:
        _engine = VoiceEngine(model_size=model_size, device=device, compute_type=compute_type)
    return _engine


def listen_and_transcribe(duration: float = DEFAULT_DURATION, language: str = "en") -> str:
    """
    Module-level shortcut: records `duration` seconds and returns transcribed text.
    Uses the shared singleton VoiceEngine (loaded on first call).
    """
    return get_engine().listen_and_transcribe(duration=duration, language=language)
