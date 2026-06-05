"""Always-on wake-word listener with a conversation session.

Passively listens to the microphone. When it hears the wake word ("hey patroam")
it opens a *conversation session*: from then on, every utterance you speak is
treated as a command — you do NOT need to repeat the wake word each time. The
session stays open until either:

  * a stretch of silence longer than `session_timeout` seconds, or
  * you say a stop phrase ("go to sleep", "stop listening", …).

While PATROAM is speaking a reply, callers should `pause()` the listener so it
doesn't hear its own voice and treat it as your next command, then `resume()`.

Wake-word and stop-phrase detection are delegated to `wakeword`, so a dedicated
wake-word engine can drop in behind the same interface later.
"""

import threading
import time

import speech_recognition as sr

from .wakeword import find_command, is_stop_phrase
from .. import config


class WakeWordListener:
    def __init__(self, on_command, on_status=None, on_wake=None, on_sleep=None,
                 on_greet=None, recognize=None, session_timeout=None):
        """
        on_command(text): called with a command to execute.
        on_status(msg):    optional, human-readable status updates.
        on_wake():         optional, called when a session opens.
        on_sleep():        optional, called when a session ends.
        on_greet():        optional, called when woken WITHOUT a command (a good
                           moment for a spoken greeting / acknowledgement).
        recognize(audio):  optional custom transcriber; defaults to Google STT.
        session_timeout:   seconds of silence before the session closes
                           (defaults to config.SESSION_TIMEOUT; 0/None = never).
        """
        self.on_command = on_command
        self.on_status = on_status or (lambda s: None)
        self.on_wake = on_wake or (lambda: None)
        self.on_sleep = on_sleep or (lambda: None)
        self.on_greet = on_greet or (lambda: None)
        self._recognize = recognize
        self.session_timeout = (config.SESSION_TIMEOUT
                                if session_timeout is None else session_timeout)

        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self._stop = None
        self._lock = threading.Lock()
        self._active = False         # in a conversation session
        self._active_until = 0.0     # session expiry timestamp
        self._paused = False         # ignore audio (e.g. while speaking)
        self._busy = False           # responding/speaking — don't let the session lapse
        self._watchdog = None
        self.listening = False

    # ── transcription ─────────────────────────────────────────────────────────
    def _transcribe(self, audio):
        if self._recognize:
            return self._recognize(audio)
        try:
            return self.recognizer.recognize_google(audio)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            self.on_status(f"STT error: {e}")
            return ""

    # ── session state ───────────────────────────────────────────────────────────
    def _touch(self):
        if self.session_timeout:
            self._active_until = time.time() + self.session_timeout

    def _open_session(self):
        self._active = True
        self._touch()

    def _close_session(self):
        was_active = self._active
        self._active = False
        if was_active:
            self.on_sleep()

    # ── audio callback ──────────────────────────────────────────────────────────
    def _callback(self, recognizer, audio):
        if self._paused:
            return

        text = self._transcribe(audio).strip()
        if not text:
            return

        with self._lock:
            active = self._active
            if (active and self.session_timeout and not self._busy
                    and time.time() > self._active_until):
                # Session lapsed during the silence before this phrase.
                self._active = False
                active = False

        if active:
            self._handle_active(text)
        else:
            self._handle_passive(text)

    def _handle_active(self, text):
        if is_stop_phrase(text):
            with self._lock:
                self._close_session()
            self.on_status('Going to sleep — say "hey patroam" to wake me.')
            return

        # Accept an optional wake-word prefix mid-conversation; otherwise the
        # whole phrase is the command.
        stripped = find_command(text)
        command = text if stripped is None else stripped
        command = command.strip()

        with self._lock:
            self._touch()

        if command:
            self.on_command(command)

    def _handle_passive(self, text):
        command = find_command(text)
        if command is None:
            return  # no wake word; stay passive

        self.on_wake()
        with self._lock:
            self._open_session()

        command = command.strip()
        if command:
            self.on_command(command)
        else:
            self.on_greet()
            self.on_status("Listening — go ahead, no need to say it again.")

    # ── watchdog (closes idle sessions even without new speech) ─────────────────
    def _watch(self):
        while self.listening:
            time.sleep(0.5)
            if not self.session_timeout:
                continue
            with self._lock:
                # Never lapse while PATROAM is busy responding/speaking.
                lapsed = (self._active and not self._busy
                          and time.time() > self._active_until)
                if lapsed:
                    self._close_session()
            if lapsed:
                self.on_status('Going to sleep — say "hey patroam" to wake me.')

    # ── control ───────────────────────────────────────────────────────────────
    def pause(self):
        """Stop reacting to audio (call while speaking a reply)."""
        self._paused = True

    def resume(self):
        """Resume reacting to audio."""
        self._paused = False

    def set_busy(self, busy):
        """Hold the conversation session open while responding/speaking. When
        released, the silence countdown restarts from now."""
        with self._lock:
            self._busy = bool(busy)
            if busy:
                self._active = True
            self._touch()   # restart the timer (and keep session alive)

    def start(self):
        if self.listening:
            return
        mic = sr.Microphone(sample_rate=16000)
        with mic as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.6)
        self._stop = self.recognizer.listen_in_background(
            mic, self._callback, phrase_time_limit=12
        )
        self.listening = True
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()
        self.on_status('Always-on: say "hey patroam"…')

    def stop(self):
        if self._stop:
            self._stop(wait_for_stop=False)
            self._stop = None
        self.listening = False
        with self._lock:
            self._active = False
        self._paused = False
