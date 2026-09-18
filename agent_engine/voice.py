"""
voice.py - Local Speech-to-Text Module (Phase 4)
Uses faster-whisper for ultra-low latency transcription entirely on-device.
Records from the default microphone via sounddevice.
"""

from __future__ import annotations

import logging
import tempfile
import wave
from pathlib import Path
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
DEFAULT_DURATION: float = 5.0  # Seconds to record per trigger


# ── VoiceEngine ──────────────────────────────────────────────────────────────

class VoiceEngine:
    """
    Wraps faster-whisper for low-latency local speech-to-text.

    The model is loaded once at construction time. Subsequent calls to
    listen_and_transcribe() only pay the recording + inference cost.

    Args:
        model_size:  Whisper model variant. "tiny" = fastest (~32 MB),
                     "base" = better accuracy (~74 MB). Both run on CPU/GPU.
        device:      "cpu" or "cuda". Auto-detected if not set.
        compute_type: "int8" for CPU (fastest), "float16" for GPU.
        duration:    Default recording length in seconds.
    """

    def __init__(
        self,
        model_size: ModelSize = "base",
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
            download_root=None,   # Uses ~/.cache/huggingface by default
            local_files_only=False,
        )
        log.info("Whisper model loaded.")

    # ── Public API ───────────────────────────────────────────────────────

    def record(self, duration: float | None = None) -> np.ndarray:
        """
        Records audio from the default microphone.

        Args:
            duration: Seconds to record. Falls back to self.duration.

        Returns:
            1-D float32 numpy array at SAMPLE_RATE Hz.
        """
        secs = duration if duration is not None else self.duration
        log.info("Recording %.1f seconds of audio...", secs)
        audio: np.ndarray = sd.rec(
            int(secs * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
        )
        sd.wait()  # Block until recording completes
        return audio.flatten()

    def transcribe(self, audio: np.ndarray, language: str = "en") -> str:
        """
        Transcribes a float32 audio array with faster-whisper.

        Args:
            audio:    1-D float32 numpy array at SAMPLE_RATE Hz.
            language: BCP-47 language code hint (speeds up detection).

        Returns:
            Transcribed text string, stripped of leading/trailing whitespace.
        """
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=1,           # Greedy decode — lowest latency
            vad_filter=True,       # Skip silent segments automatically
            vad_parameters={
                "min_silence_duration_ms": 300,
                "speech_pad_ms": 100,
            },
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
        """
        Records audio from the microphone and immediately transcribes it.
        One-shot convenience wrapper around record() + transcribe().

        Args:
            duration: Seconds to record (default: self.duration = 5s).
            language: BCP-47 language hint for faster detection.

        Returns:
            Transcribed text string, or "" on any error.
        """
        try:
            audio = self.record(duration=duration)
            return self.transcribe(audio, language=language)
        except Exception as exc:
            log.error("listen_and_transcribe failed: %s", exc)
            return ""

    def transcribe_file(self, path: str | Path, language: str = "en") -> str:
        """
        Transcribes a WAV or MP3 file on disk. Useful for testing without
        a microphone, or for processing pre-recorded clips.

        Args:
            path:     Path to audio file.
            language: BCP-47 language hint.

        Returns:
            Transcribed text string, or "" on any error.
        """
        try:
            segments, _ = self._model.transcribe(
                str(path),
                language=language,
                beam_size=1,
                vad_filter=True,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as exc:
            log.error("transcribe_file failed for '%s': %s", path, exc)
            return ""


# ── Module-level convenience ──────────────────────────────────────────────────

_engine: VoiceEngine | None = None


def get_engine(
    model_size: ModelSize = "base",
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
