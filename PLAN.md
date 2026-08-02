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

## Professional project workflow ✅ (planning · creation · management)
A senior-architect workflow for building & running projects. Key modules:
`planner.py`, `registry.py`, `manage.py`, `clickup.py`, `slack_bot.py`.

**Planning** (persona protocol, one question at a time via `ask`):
- Establish **scope** first — prototype vs production — which tunes depth.
- Consult goal / platforms / scale / deadline / constraints; **recommend** a stack
  + SEO/performance/scaling/security approach with trade-offs and "what to avoid".
- Summarise for approval → write **`plan.md`** in the project folder.

**Creation** (`create_project`, after you confirm folder + ClickUp space):
- Folder under **GitHub root** (`config.GITHUB_ROOT`) + `plan.md` + README + `.gitignore`
  + **`git init`** (you push manually — no auto-remote).
- **ClickUp** list in the chosen space (tasks/subtasks/checklists).
- Node under **Projects** in the knowledge graph (stack, decisions, scope).
- **Private Slack `#devlog-<project>`** channel (invites you, posts the plan).
- All recorded in the **project registry** (`~/.patroam/projects.json`) — the source
  of truth: name → folder, git remote, ClickUp list id, Slack channel id, plan.md.

**Management** (`resume_project` intent — "let's work on X, where were we?"):
- git (branch, last commit, uncommitted), ClickUp (in-progress task), Slack (recent
  dev-log), plan.md (next task) → focused resume + LLM-recommended next action.

### Setup for the Slack pieces
**Get your Slack user id** (for `SLACK_USER_ID`, so PATROAM can invite you to new
dev-log channels): in Slack → click your profile → **⋯ (More)** → **Copy member ID**
(looks like `U0XXXXXXX`). Add it to `~/.patroam/secrets.json` as `"SLACK_USER_ID"`.

**Add bot scopes** (so PATROAM can create/read private channels): https://api.slack.com/apps
→ your **P.A.T.R.O.A.M** app → **OAuth & Permissions** → **Bot Token Scopes** →
**Add an OAuth Scope**, add: `groups:write`, `groups:read`, `groups:history`
(private channels), and `channels:history` + `chat:write` (already present). Then
scroll up → **Reinstall to Workspace** → Allow. (The bot only sees private channels
it's a member of — it auto-joins the ones it creates.)

---

### Decisions (resolved) / open
- **ClickUp** ✅ token + spaces configured; Planner asks which space each time.
- **GitHub** ✅ root = `Documents\GitHub`; `git init` only, you push manually.
- **Slack dev-logs** ✅ private; needs the scopes + `SLACK_USER_ID` above.
- **Mail** (Phase 5, deferred): provider — Gmail API, IMAP, or Outlook?
