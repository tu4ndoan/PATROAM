"""Turn detection — when did you start talking, and when did you finish?

Two jobs, both driven by Silero VAD (a small neural net, ~3.6 ms per chunk):

  • BARGE-IN  — you started speaking while PATROAM was, so stop the reply.
  • END-OF-TURN — you stopped, so answer now.

Silero wants 512-sample chunks at 16 kHz (32 ms); the pipeline runs on 320-sample
frames (20 ms), so frames are buffered up to a chunk before scoring.

End-of-turn is deliberately not "0.5 s of silence": pausing mid-thought would
cut you off. The wait adapts — a long trailing silence is required when you seem
to be mid-sentence, a short one when you clearly finished.
"""

import collections

import numpy as np

from . import SAMPLE_RATE

CHUNK = 512                     # samples Silero expects at 16 kHz


class TurnDetector:
    """Speech/silence state with hysteresis, on top of Silero VAD."""

    def __init__(self, speech_threshold=0.5, silence_threshold=0.35,
                 start_frames=2, end_silence_ms=550):
        self.speech_threshold = speech_threshold
        self.silence_threshold = silence_threshold
        self.start_frames = start_frames          # consecutive chunks to call it speech
        self.end_silence_ms = end_silence_ms
        self._model = None
        self._buf = np.zeros(0, dtype=np.float32)
        self._speech_run = 0
        self._silence_ms = 0
        self.speaking = False                     # is the USER currently talking
        self.last_prob = 0.0
        self.speech_ms = 0                        # length of the current utterance

    def _ensure(self):
        if self._model is None:
            from silero_vad import load_silero_vad
            self._model = load_silero_vad()
        return self._model

    def warmup(self):
        """Load the model up front — it takes ~15 s the first time, which must
        not happen mid-conversation."""
        import torch
        m = self._ensure()
        m(torch.zeros(CHUNK), SAMPLE_RATE)
        return True

    def reset(self):
        self._buf = np.zeros(0, dtype=np.float32)
        self._speech_run = 0
        self._silence_ms = 0
        self.speaking = False
        self.speech_ms = 0

    def push(self, frame):
        """Feed one 20 ms int16 frame → dict describing the turn state.

        Returns {speaking, started, ended, prob, speech_ms}.
        `started` fires once when speech begins; `ended` once when the turn is
        judged complete.
        """
        import torch
        m = self._ensure()
        f = np.asarray(frame, dtype=np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, f])

        started = ended = False
        while len(self._buf) >= CHUNK:
            chunk, self._buf = self._buf[:CHUNK], self._buf[CHUNK:]
            prob = float(m(torch.from_numpy(chunk), SAMPLE_RATE).item())
            self.last_prob = prob
            ms = int(CHUNK / SAMPLE_RATE * 1000)

            if prob >= self.speech_threshold:
                self._speech_run += 1
                self._silence_ms = 0
                if not self.speaking and self._speech_run >= self.start_frames:
                    self.speaking = True
                    self.speech_ms = 0
                    started = True
                if self.speaking:
                    self.speech_ms += ms
            elif prob < self.silence_threshold:
                self._speech_run = 0
                if self.speaking:
                    self._silence_ms += ms
                    self.speech_ms += ms
                    if self._silence_ms >= self.end_silence_ms:
                        self.speaking = False
                        self._silence_ms = 0
                        ended = True
            # Between the two thresholds: hold the current state (hysteresis),
            # so a fading syllable doesn't flip it back and forth.

        return {"speaking": self.speaking, "started": started, "ended": ended,
                "prob": self.last_prob, "speech_ms": self.speech_ms}

    def set_patience(self, ms):
        """Adjust how long a pause must be before the turn counts as finished.
        The session raises this when your words look unfinished."""
        self.end_silence_ms = max(150, min(3000, int(ms)))
