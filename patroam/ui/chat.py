"""PATROAM main window.

An orb-centric, minimalist interface: the animated core fills the window and
conveys state (sleeping vs active) on its own. There is no chat transcript —
replies are spoken aloud. A slim control bar at the bottom holds the mic,
always-on toggle, and a text box for typed input.
"""

import threading
import tkinter as tk
from tkinter import ttk

from .. import config, skills
from ..agent import Agent
from ..providers import make_provider, pick_default
from ..voice.listener import WakeWordListener
from ..voice.recorder import VoiceRecorder
from ..voice.tts import TTSWorker
from .visualizer import Visualizer


class PatroamChat(tk.Tk):
    DARK_BG = "#0a0c11"
    PANEL_BG = "#0f1219"
    ACCENT = "#4f8ef7"
    TEXT_FG = "#e2e8f0"
    MUTED = "#5b6577"
    SUCCESS = "#22c55e"
    DANGER = "#ef4444"
    FONT_CODE = ("Courier New", 10)
    FONT_MAIN = ("Courier New", 11)

    def __init__(self, provider=None):
        super().__init__()
        self.title("PATROAM")
        self.geometry("760x720")
        self.minsize(420, 460)
        self.configure(bg=self.DARK_BG)

        self.agent = Agent(provider or make_provider())
        self.is_responding = False
        self.recorder = VoiceRecorder()
        self.is_recording = False
        self.tts = TTSWorker()
        self.tts.start()
        self.listener = None
        self._session_active = False

        self._build_ui()
        self._refresh_models()
        self._set_visual_state("idle")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Always-on by default — start listening shortly after the window is up.
        self.after(700, self._toggle_always_on)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        top = tk.Frame(self, bg=self.DARK_BG, pady=8)
        top.pack(fill="x", padx=16)
        tk.Label(top, text="⬡ PATROAM", bg=self.DARK_BG, fg=self.ACCENT,
                 font=("Courier New", 13, "bold")).pack(side="left")

        right = tk.Frame(top, bg=self.DARK_BG)
        right.pack(side="right")
        self.model_var = tk.StringVar()
        self.model_box = ttk.Combobox(right, textvariable=self.model_var, width=20,
                                      state="readonly", font=self.FONT_CODE)
        self.model_box.pack(side="left")
        self.model_box.bind("<<ComboboxSelected>>", self._on_model_change)
        tk.Button(right, text="⟳", bg=self.DARK_BG, fg=self.ACCENT, relief="flat",
                  font=("Courier New", 12, "bold"), activebackground=self.PANEL_BG,
                  cursor="hand2", command=self._refresh_models).pack(side="left", padx=4)

        # The orb dominates the window.
        self.viz = Visualizer(self, bg=self.DARK_BG)
        self.viz.pack(fill="both", expand=True, padx=8, pady=4)

        # Slim status line.
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, bg=self.DARK_BG, fg=self.MUTED,
                 font=("Courier New", 9), anchor="center").pack(fill="x", pady=(0, 6))

        # Control bar.
        bar = tk.Frame(self, bg=self.PANEL_BG)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        bar.configure(highlightbackground="#1b2130", highlightthickness=1)

        row = tk.Frame(bar, bg=self.PANEL_BG)
        row.pack(fill="x", padx=10, pady=8)

        self.voice_btn = tk.Button(
            row, text="🎙  Hold", bg="#161c2b", fg=self.TEXT_FG, relief="flat",
            font=("Courier New", 11, "bold"), activebackground="#202a40",
            cursor="hand2", padx=12, pady=6)
        self.voice_btn.pack(side="left")
        self.voice_btn.bind("<ButtonPress-1>", self._voice_press)
        self.voice_btn.bind("<ButtonRelease-1>", self._voice_release)

        self.wake_btn = tk.Button(
            row, text="😴 Always-on", bg="#161c2b", fg=self.MUTED, relief="flat",
            font=("Courier New", 11, "bold"), activebackground="#202a40",
            cursor="hand2", padx=12, pady=6, command=self._toggle_always_on)
        self.wake_btn.pack(side="left", padx=8)

        self.tts_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="🔊", variable=self.tts_var, bg=self.PANEL_BG,
                       fg=self.MUTED, selectcolor=self.DARK_BG,
                       activebackground=self.PANEL_BG, font=self.FONT_CODE
                       ).pack(side="left", padx=6)

        self.input_box = tk.Entry(row, bg="#0a0e16", fg=self.TEXT_FG,
                                  insertbackground=self.ACCENT, relief="flat",
                                  font=self.FONT_MAIN)
        self.input_box.pack(side="left", fill="x", expand=True, padx=8, ipady=6)
        self.input_box.bind("<Return>", lambda e: self._send_text())

        tk.Button(row, text="→", bg=self.ACCENT, fg="white", relief="flat",
                  font=("Courier New", 12, "bold"), activebackground="#3a78e0",
                  cursor="hand2", padx=12, command=self._send_text).pack(side="left")

    # ── Visual state ───────────────────────────────────────────────────────────
    def _resting_state(self):
        if self.listener and self.listener.listening:
            return "listening" if self._session_active else "sleeping"
        return "idle"

    def _set_visual_state(self, name):
        self.viz.set_state(name)

    def _rest(self):
        self._set_visual_state(self._resting_state())

    def _on_wake(self):
        self._session_active = True
        self._set_status("listening…")
        self._set_visual_state("listening")

    def _on_sleep(self):
        self._session_active = False
        self._set_status('asleep — say "hey patroam"')
        self._rest()

    def _greet(self):
        self._speak(config.greeting())

    def _set_status(self, msg, error=False):
        self.status_var.set(msg)

    # ── Speaking ────────────────────────────────────────────────────────────────
    def _speak(self, full):
        """Speak a reply (if voice is on), pausing the mic so PATROAM doesn't hear
        itself, then settle the orb back to its resting state."""
        if not self.tts_var.get():
            self._rest()
            return
        self._set_visual_state("speaking")
        listening = bool(self.listener and self.listener.listening)
        if listening:
            self.listener.pause()

        def finished():
            if listening:
                self.listener.resume()
            self.after(0, self._rest)

        self.tts.speak(full, on_finish=finished)

    # ── Sending ───────────────────────────────────────────────────────────────
    def _send_text(self):
        text = self.input_box.get().strip()
        if not text or (self.is_responding and not skills.is_stop_speaking(text)):
            return
        self.input_box.delete(0, "end")
        self._send(text)

    def _stop_now(self):
        """Halt everything: abort generation, stop speech, stay listening."""
        self.agent.cancel()
        self.tts.interrupt()
        self.is_responding = False
        self.send_btn.configure(state="normal", bg=self.ACCENT)
        self._set_status("stopped")
        self._rest()

    def _send(self, text):
        if not text.strip():
            return
        # "Stop" works even mid-generation — handle it first.
        if skills.is_stop_speaking(text):
            self._stop_now()
            return
        # Try to act on it locally first (e.g. "open Spotify").
        handled = skills.try_handle(text)
        if handled is not None:
            if handled:
                say, _show = skills.split_reply(handled)
                self._set_status(say)
                self._speak(say)
            else:
                self._stop_now()
            return

        model = self.model_var.get()
        if not model or model.startswith("("):
            self._set_status("No model selected. Is Ollama running?", error=True)
            return
        self.agent.set_model(model)
        self._respond(text)

    def _respond(self, text):
        self.is_responding = True
        self._set_status("thinking…")
        self._set_visual_state("thinking")

        def on_done(full):
            self.is_responding = False
            self._set_status("")
            self._speak(full)

        def on_error(err):
            self.is_responding = False
            self._set_status(f"error: {err}", error=True)
            self._rest()

        self.agent.send(
            text,
            lambda t: None,  # tokens stream silently; the orb conveys activity
            lambda f: self.after(0, on_done, f),
            lambda e: self.after(0, on_error, e),
        )

    # ── Push-to-talk ─────────────────────────────────────────────────────────
    def _voice_press(self, e):
        if self.is_responding:
            return
        self.is_recording = True
        self.voice_btn.configure(text="● Rec", bg=self.DANGER)
        self._set_status("recording… release to send")
        self._set_visual_state("listening")
        self.recorder.start()

    def _voice_release(self, e):
        if not self.is_recording:
            return
        self.is_recording = False
        self.voice_btn.configure(text="🔄 …", bg="#854d0e")
        self._set_status("transcribing…")

        def transcribe():
            text = self.recorder.transcribe()
            self.after(0, self._after_transcribe, text)

        threading.Thread(target=transcribe, daemon=True).start()

    def _after_transcribe(self, text):
        self.voice_btn.configure(text="🎙  Hold", bg="#161c2b")
        if not text:
            self._set_status("didn't catch that — try again", error=True)
            self._rest()
            return
        self._set_status(f'"{text}"')
        self._send(text)

    # ── Always-on wake word ────────────────────────────────────────────────────
    def _toggle_always_on(self):
        if self.listener and self.listener.listening:
            self.listener.stop()
            self.wake_btn.configure(text="😴 Always-on", fg=self.MUTED)
            self.voice_btn.configure(state="normal")
            self._session_active = False
            self._set_status("always-on off")
            self._set_visual_state("idle")
            return

        if not self.listener:
            self.listener = WakeWordListener(
                on_command=lambda t: self.after(0, self._on_wake_command, t),
                on_status=lambda s: self.after(0, self._set_status, s),
                on_wake=lambda: self.after(0, self._on_wake),
                on_sleep=lambda: self.after(0, self._on_sleep),
                on_greet=lambda: self.after(0, self._greet),
            )
        try:
            self.listener.start()
        except Exception as e:
            self._set_status(f"mic error: {e}", error=True)
            return
        self.wake_btn.configure(text="👂 Always-on", fg=self.SUCCESS)
        self.voice_btn.configure(state="disabled")
        self._session_active = False
        self._set_visual_state("sleeping")

    def _on_wake_command(self, text):
        if self.is_responding and not skills.is_stop_speaking(text):
            return
        self._send(text)

    # ── Utility ──────────────────────────────────────────────────────────────
    def _on_model_change(self, e=None):
        self.agent.set_model(self.model_var.get())

    def _refresh_models(self):
        models = self.agent.provider.list_models()
        if models:
            self.model_box["values"] = models
            if not self.model_var.get() or self.model_var.get() not in models:
                self.model_var.set(pick_default(models))
            self.agent.set_model(self.model_var.get())
            self._set_status(f"{len(models)} model(s) ready")
        else:
            self.model_box["values"] = ["(no models)"]
            self.model_var.set(self.model_box["values"][0])
            self._set_status("Ollama not reachable. Run: ollama serve", error=True)

    def _on_close(self):
        self.viz.stop()
        if self.listener:
            self.listener.stop()
        self.tts.stop()
        self.destroy()
