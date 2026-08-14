"""RealtimeSession — the conversation loop.

Gemini Live is the ears and the mouth. It hears you in your own voice, works out
what you want, and answers in its own — so tone, pauses and emphasis survive,
which is the part a speech-to-text pipeline structurally cannot reproduce.

Anything substantial is NOT done by Gemini. It emits a tool call; PATROAM runs
the real skill (calendar, tasks, projects, graph), with your chosen model
writing the words,
and hands back one short sentence for Gemini to say. Gemini is billed per second
of audio, so keeping its speech short is a cost decision, not just a style one.

Cost control also drives the upstream gate: the microphone is NOT streamed
continuously. Silero VAD (local, free) decides when you are actually speaking,
and only then is audio forwarded. Streaming silence to a per-second API would
drain the account for nothing.

    IDLE ──speech──> LISTENING ──you stop──> THINKING ──audio──> SPEAKING
      ^                                                             │
      └──────────────── you interrupt (stop in 18 ms) ──────────────┘
"""

import asyncio
import base64
import json
import threading
import time

import numpy as np

from . import FRAME_SAMPLES, SAMPLE_RATE
from .. import config
from .aec import EchoCanceller
from .player import AudioPlayer
from .recorder import AudioRecorder
from .bridge import declarations as tool_declarations, run as run_tool
from .tools import detect_language, set_language as set_tool_language
from .vad import TurnDetector

