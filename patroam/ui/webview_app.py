"""PATROAM's WebGL window.

Hosts the Three.js orb (web/index.html) in a native pywebview window and wires
it to the agent. Python pushes state to the page (window.patroam.setState/...);
the page calls back into Python (send text, toggle always-on, push-to-talk,
choose model) through pywebview's JS API.
"""

import json
import os
import queue
import threading

from .. import skills
from ..agent import Agent
from ..providers import OllamaProvider
from ..voice.listener import WakeWordListener
from ..voice.recorder import VoiceRecorder
from ..voice.tts import TTSWorker

HTML_PATH = os.path.join(os.path.dirname(__file__), "web", "index.html")


class Controller:
    """All of PATROAM's behaviour, independent of the rendering layer."""

    def __init__(self, provider=None):
        self.agent = Agent(provider or OllamaProvider())
        self.tts = TTSWorker()
        self.tts.start()
        self.recorder = VoiceRecorder()
        self.listener = None
        self.tts_enabled = True
        self.session_active = False
        self.is_responding = False

        self.window = None
        self._ready = False
        # Push JS updates from a dedicated thread so we never call evaluate_js
        # re-entrantly from inside an incoming API call.
        self._jsq = queue.Queue()
        threading.Thread(target=self._js_pump, daemon=True).start()

    # ── UI bridge ───────────────────────────────────────────────────────────
    def attach(self, window):
        self.window = window

    def _js_pump(self):
        while True:
            js = self._jsq.get()
            if js is None:
                break
            try:
                self.window.evaluate_js(js)
            except Exception:
                pass

    def _eval(self, js):
        if self._ready and self.window:
            self._jsq.put(js)

    def set_state(self, name):
        self._eval(f"window.patroam.setState({json.dumps(name)})")

    def set_status(self, msg):
        self._eval(f"window.patroam.setStatus({json.dumps(msg)})")

    def push_wake(self, on):
        self._eval(f"window.patroam.setWake({json.dumps(bool(on))})")

    # ── state helpers ───────────────────────────────────────────────────────
    def resting_state(self):
        if self.listener and self.listener.listening:
            return "listening" if self.session_active else "sleeping"
        return "idle"

    def rest(self):
        self.set_state(self.resting_state())

    # ── speaking (with self-hearing guard) ───────────────────────────────────
    def speak(self, text):
        if not self.tts_enabled:
            self.rest()
            return
        self.set_state("speaking")
        listening = bool(self.listener and self.listener.listening)
        if listening:
            self.listener.pause()

        def finished():
            if listening:
                self.listener.resume()
            self.rest()

        self.tts.speak(text, on_finish=finished)

    # ── request handling ──────────────────────────────────────────────────────
    def handle(self, text):
        text = (text or "").strip()
        if not text or self.is_responding:
            return
        # Local commands first (e.g. "open Spotify").
        reply = skills.try_handle(text)
        if reply is not None:
            self.set_status(reply)
            self.speak(reply)
            return
        if not self.agent.model:
            self.set_status("No model selected. Is Ollama running?")
            return
        self._respond(text)

    def _respond(self, text):
        self.is_responding = True
        self.set_status("thinking…")
        self.set_state("thinking")

        def on_done(full):
            self.is_responding = False
            self.set_status("")
            self.speak(full)

        def on_error(err):
            self.is_responding = False
            self.set_status(f"error: {err}")
            self.rest()

        # Provider callbacks fire on a worker thread; pushing JS from there is
        # fine (it goes through the pump thread).
        self.agent.send(text, lambda t: None, on_done, on_error)

    # ── always-on ─────────────────────────────────────────────────────────────
    def _ensure_listener(self):
        if not self.listener:
            self.listener = WakeWordListener(
                on_command=self.handle,
                on_status=self.set_status,
                on_wake=self._on_wake,
                on_sleep=self._on_sleep,
            )

    def start_listening(self):
        self._ensure_listener()
        try:
            self.listener.start()
        except Exception as e:
            self.set_status(f"mic error: {e}")
            self.push_wake(False)
            return False
        self.session_active = False
        self.set_state("sleeping")
        self.push_wake(True)
        return True

    def stop_listening(self):
        if self.listener:
            self.listener.stop()
        self.session_active = False
        self.set_status("always-on off")
        self.set_state("idle")
        self.push_wake(False)

    def toggle_always_on(self):
        if self.listener and self.listener.listening:
            self.stop_listening()
            return False
        return self.start_listening()

    def autostart(self):
        """Turn always-on on automatically (mic warm-up runs off-thread)."""
        threading.Thread(target=self.start_listening, daemon=True).start()

    def _on_wake(self):
        self.session_active = True
        self.set_status("listening…")
        self.set_state("listening")

    def _on_sleep(self):
        self.session_active = False
        self.set_status('asleep — say "hey patroam"')
        self.rest()

    # ── push-to-talk ────────────────────────────────────────────────────────
    def record_start(self):
        if self.is_responding:
            return
        self.set_status("recording…")
        self.set_state("listening")
        self.recorder.start()

    def record_stop(self):
        self.set_status("transcribing…")

        def work():
            text = self.recorder.transcribe()
            if not text:
                self.set_status("didn't catch that — try again")
                self.rest()
                return
            self.set_status(f'"{text}"')
            self.handle(text)

        threading.Thread(target=work, daemon=True).start()

    # ── model / settings ──────────────────────────────────────────────────────
    def list_models(self):
        models = self.agent.provider.list_models()
        if models and (not self.agent.model or self.agent.model not in models):
            self.agent.set_model(models[0])
        return models

    def set_model(self, name):
        if name and not name.startswith("("):
            self.agent.set_model(name)

    def set_tts(self, on):
        self.tts_enabled = bool(on)

    def shutdown(self):
        if self.listener:
            self.listener.stop()
        self.tts.stop()
        self._jsq.put(None)


class JsApi:
    """Methods exposed to JavaScript as window.pywebview.api.*"""

    def __init__(self, controller):
        self.c = controller

    def ready(self):
        """Called once the page is loaded. Returns the initial payload."""
        self.c._ready = True
        payload = {
            "models": self.c.list_models(),
            "tts": self.c.tts_enabled,
            "state": self.c.resting_state(),
        }
        # Always-on by default — start listening as soon as the UI is up.
        self.c.autostart()
        return payload

    def send(self, text):
        threading.Thread(target=self.c.handle, args=(text,), daemon=True).start()
        return True

    def toggle_always_on(self):
        return self.c.toggle_always_on()

    def record_start(self):
        self.c.record_start()

    def record_stop(self):
        self.c.record_stop()

    def get_models(self):
        return self.c.list_models()

    def set_model(self, name):
        self.c.set_model(name)

    def set_tts(self, on):
        self.c.set_tts(on)


def run(provider=None):
    import webview

    controller = Controller(provider)
    api = JsApi(controller)
    window = webview.create_window(
        "PATROAM", url=HTML_PATH, js_api=api,
        width=940, height=860, min_size=(520, 520),
        background_color="#05070b",
    )
    controller.attach(window)
    window.events.closed += controller.shutdown
    webview.start()
