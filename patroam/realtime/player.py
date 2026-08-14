"""Speaker playback — streamed in small frames, stoppable instantly.

The whole feel of "interrupting a person" lives here. Playing a synthesised
sentence as one blob means a barge-in can only take effect when that blob ends;
queueing 20 ms frames means stop() takes effect within one frame.

Also exposes `reference()` — the audio most recently sent to the speakers. The
echo canceller needs exactly that signal to subtract PATROAM's own voice from
the microphone.
"""

import collections
import queue
import threading
import time

import numpy as np

from . import CHANNELS, DTYPE, FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE

FRAME_MS_S = FRAME_MS / 1000.0


class AudioPlayer:
    """Frame-queued playback with sub-frame stop latency."""

    def __init__(self, device=None, max_queued=2000, reference_frames=120):
        self.device = device
        self._q = queue.Queue(maxsize=max_queued)
        self._stream = None
        self._lock = threading.Lock()
        self.running = False
        self.speaking = False           # True while frames are actually going out
        self._last_out = 0.0
        # Ring buffer of what we just played, for the echo canceller (~2.4 s).
        self._ref = collections.deque(maxlen=reference_frames)
        self._ref_lock = threading.Lock()
        self.stopped_at = 0.0           # timestamp of the last stop(), for metrics

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        if self.running:
            return True
        import sounddevice as sd
        with self._lock:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                blocksize=FRAME_SAMPLES, device=self.device,
                callback=self._on_need_audio, latency="low")
            self._stream.start()
            self.running = True
        return True

    def close(self):
        self.stop()
        with self._lock:
            st, self._stream = self._stream, None
            self.running = False
        if st:
            try:
                st.stop()
                st.close()
            except Exception:
                pass

    # ── playback callback (realtime thread) ──────────────────────────────────
    def _on_need_audio(self, outdata, frames, time_info, status):
        try:
            buf = self._q.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            self.speaking = False
            with self._ref_lock:
                self._ref.append(np.zeros(frames, dtype=np.int16))
            return
        if len(buf) < frames:                       # pad a short tail frame
            buf = np.concatenate([buf, np.zeros(frames - len(buf), dtype=np.int16)])
        outdata[:, 0] = buf[:frames]
        self.speaking = True
        self._last_out = time.time()
        with self._ref_lock:
            self._ref.append(buf[:frames].copy())

    # ── writing ──────────────────────────────────────────────────────────────
    def write(self, samples):
        """Queue int16 audio (any length) for playback, split into frames."""
        if samples is None or len(samples) == 0:
            return
        a = np.asarray(samples, dtype=np.int16)
        for i in range(0, len(a), FRAME_SAMPLES):
            chunk = a[i:i + FRAME_SAMPLES]
            try:
                self._q.put_nowait(chunk)
            except queue.Full:
                return          # far behind: drop the tail rather than lag further

    def stop(self):
        """Cut playback NOW — the barge-in path. Clears everything pending so the
        next callback (≤20 ms away) already outputs silence."""
        n = 0
        while True:
            try:
                self._q.get_nowait()
                n += 1
            except queue.Empty:
                break
        self.speaking = False
        self.stopped_at = time.time()
        return n                     # frames discarded (useful in tests/metrics)

    # ── echo-cancellation support ────────────────────────────────────────────
    def reference(self, n_frames=1):
        """The last `n_frames` of audio sent to the speakers, newest last."""
        with self._ref_lock:
            if not self._ref:
                return np.zeros(FRAME_SAMPLES * n_frames, dtype=np.int16)
            take = list(self._ref)[-n_frames:]
        if len(take) < n_frames:
            pad = [np.zeros(FRAME_SAMPLES, dtype=np.int16)] * (n_frames - len(take))
            take = pad + take
        return np.concatenate(take)

    @property
    def pending(self):
        return self._q.qsize()

    @property
    def is_active(self):
        """True if audio is going out, or went out within the last frame.

        The grace is one frame, not an arbitrary 50 ms: a longer window makes
        barge-in *measure* slower than it is and delays the switch back to
        listening."""
        return self.speaking or (time.time() - self._last_out) < (FRAME_MS_S * 1.5)
