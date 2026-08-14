"""The questions PATROAM must ask before creating a project.

The typed path gets this for free: the system prompt runs a two-phase protocol
(consult, then create) and the chat model asks one question at a time. The VOICE
path had no such thing — `create_project` was a single tool call, so saying
"make me a project called X" scaffolded a folder immediately: no scope, no goal,
no stack, no repo, and a ClickUp list with nothing in it.

So the brief lives here instead of in a prompt. `create_project` asks this
module what is still missing and reads the next question aloud; only once the
brief is complete AND approved does anything get created. Same questions
whichever way you talk to it.
"""

import os

from . import config

# key → (spoken question, why it matters, options, required)
FIELDS = [
    ("name", "What should the project be called, Sir?", "the folder and repo name",
     [], True),
    ("scope", "Is this a quick prototype, or a production build to ship?",
     "it decides how deep the plan goes — SEO, scaling and security only earn "
     "their place in a production build",
     ["prototype", "production"], True),
    ("goal", "What problem should it solve, and who is it for?",
     "the plan is written around this", [], True),
    ("platform", "Which platform — web, mobile, desktop, a service, or a game?",
     "it drives the stack", ["web", "mobile", "desktop", "service", "game"], True),
    ("stack", "Any stack you want, or shall I recommend one?",
     "your preference wins over mine", ["recommend one"], True),
    ("timeline", "What's the timeline?", "milestones are sized against it",
     ["no deadline"], False),
    ("integrations", "Anything it must integrate with?",
     "these become tasks in the roadmap", ["none"], False),
    ("folder", "Where should it live?", "defaults to your GitHub folder", [], False),
    ("github", "Shall I create a GitHub repo for it — private, public, or none?",
     "PATROAM creates the repo and pushes the first commit",
     ["private", "public", "none"], False),
    ("clickup_space", "Which ClickUp space should the roadmap go into?",
     "the roadmap becomes a ClickUp list with tasks and checklists", [], False),
]

_REQUIRED = [f[0] for f in FIELDS if f[4]]
_SPEC = {f[0]: f for f in FIELDS}

# One intake at a time — you plan one project at a time.
# `pending` is the question currently on the table: asking twice must return the
# SAME question, or reading the status would silently burn through the list.
_STATE = {"brief": {}, "approved": False, "asked": [], "pending": None}

_DEFAULTS = {"timeline": "no fixed deadline", "integrations": "none",
             "folder": "", "github": "private", "clickup_space": ""}

_NO = {"no", "none", "nope", "skip", "không", "khong", "chưa", "chua"}


def reset():
    _STATE.update({"brief": {}, "approved": False, "asked": [], "pending": None})


def brief():
    """The answers so far, with the optional fields filled in."""
    out = dict(_DEFAULTS)
    out.update(_STATE["brief"])
    return out


def update(**fields):
    """Record answers. Empty values are ignored so a partial call can't wipe one."""
    given = [k for k, v in fields.items() if k in _SPEC and v not in (None, "")]
    pending = _STATE["pending"]
    if pending and given:
        # He answered something. If it wasn't the optional question on the table,
        # he has moved past it — take the default rather than asking again.
        if pending in given or pending not in _REQUIRED:
            _STATE["pending"] = None
    for k, v in fields.items():
        if k not in _SPEC or v in (None, ""):
            continue
        if isinstance(v, bool):
            v = "private" if (k == "github" and v) else ("none" if k == "github" else str(v))
        v = str(v).strip()
        if not v:
            continue
        # "no" to an optional question is an answer, not a blank.
        if v.lower() in _NO and k in _DEFAULTS:
            v = "none" if k in ("integrations", "github") else _DEFAULTS[k]
        _STATE["brief"][k] = v
        _STATE["approved"] = False       # any change re-opens the approval
    return status()


def missing():
    return [k for k in _REQUIRED if not _STATE["brief"].get(k)]


def next_question():
    """The next thing to ask — required fields first, then the logistics that
    have defaults (asked once each, so it can't loop on them).

    Idempotent: until the pending question is answered, it keeps returning that
    same question."""
    pending = _STATE["pending"]
    if pending and not _STATE["brief"].get(pending):
        return _question(pending)
    for key in _REQUIRED:
        if not _STATE["brief"].get(key):
            _STATE["pending"] = key
            return _question(key)
    for key, _q, _why, _opts, req in FIELDS:
        if req or _STATE["brief"].get(key) or key in _STATE["asked"]:
            continue
        _STATE["asked"].append(key)
        _STATE["pending"] = key
        return _question(key)
    _STATE["pending"] = None
    return None


def _question(key):
    q, why, opts, _req = _SPEC[key][1], _SPEC[key][2], _SPEC[key][3], _SPEC[key][4]
    if key == "folder":
        q += f" Default is {config.GITHUB_ROOT}."
    if key == "clickup_space" and config.CLICKUP_SPACE_ID:
        q += f" Default is {config.CLICKUP_SPACE_ID}."
    return {"key": key, "question": q, "why": why, "options": opts}


def ready():
    return not missing()


def approved():
    return bool(_STATE["approved"]) and ready()


def approve():
    _STATE["approved"] = True
    return status()


def summary():
    """The plan read back for approval — say this, then ask for a yes."""
    b = brief()
    L = [f"{b.get('name', 'The project')} — {b.get('scope', 'prototype')} "
         f"{b.get('platform', '')}".strip(),
         f"Goal: {b.get('goal', '—')}",
         f"Stack: {b.get('stack', 'my recommendation')}",
         f"Timeline: {b.get('timeline')}"]
    if b.get("integrations") and b["integrations"] != "none":
        L.append(f"Integrations: {b['integrations']}")
    L.append(f"Folder: {b.get('folder') or config.GITHUB_ROOT}")
    L.append("GitHub: " + ("no repo" if b.get("github") == "none"
                           else f"{b.get('github', 'private')} repo, first commit pushed"))
    L.append("ClickUp: " + (b.get("clickup_space") or config.CLICKUP_SPACE_ID or "default space"))
    return "\n".join("• " + x for x in L)


def status():
    """Where the intake stands: what to ask, or that it's ready to build."""
    q = next_question()
    if q:
        return {"stage": "asking", "next": q, "brief": brief()}
    if not _STATE["approved"]:
        return {"stage": "review", "summary": summary(), "brief": brief()}
    return {"stage": "ready", "brief": brief()}


def to_kwargs():
    """The brief as arguments for planner.create_project."""
    b = brief()
    choices = [f"{k}: {b[k]}" for k in ("stack", "platform", "timeline", "integrations")
               if b.get(k) and b[k] not in ("none", "recommend one")]
    folder = b.get("folder") or ""
    if folder and not os.path.isabs(os.path.expanduser(folder)):
        folder = os.path.join(config.GITHUB_ROOT, folder)
    return {
        "name": b.get("name", "Project"),
        "kind": b.get("platform", ""),
        "description": b.get("goal", ""),
        "choices": choices,
        "folder": folder or None,
        "clickup_space": b.get("clickup_space") or None,
        "prototype": (b.get("scope", "").lower() != "production"),
        "github": b.get("github", "private"),
    }
