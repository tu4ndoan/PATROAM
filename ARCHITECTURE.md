# PATROAM — Architecture & Code Documentation

PATROAM is a 24/7, model-agnostic personal voice agent: a Three.js/Canvas "orb"
desktop app that listens for a wake word, understands natural language with an LLM,
maintains a knowledge graph + RAG over your documents, and acts on your behalf
(open apps, fetch live data, plan & manage software projects, take notes, brief you
each day). It runs locally on your machine and reaches out to Ollama/Claude for
inference and to Slack/ClickUp/Fab/SSI/etc. for data.

---

## 1. Run modes — `app.py`

```
python app.py                 # desktop orb window (pywebview)  ← default
python app.py --web           # serve PATROAM in a browser (FastAPI)
python app.py --web --daemon  # web AND local-machine voice together
python app.py --tk            # classic lightweight Tkinter window
python app.py --daemon        # headless, 24/7, local wake-word only
```

`main()` does, in order:
1. `_acquire_single_instance()` — binds `127.0.0.1:49517`; if taken, another PATROAM
   is running and this one exits (prevents two voices talking over each other).
2. `_bootstrap()` — verifies/installs dependencies **once**, then drops
   `~/.patroam/.bootstrap-ok` and skips the (slow) re-import checks on later launches.
3. `_start_rag()` — background-indexes the knowledge folder if needed.
4. `_start_integrations()` — graph backup, news watcher, Slack Socket-Mode bot.
5. Launches the chosen frontend (default: `ui/webview_app.run`).

Startup diagnostics are appended to `~/.patroam/startup.log` (`_log`, plus `[ui]`
and `[voice]` phase lines).

---

## 2. The brain — `patroam/agent/core.py`

`Agent` is the model-agnostic core the UI and daemon both drive. It owns:
- the **conversation history**, persona (`config.SYSTEM_PROMPT`), and per-turn system
  prompt assembly (`_system()` = persona + tool docs + graph profile + language +
  retrieved RAG/graph context),
- the **action layer**: `ACTION: <name> <json>` directives the model emits are parsed
  out (`actions.split`), executed (`actions.run`), and hidden from spoken/streamed text,
- a **tool-result loop** (`_MAX_TOOL_ROUNDS`): if a tool returns data, it's fed back so
  the model answers with it,
- `complete(prompt)` — a synchronous one-shot completion registered with `llm` so other
  subsystems (RAG extraction, skills, endpointing) can use the active model.

`send(text, on_token, on_done, on_error, images, model)` streams a reply; `cancel()`
aborts; `_learn(text)` passively records stated facts into the graph.

### Providers — `patroam/providers/`
A small abstraction so any backend works:
- `base.py` — the `Provider` interface (`list_models`, `stream_chat`).
- `ollama.py` — local Ollama over HTTP (`/api/tags`, `/api/chat`).
- `anthropic.py` — Claude via the `anthropic` SDK (needs `ANTHROPIC_API_KEY`).
- `router.py` — **`RouterProvider`** aggregates both; routes by model name
  (`claude-*` → Anthropic, else Ollama). `pick_default(models)` chooses the start model
  from `config.DEFAULT_MODEL` (exact → case-insensitive → substring).

### Portable tool-calling — `patroam/actions.py`
Native tool-calling APIs differ per backend and small local models do them poorly, so
PATROAM uses a universal text protocol: the model ends its reply with
`ACTION: <name> <json>` lines. `_balanced_json` parses multi-line JSON (even with code
inside strings). Actions: `remember/forget`, `open_app/close_app/play_music`,
`write_file/make_dir`, **`create_project`/`scaffold`/`plan`** (all → the Planner
pipeline), `ask` (A/B choice widget), `run` (shell), `relate/unrelate/merge` (graph),
plus any MCP tools. `tools_prompt()` documents them for the model.

### LLM registry — `patroam/llm.py`
A tiny singleton holding the active model's `complete(prompt, system, timeout)`. The
Agent registers it on creation so subsystems that aren't wired to a Provider can still
ask the current model for one-shot completions.

---

## 3. Voice — `patroam/voice/`

- **`listener.py` — `WakeWordListener`**: always-on mic via `speech_recognition`
  (Google STT — needs internet). Hears the wake word → opens a *conversation session*
  (no need to repeat "hey patroam"). **Smart endpointing**: speech is captured in short
  chunks fed to `_endpoint_loop`, which stitches them and waits an *adaptive grace*
  (heuristic `_completeness`, optionally an LLM check) before dispatching a COMPLETE
  command. `pause()/resume()` while speaking (avoid self-hearing); `set_busy()` holds
  the session open during a reply. Emits `[voice]` log lines for diagnosis.
