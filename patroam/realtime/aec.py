"""Echo control — stop PATROAM from interrupting itself.

Barge-in needs the microphone open while the speakers play, which means the mic
hears PATROAM's own voice. Without this module every spoken reply would look
like the user talking and cancel itself instantly.

`speexdsp` has no Windows wheel, so this is built from what we already have: the
exact signal sent to the speakers (AudioPlayer.reference()). Two stages:

  1. ALIGN   — find the acoustic delay (speaker → air → mic) by cross-correlating
               mic against reference, once, then track it.
  2. SUPPRESS— subtract the scaled, aligned reference (block NLMS), and measure
               how much of the mic frame the reference explains.

The output that matters is `is_echo`: "this frame is PATROAM hearing itself".
Perfect cancellation isn't the goal — a correct yes/no is. Speech that survives
suppression is then confirmed by the recogniser before an interrupt is honoured,
so a wrong guess here costs a moment, not a broken conversation.

With headphones there is no acoustic path at all; the correlation stays low and
everything below simply never triggers.
"""

import numpy as np

from . import FRAME_SAMPLES, SAMPLE_RATE

# How far the echo can lag the reference: speaker buffering + room + mic buffer.
MAX_DELAY_MS = 200
MAX_DELAY = SAMPLE_RATE * MAX_DELAY_MS // 1000


class EchoCanceller:
    """Suppresses PATROAM's own voice in the mic signal."""

    # The delay is measured over a long window and then LOCKED. Estimating it
    # from a single 20 ms frame made it jump between wildly wrong offsets
    # (3372, 4036, 297…) and only cancel on the rare frame that guessed right.
    LOCK_WINDOW = SAMPLE_RATE // 2          # 500 ms of audio to estimate on
    RELOCK_EVERY = 250                      # frames (~5 s) before re-checking

    def __init__(self, max_delay=MAX_DELAY):
        self.max_delay = max_delay
        self.delay = None            # LOCKED echo delay, in samples
        self.gain = 0.0              # adaptive echo gain (0 = no echo path)
        self._hist = np.zeros(max_delay + FRAME_SAMPLES * 4, dtype=np.float32)
        self.last_ratio = 0.0        # residual / mic energy (1 = nothing removed)
        self.last_corr = 0.0         # |correlation| with the reference, 0..1
        self.locked = False
        # Rolling windows used only for the (infrequent) delay estimate.
        self._mic_win = np.zeros(self.LOCK_WINDOW, dtype=np.float32)
        self._ref_win = np.zeros(self.LOCK_WINDOW + max_delay, dtype=np.float32)
        self._since_lock = 0
        self._filled = 0             # samples pushed — the window starts as zeros

    # ── internals ────────────────────────────────────────────────────────────
    def _push_ref(self, ref):
        n = len(ref)
        self._hist = np.roll(self._hist, -n)
        self._hist[-n:] = ref

    def _push_win(self, mic, ref):
        n = len(mic)
        self._mic_win = np.roll(self._mic_win, -n)
        self._mic_win[-n:] = mic
        self._ref_win = np.roll(self._ref_win, -len(ref))
        self._ref_win[-len(ref):] = ref
        self._filled += n

    def _try_lock(self):
        """Estimate the echo delay over the whole window. Half a second of audio
        gives a correlation peak that actually means something."""
        # Locking on a window that is still mostly zeros produced a garbage
        # delay that then stuck — wait until there is real audio to correlate.
        if self._filled < len(self._ref_win):
            return
        m, r = self._mic_win, self._ref_win
        if np.abs(m).max() < 1.0 or np.abs(r).max() < 1.0:
            return
        mm = m - m.mean()
        rr = r - r.mean()
        corr = np.correlate(rr, mm, mode="valid")     # len = max_delay + 1
        if corr.size == 0:
            return
        i = int(np.argmax(np.abs(corr)))
        seg = rr[i:i + len(mm)]
        denom = (np.linalg.norm(mm) * np.linalg.norm(seg)) or 1e-9
        score = float(abs(corr[i]) / denom)
        if score < 0.25:                 # no credible echo path (e.g. headphones)
            return
        # `i` indexes the reference window; convert to a lag behind "now".
        self.delay = int(len(rr) - len(mm) - i)
        self.locked = True
        self.last_corr = min(1.0, score)

    # ── public API ───────────────────────────────────────────────────────────
    def process(self, mic_frame, ref_frame):
        """Return (residual, is_echo).

        `mic_frame` and `ref_frame` are int16 arrays of the same length; the
        reference is what AudioPlayer just sent to the speakers.
        """
        mic = np.asarray(mic_frame, dtype=np.float32)
        ref = np.asarray(ref_frame, dtype=np.float32)
        self._push_ref(ref)
        self._push_win(mic, ref)

        mic_energy = float(np.dot(mic, mic)) / max(len(mic), 1)
        # Nothing was played recently → nothing to cancel.
        if np.abs(self._hist).max() < 1.0:
            self.last_ratio, self.last_corr, self.gain = 1.0, 0.0, 0.0
            return mic_frame, False

        # Lock the delay once on a long window, then re-check only occasionally.
        self._since_lock += 1
        if not self.locked or self._since_lock >= self.RELOCK_EVERY:
            self._try_lock()
            self._since_lock = 0
        if not self.locked:
            self.last_ratio = 1.0
            return mic_frame, False

        # Take the reference from `delay` samples ago in the history. The newest
        # sample sits at the end, so "now minus delay" counts back from there.
        end = len(self._hist) - self.delay
        start = end - len(mic)
        if start < 0:
            self.last_ratio = 1.0
            return mic_frame, False
        aligned = self._hist[start:end]
        if len(aligned) < len(mic):
            aligned = np.concatenate([aligned, np.zeros(len(mic) - len(aligned), np.float32)])

        # Least-squares gain for this frame (how loudly the room returns it),
        # smoothed so a single odd frame can't swing the estimate.
        denom = float(np.dot(aligned, aligned)) or 1e-9
        g = float(np.dot(mic, aligned)) / denom
        g = max(-4.0, min(4.0, g))
        self.gain = 0.8 * self.gain + 0.2 * g if self.gain else g

        residual = mic - self.gain * aligned
        res_energy = float(np.dot(residual, residual)) / max(len(residual), 1)
        ratio = res_energy / (mic_energy + 1e-9)
        self.last_ratio = float(ratio)

        # Echo when the reference explains most of the frame AND the two are
        # genuinely correlated. Requiring both keeps the user's speech through:
        # it is uncorrelated with what we are playing, so the ratio stays high.
        is_echo = (ratio < 0.35 and self.last_corr > 0.30)
        out = np.clip(residual, -32768, 32767).astype(np.int16)
        return out, is_echo

    def reset(self):
        self.delay = None
        self.gain = 0.0
        self._hist[:] = 0
        self._mic_win[:] = 0
        self._ref_win[:] = 0
        self.locked = False
        self._since_lock = 0
        self._filled = 0
        self.last_ratio = 0.0
        self.last_corr = 0.0
