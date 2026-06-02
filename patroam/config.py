"""Central configuration for PATROAM.

Anything that might change per-machine or per-user lives here so the rest of
the package never hardcodes paths, URLs, or the agent's persona.
"""

import os
import tempfile

# ── Backend ──────────────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("PATROAM_OLLAMA_URL", "http://localhost:11434")

# ── Voice / wake word ──────────────────────────────────────────────────────────
# Canonical wake word + the way speech-to-text commonly mishears it.
WAKE_WORD = "patroam"
WAKE_WORD_VARIANTS = [
    "patroam", "patrom", "patroum", "patroan", "patriam",
    "patron", "patrolam", "pat rome", "petroam", "patram",
]
# How close a heard token must be to "patroam" to count as the wake word (0–1).
WAKE_WORD_FUZZ = 0.78

# Conversation session: after the wake word, PATROAM stays awake and treats every
# following utterance as a command (no need to repeat "hey patroam"). The session
# ends after this many seconds of silence, or when a stop phrase is heard.
# Set to 0 (or None) to stay awake until a stop phrase only.
SESSION_TIMEOUT = 30

# Spoken phrases that end the conversation session and return to wake-word mode.
STOP_PHRASES = [
    "stop listening", "go to sleep", "go back to sleep", "goodbye patroam",
    "that's all", "that is all", "never mind", "stop patroam",
    "thank you that's all",
]

# Temp file for recorded audio. Uses the OS temp dir so this works on Windows
# (the original /tmp/... path only existed on Unix).
VOICE_TMP_WAV = os.path.join(tempfile.gettempdir(), "patroam_voice_input.wav")
TTS_TMP_MP3 = os.path.join(tempfile.gettempdir(), "patroam_tts.mp3")

# ── Text-to-speech ─────────────────────────────────────────────────────────────
# "edge"    : Microsoft Edge neural voices — natural, human-like, needs internet.
# "pyttsx3" : offline Windows SAPI voices — robotic, but works with no connection.
# "auto"    : prefer edge, fall back to pyttsx3 if it's unavailable/offline.
TTS_BACKEND = os.environ.get("PATROAM_TTS_BACKEND", "auto")

# A natural-sounding male British voice for the Edge neural backend.
# Other good UK males: "en-GB-ThomasNeural". List all: `edge-tts --list-voices`.
TTS_VOICE_EDGE = os.environ.get("PATROAM_TTS_VOICE", "en-GB-RyanNeural")
TTS_RATE = "+0%"     # edge prosody rate, e.g. "-10%" slower, "+10%" faster
TTS_PITCH = "+0Hz"   # edge prosody pitch
TTS_VOLUME = "+0%"   # edge prosody volume

# For the pyttsx3 fallback: prefer a British male voice if one is installed.
TTS_PYTTSX3_PREFER = [
    "george", "ryan", "british", "united kingdom", "en-gb", "en_gb",
    "daniel", "oliver", "arthur",
]
TTS_PYTTSX3_RATE = 170

# ── Persona ──────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are PATROAM, the personal assistant to your user. You have the manner of "
    "a refined, warm, quietly witty British gentleman — think a trusted butler or "
    "valet. Only occasionally address the user as \"Master\" or \"Sir\" — perhaps "
    "once every few replies, when greeting, confirming a task, or signing off — and "
    "the rest of the time simply speak naturally without any honorific. Never use an "
    "honorific more than once in a reply, and don't force it into every message. "
    "Speak conversationally the way a real person speaks aloud: use contractions, an "
    "easy rhythm, and keep replies short and to the point, since they are read "
    "aloud. Be courteous and characterful, never robotic or verbose."
)