- **`wakeword.py`** — `find_command` (fuzzy-matches `config.WAKE_PHRASES`, returns any
  trailing command) and `is_stop_phrase`.
- **`recorder.py`** — push-to-talk recording + transcription.
- **`tts.py` — `TTSWorker`**: speaks replies. Edge neural voice (natural, needs net) with
  a pyttsx3 offline fallback; per-language voice; `interrupt()` for barge-in.

---

## 4. Desktop UI — `patroam/ui/webview_app.py` + `web/index.html`

`web/index.html` is a single-file app: the Canvas2D **orb**, the **knowledge-graph**
visualizer (3D sphere / flat plane, drag/zoom/pan/hover/select, colour picker), the
**chat** panel (streaming, code blocks + copy, image drop/paste, A/B widgets, file
links), and the **inspector** (graph + RAG). Styled to the tu4ndoan design system
(monochrome, JetBrains/Share-Tech Mono, technical grid), with the T-monogram
favicon/logo.

`webview_app.py`:
- **`Controller`** — all behaviour independent of rendering. Bridges Python→page via a
  pumped `evaluate_js` queue (`_eval`, `set_state/set_status/chat*`), and owns the
  request pipeline `handle()` → deterministic `data_handle` skills → LLM `_respond`.
  Key details: barge-in, "stop", the **awaiting-answer** gate (a reply to an A/B question
  goes to the model, not the intent router), the focus-playlist offer, wake→briefing,
  and graph fullscreen-on-wake.
- **`JsApi`** — methods exposed to JS (`ready`, `send`, `abort`, `new_note`,
  `get_graph/get_node/graph_*`, `rag_*`, `reindex`, model/tts, clipboard, open url/path).
  `ready()` returns instantly and loads models **off the UI thread** (`_push_models`) so
  the window never freezes.
- **`NoteApi`** — the small "New Note" popup window.

Other frontends: `web/server.py` (FastAPI + static `web/static/`) for `--web`;
`ui/__init__.py`'s Tkinter `PatroamChat` for `--tk`; `daemon.py` for headless voice.

---

## 5. Skills, routing & actions — `patroam/skills.py`

The bridge between what you say and what PATROAM does. `data_handle(text)`:
1. **LLM intent router** (`_route_intent`) — the model classifies the message into a
   skill + params (stock/index/gold/fab/ads/news/**briefing**/new_note/project_status/
   **resume_project**/note_suggestions/backup_graph/none) — *any wording, no keywords*.
2. `_dispatch` routes to the deterministic skill (exact output, clickable links).
3. `_regex_data_handle` is the **offline/fallback** backstop (regexes) used only when the
   model is unavailable.

`command_handle` runs system commands/graph edits as a fallback when the model didn't
emit the matching tool. Also here: `split_reply`, `graph_view_mode` (flat/sphere by
voice), `is_affirmative`, project-type detection, `open_url_in_brave`, etc.

---

## 6. Knowledge graph — `patroam/graph.py`

Triples `(subject, relation, object)` with confidence + timestamp in
`~/.patroam/graph.json`. First-class container nodes: **`You`** (your memory — no
separate memory.json), **`Projects`**, **`Notes`**. Core: `add/remove_triple/forget`,
`rename/merge/merge_duplicates` (dedupe variants without losing links), per-node
`set_color`, `render_profile`/`render_for` (context injection), `extract_into` (LLM
document→triples). Project/Note helpers: `add_project`, `add_note_entry`,
`index_projects`/`index_notes` (title from the note's `# ` line → keeps Vietnamese),
**`sync_projects`** (Projects node = real GitHub repos + ClickUp lists), `backup`.

`patroam/media.py` extracts images from documents (PDF figures) so clicking a node can
show the document's pictures.

---

## 7. RAG — `patroam/rag.py`

Drop files in `KNOWLEDGE_DIR` → `ingest()` chunks + embeds (Ollama `nomic-embed-text`,
else keyword fallback) into ChromaDB (if available) or a JSON index, and LLM-extracts a
knowledge graph from the docs. `retrieve/context_for` return the top passages injected
into the Agent's context. `rebuild_graph()` re-extracts the graph from docs (used on
launch when the graph is empty). PDFs via PyMuPDF/pypdf; legacy VNI-Win Vietnamese is
decoded (`patroam/vni.py`) and NFC-normalised.

---

