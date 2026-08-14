"""PATROAM's desktop window.

Hosts the Canvas2D orb + knowledge graph (web/index.html) in a native pywebview
window and wires it to the agent. No WebGL and no JS libraries — everything is
drawn with plain Canvas2D so it works offline. Python pushes state to the page
(window.patroam.setState/...); the page calls back into Python (send text,
toggle always-on, push-to-talk, choose model) through pywebview's JS API.
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
        self._rt = None                 # RealtimeSession while conversational mode is on
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

    # ── realtime voice ────────────────────────────────────────────────────────
    def realtime_status(self):
        s = getattr(self, "_rt", None)
        if not s:
            return {"running": False, "state": "off", "error": "",
                    "configured": bool(config.GEMINI_API_KEY)}
        st = s.stats()
        st["running"] = s.running
        st["configured"] = True
        return st

    def realtime_toggle(self):
        """Start or stop the always-on conversational mode."""
        s = getattr(self, "_rt", None)
        if s and s.running:
            s.stop()
            self._rt = None
            # Hand the microphone back to the classic wake-word listener.
            if self.listener and not self.listener.listening:
                threading.Thread(target=self.listener.start, daemon=True).start()
            self.set_status("realtime voice off")
            self._eval("window.patroam.realtimeChanged && window.patroam.realtimeChanged()")
            return {"running": False}
        if not config.GEMINI_API_KEY:
            return {"running": False, "error": "GEMINI_API_KEY chưa được đặt"}
        if getattr(self, "_rt_starting", False):
            return {"running": False, "error": "đang khởi động"}   # no double session
        self._rt_starting = True
        from ..realtime.session import RealtimeSession
        # Only one thing may own the microphone; park the wake-word listener.
        try:
            if self.listener and self.listener.listening:
                self.listener.stop()
        except Exception:
            pass
        try:
            s = RealtimeSession(on_state=self._rt_state, on_text=self._rt_text,
                                on_ui=self._rt_ui,
                                # Hand over what you were already talking about.
                                history=list(getattr(self.agent, "history", []) or []))
            ok = s.start()
        finally:
            self._rt_starting = False       # always clear, or the button jams
        if not ok:
            self._rt = None
            return {"running": False, "error": s.error}
        self._rt = s
        self.set_status("realtime voice on — cứ nói tự nhiên")
        self._eval("window.patroam.realtimeChanged && window.patroam.realtimeChanged()")
        return {"running": True}

    def _rt_state(self, state):
        """Session state → the orb + the status line."""
        # The orb already knows these moods; reuse them rather than inventing new.
        self.set_state({"listening": "listening", "thinking": "thinking",
                        "speaking": "speaking"}.get(state, "idle"))
        self._eval("window.patroam.realtimeState && window.patroam.realtimeState(%s)"
                   % json.dumps(state))

    def _rt_text(self, who, text):
        """Both sides of the spoken conversation → the chat log.

        The Live model only returns audio, so without the transcriptions the
        chat window stayed empty while you talked."""
        if who == "tool":
            self._eval("window.patroam.realtimeTool && window.patroam.realtimeTool(%s)"
                       % json.dumps(text))
            return
        if who == "detail":
            # The full result (links, paths, task lists) — shown, never spoken.
            self._chat_done(text)
            return
        if not text:
            return
        # Record spoken turns in the SAME history the typed chat uses, so you can
        # switch between talking and typing mid-thought and neither side forgets.
        try:
            role = "user" if who == "you" else "assistant"
            self.agent.history.append({"role": role, "content": text})
        except Exception:
            pass
        if who == "you":
            self._chat_user(text)
        else:
            self._chat_done(text)

    def _rt_ui(self, hint):
        """Open whatever the answer is about — spoken replies alone left the
        screen showing something unrelated."""
        target, _, arg = (hint or "").partition(":")
        if target == "todo":
            self._eval("window.patroam.openTodo && window.patroam.openTodo()")
            self._eval("window.patroam.tasksChanged && window.patroam.tasksChanged()")
        elif target == "calendar":
            self._eval("window.patroam.openCalendar && window.patroam.openCalendar()")
            self._eval("window.patroam.calendarChanged && window.patroam.calendarChanged()")
        elif target == "notes":
            self._eval("window.patroam.openNotes && window.patroam.openNotes()")
            self._eval("window.patroam.notesChanged && window.patroam.notesChanged()")
        elif target == "automations":
            self._eval("window.patroam.openAutomations && window.patroam.openAutomations()")
        elif target == "connectors":
            self._eval("window.patroam.openConnectors && window.patroam.openConnectors()")
        elif target == "graph":
            self._eval("window.patroam.exploreFromText && "
                       "window.patroam.exploreFromText(%s)" % json.dumps(arg or ""))
        elif target == "project":
            self._eval("window.patroam.focusFromText && "
                       "window.patroam.focusFromText(%s)" % json.dumps(arg or ""))
        elif target == "chat":
            self._eval("window.patroam.showChat && window.patroam.showChat()")

    def _ask_pending_nodes(self):
        """If the model proposed facts that would create new nodes, surface them
        and wait for a yes/no instead of writing them silently."""
        try:
            from .. import graph as _g
            nodes = _g.pending_nodes()
            if not nodes:
                return False
            facts = _g.pending()
            vi = (config.RESPONSE_LANGUAGE or "").lower().startswith("viet")
            lines = [("🔗 " + ("Thêm node mới vào graph?" if vi
                               else "Add these new nodes to the graph?"))]
            lines += ["  • " + n for n in nodes[:8]]
            if len(nodes) > 8:
                lines.append(f"  … +{len(nodes) - 8}")
            lines += ["", ("Từ các quan hệ:" if vi else "From:")]
            lines += [f"  {f['s']} —{f['r'].replace('_', ' ').lower()}→ {f['o']}"
                      for f in facts[:6]]
            say = (f"Em muốn thêm {len(nodes)} node mới vào graph: "
                   + ", ".join(nodes[:3]) + ". Anh duyệt không?") if vi else \
                  (f"I'd like to add {len(nodes)} new node"
                   + ("s" if len(nodes) != 1 else "") + " to the graph: "
                   + ", ".join(nodes[:3]) + ". Approve?")
            self._pending_offer = "graph_nodes"
            self._chat_done("\n".join(lines))
            self.speak(say)
            self._eval("window.patroam.graphPendingChanged && "
                       "window.patroam.graphPendingChanged()")
            return True
        except Exception:
            return False

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
            # Always keep the Projects node = real GitHub repos + ClickUp lists,
            # and each project's structure (modules + key files) current.
            try:
                graph.sync_projects()
            except Exception:
                pass
            try:
                graph.index_codebase()
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
        # Single chokepoint for the old TTS — streamed model replies arrive here
        # too, not only through speak(). Gemini owns the voice while it is live.
        if self._rt and self._rt.running:
            return
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
        # While realtime voice is on, GEMINI is the voice. Letting the old TTS
        # speak too produced two overlapping voices — and since its voice follows
        # RESPONSE_LANGUAGE, it was often talking Vietnamese over Gemini's English.
        if self._rt and self._rt.running:
            return
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
        # Just my name, nothing after it: he's getting my attention, not asking
        # for anything. First time in the session that gets the introduction.
        if text and not images and self._is_name_only(text):
            if echo:
                self._chat_user(text)
            self.introduce()
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
        # PATROAM asked to approve new graph nodes — "yes" commits them.
        if self._pending_offer == "graph_nodes" and text:
            self._pending_offer = None
            if echo:
                self._chat_user(text)
            from .. import graph as _g
            vi = (config.RESPONSE_LANGUAGE or "").lower().startswith("viet")
            if skills.is_affirmative(text):
                n = _g.approve_pending()
                msg = (f"Đã thêm {n} fact vào graph." if vi
                       else f"Added {n} fact" + ("s" if n != 1 else "") + " to the graph.")
                self._inspector_dirty()
            else:
                n = _g.reject_pending()
                msg = (f"Đã bỏ qua {n} đề xuất." if vi
                       else f"Discarded {n} proposed fact" + ("s" if n != 1 else "") + ".")
            self._eval("window.patroam.graphPendingChanged && "
                       "window.patroam.graphPendingChanged()")
            self.set_status(msg)
            self._chat_done(msg)
            self.speak(msg)
            return
        # PATROAM asked for the event's missing title/time — this reply IS the
        # answer, so complete the booking instead of sending it to the chat model.
        if self._pending_offer == "cal_slot" and text:
            self._pending_offer = None
            if echo:
                self._chat_user(text)
            rep = skills.supply_event_slot(text)
            if rep:
                if rep.get("offer") in ("cal_add", "cal_slot"):
                    self._pending_offer = rep["offer"]     # still needs an answer
                say, show = skills.split_reply(rep)
                self.set_status(say)
                self._chat_done(show)
                self.speak(say)
                return
        # An event was held back because it clashed — "yes" books it anyway.
        if self._pending_offer == "cal_add" and text:
            self._pending_offer = None
            if echo:
                self._chat_user(text)
            if skills.is_affirmative(text):
                rep = skills.confirm_pending_event()
                if rep:
                    say, show = skills.split_reply(rep)
                    self.set_status(say)
                    self._chat_done(show)
                    self.speak(say)
                    return
            else:
                skills.cancel_pending_event()
                self.speak("Left your calendar as it is, Sir.")
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
                if isinstance(data, dict) and data.get("ui"):
                    # Typed and spoken answers open the SAME panels — one mapping
                    # for both, so a skill's hint can never work by voice only.
                    self._rt_ui(data["ui"])
                if isinstance(data, dict) and data.get("offer") == "focus":
                    self._pending_offer = "focus"   # briefing offered the Focus playlist
                    self._last_brief = time.time()
                if isinstance(data, dict) and data.get("offer") in ("cal_add", "cal_slot"):
                    # cal_add  → clashed, waiting for a yes/no
                    # cal_slot → half-specified, waiting for the title or the time
                    self._pending_offer = data["offer"]
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
            # The model may have proposed facts introducing new entities — those
            # are held back until you approve them.
            self._ask_pending_nodes()
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
        # He said the name and nothing else. The FIRST time in a session that
        # earns a real introduction — what I do, what is connected, what to try.
        from .. import intro
        if not intro.already_given():
            self.introduce()
            return
        # Afterwards: the full briefing (barge-in to skip), unless one ran
        # recently (cooldown) — then just a quick greeting.
        if config.BRIEF_ON_WAKE and (time.time() - self._last_brief) > config.BRIEF_ON_WAKE_COOLDOWN:
            threading.Thread(target=self._brief_local, daemon=True).start()
        else:
            self.greet()   # quick time-of-day greeting

    @staticmethod
    def _is_name_only(text):
        """True for "patroam", "hey patroam", "patroam?" — the name and nothing
        else. `find_command` returns "" for exactly that case, and None when the
        name isn't there at all, so anything with a request attached is safe."""
        try:
            from ..voice.wakeword import find_command
            return find_command(text) == ""
        except Exception:
            return False

    def introduce(self):
        """Say hello properly: capabilities, live integrations, where he stands."""
        from .. import intro
        rep = intro.on_name_only()
        if rep.get("show"):
            self._chat_done(rep["show"])
            self._eval("window.patroam.showChat && window.patroam.showChat()")
        self.set_status(rep["say"])
        self.speak(rep["say"])

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
            if not models:
                # No backend at all: without this PATROAM just sits there silently
                # and a new user has no idea why it never answers.
                msg = ("No AI model found — install Ollama (ollama.com), then run "
                       "'ollama pull qwen3.5', or set ANTHROPIC_API_KEY for Claude.")
                _dlog("no models available")
                self.set_status(msg)
                self._chat_done(msg)
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
        try:
            if self._rt:        # close the mic/websocket, don't strand the session
                self._rt.stop()
        except Exception:
            pass
        try:
            from .. import n8n
            n8n.stop()          # don't leave the automation engine orphaned
        except Exception:
            pass
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
            # No briefing on startup — launching the app isn't a request for one.
            # It runs when you ask ("brief me") or on wake; this flag only exists
            # for putting it back. It also never checked the flag before.
            if config.LAUNCH_BRIEFING:
                threading.Thread(target=self._c._launch_briefing, daemon=True).start()
            # Load models + rebuild graph OFF the UI thread; models push in when ready.
            threading.Thread(target=self._c._push_models, daemon=True).start()
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
            self._c._rt_ui("notes")          # and show it in the Notes panel
            return {"ok": bool(res.get("ok"))}
        self._c._rt_ui("notes")
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

    def refresh_graph(self):
        """Re-scan the real world and update the graph: projects (GitHub repos +
        ClickUp lists) and each project's structure. Saved node positions are left
        untouched, so the layout you arranged survives — only genuinely new nodes
        need placing. Returns the fresh graph payload."""
        try:
            graph.sync_projects()
        except Exception:
            pass
        added = 0
        try:
            added = graph.index_codebase() or 0
        except Exception:
            pass
        try:
            return {"ok": True, "added": added, "triples": graph.all_triples(),
                    "colors": graph.get_colors(), "layout": graph.get_layout()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── realtime voice (Gemini Live) ──────────────────────────────────────────
    def realtime_options(self):
        """What the dropdowns offer: worker models and Gemini voices."""
        try:
            from ..realtime.llm import options as worker_options
            return {
                "workers": worker_options(),
                "worker": config.WORKER_MODEL,
                "voices": [
                    {"id": "Charon", "label": "Charon — nam, trầm"},
                    {"id": "Puck", "label": "Puck — nam, trẻ"},
                    {"id": "Orus", "label": "Orus — nam, ấm"},
                    {"id": "Fenrir", "label": "Fenrir — nam, mạnh"},
                    {"id": "Kore", "label": "Kore — nữ"},
                    {"id": "Aoede", "label": "Aoede — nữ, nhẹ"},
                    {"id": "Leda", "label": "Leda — nữ, trẻ"},
                    {"id": "Zephyr", "label": "Zephyr — nữ, sáng"},
                ],
                "voice": config.REALTIME_VOICE,
                "accent": config.REALTIME_LANG_CODE,
                "accents": [
                    {"id": "en-GB", "label": "British"},
                    {"id": "en-US", "label": "American"},
                    {"id": "en-AU", "label": "Australian"},
                    {"id": "", "label": "Tự động"},
                ],
            }
        except Exception as e:
            return {"workers": [], "voices": [], "error": str(e)}

    def realtime_set(self, worker=None, voice=None, accent=None):
        """Apply a dropdown choice. Voice/accent need a restart of the session —
        they are fixed when the WebSocket is set up."""
        if worker:
            config.set_worker(worker)
        if voice:
            config.REALTIME_VOICE = voice
        if accent is not None:
            config.REALTIME_LANG_CODE = accent
        restart = bool((voice or accent is not None) and self._rt and self._rt.running)
        if restart:
            self.realtime_toggle()      # off
            self.realtime_toggle()      # on, with the new voice
        return {"worker": config.WORKER_MODEL, "voice": config.REALTIME_VOICE,
                "accent": config.REALTIME_LANG_CODE, "restarted": restart}

    def realtime_status(self):
        try:
            return self._c.realtime_status()
        except Exception as e:
            return {"running": False, "error": str(e)}

    def realtime_toggle(self):
        try:
            return self._c.realtime_toggle()
        except Exception as e:
            return {"running": False, "error": str(e)}

    # ── n8n automation engine ─────────────────────────────────────────────────
    def n8n_status(self):
        """Where the local n8n process is up to (for the Automations panel)."""
        try:
            from .. import n8n
            return n8n.status()
        except Exception as e:
            return {"state": "error", "detail": str(e), "url": "", "installed": False}

    def n8n_start(self):
        try:
            from .. import n8n
            ok = n8n.start()
            return {"ok": bool(ok), "status": n8n.status()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def n8n_open_window(self):
        """Open the n8n editor in its own frameless PATROAM window (not a browser)."""
        try:
            import webview
            from .. import n8n
            webview.create_window("PATROAM · Automations", url=n8n.base_url(),
                                  width=1280, height=860, min_size=(760, 560),
                                  background_color="#0a0a0a", frameless=False)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Notes panel ───────────────────────────────────────────────────────────
    def notes_snapshot(self):
        try:
            from .. import notes
            return notes.snapshot()
        except Exception as e:
            return {"notes": [], "counts": {}, "error": str(e)}

    def note_save(self, title, text):
        """Create or overwrite a note (same title = same file)."""
        try:
            from .. import notes
            r = notes.save_note(title, text)
            return {"ok": bool(r.get("ok")), "path": r.get("path", "")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def note_rename(self, note_id, title):
        try:
            from .. import notes
            return {"ok": notes.rename_note(note_id, title)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def note_delete(self, note_id):
        try:
            from .. import notes
            return {"ok": notes.delete_note(note_id)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── MCP connectors ────────────────────────────────────────────────────────
    def mcp_list(self):
        """Configured connectors + live status, for the MCP panel."""
        try:
            from ..mcp_client import MCPClient, catalog, get_mcp
            m = get_mcp()
            return {"servers": m.servers(), "catalog": catalog(),
                    "tools": m.tool_names(), "sdk": MCPClient.sdk_available()}
        except Exception as e:
            return {"servers": [], "catalog": [], "tools": [], "sdk": False,
                    "error": str(e)}

    def mcp_add(self, spec):
        """Add a connector. `spec` comes from the panel:
        {name, url|command, args, transport, auth: none|oauth|key,
         key, secret, header, prefix, client_id, client_secret}

        A pasted key never reaches mcp.json — it is saved to secrets.json and
        the server config only refers to it as ${NAME}."""
        try:
            from .. import config
            from ..mcp_client import get_mcp
            spec = dict(spec or {})
            auth = (spec.pop("auth", "") or "").lower()
            key = (spec.pop("key", "") or "").strip()
            secret_name = (spec.pop("secret", "") or "").strip()
            header = (spec.pop("header", "") or "Authorization").strip()
            prefix = spec.pop("prefix", "") or ""
            secrets = {}
            if auth == "oauth":
                spec["oauth"] = True
                # A client secret is a credential like any other — secrets.json.
                cs = (spec.pop("client_secret", "") or "").strip()
                if cs:
                    key = "MCP_" + "".join(
                        ch if ch.isalnum() else "_"
                        for ch in (spec.get("name") or "server")).upper() + "_CLIENT_SECRET"
                    secrets[key] = cs
                    spec["client_secret"] = "${" + key + "}"
            elif key:
                # Name the secret after the server if the catalog didn't.
                secret_name = secret_name or ("MCP_" + "".join(
                    ch if ch.isalnum() else "_"
                    for ch in (spec.get("name") or "server")).upper() + "_KEY")
                secrets[secret_name] = key
                ref = prefix + "${" + secret_name + "}"
                if spec.get("command"):
                    spec.setdefault("env", {})[secret_name] = "${" + secret_name + "}"
                else:
                    spec.setdefault("headers", {})[header] = ref
            elif secret_name and config.read_secrets().get(secret_name):
                # Re-adding a server whose key is already stored.
                ref = prefix + "${" + secret_name + "}"
                spec.setdefault("headers", {})[header] = ref
            spec = {k: v for k, v in spec.items() if v not in ("", None, [], {})}
            return get_mcp().add_server(spec, secrets=secrets)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mcp_remove(self, name):
        try:
            from ..mcp_client import get_mcp
            return get_mcp().remove_server(name)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mcp_connect(self, name):
        """Connect (or reconnect) one server — this is what triggers the OAuth
        window when the server wants authorization."""
        try:
            from ..mcp_client import get_mcp
            return {"ok": True, "status": get_mcp().connect(name, timeout=300)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mcp_toggle(self, name, disabled):
        try:
            from ..mcp_client import get_mcp
            return get_mcp().set_disabled(name, bool(disabled))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mcp_sign_out(self, name):
        """Drop a connector's stored OAuth token."""
        try:
            from .. import mcp_oauth
            from ..mcp_client import get_mcp
            get_mcp().disconnect(name)
            return {"ok": mcp_oauth.forget(name)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Calendar panel (Google Calendar) ──────────────────────────────────────
    def calendar_snapshot(self, days=14):
        """Upcoming events grouped for the panel, plus which calendars exist."""
        try:
            from .. import gcal
            if not gcal.available():
                return {"events": [], "calendars": [], "counts": {},
                        "error": "Google Calendar isn't connected. Run: "
                                 "python -m patroam.wire_gcal"}
            evs = gcal.list_events(days=int(days), limit=60)
            import datetime
            today = datetime.datetime.now(tz=gcal._tz()).date()
            for e in evs:
                d = gcal._parse(e["start"])
                e["day"] = d.date().isoformat() if d else ""
                e["is_today"] = bool(d and d.date() == today)
                e["time"] = "" if e["all_day"] else (d.strftime("%H:%M") if d else "")
            return {"events": evs, "calendars": gcal.calendars(),
                    "counts": {"total": len(evs),
                               "today": len([e for e in evs if e["is_today"]])}}
        except Exception as e:
            return {"events": [], "calendars": [], "counts": {}, "error": str(e)}

    def calendar_add(self, title, when_text, duration=60, location=""):
        try:
            from .. import gcal, skills
            start = skills._parse_when(when_text) if (when_text or "").strip() else None
            if not start:
                return {"ok": False, "error": "Không hiểu thời gian: " + str(when_text)}
            try:
                mins = int(duration or 60)
            except (TypeError, ValueError):
                mins = 60
            clash = gcal.conflicts(start, start + __import__("datetime").timedelta(minutes=mins))
            ev = gcal.create_event(title, start, duration_minutes=mins, location=location)
            if not ev:
                return {"ok": False, "error": gcal.last_error() or "create failed"}
            return {"ok": True, "event": ev,
                    "clash": [c["title"] for c in clash]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def calendar_delete(self, event_id, calendar_id="primary"):
        try:
            from .. import gcal
            return {"ok": bool(gcal.delete_event(event_id, calendar_id))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── TODO panel (Google Tasks) ─────────────────────────────────────────────
    def tasks_snapshot(self):
        """Open tasks in working order + recently completed, for the TODO tab."""
        try:
            from .. import gcal
            if not gcal.available():
                return {"open": [], "done": [], "counts": {}, "lists": [],
                        "error": "Google Tasks isn't connected. Run: "
                                 "python -m patroam.wire_gcal"}
            snap = gcal.tasks_snapshot()
            if not snap["open"] and not snap["done"] and gcal.last_error():
                snap["error"] = gcal.last_error()
            return snap
        except Exception as e:
            return {"open": [], "done": [], "counts": {}, "lists": [], "error": str(e)}

    def task_add(self, title, due_text="", notes=""):
        """Add a task. `due_text` is free text ("tomorrow", "thứ 6") — parsed by
        the same model-based date reader the calendar uses."""
        try:
            from .. import gcal, skills
            due = skills._parse_when(due_text) if (due_text or "").strip() else None
            t = gcal.create_task(title, due=due, notes=notes)
            if not t:
                return {"ok": False, "error": gcal.last_error() or "create failed"}
            return {"ok": True, "task": t}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def task_done(self, task_id, done=True):
        try:
            from .. import gcal
            ok = gcal.complete_task(task_id) if done else gcal.reopen_task(task_id)
            return {"ok": bool(ok), "error": "" if ok else gcal.last_error()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def task_delete(self, task_id):
        try:
            from .. import gcal
            return {"ok": bool(gcal.delete_task(task_id))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def task_update(self, task_id, title=None, due_text=None):
        try:
            from .. import gcal, skills
            due = None
            if due_text is not None:
                due = skills._parse_when(due_text) if due_text.strip() else False
            ok = gcal.update_task(task_id, title=title,
                                  due=(None if due is None else (due or None)))
            return {"ok": bool(ok), "error": "" if ok else gcal.last_error()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_project_names(self):
        """Names linked under the graph's Projects node — so the UI knows which
        graph nodes should open the project view."""
        try:
            return {"names": graph.projects()}
        except Exception as e:
            return {"names": [], "error": str(e)}

    def get_project_view(self, name):
        """Live project panel data: folder, git state, recent commits, and ClickUp
        tasks (open + recently completed)."""
        try:
            from .. import manage
            return manage.project_view(name)
        except Exception as e:
            return {"name": name, "found": False, "error": str(e)}

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
            # You typed this link yourself — no confirmation needed.
            ok = graph.add(subject, relation or "RELATED_TO", obj, trusted=True)
            return {"ok": bool(ok)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── new-node approvals ────────────────────────────────────────────────────
    def graph_pending(self):
        """Facts the model proposed that would create new nodes, awaiting you."""
        try:
            return {"facts": graph.pending(), "nodes": graph.pending_nodes()}
        except Exception as e:
            return {"facts": [], "nodes": [], "error": str(e)}

    def graph_approve(self, names=None):
        try:
            n = graph.approve_pending(names or None)
            self._inspector_dirty()
            return {"ok": True, "added": n}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def graph_reject(self, names=None):
        try:
            return {"ok": True, "dropped": graph.reject_pending(names or None)}
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

    # When an MCP connector wants authorization, show its sign-in page in a
    # PATROAM window instead of throwing you out to a browser. The window is
    # closed automatically once the redirect comes back with the code.
    def _auth_window(url):
        return webview.create_window(
            "PATROAM · Authorize connector", url=url,
            width=520, height=720, min_size=(420, 520),
            background_color="#0a0a0a", frameless=False)

    try:
        from .. import mcp_oauth
        mcp_oauth.set_opener(_auth_window)
    except Exception:
        pass

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
    # private_mode defaults to True, which throws away cookies and localStorage on
    # exit — that is why n8n asked you to log in again every single launch. A
    # persistent profile keeps its session (and anything else embedded) signed in.
    storage = os.path.join(os.path.expanduser("~"), ".patroam", "webview")
    os.makedirs(storage, exist_ok=True)
    webview.start(debug=debug, private_mode=False, storage_path=storage)
