"""Realtime voice — continuous listening, streaming replies, instant barge-in.

Runs ALONGSIDE the existing chunk-based voice path (voice/listener.py), which
stays as the fallback. Enable with PATROAM_REALTIME=1.

Pipeline, all of it overlapping rather than sequential:

    mic ─20ms─→ AEC ─→ VAD ─→ Whisper ──partial──→ turn detector
                                                        │
                          LLM (streaming) ←─────────────┘
                                 │
                       first sentence ─→ TTS ─→ player
                                                   ↑
                                    barge-in ──stop()

The pieces are deliberately separate classes so each can be swapped: local
one model for another, one TTS for another.
"""

SAMPLE_RATE = 16000        # Whisper's native rate; everything upstream matches
FRAME_MS = 20              # 20 ms frames — small enough that stop() feels instant
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000     # 320
CHANNELS = 1
DTYPE = "int16"
