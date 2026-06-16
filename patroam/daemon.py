"""Local always-on PATROAM (server-side voice).

Starts the wake-word listener on the host machine: on "hey patroam <command>"
it streams a reply from the model and speaks it through the host's speakers.

`start_local_voice()` sets this up on background threads and returns a handle
(non-blocking) so it can run alongside the web server. `run_daemon()` uses it
for the standalone 24/7 mode.

An optional `on_event` callback receives ("state"|"status"|"reply", value) so
another layer (e.g. the web server) can mirror what's happening locally.
"""

import time

from . import config, skills
from .agent import Agent
from .providers import make_provider, pick_default
from .voice.listener import WakeWordListener
from .voice.tts import TTSWorker


class LocalVoice:
    def __init__(self, provider=None, on_event=None):
        self.provider = provider or make_provider()
        self.on_event = on_event or (lambda kind, val: None)
        self.agent = None
        self.tts = None
        self.listener = None
        self._speaking = False
        self._speaking_text = ""
        self._pending = 0      # utterances still queued/playing
        self._buf = ""         # streamed text not yet spoken

    def _emit(self, kind, val):
        try:
            self.on_event(kind, val)
        except Exception:
            pass

    def _set_busy(self, busy):
        if self.listener:
            self.listener.set_busy(busy)

    def _say_chunk(self, text):
        # Speak one chunk; keep listening throughout so the user can barge in.
        text = text.strip()
        if not text:
            return
        if config.skip_in_speech(text):
            return                       # don't read protocol scaffolding aloud
        text = config.strip_urls(text)   # never read links aloud
        if not text:
            return
        if self._pending == 0:
            self._emit("state", "speaking")
            self._set_busy(True)          # keep the session alive while speaking
        self._speaking = True
        self._pending += 1
        self._speaking_text = (self._speaking_text + " " + text)[-400:]

        def done():
            self._pending -= 1
            if self._pending <= 0:
                self._pending = 0
                self._speaking = False
                self._set_busy(False)     # speech done — restart the silence timer
                self._rest()

        self.tts.speak(text, on_finish=done)

    def _flush_sentences(self):
        while True:
            chunk, self._buf = config.next_speech_chunk(self._buf)
            if chunk is None:
                break
            self._say_chunk(chunk)

    def _flush_rest(self):
        rest, self._buf = self._buf, ""
        self._say_chunk(rest)

    def _speak(self, text):
        self._buf = ""
        self._say_chunk(text)

    def _rest(self):
        self._emit("state", "listening" if getattr(self.listener, "_active", False) else "sleeping")

    def _stop_now(self):
        """Halt everything: abort generation, stop speech, stay listening."""
        if self.agent:
            self.agent.cancel()
        self.tts.interrupt()
        self._pending = 0
        self._speaking = False
        self._buf = ""
        self._set_busy(False)
        self._rest()

    def _on_command(self, text):
        # "Stop" works even mid-generation — handle it before anything else.
        if skills.is_stop_speaking(text):
            print("\n[patroam] stopped")
            self._stop_now()
            return
        # Barge-in: if PATROAM is talking and the user says something new, stop
        # talking and act on it (ignore it hearing its own voice).
        if self._speaking:
            if config.is_echo(self._speaking_text, text):
                return
            self.tts.interrupt()
            self._pending = 0
            self._speaking = False
            self._buf = ""
        print(f"\n> {text}")
        self._emit("status", text)
        reply = skills.try_handle(text)
        if reply is not None:
            if reply:
                say, show = skills.split_reply(reply)
                print(show)
                self._speak(say)
            else:
                self._stop_now()
            return
        self._emit("state", "thinking")
        self._set_busy(True)            # hold the session through thinking + speaking
        self._buf = ""
        self._speaking_text = ""

        def on_token(t):
            print(t, end="", flush=True)
            self._buf += t
            self._flush_sentences()     # speak each sentence the moment it's ready

        def on_done(full):
            print()
            self._flush_rest()
            if self._pending == 0:
                self._set_busy(False)
                self._rest()

        def on_error(e):
            print(f"[error] {e}")
            self._emit("status", f"error: {e}")
            self._set_busy(False)
            self._rest()

        self.agent.send(text, on_token, on_done, on_error)

    def _on_greet(self):
        g = config.time_greeting()   # time-of-day greeting on wake
        print(f"[patroam] {g}")
        self._speak(g)

    def start(self):
        models = self.provider.list_models()
        if not models:
            print("No models found. Start Ollama and pull a model, e.g.:")
            print("  ollama serve")
            print("  ollama pull llama3")
            print("  (or set ANTHROPIC_API_KEY to use Claude/Opus)")
            return False
        self.agent = Agent(self.provider, model=pick_default(models),
                           system_prompt=config.SYSTEM_PROMPT)
        self.tts = TTSWorker()
        self.tts.start()
        self.listener = WakeWordListener(
            on_command=self._on_command,
            on_status=lambda s: (print(f"[patroam] {s}"), self._emit("status", s)),
            on_wake=lambda: self._emit("state", "listening"),
            on_sleep=lambda: (print("[patroam] 💤 session ended"), self._emit("state", "sleeping")),
            on_greet=self._on_greet,
        )
        try:
            self.listener.start()
        except Exception as e:
            # No mic / audio device — don't take down whatever else is running.
            print(f"[patroam] local voice unavailable (mic error): {e}")
            self.tts.stop()
            return False
        print(f"Local voice online.  Model: {self.agent.model}.  Say \"hey patroam\".")
        # Greet on startup, based on the time of day.
        g = config.time_greeting()
        print(f"[patroam] {g}")
        self._speak(g)
        return True

    def stop(self):
        if self.listener:
            self.listener.stop()
        if self.tts:
            self.tts.stop()


def start_local_voice(provider=None, on_event=None):
    """Start the local voice listener on background threads (non-blocking).
    Returns the LocalVoice handle, or None if it couldn't start."""
    lv = LocalVoice(provider=provider, on_event=on_event)
    return lv if lv.start() else None


def run_daemon():
    """Standalone 24/7 mode — start local voice and block until Ctrl+C."""
    lv = start_local_voice()
    if not lv:
        return
    print("Ctrl+C to quit.\n")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        lv.stop()
