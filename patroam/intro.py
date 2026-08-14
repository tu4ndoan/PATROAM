"""The introduction PATROAM gives the first time you call it by name.

Saying just "Patroam" — the name on its own, no request attached — is a way of
checking someone is there. The first time in a session it gets a real answer:
what he can do, what is actually connected right now, and what to try. Every
time after that it is just "Yes, Sir?", because repeating the tour would be
tiresome.

The content is READ FROM THE LIVE SYSTEM, never a hardcoded blurb: if ClickUp is
not connected or n8n has no workflows, it says so instead of promising them.
"""

from . import config

_GIVEN = {"done": False}


def already_given():
    return _GIVEN["done"]


def reset():
    _GIVEN["done"] = False


def _integrations():
    """[(name, detail_or_empty, connected)] — what is genuinely wired up."""
    rows = []

    def probe(label, fn):
        try:
            rows.append((label,) + fn())
        except Exception:
            rows.append((label, "", False))

    def google():
        from . import gcal
        if not gcal.available():
            return ("run python -m patroam.wire_gcal", False)
        return ("calendar and tasks", True)

    def clickup_():
        from . import clickup
        return ("project roadmaps", clickup.available())

    def slack_():
        return ("dev-log channels", config.slack_enabled())

    def n8n_():
        from . import n8n
        wfs = n8n.workflows()
        st = n8n.status()
        if not n8n.installed():
            return ("not installed", False)
        return (f"{len(wfs)} workflow" + ("s" if len(wfs) != 1 else "")
                + ("" if st["state"] == "running" else ", engine stopped"), bool(wfs))

    def mcp_():
        from .mcp_client import get_mcp
        m = get_mcp()
        servers = [s for s in m.servers() if s["state"] == "ready"]
        tools = m.tool_names()
        if not servers:
            return ("none connected — add one in the Connectors panel", False)
        return (", ".join(s["name"] for s in servers)
                + f" ({len(tools)} tools)", True)

    def voice():
        return ("Gemini Live" if config.GEMINI_API_KEY else "local voice only",
                bool(config.GEMINI_API_KEY))

    for label, fn in (("Google", google), ("ClickUp", clickup_), ("Slack", slack_),
                      ("n8n automations", n8n_), ("MCP connectors", mcp_),
                      ("Realtime voice", voice)):
        probe(label, fn)
    return rows


def _workload():
    """A one-line 'here is where you stand' — the point of an assistant."""
    bits = []
    try:
        from . import gcal
        if gcal.available():
            evs = gcal.list_events(days=1, limit=10)
            snap = gcal.tasks_snapshot()
            c = snap["counts"]
            if evs:
                bits.append(f"{len(evs)} thing" + ("s" if len(evs) != 1 else "")
                            + " on today's calendar")
            if c.get("open"):
                bits.append(f"{c['open']} open task" + ("s" if c["open"] != 1 else "")
                            + (f" ({c['overdue']} overdue)" if c.get("overdue") else ""))
    except Exception:
        pass
    try:
        from . import manage
        n = len(manage.discover_projects())
        if n:
            bits.append(f"{n} project" + ("s" if n != 1 else "") + " tracked")
    except Exception:
        pass
    return bits


CAN_DO = [
    "run your day — briefing, calendar, tasks",
    "plan and create projects, with the repo, roadmap and Slack channel",
    "keep your knowledge graph and notes",
    "watch markets, news, ads and your Fab sales",
    "run your n8n automations, and anything an MCP connector adds",
]


def introduction(spoken_limit=2):
    """{say, show} — the tour. `say` stays short enough to listen to."""
    _GIVEN["done"] = True
    rows = _integrations()
    live = [r for r in rows if r[2]]
    work = _workload()

    say = ["At your service, Sir. I run your day, plan and build your projects, "
           "and keep track of what you know."]
    if live:
        say.append("Connected right now: " + ", ".join(r[0] for r in live) + ".")
    if work:
        say.append("You have " + ", and ".join(work[:2]) + ".")
    say.append("Ask me for a briefing, or tell me what you're building.")

    L = ["🎩 **PATROAM at your service**", "", "**What I can do**"]
    L += ["  • " + c for c in CAN_DO]
    L += ["", "**Connected**"]
    for label, detail, ok in rows:
        L.append(f"  {'●' if ok else '○'} {label}" + (f" — {detail}" if detail else ""))
    if work:
        L += ["", "**Where you stand**"] + ["  • " + w for w in work]
    L += ["", "**Try saying**",
          '  • "briefing" — the start-of-day rundown',
          '  • "what\'s on tomorrow" · "add a task"',
          '  • "let\'s plan a new project" — I\'ll ask you through it',
          '  • "run my automations" · "what do you know about X"']
    return {"say": " ".join(say[:max(1, spoken_limit) + 2]), "show": "\n".join(L)}


def on_name_only():
    """Called when he says the name and nothing else.

    First time in the session → the full introduction. After that a short
    acknowledgement, because he is just getting my attention."""
    if _GIVEN["done"]:
        return {"say": config.greeting(), "show": ""}
    return introduction()
