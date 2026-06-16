"""Text-to-speech.

`TTSWorker` keeps its simple interface (speak / stop / on_finish) and runs on
its own thread so speaking never blocks the agent or UI. The actual synthesis is
delegated to a pluggable engine so we can offer a natural neural voice while
keeping an offline fallback:

  * EdgeTTSEngine  — Microsoft Edge neural voices (e.g. en-GB-RyanNeural).
                     Natural, human-like British male. Needs internet.
  * Pyttsx3Engine  — offline Windows SAPI voices. Robotic, but always available;
                     picks a British voice if one is installed.

`make_engine()` chooses based on config.TTS_BACKEND, with graceful fallback.
"""

import os
import queue
import threading
import time

from .. import config

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


# ── Engines ────────────────────────────────────────────────────────────────────
class Pyttsx3Engine:
    """Offline SAPI voice. Robotic, but needs no network."""

    name = "pyttsx3"

    def __init__(self):
        import pyttsx3
        self.engine = pyttsx3.init()
        self._select_voice()
        self.engine.setProperty("rate", config.TTS_PYTTSX3_RATE)
        self.engine.setProperty("volume", 0.95)

    def _select_voice(self):
        voices = self.engine.getProperty("voices")
        # Prefer a British voice; fall back to any male voice; then the first.
        for keys in (config.TTS_PYTTSX3_PREFER,
                     ["male", "david", "mark", "james", "paul", "george"]):
            for v in voices:
                hay = f"{v.name or ''} {v.id or ''}".lower()
                if any(k in hay for k in keys):
                    self.engine.setProperty("voice", v.id)
                    return
        if voices:
            self.engine.setProperty("voice", voices[0].id)

    def speak(self, text):
        self.engine.say(text)
        self.engine.runAndWait()

    def interrupt(self):
        try:
            self.engine.stop()
        except Exception:
            pass

    def shutdown(self):
        try:
            self.engine.stop()
        except Exception:
            pass


class EdgeTTSEngine:
    """Microsoft Edge neural TTS — natural, human-like. Requires internet.

    Falls back to a Pyttsx3Engine per-utterance if synthesis fails (e.g. offline).
    """

    name = "edge"

    def __init__(self):
        import asyncio  # noqa: F401  (validate availability up front)
        import edge_tts  # noqa: F401
        import pygame

        self.voice = config.TTS_VOICE_EDGE
        self.tmp = config.TTS_TMP_MP3
        pygame.mixer.init()
        self._pygame = pygame
        self._fallback = None  # lazily created Pyttsx3Engine

    def _synth(self, text):
        import asyncio
        import edge_tts

        async def run():
            # Read the voice fresh each time so switching language at runtime
            # (e.g. "reply in Vietnamese") takes effect immediately.
            comm = edge_tts.Communicate(
                text, config.TTS_VOICE_EDGE or self.voice,
                rate=config.TTS_RATE, volume=config.TTS_VOLUME, pitch=config.TTS_PITCH,
            )
            await comm.save(self.tmp)

        asyncio.run(run())

    def _play(self, path):
        music = self._pygame.mixer.music
        music.load(path)
        music.play()
        while music.get_busy():
            time.sleep(0.05)
        music.unload()  # release the file lock so it can be overwritten next time

    def speak(self, text):
        try:
            self._synth(text)
            self._play(self.tmp)
        except Exception as e:
            # Network down or playback failed — degrade to offline voice.
            print(f"Edge TTS unavailable ({e}); using offline voice.")
            if self._fallback is None:
                self._fallback = Pyttsx3Engine()
            self._fallback.speak(text)

    def interrupt(self):
        try:
            self._pygame.mixer.music.stop()
        except Exception:
            pass
        if self._fallback:
            self._fallback.interrupt()

    def shutdown(self):
        try:
            self._pygame.mixer.quit()
        except Exception:
            pass
        if self._fallback:
            self._fallback.shutdown()


def make_engine():
    """Build a TTS engine per config.TTS_BACKEND, falling back gracefully."""
    backend = (config.TTS_BACKEND or "auto").lower()
    if backend in ("edge", "auto"):
        try:
            return EdgeTTSEngine()
        except Exception as e:
            if backend == "edge":
                print(f"Edge TTS init failed ({e}); falling back to offline voice.")
    return Pyttsx3Engine()


# ── Worker ─────────────────────────────────────────────────────────────────────
class TTSWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.queue = queue.Queue()
        self.engine = None

    def run(self):
        self.engine = make_engine()
        while True:
            item = self.queue.get()
            if item is None:
                break
            text, on_finish = item
            try:
                self.engine.speak(text)
            except Exception as e:
                print(f"TTS error: {e}")
            finally:
                if on_finish:
                    try:
                        on_finish()
                    except Exception as e:
                        print(f"TTS on_finish error: {e}")
        self.engine.shutdown()

    def speak(self, text, on_finish=None):
        """Queue text to be spoken. `on_finish` (if given) runs on the TTS thread
        once this utterance finishes."""
        self.queue.put((text, on_finish))

    def interrupt(self):
        """Stop the current utterance and drop any queued ones (for barge-in)."""
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass
        if self.engine and hasattr(self.engine, "interrupt"):
            try:
                self.engine.interrupt()
            except Exception:
                pass

    def stop(self):
        self.queue.put(None)