## 8. Projects — planning, creation, management

- **`planner.py`** — `build_plan` (LLM → scope/stack/non-functionals/milestones/backlog/
  risks), writes **`plan.md`** + README, `git init` (you push manually) in the GitHub
  root, pushes a **ClickUp** list, records the project in the graph, opens a **private
  Slack `#devlog-<project>`** channel, and registers everything. `project_status()`
  reads real projects.
- **`registry.py`** — `~/.patroam/projects.json`: name → folder / git remote / ClickUp
  list id / Slack channel id / plan.md. The single source of truth for resuming.
- **`manage.py`** — `discover_projects()` (git repos in the GitHub root + ClickUp lists),
  `project_progress()`, and **`resume(name)`** ("let's work on X, where were we?") which
  pulls git status + ClickUp in-progress task + recent Slack dev-log + plan.md next step
  → a focused resume with an LLM-recommended next action.
- **`clickup.py`** — REST client: `resolve_space`, `push_roadmap`, `list_tasks`,
  `summary`. **`slack_bot.py`** — Socket-Mode bot (chat from your phone) +
  `create_devlog_channel` + proactive `notify` DMs.

---

## 9. Notes, briefing, live data

- **`notes.py`** — quick capture to `~/.patroam/notes/*.md` (same title overwrites → no
  duplicates), indexed into the graph; `review()` surfaces suggestions + schedule
  conflicts. The 📝 button / "take a note" opens the note window or saves dictated text.
- **`briefing.py`** — the daily Chief-of-Staff briefing: **(A)** spoken executive summary,
  **(B)** a chat-only dashboard (priorities, business, ClickUp tasks, projects, news,
  notes, next action), **(C)** a spoken "Focus playlist?" offer. Uses a
  `~/.patroam/session.json` snapshot for since-last-session deltas (Fab sales, completed
  ClickUp tasks, "what you were working on"). Fires on launch, on wake, and by intent
  ("time to work"). Delivered via the `notify` hub.
- **Live data**: `gold.py` (USD+VND), `stocks.py` (SSI FastConnect — VN stocks/indices),
  `fab.py` (Fab store sales via a downloaded CSV), `news.py` (+ `news_watch.py` polls
  every 5 min), `meta_ads.py`. **`notify.py`** = pub/sub hub so proactive messages reach
  the orb (spoken) and Slack (DM).

---

## 10. Config, secrets & data files

- **`config.py`** — every per-machine setting, paths, model choices, persona/system
  prompt, wake/endpointing params, feature flags. Loads `~/.patroam/secrets.json` into
  the environment (keeps keys out of source).
- **Secrets** (`~/.patroam/secrets.json`): `ANTHROPIC_API_KEY`, `GOLD_API_KEY`,
  `NEWSAPI_KEY`, `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN`/`SLACK_DM_CHANNEL`/
  `SLACK_FEEDBACK_CHANNEL`/`SLACK_USER_ID`, `CLICKUP_API_TOKEN`/`CLICKUP_SPACE_ID`,
  `SSI_CONSUMER_ID`/`SSI_CONSUMER_SECRET`, `SPOTIFY_FOCUS_URL`, `META_*`.
- **Data dir `~/.patroam/`**: `graph.json`, `rag_index.json`/`chroma/`, `knowledge/`,
  `notes/`, `projects.json`, `session.json`, `news_seen.json`, `backups/`, `media/`,
  `secrets.json`, `startup.log`, `.bootstrap-ok`.

---

## 11. Key flows

- **Voice command**: wake word → session → endpointed command → `Controller.handle` →
  `data_handle` (intent router) or `_respond` (model + tools) → spoken summary + chat.
- **Chat turn**: `handle` → awaiting-answer? / stop? / data skill? → else `_respond`
  streams the model, runs `ACTION`s, feeds tool results back, shows files/A-B widgets.
- **Create a project**: model consults (scope → stack → choices via `ask`) → confirms
  folder + ClickUp space → `ACTION: create_project` → `planner.create_project` builds
  folder/plan.md/git/ClickUp/graph/Slack + registry.
- **Resume**: "let's work on X" → `resume_project` → `manage.resume` gathers git/ClickUp/
  Slack/plan → focused briefing.
- **Launch**: window shows instantly → models load off-thread → graph self-heals +
  syncs projects → ~5s later the daily briefing.

---

*Diagnostics live in `~/.patroam/startup.log`. `PATROAM_DEBUG=1` enables webview
DevTools; `[voice]` lines trace the wake word; `[ui]` lines trace startup phases.*
