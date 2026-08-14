"""Always-on wake-word listener with a conversation session + smart endpointing.

Passively listens to the microphone. When it hears the wake word ("hey patroam")
it opens a *conversation session*: from then on, every utterance you speak is
treated as a command — you do NOT need to repeat the wake word each time. The
session stays open until either:

  * a stretch of silence longer than `session_timeout` seconds, or
  * you say a stop phrase ("go to sleep", "stop listening", …).

Smart endpointing: speech is captured in short chunks, then an endpoint worker
decides when your command is actually COMPLETE before executing it. After each
chunk it waits a grace period for you to continue; the grace adapts to whether
the phrase looks finished (instant heuristic), and for genuinely ambiguous short
phrases it can ask the LLM (off the audio thread, with a timeout). So trailing
off on "and…/to…" keeps it listening, and it acts once you're truly done.

While PATROAM is speaking a reply, callers should `pause()` the listener so it
doesn't hear its own voice, then `resume()`.
"""

import queue
import re
import threading
import time

import speech_recognition as sr

from .wakeword import find_command, is_stop_phrase
from .. import config

# Trailing words that strongly imply the speaker hasn't finished the thought.
_CONT_WORDS = {
    "and", "or", "but", "so", "to", "the", "a", "an", "of", "for", "with", "my",
    "your", "our", "his", "her", "their", "its", "then", "also", "because",
    "that", "which", "who", "whom", "whose", "when", "where", "while", "if",
    "into", "onto", "on", "in", "at", "by", "from", "as", "is", "are", "am",
    "was", "were", "be", "been", "being", "um", "uh", "er", "like", "plus",
    "about", "over", "under", "between", "within", "without", "per", "via",
    "near", "upon", "i", "we", "you", "he", "she", "they", "it", "me", "us",
    "him", "them", "this", "these", "those", "can", "could", "would", "will",
    "should", "let", "let's", "gonna", "wanna", "trying", "going",
    # Vietnamese — a sentence ending on these is still mid-thought. Without them
    # PATROAM judged every paused Vietnamese phrase "complete" and cut in.
    "và", "rồi", "thì", "cho", "với", "để", "mà", "là", "của", "ở", "khi",
    "nếu", "nhưng", "hoặc", "tại", "vì", "các", "những", "một", "cái", "này",
    "đó", "sẽ", "đang", "bị", "được", "cùng", "theo", "về", "từ", "đến",
    "sang", "trong", "ngoài", "trên", "dưới", "sau", "trước", "giữa", "bằng",
    "tôi", "anh", "em", "nó", "họ", "mình", "bạn", "ông", "bà", "chị",
    "hãy", "có", "không", "chưa", "vẫn", "cứ", "thêm", "nữa", "hơn",
}
# Unicode-aware: [a-z0-9']+ shredded accented Vietnamese ("rồi" → "r","i"), so the
# continuation words above could never match.
_WORDS_RE = re.compile(r"[^\W_]+['’]?[^\W_]*", re.UNICODE)


def _vlog(msg):
    """Append a [voice] line to the startup log (to diagnose the wake word)."""
    try:
        import datetime
        import os
        with open(os.path.join(os.path.expanduser("~"), ".patroam", "startup.log"),
                  "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S}  [voice] {msg}\n")
    except Exception:
        pass


