# PATROAM — Personal Agent That Runs On Any Model

A 24/7 personal voice agent that listens for **"hey patroam"**, understands what
you mean, talks back in a natural British voice, and *acts* on your machine —
opening apps, building projects, writing files, reading documents, looking at
your screen, and remembering you. It's **model-agnostic**: run it on a local
model (Llama / Qwen via Ollama) or a cloud model (Claude, or Ollama cloud
models), and it auto-switches to the best model for the task.

---

## What it can do

### 🎙️ Voice & conversation
- **Always-on wake words** — "hey patroam", "patroam", "hey bro", "hey dude",
  "hey agent P", "hey P" (edit `config.WAKE_PHRASES`). After waking it stays in a
  conversation: every utterance is a command, no need to repeat the wake word,
  until ~30 s silence or a stop phrase ("go to sleep", "stop listening").
- **Smart endpointing** — it waits for you to *finish* a sentence (stitches
  pauses together) instead of cutting you off mid-thought.
- **Barge-in** — talk over it and it stops to listen to your new command.
- **"Stop"** — instantly halts both speech and the in-flight reply.
- **Natural voice** — Edge neural TTS (British male `en-GB-RyanNeural`), with an
  offline SAPI fallback. Can **reply in other languages** — say *"reply in
  Vietnamese"* and both the text and the voice switch.
- **Speaks a summary** — long answers are voiced as a one-line gist; the full
  text, code, and links stay in the chat (so it never reads code or URLs aloud).

### 🧠 Runs on any model — and routes by task
- One picker lists local Ollama models **and** cloud models (Claude, Ollama
  `*-cloud`) together. `claude-*` → Anthropic; everything else → Ollama.
- **Automatic per-request routing:** coding questions → a coding model
  (`minimax-m3:cloud`); image/screen questions → the best available vision model
  (Claude if a key is set, else local `qwen2.5vl`); everything else → your
  default. No manual switching. Configure with `PATROAM_MODEL`,
  `PATROAM_CODE_MODEL`, `PATROAM_VISION_MODEL`.

### 🪟 Desktop orb + knowledge-graph inspector
- A glowing **3D-projected neural orb** (pure Canvas2D — no WebGL, no CDN, works
  offline) that reflects state (idle / listening / thinking / speaking); drag to
  rotate, scroll to zoom.
- A side **chat panel** (💬) with clickable links, **syntax-highlighted code
  blocks + Copy buttons**, image thumbnails, and clickable links to files it
  creates.
- A **🧠 Inspector** with two tabs:
  - **Knowledge Graph** — a 3D, rotatable, document-clustered graph; **search** a
    node, **click to focus** (it centers and highlights connections), **fullscreen
    (⤢)**, and **rename / add-link / delete** nodes live.
  - **RAG** — shows the active backend, indexed sources, a **Re-index** button,
    and a test-retrieval box that proves what gets retrieved.

### 💾 Memory & knowledge graph
- Everything PATROAM remembers lives in **one knowledge graph** (`~/.patroam/
  graph.json`) under a `You` entity — no separate memory store.
- It learns from conversation: *"remember that I'm a doctor"*, *"Trump is
  handsome"*, *"connect A to B"*, *"forget that…"* all update the graph.
- **Documents feed the graph** too — indexing runs LLM entity/relation extraction.
- Clean-up commands: *"merge duplicate nodes"*, *"merge A into B"*,
  *"clear the knowledge graph"* (keeps your personal memory).

### 📚 RAG over your documents
- Drop files in `~/.patroam/knowledge` (`.txt .md .pdf .py .json .csv .html …`),
  say *"index my docs"* (or use the Inspector), and answers are grounded in them
  with source citations.
- Real vector DB (**ChromaDB** + Ollama `nomic-embed-text`) with a JSON/keyword
  fallback. **PDF** text via PyMuPDF/pypdf, and **legacy VNI Vietnamese PDFs are
  auto-decoded** to proper Unicode.

### 👁️ Vision — it can see
- *"Look at my screen"* captures the screen; **drag-drop / paste / 📎** an image
  into the chat to ask about it. Routed to the best vision model available
  (`qwen2.5vl` locally, or Claude with a key).

### ⌨️ Coding & projects
- **Create projects** with correct structure — *"create a Flutter iOS app"*,
  *"a Python app"*, *"a website"* — scaffolded from built-in templates (uses
  `flutter create` if installed) and filled with the model's code.
- **Generates files** of any type (`.py .cpp .txt .html …`) into your `~/PATROAM`
  workspace, with **clickable links** to open them.
- **Runs tests / commands** (`pytest`, `flutter test`, `npm test`, builds) in the
  workspace and reacts to the output.
- **Multi-step & interactive** — for non-trivial builds it confirms first, asking
  *"Provider or Riverpod, Sir?"* with **clickable A/B buttons**, then executes.

