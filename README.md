# PATROAM — Personal Agent That Runs On Any Model

A personal voice agent that listens for **"hey patroam"** and runs your commands,
designed so you can plug in **any model** behind it — local (Llama via Ollama) or
online (GPT, Opus, …).

## Status

Early. The package today provides:

- **Model-agnostic core** — the agent talks to a `Provider`, so backends are
  swappable. Ollama ships now; OpenAI/Anthropic are next.
- **Wake words + conversation mode** — wake it with any of several phrases
  ("hey patroam", "patroam", "hey bro", "hey dude", "hey agent P", "hey P" —
  edit `config.WAKE_PHRASES`). After waking it stays awake, running every command
  you speak with no need to repeat the wake word, until ~30s of silence
  (`config.SESSION_TIMEOUT`) or a stop phrase ("go to sleep", "stop listening").
- **Barge-in** — while PATROAM is speaking, just talk over it: it stops and
  acts on your new command. It filters out hearing its own voice, but works best
  with headphones (over speakers, echo can confuse it without hardware echo
  cancellation).
- **Voice in/out** — speech-to-text input and spoken replies. Default voice is a
  natural neural British male (Edge TTS, `en-GB-RyanNeural`); falls back to an
  offline SAPI voice with no internet. Change it via `config.TTS_VOICE_EDGE` or
  `PATROAM_TTS_VOICE`. PATROAM speaks as a refined British assistant and addresses
  you as "Master" (persona in `config.SYSTEM_PROMPT`).
- **Web app** — `python app.py --web` serves PATROAM in the browser (FastAPI +
  WebSocket). Same orb, model streaming, and skills; voice runs in the browser
  (Web Speech API for mic + speechSynthesis for replies). Includes a collapsible
  side **chat panel** (💬 button) showing the conversation. Add `--lan` to reach
  it from your phone/other devices on the network.
- **Speaks as it generates** — replies are spoken sentence-by-sentence the moment
  each sentence is ready, instead of waiting for the whole answer, so it starts
  talking almost immediately.
- **Time-of-day greeting** — on launch and on wake, PATROAM greets you with
  "Good morning/afternoon/evening, Sir." based on the clock.
- **Toggleable chat panel** — both the desktop orb and the web app have a 💬
  button to show/hide a side transcript of the conversation.
- **Desktop GUI + headless daemon** — a native orb window, or a 24/7 background mode.
- **Command execution** — acts on system commands instead of just chatting.
  "Open Spotify" / "launch VS Code" / "close Chrome" actually launch/close the
  app (resolved via Start Menu, registered URI schemes, App Paths, or PATH;
  unknown names like "open YouTube" fall back to the website). "Play some music"
  opens Spotify on your Liked Songs and starts playback. See `skills.py`.
- **Memory (learns you over time)** — remembers facts about you across sessions
  and feeds them into every reply. Say "remember that I…", "forget…", or "what
  do you remember about me", and the model can also save things on its own.
  Stored in `~/.patroam/memory.json`. See `memory.py`.
- **Model-driven actions (tool-calling)** — the model can decide to remember,
  open/close an app, or play music mid-conversation via a portable `ACTION:`
  protocol that works on any model. See `actions.py`.
- **Ad stats** — ask "how are my ads doing" / "how's my latest ad" and PATROAM
  reads your Meta ad numbers (spend, impressions, clicks, CTR). Direct Meta API,
  no OAuth or model tool-calling, so it's reliable on any model. See `meta_ads.py`.
- **News** — say "what's up" (or "what's new", "tech news", "headlines") and it
  reads the latest headlines via NewsAPI. See `news.py`.
- **Secrets** — API keys/tokens go in `~/.patroam/secrets.json` (see
  `secrets.example.json`), loaded at startup — never in tracked source, no env-var
  fiddling. Keys: `ANTHROPIC_API_KEY`, `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`,
  `NEWSAPI_KEY`.
- **MCP connectors** — connect PATROAM to external MCP servers (e.g. Meta's
  official Meta Ads connector) and their tools become callable by the model; data
  tools (like ad stats) flow back into the reply. Supports stdio, HTTP/SSE, and
  **OAuth** (browser authorize once, tokens stored & refreshed). Configure servers
  in `~/.patroam/mcp.json` (see `mcp.example.json`). See `mcp_client.py`.
- **Always-on by default** — the wake-word listener starts automatically; just
  say "hey patroam". (Toggle it off with the on-screen button if needed.)
- **Wireframe orb** — the default window is a glowing, displaced wireframe
  sphere (blue→magenta neon, bright nodes, surrounding dot cloud, pink bloom).
  It's pure Canvas 2D — **no WebGL/GPU and no CDN**, so it's light and works
  offline. Drag to rotate, scroll to zoom (interactable); it reacts to state
  via colour, surface turbulence, rotation and glow, with an adaptive quality
  step-down if the framerate dips. Runs in a native window via pywebview;
  falls back to a Tk orb (`--tk`) if pywebview is unavailable.

## Run

```bash
pip install -r requirements.txt   # or let app.py auto-install on first run

# Make sure Ollama is running with a model:
ollama serve
ollama pull llama3

python app.py               # desktop orb window (drag to rotate, scroll to zoom)
python app.py --web         # serve in the browser at http://127.0.0.1:8800
python app.py --web --daemon  # web app AND local-machine voice, at the same time
python app.py --web --lan   # also reachable from other devices on your network
python app.py --tk          # classic Tk orb fallback
python app.py --daemon      # headless, local wake-word only
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
  ui/web/index.html    desktop orb page (pywebview)
  web/server.py        FastAPI backend (REST + WebSocket) for web mode
  web/static/          web frontend: index.html, orb.js, app.js
  ui/chat.py           classic Tk window (--tk fallback)
  ui/visualizer.py     2D animated orb used by the Tk window
```

## Roadmap

1. ✅ Model abstraction + ✅ wake word + always-on + conversation mode
2. ✅ Command execution — open/close apps & sites (extend in `skills.py`)
3. ✅ Any model — local Ollama **and** Claude (Opus/Sonnet/Haiku) via one picker
4. More providers — OpenAI (GPT)
5. ✅ Memory & personalization + ✅ model-driven actions (tool-calling)
6. ✅ MCP connectors (external tools, e.g. Meta Ads)
7. More providers — OpenAI (GPT); more skills
8. Cross-device (phone, home system) via a local hub

## Running on Claude / Opus

PATROAM is model-agnostic. To run it on Claude Opus:

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."   # Windows (new terminal after)
# then pick "claude-opus-4-8" from the model dropdown, or default to it:
set PATROAM_MODEL=claude-opus-4-8 && python app.py
```

Local Ollama models and Claude models appear together in the picker; choosing a
`claude-*` model routes requests to Anthropic, anything else to Ollama. (Note:
"Opus 3" was retired by Anthropic — the current Opus is `claude-opus-4-8`.)