class WakeWordListener:
    def __init__(self, on_command, on_status=None, on_wake=None, on_sleep=None,
                 on_greet=None, recognize=None, session_timeout=None):
        """
        on_command(text): called with a COMPLETE command to execute.
        on_status(msg):    optional, human-readable status updates.
        on_wake():         optional, called when a session opens.
        on_sleep():        optional, called when a session ends.
        on_greet():        optional, called when woken WITHOUT a command.
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
        # Capture chunks quickly; the endpoint worker decides when you're done.
        self.recognizer.pause_threshold = config.PAUSE_THRESHOLD
        self.recognizer.phrase_threshold = config.PHRASE_THRESHOLD
        self.recognizer.non_speaking_duration = min(0.5, config.PAUSE_THRESHOLD)

        self._stop = None
        self._lock = threading.Lock()
        self._active = False         # in a conversation session
        self._active_until = 0.0     # session expiry timestamp
        self._paused = False         # ignore audio (e.g. while speaking)
        self._busy = False           # responding/speaking — don't let the session lapse
        self._watchdog = None
        self.listening = False

        # Endpointing: captured chunks flow through this queue to a worker that
        # stitches them into a full command and dispatches when you're finished.
        self._chunks = queue.Queue()
        self._endpoint = None

    # ── transcription ─────────────────────────────────────────────────────────
    def _transcribe(self, audio):
        """Best transcript for `audio`. Also stashes Google's other hypotheses in
        self.alternatives — "patroam" isn't a real word, so the wake phrase often
        lands in a lower-ranked alternative while the top guess is nonsense."""
        self.alternatives = []
        if self._recognize:
            return self._recognize(audio)
        try:
            res = self.recognizer.recognize_google(audio, show_all=True)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            self.on_status(f"STT error: {e}")
            return ""
        except Exception:
            return ""
        # show_all=True → {"alternative": [{"transcript": ...}, ...]}; older/edge
        # responses can still be a bare string.
        if isinstance(res, str):
            return res
        alts = [a.get("transcript", "") for a in (res or {}).get("alternative", [])
                if a.get("transcript")]
        self.alternatives = alts
        return alts[0] if alts else ""

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

    # ── audio callback (runs on the recognizer's background thread) ──────────────
    def _callback(self, recognizer, audio):
        if self._paused:
            return

        text = self._transcribe(audio).strip()
        if not text:
            return
        _vlog(f"heard: {text!r} (active={self._active})")

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
            self._chunks.put(("clear", None))      # drop any half-built command
            self.on_status('Going to sleep — say "hey patroam" to wake me.')
            return

        # Accept an optional wake-word prefix mid-conversation; otherwise the
        # whole phrase is (part of) the command.
        stripped = find_command(text)
        command = (text if stripped is None else stripped).strip()
        with self._lock:
            self._touch()
        if command:
            self._chunks.put(("cmd", command))     # → endpoint worker stitches it

    def _handle_passive(self, text):
        # Check EVERY hypothesis Google returned, not just its top guess: the
        # wake word is a made-up name, so the winning transcript is often junk
        # ("high bun") while a lower alternative got it right.
        command = find_command(text)
        heard = text
        if command is None:
            for alt in (getattr(self, "alternatives", None) or [])[1:]:
                command = find_command(alt)
                if command is not None:
                    heard = alt
                    break
        if command is None and config.VOICE_ALWAYS_ON:
            # No wake word — but in always-on mode the utterance still counts if
            # it was actually meant for PATROAM. The gate errs towards ignoring.
            from . import attention
            if attention.is_for_me(text):
                _vlog(f"ATTENTION accepted {text!r}")
                command = text
            else:
                _vlog(f"ignored (not addressed): {text!r}")
                return
        if command is None:
            _vlog(f"no wake word in {text!r}"
                  + (f" (nor in {len(self.alternatives) - 1} alternatives)"
                     if len(getattr(self, "alternatives", []) or []) > 1 else ""))
            return  # no wake word; stay passive
        else:
            _vlog(f"WAKE matched in {heard!r}; command={command!r}")

        self.on_wake()
        with self._lock:
            self._open_session()

        command = command.strip()
        if command:
            self._chunks.put(("cmd", command))
        else:
            self.on_greet()
            self.on_status("Listening — go ahead, no need to say it again.")

    # ── endpointing: stitch chunks into a complete command ───────────────────────
    def _completeness(self, text):
        """Heuristic verdict on whether `text` is a finished command."""
        words = _WORDS_RE.findall(text.lower())
        if not words:
            return "complete"
        if words[-1] in _CONT_WORDS:
            return "incomplete"           # trailed off — keep listening
        if len(words) <= 2:
            return "ambiguous"            # too short to be sure
        return "complete"

    def _llm_incomplete(self, text):
        """Ask the model if the user is likely still mid-sentence (best-effort)."""
        try:
            from .. import llm
            if not llm.available():
                return False
            ans = llm.complete(
                "You detect end-of-turn for a voice assistant. Decide if the user "
                "has FINISHED a complete spoken command, or is likely still "
                "mid-sentence. Reply with exactly one word: COMPLETE or INCOMPLETE.\n"
                f'Utterance: "{text}"',
                timeout=config.ENDPOINT_LLM_TIMEOUT)
            return bool(ans) and "INCOMPLETE" in ans.upper()
        except Exception:
            return False

    def _grace(self, pending):
        """How long to wait for a continuation, based on how finished it looks."""
        state = self._completeness(pending)
        if state == "incomplete":
            return config.ENDPOINT_INCOMPLETE_GRACE
        if state == "ambiguous":
            if config.ENDPOINT_USE_LLM and self._llm_incomplete(pending):
                return config.ENDPOINT_INCOMPLETE_GRACE
            return config.ENDPOINT_AMBIGUOUS_GRACE
        return config.ENDPOINT_COMPLETE_GRACE

    def _dispatch(self, command):
        command = command.strip()
        if not command:
            return
        with self._lock:
            self._touch()
        self.on_command(command)

    def _endpoint_loop(self):
        pending = ""
        started = 0.0
        while self.listening:
            timeout = self._grace(pending) if pending else None
            try:
                kind, text = self._chunks.get(timeout=timeout)
            except queue.Empty:
                # Silence outlasted the adaptive grace → the command is finished.
                if pending:
                    cmd, pending = pending, ""
                    self._dispatch(cmd)
                continue
            if kind == "stop":
                break
            if kind == "clear":
                pending = ""
                continue
            # kind == "cmd": stitch this chunk onto the command in progress.
            if not pending:
                started = time.time()
            pending = (pending + " " + text).strip()
            if time.time() - started >= config.ENDPOINT_MAX_WAIT:
                cmd, pending = pending, ""
                self._dispatch(cmd)

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
        try:
            mic = sr.Microphone(sample_rate=16000)
            with mic as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.6)
        except Exception as e:
            _vlog(f"MIC ERROR: {e}")
            raise
        self._stop = self.recognizer.listen_in_background(
            mic, self._callback, phrase_time_limit=config.PHRASE_TIME_LIMIT
        )
        self.listening = True
        _vlog("listener started OK (mic open, listening for wake word)")
        self._endpoint = threading.Thread(target=self._endpoint_loop, daemon=True)
        self._endpoint.start()
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()
        self.on_status('Always-on: say "hey patroam"…')

    def stop(self):
        if self._stop:
            self._stop(wait_for_stop=False)
            self._stop = None
        self.listening = False
        self._chunks.put(("stop", None))   # wake the endpoint worker so it exits
        with self._lock:
            self._active = False
        self._paused = False
