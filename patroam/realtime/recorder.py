"""Microphone capture — continuous 20 ms frames.

Runs for the WHOLE session, including while PATROAM is speaking. That is the
non-negotiable requirement for barge-in: you cannot interrupt something that
stopped listening to you. The echo canceller downstream is what stops PATROAM
from hearing its own voice and interrupting itself.

The audio callback runs on a realtime thread — it must never block, allocate
heavily, or do I/O, so it only drops a frame into a bounded queue.
"""

import queue
import threading

import numpy as np

from . import CHANNELS, DTYPE, FRAME_SAMPLES, SAMPLE_RATE


class AudioRecorder:
    """Continuous microphone capture into a frame queue.

    frames() yields int16 numpy arrays of FRAME_SAMPLES, oldest first.
    """

    def __init__(self, device=None, max_queued=200):
        self.device = device
        self._q = queue.Queue(maxsize=max_queued)   # ~4 s at 20 ms/frame
        self._stream = None
        self._lock = threading.Lock()
        self.running = False
        self.dropped = 0          # frames lost because the consumer fell behind
        self.level = 0.0          # most recent RMS, 0..1 (for the UI meter)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        if self.running:
            return True
        import sounddevice as sd
        with self._lock:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                blocksize=FRAME_SAMPLES, device=self.device,
                callback=self._on_audio, latency="low")
            self._stream.start()
            self.running = True
        return True

    def stop(self):
        with self._lock:
            st, self._stream = self._stream, None
            self.running = False
        if st:
            try:
                st.stop()
                st.close()
            except Exception:
                pass
        self.clear()

    # ── capture callback (realtime thread — keep it trivial) ─────────────────
    def _on_audio(self, indata, frames, time_info, status):
        buf = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        # Cheap RMS for the level meter; float32 avoids int16 overflow.
        f = buf.astype(np.float32) / 32768.0
        self.level = float(np.sqrt(np.mean(f * f))) if f.size else 0.0
        try:
            self._q.put_nowait(buf)
        except queue.Full:
            # Drop the OLDEST frame: in a live conversation the newest audio is
            # what matters, and blocking here would glitch the input stream.
            try:
                self._q.get_nowait()
                self._q.put_nowait(buf)
            except queue.Empty:
                pass
            self.dropped += 1

    # ── consumption ──────────────────────────────────────────────────────────
    def read(self, timeout=0.5):
        """The next frame, or None if none arrived within `timeout`."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def frames(self):
        """Yield frames until stopped."""
        while self.running:
            f = self.read()
            if f is not None:
                yield f

    def clear(self):
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    @property
    def queued(self):
        return self._q.qsize()
