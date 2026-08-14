"""Speech to text — transcribes while you are still talking.

Whisper is not a streaming model: it transcribes a finished audio buffer. The
streaming feel comes from *when* we run it, not from the model — audio is
transcribed incrementally during the utterance so that by the time you stop, the
only thing left is the tail. That turns a ~450 ms transcription into ~100 ms of
perceived wait.

Only used by the FALLBACK pipeline — in the Gemini Live path the model does its
own listening. Runs faster-whisper locally.
"""

import io
import json
import os
import threading
import time
import urllib.request
import wave

import numpy as np

from . import SAMPLE_RATE
from .. import config

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"


def _wav_bytes(pcm16, rate=SAMPLE_RATE):
    """Wrap raw PCM in a WAV container (what the API wants)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(pcm16, dtype=np.int16).tobytes())
    return buf.getvalue()


class StreamingSTT:
    """Accumulates audio and transcribes it, incrementally then finally."""

    def __init__(self, backend=None, language="vi", model_size="small"):
        self.backend = backend or config.REALTIME_STT
        self.language = language
        self.model_size = model_size
        self._audio = []                  # list of int16 frames
        self._lock = threading.Lock()
        self._model = None                # local backend only
        self.partial_text = ""
        self.last_ms = 0.0
        self.error = ""

    # ── lifecycle ────────────────────────────────────────────────────────────
    def warmup(self):
        """Load/verify the backend up front — never mid-conversation."""
        if self.backend == "local":
            self._ensure_local()
            self.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.int16))
        return True

    def _ensure_local(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            try:
                self._model = WhisperModel(self.model_size, device="cuda",
                                           compute_type="int8")
            except Exception:
                # VRAM is tight on this machine; CPU still beats not working.
                self._model = WhisperModel(self.model_size, device="cpu",
                                           compute_type="int8")
        return self._model

    # ── audio in ─────────────────────────────────────────────────────────────
    def reset(self):
        with self._lock:
            self._audio = []
        self.partial_text = ""

    def feed(self, frame):
        with self._lock:
            self._audio.append(np.asarray(frame, dtype=np.int16))

    def buffered_ms(self):
        with self._lock:
            n = sum(len(a) for a in self._audio)
        return n / SAMPLE_RATE * 1000

    def _buffer(self):
        with self._lock:
            if not self._audio:
                return np.zeros(0, dtype=np.int16)
            return np.concatenate(self._audio)

    # ── transcription ────────────────────────────────────────────────────────
    def transcribe(self, pcm=None):
        """Transcribe `pcm` (or everything buffered). Returns text ('' on error)."""
        audio = self._buffer() if pcm is None else np.asarray(pcm, dtype=np.int16)
        if len(audio) < SAMPLE_RATE // 10:        # <100 ms is never a sentence
            return ""
        t0 = time.time()
        try:
            text = (self._groq(audio) if self.backend == "groq"
                    else self._local(audio))
            self.error = ""
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            text = ""
        self.last_ms = (time.time() - t0) * 1000
        return text.strip()

    def final(self):
        """Transcribe the whole utterance (called at end of turn)."""
        return self.transcribe()

    def _local(self, audio):
        m = self._ensure_local()
        f32 = audio.astype(np.float32) / 32768.0
        segs, _ = m.transcribe(f32, language=self.language, beam_size=1,
                               vad_filter=False, condition_on_previous_text=False)
        return " ".join(s.text for s in segs)

    def _groq(self, audio):
        key = config.GROQ_API_KEY
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        b = "----patroam-stt"
        parts = []

        def field(name, value):
            parts.extend([f"--{b}".encode(),
                          f'Content-Disposition: form-data; name="{name}"'.encode(),
                          b"", str(value).encode()])
        field("model", GROQ_MODEL)
        field("response_format", "json")
        if self.language:
            field("language", self.language)
        parts.extend([f"--{b}".encode(),
                      b'Content-Disposition: form-data; name="file"; filename="a.wav"',
                      b"Content-Type: audio/wav", b"", _wav_bytes(audio),
                      f"--{b}--".encode()])
        body = b"\r\n".join(parts)
        req = urllib.request.Request(
            GROQ_STT_URL, data=body,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": f"multipart/form-data; boundary={b}",
                     # Cloudflare rejects urllib's default agent with 403/1010.
                     "User-Agent": "PATROAM/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("text", "")
