# PATROAM — Personal Agent That Runs On Any Model

A personal voice agent that listens for **"hey patroam"** and runs your commands,
designed so you can plug in **any model** behind it — local (Llama via Ollama) or
online (GPT, Opus, …).

## Status

Early. The package today provides:

- **Model-agnostic core** — the agent talks to a `Provider`, so backends are
  swappable. Ollama ships now; OpenAI/Anthropic are next.
- **Always-on wake word + conversation mode** — say "hey patroam" once and it
  stays awake, running every command you speak with no need to repeat the wake
  word. The session ends after ~30s of silence (configurable via
  `config.SESSION_TIMEOUT`) or when you say a stop phrase ("go to sleep",
  "stop listening", …). It mutes the mic while speaking so it doesn't hear
  itself. (v1 spots the word in continuous speech-to-text; a dedicated
  wake-word engine can drop in behind the same interface.)
- **Voice in/out** — speech-to-text input and spoken replies. Default voice is a
  natural neural British male (Edge TTS, `en-GB-RyanNeural`); falls back to an
  offline SAPI voice with no internet. Change it via `config.TTS_VOICE_EDGE` or
  `PATROAM_TTS_VOICE`. PATROAM speaks as a refined British assistant and addresses
  you as "Master" (persona in `config.SYSTEM_PROMPT`).
- **GUI + headless daemon** — a chat window, or a 24/7 background mode.
- **Command execution** — acts on system commands instead of just chatting.
  "Open Spotify" / "launch VS Code" / "close Chrome" actually launch/close the
  app (resolved via Start Menu, registered URI schemes, App Paths, or PATH;
  unknown names like "open YouTube" fall back to the website). See `skills.py`.
- **3D WebGL orb** — the default window is a Three.js particle-sphere energy
  core with additive glow, bloom postprocessing, a swirling accretion disk and
  energy filaments. Drag to rotate, scroll to zoom (interactable), and it reacts
  to state — colour, turbulence, rotation, bloom (orange core + blue energy when
  active, cool and calm when asleep). Runs in a native window via pywebview;
  falls back to a lightweight Tk orb (`--tk`) if pywebview/WebGL is unavailable.

## Run

```bash
pip install -r requirements.txt   # or let app.py auto-install on first run

# Make sure Ollama is running with a model:
ollama serve
ollama pull llama3

python app.py            # 3D WebGL orb window (drag to rotate, scroll to zoom)
python app.py --tk       # classic lightweight Tk orb (no WebGL needed)
python app.py --daemon   # headless, wake-word only
```

## Layout

```
app.py                 entry point (GUI / --daemon)
patroam/
  config.py            URLs, wake-word settings, persona
  skills.py            local command execution (open/close apps, sites)
  agent/core.py        the model-agnostic brain (history + persona)
  providers/           model backends (base interface + ollama)
  voice/
    wakeword.py        "hey patroam" spotting
    listener.py        always-on background listener
    recorder.py        push-to-talk capture
    tts.py             spoken replies
  ui/webview_app.py    default UI: controller + pywebview bridge to the orb
  ui/web/index.html    Three.js 3D orb (WebGL, bloom, interactable)
  ui/chat.py           classic Tk window (--tk fallback)
  ui/visualizer.py     2D animated orb used by the Tk window
```

## Roadmap

1. ✅ Model abstraction (Ollama) + ✅ wake word + always-on + conversation mode
2. ✅ Command execution — open/close apps & sites (extend in `skills.py`)
3. More providers — OpenAI (GPT), Anthropic (Opus)
4. More skills + LLM tool-calling (let the model decide which action to run)
5. Memory & personalization
6. Cross-device (phone, home system) via a local hub
