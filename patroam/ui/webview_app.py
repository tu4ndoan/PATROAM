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
import time

from .. import config, files, graph, media, notify, rag, skills
from ..agent import Agent
from ..providers import make_provider, pick_default
from ..voice.listener import WakeWordListener
from ..voice.recorder import VoiceRecorder
from ..voice.tts import TTSWorker

HTML_PATH = os.path.join(os.path.dirname(__file__), "web", "index.html")


def _dlog(msg):
    """Append a timestamped UI-phase line to the startup log (to locate hangs)."""
    try:
        import datetime
        d = os.path.join(os.path.expanduser("~"), ".patroam")
        with open(os.path.join(d, "startup.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S.%f}  [ui] {msg}\n")
    except Exception:
        pass

# Small standalone editor for the "New Note" pop-up window (tu4ndoan styling).
NOTE_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"/><style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap');
*{box-sizing:border-box} body{margin:0;background:#0a0a0a;color:#fff;
 font-family:"JetBrains Mono",monospace;padding:16px;display:flex;flex-direction:column;height:100vh}
h1{font-size:12px;letter-spacing:.16em;color:#d4d4d8;margin:0 0 12px;font-weight:600}
input,textarea{width:100%;background:#111;border:1px solid #1a1a1a;border-radius:8px;color:#fff;
 font-family:inherit;font-size:13px;padding:9px 11px;outline:none}
input:focus,textarea:focus{border-color:#d4d4d8}
textarea{flex:1;resize:none;margin:10px 0;line-height:1.6}
.row{display:flex;gap:8px}
button{cursor:pointer;border:1px solid #1a1a1a;background:#18181b;color:#fff;border-radius:8px;
 padding:9px 14px;font-family:inherit;font-size:13px;font-weight:600}
button.primary{background:#fff;color:#0a0a0a;border-color:#fff} button:hover{border-color:#d4d4d8}
#msg{font-size:11px;color:#a1a1aa;margin-top:8px;min-height:14px}
</style></head><body>
<h1>— NEW NOTE</h1>
<input id="title" placeholder="Title (optional)"/>
<textarea id="body" placeholder="Write your note…  (Ctrl+Enter to save, Esc to close)"></textarea>
<div class="row"><button class="primary" id="save">Save</button><button id="close">Close</button></div>
<div id="msg"></div>
<script>
const api=()=>(window.pywebview&&window.pywebview.api)||null; const $=i=>document.getElementById(i);
$('save').onclick=async()=>{ const a=api(); if(!a)return;
  const t=$('title').value.trim(), b=$('body').value.trim();
  if(!t&&!b){ $('msg').textContent='Nothing to save.'; return; }
  const r=await a.save(t,b); $('msg').textContent=(r&&r.ok)?'Saved ✓':'Save failed';
  if(r&&r.ok) setTimeout(()=>{ a.close&&a.close(); },500); };
$('close').onclick=()=>{ const a=api(); a&&a.close&&a.close(); };
addEventListener('keydown',e=>{ if(e.key==='Escape'){ const a=api(); a&&a.close&&a.close(); }
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){ $('save').click(); } });
setTimeout(()=>$('title').focus(),120);
</script></body></html>"""


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
        self._pending_offer = None      # e.g. "focus" after the briefing offers a playlist
        self._last_brief = 0.0          # timestamp of the last briefing (wake cooldown)
        self._awaiting_answer = False   # model just asked a choice → next msg is the answer

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

    def _ensure_graph(self):
        """If the knowledge graph is empty, rebuild it from documents + notes +
        projects. Runs AFTER the model is registered (so LLM extraction works) —
        unlike the early RAG bootstrap. Only runs when empty, so it self-heals a
        wiped graph without re-extracting every launch."""
        _dlog("ensure_graph start")
        try:
            from .. import config, graph, rag
            if not getattr(graph, "_LOAD_OK", True):
                # The launch-time read failed (file briefly locked by the previous
                # instance). Retry now instead of mistaking the graph for empty
                # and re-extracting everything from scratch.
                graph._CACHE = None
            was_empty = not graph.all_triples()
            # Always keep the Projects node = real GitHub repos + ClickUp lists.
            try:
                graph.sync_projects()
            except Exception:
                pass
            if not was_empty:
                self._inspector_dirty()
                _dlog("ensure_graph: synced projects, graph already populated")
                return
            # Graph was empty → rebuild from documents + notes.
            added = 0
            has_docs = any(f != "README.txt"
                           for _, _, fs in os.walk(config.KNOWLEDGE_DIR) for f in fs)
            if has_docs:
                added += rag.rebuild_graph() or 0
            added += graph.index_notes() or 0
            self._inspector_dirty()
        except Exception:
            pass
        _dlog("ensure_graph done")

    def _launch_briefing(self):
        """Build the startup briefing (executive summary + dashboard + offer) and
        broadcast it (spoken here, DM'd to Slack). Runs off-thread."""
        _dlog("briefing start")
        try:
            from .. import briefing
            rep = briefing.broadcast_launch()
            self._last_brief = time.time()
            if rep and rep.get("offer") == "focus":
                self._pending_offer = "focus"   # a following "yes" plays the playlist
        except Exception:
            pass
        _dlog("briefing done")

    def _brief_local(self):
        """Speak + show the briefing locally (no Slack DM) — used on wake."""
        try:
            from .. import briefing
            rep = briefing.gather()
            self._last_brief = time.time()
            if rep:
                self._chat_done(rep["show"])
                self.speak(rep["say"])
                if rep.get("offer") == "focus":
                    self._pending_offer = "focus"
            else:
                self.speak(config.time_greeting())
        except Exception:
            self.speak(config.time_greeting())

    def open_note_window(self):
        """Pop a small editor window to write a note (saved to the Notes folder)."""
        try:
            import webview
            api = NoteApi(self)
            api._win = webview.create_window(
                "New Note", html=NOTE_HTML, js_api=api,
                width=460, height=560, background_color="#0a0a0a")
        except Exception as e:
            self.set_status(f"note window error: {e}")

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
        # If the model just asked a choice question, the next message is the ANSWER —
        # send it to the model to CONTINUE the conversation, don't re-route it as a
        # fresh command (that misfired: "Quick Prototype" → resume_project).
        if self._awaiting_answer and text:
            self._awaiting_answer = False
            if echo:
                self._chat_user(text)
            self._respond(text)
            return
        # The briefing offered the Focus playlist — honour a "yes"; any reply clears it.
        if self._pending_offer == "focus" and text:
            self._pending_offer = None
            if skills.is_affirmative(text):
                if echo:
                    self._chat_user(text)
                try:
                    import webbrowser
                    webbrowser.open(config.SPOTIFY_FOCUS_URL)
                except Exception:
                    pass
                self.speak("Putting on your focus playlist, Sir.")
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
                if isinstance(data, dict) and data.get("ui") == "new_note":
                    self.open_note_window()      # pop the note editor
                if isinstance(data, dict) and data.get("offer") == "focus":
                    self._pending_offer = "focus"   # briefing offered the Focus playlist
                    self._last_brief = time.time()
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
        asking = bool(self.agent.ask_widget) or reply.strip().endswith("?")
        if asking:
            return
        wants = skills.wants_file(text) or skills.GO_RE.search(text or "")
        if not wants:
            return
        kind = skills.project_type(text) or skills.project_type(reply) or self._recent_ptype()
        if kind:                                   # a real project → full Planner pipeline
            name = files.guess_project_name(reply, text)
            from .. import planner
            rep = planner.create_project(name, kind, description=text)
            self.agent.files_made = []
            self._chat_done(rep.get("show", ""))   # README path + ClickUp board link
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
            if not self.agent.acted and not self.agent.files_made:
                self._maybe_make_files(text, full)
            if self.agent.files_made:   # show clickable links to any files created
                self._chat_files(self.agent.files_made)
            # Show choice buttons ONLY from the model's explicit `ask` action — no
            # scraping "A or B?" out of prose (that produced bogus options like
            # "scratch"/"do").
            self._awaiting_answer = bool(self.agent.ask_widget
                                         and self.agent.ask_widget.get("options"))
            if self.agent.ask_widget:
                q = self.agent.ask_widget.get("question", "")
                opts = self.agent.ask_widget.get("options", [])
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
        # Awake / work mode → reveal the working interfaces (KG + panels) with the
        # slide-in animation.
        self._eval("window.patroam.enterWork && window.patroam.enterWork(true)")

    def _greet(self):
        # On wake: give the full briefing (you can barge-in to skip), unless one ran
        # recently (cooldown) — then just a quick greeting.
        if config.BRIEF_ON_WAKE and (time.time() - self._last_brief) > config.BRIEF_ON_WAKE_COOLDOWN:
            threading.Thread(target=self._brief_local, daemon=True).start()
        else:
            self.greet()   # quick time-of-day greeting

    def _on_sleep(self):
        self.session_active = False
        self.set_status('asleep — say "hey patroam"')
        # Asleep → hide the interfaces and show just the orb.
        self._eval("window.patroam.enterRest && window.patroam.enterRest('sleep')")
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

    def _push_models(self):
        """Load models OFF the UI thread (the Ollama /api/tags call can take seconds
        and must never block ready()/the GUI), then push them to the picker and,
        once a model is set, rebuild the graph if empty."""
        try:
            models = self.list_models()
            self._eval("window.patroam.setModels(%s)" % json.dumps(models))
        except Exception:
            pass
        try:
            self._ensure_graph()
        except Exception:
            pass

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


class NoteApi:
    """JS API for the standalone New-Note window."""

    def __init__(self, controller):
        self._c = controller
        self._win = None

    def save(self, title, text):
        from .. import notes
        res = notes.save_note(title, text)
        try:
            if res.get("ok"):
                self._c._chat_done(res.get("show", ""))   # mirror into the main chat
                self._c._inspector_dirty()                 # graph gained a Note node
        except Exception:
            pass
        return {"ok": bool(res.get("ok")), "path": res.get("path", "")}

    def close(self):
        try:
            self._win.destroy()
        except Exception:
            pass
        return True


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
        _dlog("ready() enter")
        first = not self._c._ready
        self._c._ready = True
        # Return IMMEDIATELY with no models — loading them hits Ollama (seconds) and
        # this runs on the UI thread, so blocking here greys out the whole window.
        payload = {"models": [], "tts": self._c.tts_enabled, "state": self._c.resting_state()}
        if first:
            self._c.autostart()                     # start listening
            threading.Timer(0.6, self._c.greet).start()   # time-of-day greeting
            # Load models + rebuild graph OFF the UI thread; models push in when ready.
            threading.Thread(target=self._c._push_models, daemon=True).start()
            # Launch briefing a few seconds later (its own thread).
            threading.Timer(5.0, lambda: threading.Thread(
                target=self._c._launch_briefing, daemon=True).start()).start()
        _dlog("ready() return")
        return payload

    # ── frameless window controls (the page draws its own min/max/close) ───────
    def win_minimize(self):
        try:
            self._c.window.minimize()
        except Exception:
            pass
        return True

    def win_maximize(self):
        """Toggle fullscreen — our stand-in for maximise on a frameless window."""
        try:
            self._c.window.toggle_fullscreen()
        except Exception:
            pass
        return True

    def win_close(self):
        try:
            self._c.window.destroy()
        except Exception:
            pass
        return True

    def send(self, text):
        threading.Thread(target=self._c.handle, args=(text,), daemon=True).start()
        return True

    def abort(self):
        """Abort everything in flight: stop generation + speech (from the button)."""
        self._c._stop_now()
        return True

    def new_note(self, text=""):
        """📝 button / 'take a note'. With text → save directly; else open the editor."""
        text = (text or "").strip()
        if text:
            from .. import notes
            res = notes.save_note("", text)
            self._c._chat_done(res.get("show", ""))
            self._c._inspector_dirty()
            return {"ok": bool(res.get("ok"))}
        self._c.open_note_window()
        return {"ok": True}

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
        """Triples + custom node colours + saved positions for the visualizer."""
        try:
            return {"triples": graph.all_triples(), "colors": graph.get_colors(),
                    "layout": graph.get_layout()}
        except Exception as e:
            return {"triples": [], "colors": {}, "layout": {}, "error": str(e)}

    def graph_set_color(self, name, color):
        """Persist a node's colour (hex '#rrggbb', or '' to reset)."""
        try:
            return {"ok": bool(graph.set_color(name, color))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def graph_save_layout(self, positions):
        """Persist node positions the user arranged in the graph, so the layout
        survives restarts. `positions` = {name: {x, y, z, pinned}}."""
        try:
            return {"ok": bool(graph.save_layout(positions or {}))}
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
        frameless=True, easy_drag=False,   # no OS title bar — custom controls in the page
    )
    controller.attach(window)
    window.events.closed += controller.shutdown

    # Make sure the window is actually visible & frontmost once it loads — guards
    # against it being created hidden/minimised/behind at login. Also set the
    # taskbar/title-bar icon to the T-monogram (pywebview has no icon API, so we
    # push it to the native window via WM_SETICON).
    def _set_icon():
        try:
            import ctypes
            ico = os.path.join(os.path.dirname(__file__), "web", "monogram.ico")
            if not os.path.exists(ico):
                return
            u = ctypes.windll.user32
            hwnd = u.FindWindowW(None, "PATROAM")
            if not hwnd:
                return
            WM_SETICON, IMAGE_ICON, LR_LOADFROMFILE = 0x80, 1, 0x10
            for size, which in ((16, 0), (32, 1)):   # ICON_SMALL=0, ICON_BIG=1
                h = u.LoadImageW(None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
                if h:
                    u.PostMessageW(hwnd, WM_SETICON, which, h)   # async — never blocks
        except Exception:
            pass

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
        # Icon setting temporarily disabled while diagnosing the startup freeze.
        if os.environ.get("PATROAM_SET_ICON", "0") in ("1", "true", "True"):
            for delay in (0.5, 1.5, 3.0):
                threading.Timer(delay, _set_icon).start()
    try:
        window.events.loaded += _bring_to_front
    except Exception:
        pass

    # DevTools OFF by default (it kept popping up). Set PATROAM_DEBUG=1 to enable it
    # for troubleshooting (right-click → Inspect → Console).
    debug = os.environ.get("PATROAM_DEBUG", "0") in ("1", "true", "True")
    webview.start(debug=debug)
