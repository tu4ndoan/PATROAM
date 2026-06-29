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

from .. import config, files, graph, media, notify, rag, skills
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
        # Receive proactive messages (e.g. the news watch) → speak + show them.
        notify.subscribe(self._notify)

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

    def _explore_graph(self, text):
        """Open the knowledge graph fullscreen and focus what the user asked about."""
        self._eval("window.patroam.exploreFromText && window.patroam.exploreFromText("
                   + json.dumps(text or "") + ")")

    # ── chat panel push ─────────────────────────────────────────────────────────
    def _chat_user(self, text):
        self._eval(f"window.patroam.chatUser({json.dumps(text)})")

    def _chat_token(self, text):
        self._eval(f"window.patroam.chatToken({json.dumps(text)})")

    def _chat_done(self, text):
        self._eval(f"window.patroam.chatDone({json.dumps(text)})")

    def _chat_files(self, paths):
        """Show clickable links to files PATROAM just created."""
        if paths:
            self._eval(f"window.patroam.chatFiles({json.dumps(paths)})")

    def _notify(self, payload):
        """A proactive alert (news, etc.) arrived: show it in chat, and speak it
        if PATROAM isn't already busy talking/answering."""
        try:
            show = (payload or {}).get("show") or ""
            say = (payload or {}).get("say") or show
            if show:
                self._chat_done(show)
            if say and self.tts_enabled and not self.is_responding and not self._speaking:
                self.speak(say)
        except Exception:
            pass

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
        if config.skip_in_speech(text) or "```" in text:
            return                       # don't read scaffolding or code aloud
        text = config.strip_urls(text)   # never read links aloud
        if not text:
            return
        # Summary mode (model replies): voice only the first ~SPEAK_SUMMARY_CHARS,
        # i.e. the lead summary; the full reply still appears in the chat.
        if getattr(self, "_summarizing", False):
            if self._spoken_len >= config.SPEAK_SUMMARY_CHARS:
                return
            self._spoken_len += len(text)
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
        self._summarizing = False      # skill/greeting replies are spoken in full
        self._spoken_len = 0
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
    def handle(self, text, images=None, echo=True):
        text = (text or "").strip()
        if not text and not images:
            return
        # "Stop" works even mid-generation — handle it before any other gate.
        if text and skills.is_stop_speaking(text):
            self._stop_now()
            return
        # Barge-in: interrupt a reply in progress when the user speaks anew.
        if self._speaking:
            if text and config.is_echo(self._speaking_text, text):
                return
            self.tts.interrupt()
            self._speaking = False
            self._pending = 0
            self._buf = ""
        if self.is_responding:
            return
        if echo:
            self._chat_user(text)
        # "Change the knowledge-graph view" (flat / sphere / toggle) — handled in the UI.
        gm = skills.graph_view_mode(text)
        if gm:
            self._eval("window.patroam.setGraphMode(%s)" % json.dumps(gm))
            say = ("I've flattened the knowledge graph." if gm == "flat"
                   else "Back to the sphere view." if gm == "sphere"
                   else "I've changed the graph view.")
            self.set_status(say)
            self.speak(say)
            return
        # An attached image (dropped/pasted into the chat) → straight to the vision model.
        if images:
            from .. import vision
            imgs = [i for i in (vision.normalize_image_b64(i) for i in images) if i]
            vm = config.choose_vision_model(self.agent.provider.list_models())
            if not vm:
                self.set_status("No vision model available — pull qwen2.5vl or add a Claude key.")
                self._respond(text or "Describe this image.")
                return
            self._respond(text or "What's in this image? Describe it.", images=imgs, model=vm)
            return
        # Live data fetches stay deterministic (news, ads) — reliable, no double-talk.
        data = skills.data_handle(text)
        if data is not None:
            if data:
                say, show = skills.split_reply(data)
                self.set_status(say)
                self._chat_done(show)     # chat shows links; speech omits them
                self.speak(say)
                self._focus_graph(text)
            return
        if not self.agent.model:
            # No model available → fall back to deterministic commands entirely.
            self._run_command(text, spoken="")
            return
        # LLM-first: the model understands & converses; it calls tools to act, and
        # _respond() runs the deterministic command as a fallback if it didn't.
        want_screen = skills.wants_screen(text)
        if skills.is_info_query(text) and not want_screen:
            self._explore_graph(text)
        images = None
        req_model = None
        if want_screen:
            from .. import vision
            vm = config.choose_vision_model(self.agent.provider.list_models())
            if not vm:
                self.set_status("No vision model available — pull qwen2.5vl or add a Claude key.")
            else:
                self.set_status("looking at your screen…")
                shot = vision.screenshot_b64()
                if shot:
                    images = [shot]
                    req_model = vm                       # superior VISION model
        elif config.CODE_MODEL and skills.is_coding_query(text):
            if config.CODE_MODEL in self.agent.provider.list_models():
                req_model = config.CODE_MODEL            # switch to the CODING model
        self._respond(text, images=images, model=req_model)

    def _recent_ptype(self):
        """Project type mentioned earlier in the conversation (flutter/python/…)."""
        for m in reversed(self.agent.history[-10:]):
            if m.get("role") == "user":
                t = skills.project_type(m.get("content", ""))
                if t:
                    return t
        return None

    def _maybe_make_files(self, text, reply):
        """After a reply: if the user wanted a project/file and the model is
        actually proceeding (not still asking), create the files for real."""
        asking = bool(skills.extract_choices(reply)) or reply.strip().endswith("?")
        if asking:
            return
        wants = skills.wants_file(text) or skills.GO_RE.search(text or "")
        if not wants:
            return
        kind = skills.project_type(text) or skills.project_type(reply) or self._recent_ptype()
        if kind:                                   # build a real project of that type
            name = files.guess_project_name(reply, text)
            self.agent.files_made = files.scaffold_from_reply(kind, name, reply)
        else:                                      # plain file(s) from the reply's code
            self.agent.files_made = (files.save_project_from_reply(reply, text)
                                     or files.save_code_from_reply(reply, text))

    def _run_command(self, text, spoken=""):
        """Run a deterministic command. If the model already `spoken` something,
        only execute the side-effect (no double-talk); else speak the result."""
        reply = skills.command_handle(text)
        if reply is None:
            if not spoken.strip():
                self.set_status("No model selected. Is Ollama running?")
            return
        if reply == "":
            self._stop_now()
            return
        say, show = skills.split_reply(reply)
        self.set_status(say)
        if not spoken.strip():        # the model said nothing → voice the command's reply
            self._chat_done(show)
            self.speak(say)
        self._inspector_dirty()

    def _respond(self, text, images=None, model=None):
        self.is_responding = True
        self.set_status("thinking…")
        self.set_state("thinking")
        self._set_busy(True)            # hold the session through thinking + speaking
        self._buf = ""
        self._speaking_text = ""
        self._summarizing = config.SPEAK_SUMMARY   # voice only the lead summary
        self._spoken_len = 0

        # Watchdog: if the model never calls back (e.g. a cloud model stalls / is
        # offline), don't leave the UI stuck on "thinking" with is_responding=True
        # forever — that silently drops every later message. Auto-recover.
        def _watchdog():
            if self.is_responding:
                self.is_responding = False
                self.agent.cancel()
                self.set_status("The model didn't respond — please try again, Sir.")
                self._set_busy(False)
                self.rest()
        self._resp_watch = threading.Timer(90, _watchdog)
        self._resp_watch.daemon = True
        self._resp_watch.start()

        def _cancel_watch():
            try:
                self._resp_watch.cancel()
            except Exception:
                pass

        def on_token(t):
            self._chat_token(t)
            self._buf += t
            self._flush_sentences()     # speak each sentence as soon as it's ready

        def on_done(full):
            _cancel_watch()
            self.is_responding = False
            self.set_status("")
            self._chat_done(full)
            self._flush_rest()
            # LLM-first fallback: if the model understood but didn't emit the tool
            # call for a clear command, run it deterministically so it still happens.
            if not self.agent.acted:
                self._run_command(text, spoken=full)
            # If the user wanted a file and the model wrote code (in a block) but
            # didn't emit a write_file action, save the code block(s) ourselves.
            if not self.agent.files_made:
                self._maybe_make_files(text, full)
            if self.agent.files_made:   # show clickable links to any files created
                self._chat_files(self.agent.files_made)
            # Show choice buttons — from the model's ask action, or detected "A or B?".
            q, opts = "", None
            if self.agent.ask_widget:
                q = self.agent.ask_widget.get("question", "")
                opts = self.agent.ask_widget.get("options", [])
            else:
                opts = skills.extract_choices(full)
            if opts:
                self._eval("window.patroam.askWidget(%s, %s)" % (json.dumps(q), json.dumps(opts)))
            self._inspector_dirty()     # the model may have recorded a relation
            self._focus_graph(text)     # focus a node the user asked about
            if self._pending == 0:      # nothing was spoken (e.g. empty/tts off)
                self._set_busy(False)
                self.rest()

        def on_error(err):
            _cancel_watch()
            self.is_responding = False
            self.set_status(f"error: {err}")
            self._set_busy(False)
            self.rest()

        # Provider callbacks fire on a worker thread; pushing JS from there is
        # fine (it goes through the pump thread).
        try:
            self.agent.send(text, on_token, on_done, on_error, images=images, model=model)
        except Exception as e:
            on_error(e)

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

    def abort(self):
        """Abort everything in flight: stop generation + speech (from the button)."""
        self._c._stop_now()
        return True

    def send_image(self, text, image_b64):
        """Send a message with an image dropped/pasted into the chat. The page
        already showed the thumbnail, so don't echo the user bubble again."""
        threading.Thread(target=self._c.handle, args=(text,),
                         kwargs={"images": [image_b64], "echo": False}, daemon=True).start()
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

    def open_url(self, url):
        """Open a link from the chat in the system browser (not inside the orb)."""
        try:
            import webbrowser
            webbrowser.open((url or "").strip())
            return True
        except Exception:
            return False

    def open_path(self, path):
        """Open a file PATROAM created with its default app (from a chat link)."""
        try:
            import os
            os.startfile(path)   # Windows: open with the associated program
            return True
        except Exception:
            try:
                import subprocess
                subprocess.Popen(["explorer", "/select,", path])  # fallback: reveal it
                return True
            except Exception:
                return False

    def copy_clipboard(self, text):
        """Copy text (e.g. a code snippet) to the Windows clipboard. Works from
        this worker thread without needing a browser secure-context."""
        try:
            import ctypes
            from ctypes import wintypes
            text = text or ""
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            # 64-bit-correct signatures (defaults truncate handles/pointers to 32-bit).
            k.GlobalAlloc.restype = wintypes.HGLOBAL
            k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
            k.GlobalLock.restype = ctypes.c_void_p
            k.GlobalLock.argtypes = [wintypes.HGLOBAL]
            k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
            u.SetClipboardData.restype = wintypes.HANDLE
            u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
            data = text.encode("utf-16-le") + b"\x00\x00"
            if not u.OpenClipboard(0):
                return False
            try:
                u.EmptyClipboard()
                h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
                p = k.GlobalLock(h)
                ctypes.memmove(p, data, len(data))
                k.GlobalUnlock(h)
                u.SetClipboardData(CF_UNICODETEXT, h)
            finally:
                u.CloseClipboard()
            return True
        except Exception:
            return False

    # ── Inspector: read-only views into RAG + the knowledge graph ──────────────
    def get_graph(self):
        """Triples + custom node colours for the knowledge-graph visualizer."""
        try:
            return {"triples": graph.all_triples(), "colors": graph.get_colors()}
        except Exception as e:
            return {"triples": [], "colors": {}, "error": str(e)}

    def graph_set_color(self, name, color):
        """Persist a node's colour (hex '#rrggbb', or '' to reset)."""
        try:
            return {"ok": bool(graph.set_color(name, color))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_node(self, name):
        """Detail for a clicked graph node: the source documents it came from and
        any images in those documents (as inline data URIs)."""
        try:
            docs = graph.node_docs(name)
            images = []
            for d in docs:
                for p in media.images_for_doc(d):
                    uri = media.data_uri(p)
                    if uri:
                        images.append({"src": uri, "doc": d})
                    if len(images) >= 8:
                        break
                if len(images) >= 8:
                    break
            return {"docs": docs, "images": images}
        except Exception as e:
            return {"docs": [], "images": [], "error": str(e)}

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

    # ── live graph editing from the inspector ─────────────────────────────────
    def graph_rename(self, old, new):
        try:
            n = graph.rename(old, new)
            return {"ok": True, "moved": n, "name": graph._norm(new)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def graph_remove(self, name):
        try:
            return {"ok": True, "removed": graph.forget(name)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def graph_add(self, subject, relation, obj):
        try:
            ok = graph.add(subject, relation or "RELATED_TO", obj)
            return {"ok": bool(ok)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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

    # Make sure the window is actually visible & frontmost once it loads — guards
    # against it being created hidden/minimised/behind at login.
    def _bring_to_front():
        try:
            window.restore()
        except Exception:
            pass
        try:
            window.on_top = True
            window.on_top = False
        except Exception:
            pass
    try:
        window.events.loaded += _bring_to_front
    except Exception:
        pass

    # debug=True enables the webview DevTools (right-click → Inspect → Console),
    # so JavaScript errors in the orb/chat UI are visible. Toggle with
    # PATROAM_DEBUG=0 to disable.
    debug = os.environ.get("PATROAM_DEBUG", "1") not in ("0", "false", "False", "")
    webview.start(debug=debug)