### ⚙️ Skills (deterministic, work on any model)
- **Open / close apps** ("open Spotify", "close Chrome"), **play music** (Spotify
  Liked Songs).
- **News** — *"what's up"* reads headlines from **your trusted RSS feeds**
  (`config.NEWS_FEEDS` or `~/.patroam/news.json`, ranked by your interests); it
  speaks the titles and puts **clickable links** in the chat.
- **Meta Ads** — *"how are my ads doing"* reads spend / impressions / clicks / CTR.
- **MCP connectors** — external MCP servers (stdio / HTTP / OAuth) become callable.

---

## Run

```bash
pip install -r requirements.txt        # or let app.py auto-install on first run
ollama serve && ollama pull llama3     # a local model to start on

python app.py                 # desktop orb (default)
python app.py --web           # browser app (FastAPI + WebSocket)
python app.py --web --lan     # also reachable from your phone on the LAN
python app.py --tk            # classic Tk orb fallback
python app.py --daemon        # headless, local wake-word only
```

Only **one** instance runs at a time (a single-instance lock prevents two voices
talking at once). To start automatically at login, keep `PATROAM.vbs` in your
Startup folder (it runs `pythonw app.py`). Startup issues are logged to
`~/.patroam/startup.log`.

---

## Configuration

- **Secrets** → `~/.patroam/secrets.json` (see `secrets.example.json`), loaded at
  startup: `ANTHROPIC_API_KEY`, `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`,
  `NEWSAPI_KEY`. Never put keys in tracked source.
- **News feeds** → `~/.patroam/news.json`: `{"feeds": ["…rss…"], "interests": ["ai","vietnam"]}`.
- **Knowledge** → drop documents in `~/.patroam/knowledge`.
- **Workspace** → created files/projects go to `~/PATROAM`.
- **Handy env vars** — `PATROAM_MODEL`, `PATROAM_CODE_MODEL`,
  `PATROAM_VISION_MODEL`, `PATROAM_LANGUAGE`, `PATROAM_SPEAK_SUMMARY`,
  `PATROAM_PAUSE_THRESHOLD`, `PATROAM_TTS_VOICE`.

---

## Project layout

```
app.py                  entry point + single-instance lock + startup log
patroam/
  config.py             models, persona, voice, language, routing, news, paths
  agent/core.py         model-agnostic brain: history, persona, ACTION tool loop,
                        per-request model/vision override, passive graph learning
  providers/            base interface + ollama, anthropic (vision), router
  voice/                wakeword · listener (smart endpointing) · recorder · tts
  skills.py             deterministic commands + intent detectors (coding, vision,
                        file, info, news, language, choices) + data/command split
  actions.py            portable ACTION tool-calling (robust multi-line JSON):
                        write_file, create_project, scaffold, run, ask, relate, …
  files.py              sandboxed file ops, project templates/scaffolding,
                        code-block → file extraction, command/test runner
  graph.py              knowledge graph + memory (You entity), merge/rename/clear,
                        LLM extraction from documents
  rag.py                chunk · embed · retrieve (ChromaDB / JSON), VNI/PDF read
  vision.py             screen capture + image normalization
  vni.py                VNI-Win → Unicode decoder (legacy Vietnamese PDFs)
  news.py               RSS/Atom feeds (+ NewsAPI fallback), interest ranking
  meta_ads.py           Meta Ads stats          llm.py  shared completion registry
  mcp_client.py         MCP connectors          mcp_oauth.py  MCP OAuth
  ui/webview_app.py     desktop controller + pywebview bridge
  ui/web/index.html     orb + chat + 3D graph inspector + widgets
  web/server.py         FastAPI backend for --web         ui/chat.py  Tk fallback
```

---

## Honest notes

- **Quality scales with the model.** Tool-calling, multi-step builds, and graph
  extraction are far better on capable models (Claude / minimax-m3:cloud) than on
  small local ones like `llama3`. Coding requests route to a capable model for
  this reason.
- **Cloud features need internet** (Claude, `*-cloud` models, news, neural voice);
  the local LLM, RAG, graph, and local vision (`qwen2.5vl`) run offline.
- **Llama-3.2-Vision (`mllama`) does not run** on llama.cpp/Ollama — use
  `qwen2.5vl` (local) or Claude for vision.

## Roadmap

1. ✅ Model abstraction · wake word · conversation mode · smart endpointing
2. ✅ Command execution · file/project creation · run tests
3. ✅ Any model + automatic task-based routing (coding / vision / default)
4. ✅ Memory + knowledge graph (3D inspector, live editing)
5. ✅ RAG + vector DB (PDF, VNI decode) · ✅ vision (screen + images)
6. ✅ Interactive widgets · multi-step confirm-then-build
7. ✅ News (RSS) · Meta Ads · MCP connectors
8. ⏳ OpenAI (GPT) provider · richer per-framework scaffolders · cross-device hub