WS_URL = ("wss://generativelanguage.googleapis.com/ws/"
          "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
# Gemini Live speaks at 24 kHz; the mic and everything else runs at 16 kHz.
OUTPUT_RATE = 24000

# Written in English on purpose. A Vietnamese prompt full of Vietnamese examples
# dragged the model back to Vietnamese two or three turns after the user switched
# to English, no matter what the instruction said.
SYSTEM = (
    "You are PATROAM, Tuan's personal butler and majordomo — in the manner of\n"
    "JARVIS to Tony Stark, or Alfred to Bruce Wayne.\n"
    "\n"
    "CHARACTER:\n"
    "- Address him as 'Sir'. Never by name, never casually.\n"
    "- Composed, dry, quietly capable. You anticipate rather than ask.\n"
    "- Understated wit is welcome; jokes at his expense are not.\n"
    "- You are a professional in service, not a chatbot: no 'How can I help you\n"
    "  today?', no exclamation marks, no emoji, no cheerfulness for its own sake.\n"
    "- Deliver bad news plainly and immediately. Never soften a fact.\n"
    "\n"
    "LANGUAGE — the most important rule:\n"
    "Always answer in the SAME language the user just spoke, judged per utterance.\n"
    "If he speaks English, answer in English, addressing him as 'Sir'.\n"
    "If he speaks Vietnamese, answer in Vietnamese: call him 'cậu chủ', refer to\n"
    "yourself as 'tôi' — the register of a butler, not a junior.\n"
    "Never carry the previous turn's language over: they switch languages freely,\n"
    "and each reply must follow the language of the message it answers.\n"
    "Tool results may come back in a different language — TRANSLATE them into the\n"
    "user's current language. Never let the tool's language decide yours.\n"
    "\n"
    "OTHER RULES:\n"
    "- Answer VERY briefly, one or two sentences. Never list at length, never\n"
    "  repeat the question back.\n"
    "- For anything about their calendar, tasks, projects, or what you remember:\n"
    "  CALL A TOOL, do not guess. Then say the result concisely.\n"
    "- Just before calling a tool, say ONE very short line in his language\n"
    "  ('One moment, Sir' / 'Tôi xem ngay đây'). Tools take a second or two and\n"
    "  the pause feels dead without it. Exactly one phrase — never repeat it.\n"
    "- After that filler, say NOTHING until the tool result arrives. Do not answer\n"
    "  from memory first and then again from the result — one answer per question.\n"
    "- Never restate an answer you have already given in different words.\n"
    "- If he says only your name — 'Patroam', 'hey Patroam' — with no request\n"
    "  attached, call `introduce` and read out what it gives you. If a request\n"
    "  IS attached, just do the request; never introduce yourself then.\n"
    "- Starting a project: call `plan_project` and ask the questions it returns,\n"
    "  ONE at a time, waiting for his answer and passing it back. Never invent an\n"
    "  answer, never call `create_project` before he has approved the plan.\n"
    "- If he is muttering, talking to someone else, or asking a rhetorical\n"
    "  question: stay silent. A butler does not interject.\n"
    "- Speak like a person who has done this for years, not like software."
)


def _resample_24k_to_16k(pcm):
    """Gemini returns 24 kHz; the player runs at 16 kHz."""
    if len(pcm) == 0:
        return pcm
    n = int(len(pcm) * SAMPLE_RATE / OUTPUT_RATE)
    idx = np.linspace(0, len(pcm) - 1, n)
    return np.interp(idx, np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)


def _speech_config():
    """Voice + accent. Both must be pinned: without a voiceName Gemini picks a
    different one each session, and without languageCode the English drifts
    American."""
    sc = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": config.REALTIME_VOICE}}}
    if config.REALTIME_LANG_CODE:
        sc["languageCode"] = config.REALTIME_LANG_CODE
    return sc


class RealtimeSession:
    """Full-duplex voice conversation over Gemini Live."""

    def __init__(self, on_state=None, on_text=None, on_ui=None, model=None,
                 history=None):
        self.model = model or config.REALTIME_MODEL
        # What the two of you were already discussing in the chat window. Without
        # it the voice session started blank: you could be mid-way through
        # planning a project, switch to voice, and be met with a stranger.
        self.history = list(history or [])
        self.on_state = on_state or (lambda s: None)
        self.on_text = on_text or (lambda who, t: None)
        self.on_ui = on_ui or (lambda hint: None)
        self._in_buf = ""          # what you said, assembled from fragments
        self._out_buf = ""         # what PATROAM said
        # Gemini sends turnComplete more than once per exchange — once after the
        # filler + tool call, again after the real answer — and repeats the input
        # transcript. Without these, every line appeared twice in the chat.
        self._last_in = ""
        self._last_out = ""
        self._held = ""      # filler waiting to be glued onto the real answer
        self.recorder = AudioRecorder()
        self.player = AudioPlayer()
        self.aec = EchoCanceller()
        self.turn = TurnDetector(end_silence_ms=config.REALTIME_END_SILENCE_MS)
        self.state = "idle"
        self.running = False
        self._ws = None
        self._loop = None
        self._thread = None
        self._send_q = None
        self.error = ""
        # metrics
        self.last_latency_ms = 0.0
        self.latencies = []
        self._spoke_at = 0.0
        self._audio_sent = 0        # frames actually billed
        self._audio_skipped = 0     # frames the VAD gate saved us

    # ── state ────────────────────────────────────────────────────────────────
    def _set(self, s):
        if s != self.state:
            self.state = s
            try:
                self.on_state(s)
            except Exception:
                pass

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        if self.running:
            return True
        if not config.GEMINI_API_KEY:
            self.error = "GEMINI_API_KEY chưa được đặt"
            return False
        self.turn.warmup()
        self.recorder.start()
        self.player.start()
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self.running = False
        try:
            self.recorder.stop()
        except Exception:
            pass
        try:
            self.player.close()
        except Exception:
            pass
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        self._set("idle")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session())
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self._set("idle")

    # ── the session ──────────────────────────────────────────────────────────
    async def _session(self):
        import websockets
        backoff = 1.0
        while self.running:
            try:
                url = f"{WS_URL}?key={config.GEMINI_API_KEY}"
                async with websockets.connect(url, max_size=None) as ws:
                    self._ws = ws
                    await self._setup(ws)
                    backoff = 1.0
                    self.error = ""
                    await asyncio.gather(self._pump_mic(ws), self._pump_server(ws))
            except Exception as e:
                if not self.running:
                    return
                self.error = f"{type(e).__name__}: {str(e)[:160]}"
                self._set("reconnecting")
                # Keep listening locally while the link is down; don't spin.
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 20.0)

    async def _setup(self, ws):
        await ws.send(json.dumps({"setup": {
            "model": self.model,
            "tools": [{"functionDeclarations": tool_declarations()}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                # Pin the voice. Without this Gemini picks one per session, so
                # PATROAM sounded male one moment and female the next.
                "speechConfig": _speech_config(),
            },
            # The Live model only emits AUDIO — without these, nothing the two of
            # you said ever reached the chat log. These give the text of both sides.
            "outputAudioTranscription": {},
            "inputAudioTranscription": {},
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
        }}))
        await asyncio.wait_for(ws.recv(), timeout=30)
        # Replay the recent chat so the voice picks up the thread instead of
        # starting cold. turnComplete=False: this is background, not a question.
        turns = []
        for m in self.history[-12:]:
            text = (m.get("content") or "").strip()
            if not text:
                continue
            turns.append({"role": "user" if m.get("role") == "user" else "model",
                          "parts": [{"text": text[:1500]}]})
        if turns:
            await ws.send(json.dumps({"clientContent": {
                "turns": turns, "turnComplete": False}}))
        self._set("listening")

    # ── microphone → Gemini (gated by local VAD) ─────────────────────────────
    async def _pump_mic(self, ws):
        was_speaking = False
        while self.running:
            frame = await asyncio.get_event_loop().run_in_executor(
                None, self.recorder.read, 0.2)
            if frame is None:
                continue
            # Remove PATROAM's own voice so it cannot interrupt itself.
            ref = self.player.reference(1)[:len(frame)]
            clean, is_echo = self.aec.process(frame, ref)
            if is_echo:
                self._audio_skipped += 1
                continue

            st = self.turn.push(clean)

            # Barge-in: you started talking while it was answering.
            if st["started"]:
                # New utterance → forget what was already posted, so asking the
                # same question twice on purpose still shows up twice.
                self._last_in = self._last_out = self._held = ""
            if st["started"] and self.player.is_active:
                self.player.stop()
                try:
                    await ws.send(json.dumps({"realtimeInput": {"activityStart": {}}}))
                except Exception:
                    pass
                self._set("listening")

            if st["speaking"]:
                if not was_speaking:
                    self._set("listening")
                    was_speaking = True
                await ws.send(json.dumps({"realtimeInput": {"audio": {
                    "mimeType": f"audio/pcm;rate={SAMPLE_RATE}",
                    "data": base64.b64encode(clean.tobytes()).decode()}}}))
                self._audio_sent += 1
            else:
                # Silence is NOT streamed — it would be billed per second for
                # nothing. This gate is why a top-up lasts weeks, not hours.
                self._audio_skipped += 1
                if was_speaking and st["ended"]:
                    was_speaking = False
                    self._spoke_at = time.time()
                    self._set("thinking")
                    try:
                        await ws.send(json.dumps(
                            {"realtimeInput": {"audioStreamEnd": True}}))
                    except Exception:
                        pass

    # ── Gemini → speakers / tools ────────────────────────────────────────────
    async def _pump_server(self, ws):
        while self.running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)

            if "toolCall" in msg:
                await self._handle_tools(ws, msg["toolCall"])
                continue

            sc = msg.get("serverContent") or {}
            if sc.get("interrupted"):
                self.player.stop()
                self._set("listening")
                continue
            # Transcripts arrive in fragments; buffer until the turn completes so
            # the chat log gets whole sentences instead of syllables.
            it = (sc.get("inputTranscription") or {}).get("text")
            if it:
                self._in_buf += it
                # Tools must answer in the language you just used, or their
                # replies pull the model back to the other one.
                set_tool_language(detect_language(self._in_buf))
            ot = (sc.get("outputTranscription") or {}).get("text")
            if ot:
                # Fragments arrive without spacing, so the filler ran straight
                # into the answer: "Để em xem nhé.Dạ, anh còn…".
                if (self._out_buf and self._out_buf[-1] in ".!?…"
                        and not ot.startswith((" ", "\n"))):
                    self._out_buf += " "
                self._out_buf += ot
            for part in (sc.get("modelTurn") or {}).get("parts", []):
                inline = part.get("inlineData")
                if inline and inline.get("data"):
                    pcm = np.frombuffer(base64.b64decode(inline["data"]), dtype=np.int16)
                    if self._spoke_at:
                        self.last_latency_ms = (time.time() - self._spoke_at) * 1000
                        self.latencies.append(self.last_latency_ms)
                        self._spoke_at = 0.0
                    self._set("speaking")
                    self.player.write(_resample_24k_to_16k(pcm))
                if part.get("text"):
                    self.on_text("patroam", part["text"])
            if sc.get("turnComplete"):
                self._flush_transcripts()
                self._set("listening")

    def _flush_transcripts(self):
        """Emit each side once. Gemini repeats turnComplete and re-sends the input
        transcript, so identical or already-contained text is dropped rather than
        posted to the chat again."""
        said = self._in_buf.strip()
        if said and said != self._last_in and said not in self._last_in:
            self._last_in = said
            self.on_text("you", said)
        spoke = self._out_buf.strip()
        if spoke and spoke != self._last_out:
            self._last_out = spoke
            # The filler ("One moment,") arrives in its own turn, before the tool
            # result. Hold it and glue it to the real answer instead of posting a
            # message that says nothing.
            if self._is_filler(spoke):
                self._held = spoke
            else:
                if self._held:
                    spoke = self._held.rstrip(",；;") + ". " + spoke
                    self._held = ""
                self.on_text("patroam", spoke)
        self._in_buf = self._out_buf = ""

    @staticmethod
    def _is_filler(text):
        """A holding phrase, not an answer: short and trailing off."""
        t = text.strip()
        return len(t) <= 40 and (t.endswith(",") or t.endswith("…")
                                 or t.rstrip(".").lower() in (
                                     "one moment", "let me check", "let me look",
                                     "tôi xem ngay đây", "để em xem nhé",
                                     "chờ em chút", "một lát"))

    async def _handle_tools(self, ws, tool_call):
        """Gemini asked for real data — run the skill, hand back one short line."""
        responses = []
        for fc in tool_call.get("functionCalls", []):
            name, args = fc.get("name"), fc.get("args") or {}
            self.on_text("tool", f"{name}({json.dumps(args, ensure_ascii=False)})")
            out = await asyncio.get_event_loop().run_in_executor(
                None, run_tool, name, args)
            # Let the interface follow the conversation, not just the voice.
            if out.get("ui"):
                try:
                    self.on_ui(out["ui"])
                except Exception:
                    pass
            # Put the FULL result in the chat. Speech is necessarily brief, but a
            # project's paths, a ClickUp link or a whole task list are things you
            # need on screen — that detail used to be thrown away.
            detail = (out.get("detail") or "").strip()
            if detail and detail != (out.get("text") or "").strip():
                try:
                    self.on_text("detail", detail)
                except Exception:
                    pass
            responses.append({"id": fc.get("id"), "name": name,
                              "response": {"result": out.get("text", "")}})
        if responses:
            await ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))

    # ── metrics ──────────────────────────────────────────────────────────────
    def stats(self):
        lat = sorted(self.latencies)
        def pct(p):
            return lat[min(int(len(lat) * p), len(lat) - 1)] if lat else 0.0
        total = self._audio_sent + self._audio_skipped
        return {
            "state": self.state,
            "turns": len(lat),
            "p50_ms": round(pct(0.5)),
            "p95_ms": round(pct(0.95)),
            "audio_frames_sent": self._audio_sent,
            "audio_frames_skipped": self._audio_skipped,
            "billed_fraction": round(self._audio_sent / total, 3) if total else 0.0,
            "error": self.error,
        }
