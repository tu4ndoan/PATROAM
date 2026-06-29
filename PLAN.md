# PATROAM — Implementation Plan

Derived from `TODO.md`. Guiding rule: **every new command is recognised by the LLM
intent router (`skills._route_intent`) — no hardcoded keyword matching.**

Reused building blocks: `skills._route_intent`/`_dispatch`, the `notify` pub/sub hub,
`graph` (knowledge graph + `You` node pattern), `rag` (+ `media`), `brain` (headless
text agent), `slack_bot` (Socket Mode), and the desktop `Controller`.

---

## Phase 0 — Fixes & cleanup
- [ ] Edge dots hidden by default, shown only on the **selected** node's edges.
- [ ] Allow zooming out further (lower `G.zoom` minimum / widen wheel range).
- [ ] Pan the graph with **middle-mouse** drag (move `G.panX/panY`).
- [ ] Remove dead files/`__pycache__`, refactor duplication, rewrite `README.md`.

## Phase 1 — LLM command backbone
- [ ] Add intents to `_route_intent`: `new_note`, `create_project`, `project_status`,
      `check_mail`, `note_suggestions`.
- [ ] Add `_dispatch` branches + stub handlers (provable routing before real logic).

## Phase 2 — Graph backbone: Projects, Notes, backup
- [ ] Top-level `Projects` and `Notes` nodes (mirror the `You` node).
- [ ] `graph.backup()` → timestamped copies in `~/.patroam/backups/`; auto-backup on
      launch + before destructive ops; a "back up the graph" intent.
- [ ] Index project `README.md`s and note files into the graph (via `rag`/`graph`).

## Phase 3 — Planner Agent
- [ ] `clickup.py` — ClickUp REST API (token in `secrets.json`): lists/tasks/subtasks/
      checklists. *(Decision: workspace/space + API token.)*
- [ ] Multi-step project intake (reuse `ask`/A-B widgets): consult → flag blockers →
      verify requirements → roadmap (milestones, tasks, subtasks, checkboxes, backlog).
- [ ] Scaffold folder + `README.md`, push tasks to ClickUp, index README into `Projects`.
- [ ] Launch progress report: per-project progress, where you left off, next steps,
      on-schedule check (via `notify`).

## Phase 4 — Note Taker
- [ ] "New Note" popup window → save to `Notes/` folder.
- [ ] Auto-index notes into the graph.
- [ ] Launch review of notes → suggestions ("what to do", "bug to fix").
- [ ] Cross-note connection & conflict detection (e.g. schedule clashes).

## Phase 5 — Mail
- [ ] `mail.py` — read inbox, classify important / awaiting-reply / news / promotions.
      *(Decision: Gmail API vs IMAP vs Outlook.)*
- [ ] `check_mail` intent + launch summary line.

## Phase 6 — Unified launch briefing
- [ ] One startup briefing combining project progress + note suggestions + mail +
      the existing "what's up" (gold, VN-Index, Fab, news), spoken + DM'd to Slack.

---

### Decisions needed
- **ClickUp** (Phase 3): API token + workspace/space.
- **Mail** (Phase 5): provider — Gmail API, IMAP, or Outlook?
