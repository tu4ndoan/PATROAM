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

from .. import config, graph, rag, skills
from ..agent import Agent
from ..providers import make_provider, pick_default
from ..voice.listener import WakeWordListener
from ..voice.recorder import VoiceRecorder
from ..voice.tts import TTSWorker

HTML_PATH = os.path.join(os.path.dirname(__file__), "web", "index.html")


class Controller:
    """All of PATROAM's behaviour, independent of the rendering layer."""

    def __init__(self, provider=None):
        self.agent = Agent(provider or make_provider())
        self.tts = TTSWorker()
        self.tts.start()
        self.recorder = VoiceRecorder()
        self.listener = None
        self.tts_enabled = True
        self.session_active = False
        self.is_responding = False

        self.window = None
        self._ready = False
        self._speaking = False
        self._speaking_text = ""
        self._pending = 0
        self._buf = ""
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

    def _inspector_dirty(self):
        """Tell the open inspector to reload (graph/RAG may have changed)."""
        self._eval("window.patroam.inspectorChanged && window.patroam.inspectorChanged()")

    def _focus_graph(self, text):
        """Ask the inspector to focus the graph node mentioned in `text` (if any)."""
        self._eval("window.patroam.focusFromText && window.patroam.focusFromText("
                   + json.dumps(text or "") + ")")

    # ── chat panel push ─────────────────────────────────────────────────────────
    def _chat_user(self, text):
        self._eval(f"window.patroam.chatUser({json.dumps(text)})")

    def _chat_token(self, text):
        self._eval(f"window.patroam.chatToken({json.dumps(text)})")

    def _chat_done(self, text):
        self._eval(f"window.patroam.chatDone({json.dumps(text)})")

    def greet(self):
        """Speak a time-of-day greeting and log it (used on startup and on wake)."""
        g = config.time_greeting()
        self._chat_done(g)
        self.speak(g)

    # ── state helpers ───────────────────────────────────────────────────────
    def resting_state(self):
        if self.listener and self.listener.listening:
            return "listening" if self.session_active else "sleeping"
        return "idle"

    def rest(self):
        self.set_state(self.resting_state())

    # ── speaking (streamed chunks; barge-in: keep listening, filter echo) ──────
    def _set_busy(self, busy):
        if self.listener:
            self.listener.set_busy(busy)

    def _say_chunk(self, text):
        text = text.strip()
        if not text:
            return
        if not self.tts_enabled:
            self._set_busy(False)
            self.rest()
            return
        if self._pending == 0:
            self.set_state("speaking")
            self._set_busy(True)          # keep the session alive while speaking
        self._speaking = True
        self._pending += 1
        self._speaking_text = (self._speaking_text + " " + text)[-400:]

        def finished():
            self._pending -= 1
            if self._pending <= 0:
                self._pending = 0
                self._speaking = False
                self._set_busy(False)     # speech done — restart the silence timer
                self.rest()

        self.tts.speak(text, on_finish=finished)

    def _flush_sentences(self):
        while True:
            chunk, self._buf = config.next_speech_chunk(self._buf)
            if chunk is None:
                break
            self._say_chunk(chunk)

    def _flush_rest(self):
        rest, self._buf = self._buf, ""
        self._say_chunk(rest)

    def speak(self, text):
        self._buf = ""
        self._speaking_text = ""
        self._say_chunk(text)

    def _stop_now(self):
        """Halt everything: abort generation, stop speech, stay listening."""
        self.agent.cancel()
        self.tts.interrupt()
        self._speaking = False
        self._pending = 0
        self._buf = ""
        self.is_responding = False
        self._set_busy(False)
        self.set_status("stopped")
        self.rest()

    # ── request handling ──────────────────────────────────────────────────────
    def handle(self, text):
        text = (text or "").strip()
        if not text:
            return
        # "Stop" works even mid-generation — handle it before any other gate.
        if skills.is_stop_speaking(text):
            self._stop_now()
            return
        # Barge-in: interrupt a reply in progress when the user speaks anew.
        if self._speaking:
            if config.is_echo(self._speaking_text, text):
                return
            self.tts.interrupt()
            self._speaking = False
            self._pending = 0
            self._buf = ""
        if self.is_responding:
            return
        self._chat_user(text)
        # Local commands first (e.g. "open Spotify").
        reply = skills.try_handle(text)
        if reply is not None:
            if reply:
                self.set_status(reply)
                self._chat_done(reply)
                self.speak(reply)
                self._inspector_dirty()   # a skill may have changed graph/RAG
                self._focus_graph(text)
            else:
                self._stop_now()
            return
        if not self.agent.model:
            self.set_status("No model selected. Is Ollama running?")
            return
        self._respond(text)

    def _respond(self, text):
        self.is_responding = True
        self.set_status("thinking…")
        self.set_state("thinking")
        self._set_busy(True)            # hold the session through thinking + speaking
        self._buf = ""
        self._speaking_text = ""

        def on_token(t):
            self._chat_token(t)
            self._buf += t
            self._flush_sentences()     # speak each sentence as soon as it's ready

        def on_done(full):
            self.is_responding = False
            self.set_status("")
            self._chat_done(full)
            self._flush_rest()
            self._inspector_dirty()     # the model may have recorded a relation
            self._focus_graph(text)     # focus a node the user asked about
            if self._pending == 0:      # nothing was spoken (e.g. empty/tts off)
                self._set_busy(False)
                self.rest()

        def on_error(err):
            self.is_responding = False
            self.set_status(f"error: {err}")
            self._set_busy(False)
            self.rest()

        # Provider callbacks fire on a worker thread; pushing JS from there is
        # fine (it goes through the pump thread).
        self.agent.send(text, on_token, on_done, on_error)

    # ── always-on ─────────────────────────────────────────────────────────────
    def _ensure_listener(self):
        if not self.listener:
            self.listener = WakeWordListener(
                on_command=self.handle,
                on_status=self.set_status,
                on_wake=self._on_wake,
                on_sleep=self._on_sleep,
                on_greet=self._greet,
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

    def _greet(self):
        self.greet()   # time-of-day greeting on wake

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
            self.agent.set_model(pick_default(models))
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
    """Methods exposed to JavaScript as window.pywebview.api.*

    IMPORTANT: the controller reference is underscore-prefixed (`_c`). pywebview
    serializes the *public* attributes of the js_api object to expose them to JS;
    a public reference to the Controller would lead it into the pywebview window
    (a .NET object) and recurse forever on `SyncRoot` ("maximum recursion depth
    exceeded"). Keeping it private avoids that. All exposed members below are
    methods returning only plain JSON types.
    """

    def __init__(self, controller):
        self._c = controller

    def ready(self):
        """Called when the page is loaded. Idempotent — the page may call this
        more than once (event + fallback), but we only start listening and greet
        once."""
        first = not self._c._ready
        self._c._ready = True
        payload = {
            "models": self._c.list_models(),
            "tts": self._c.tts_enabled,
            "state": self._c.resting_state(),
        }
        if first:
            # Always-on by default — start listening as soon as the UI is up.
            self._c.autostart()
            # Greet the user on launch, based on time of day (once).
            threading.Timer(0.6, self._c.greet).start()
        return payload

    def send(self, text):
        threading.Thread(target=self._c.handle, args=(text,), daemon=True).start()
        return True

    def toggle_always_on(self):
        return bool(self._c.toggle_always_on())

    def record_start(self):
        self._c.record_start()
        return True

    def record_stop(self):
        self._c.record_stop()
        return True

    def get_models(self):
        return self._c.list_models()

    def set_model(self, name):
        self._c.set_model(name)
        return True

    def set_tts(self, on):
        self._c.set_tts(on)
        return True

    # ── Inspector: read-only views into RAG + the knowledge graph ──────────────
    def get_graph(self):
        """Triples for the knowledge-graph visualizer."""
        try:
            return {"triples": graph.all_triples()}
        except Exception as e:
            return {"triples": [], "error": str(e)}

    def get_rag(self):
        """Index status: backend, chunk count, source files."""
        try:
            return rag.stats()
        except Exception as e:
            return {"backend": f"error: {e}", "chunks": 0, "sources": []}

    def rag_query(self, q):
        """Retrieve passages for `q` — proves RAG works from the UI."""
        try:
            return {"hits": rag.search((q or "").strip())}
        except Exception as e:
            return {"hits": [], "error": str(e)}

    def reindex(self):
        """Rebuild the document index + knowledge graph from the knowledge folder."""
        try:
            chunks, files, triples = rag.ingest()
            return {"chunks": chunks, "files": files, "triples": triples}
        except Exception as e:
            return {"chunks": 0, "files": 0, "triples": 0, "error": str(e)}


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
