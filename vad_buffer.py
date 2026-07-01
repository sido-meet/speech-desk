"""Voice activity detection + utterance buffer for streaming ASR.

Energy-based VAD via stdlib ``audioop`` (RMS in dBFS). Originally planned
to use ``webrtcvad`` but that has no prebuilt wheel on Python 3.12 /
Windows and requires MSVC build tools, so we use ``audioop`` instead. A
pure-Python RMS fallback keeps the module working on 3.13+ where
``audioop`` is removed.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Optional

try:
    import audioop  # type: ignore[import-not-found]

    _HAS_AUDIOOP = True
except ImportError:  # Python 3.13+
    _HAS_AUDIOOP = False


_FRAME_MS = 30
_SILENCE_FRAMES_TO_FLUSH = 24  # 720 ms of continuous silence
_MIN_SPEECH_FRAMES = 15  # 450 ms minimum speech to count as a real utterance
_ENERGY_THRESHOLD_DB = -40.0
_SAMPLE_RATES = (8000, 16000, 32000, 48000)
_FRAME_BYTES = {
    8000: 480,
    16000: 960,
    32000: 1920,
    48000: 2880,
}  # 30 ms * rate * 2 bytes/sample


def _frame_dbfs(int16_frame: bytes) -> float:
    """Return dBFS of an Int16-LE mono frame. -120 for full silence."""
    if _HAS_AUDIOOP:
        rms = audioop.rms(int16_frame, 2)
        if rms <= 0:
            return -120.0
        return 20.0 * math.log10(rms / 32768.0)
    # Pure-Python fallback
    n = len(int16_frame) // 2
    if n == 0:
        return -120.0
    samples = struct.unpack(f"<{n}h", int16_frame)
    sq_sum = 0
    for s in samples:
        sq_sum += s * s
    rms = math.sqrt(sq_sum / n)
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(rms / 32768.0)


@dataclass
class UtteranceDetector:
    """Stateful energy-VAD. Feed raw Int16-LE PCM bytes; get completed utterance audio."""

    sample_rate: int
    threshold_db: float = _ENERGY_THRESHOLD_DB

    _frame_bytes: int = field(init=False)
    _buffer: bytearray = field(default_factory=bytearray)
    _pending_frame: bytes = b""
    _speech_frames: int = 0
    _silence_frames: int = 0
    _in_speech: bool = False
    _emitted_empty: bool = True

    def __post_init__(self) -> None:
        if self.sample_rate not in _SAMPLE_RATES:
            raise ValueError(
                f"sample_rate must be one of {_SAMPLE_RATES}, got {self.sample_rate}"
            )
        self._frame_bytes = _FRAME_BYTES[self.sample_rate]

    def feed(self, pcm: bytes) -> Optional[bytes]:
        """Append PCM bytes. Return completed utterance audio on boundary, else None."""
        self._emitted_empty = False
        combined = self._pending_frame + bytes(self._buffer) + pcm
        self._buffer.clear()
        cursor = 0
        while len(combined) - cursor >= self._frame_bytes:
            frame = combined[cursor : cursor + self._frame_bytes]
            cursor += self._frame_bytes
            if self._on_frame(frame):
                remainder = combined[cursor:]
                emitted = bytes(combined[:cursor])
                self._buffer.extend(remainder)
                self._pending_frame = b""
                self._in_speech = False
                self._speech_frames = 0
                self._silence_frames = 0
                return emitted
        self._pending_frame = combined[cursor:]
        return None

    def flush(self) -> Optional[bytes]:
        """Finalize: emit trailing audio if it has enough speech."""
        if self._emitted_empty and not self._buffer and not self._pending_frame:
            return None
        audio = bytes(self._pending_frame) + bytes(self._buffer)
        self._pending_frame = b""
        self._buffer.clear()
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._emitted_empty = True
        if len(audio) < self._frame_bytes * _MIN_SPEECH_FRAMES:
            return None
        return audio or None

    def _on_frame(self, frame: bytes) -> bool:
        is_speech = _frame_dbfs(frame) > self.threshold_db
        if is_speech:
            self._silence_frames = 0
            self._speech_frames += 1
            self._in_speech = True
            return False
        if not self._in_speech:
            return False
        self._silence_frames += 1
        if (
            self._silence_frames >= _SILENCE_FRAMES_TO_FLUSH
            and self._speech_frames >= _MIN_SPEECH_FRAMES
        ):
            return True
        return False
