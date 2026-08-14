"""Every skill PATROAM can do by typing, exposed to the voice.

The first version of the voice tools was a hand-written list of eight. It went
stale immediately: you asked it to create a project and it could not, because
`create_project` existed in the typed router and nobody had copied it across.

So the list is generated from ONE source of truth — the same `skills._dispatch`
the chat window uses. Adding a skill there makes it callable by voice with no
further work; nothing can drift out of sync again.

Each entry only needs a spoken description and its arguments. The work, the
error handling and the UI hints all come from the skill itself.
"""

from .. import config

# skill name → (description, {arg: (type, description)}, ui hint)
# Descriptions are what Gemini reads to decide which tool fits, so they are
# written the way you would actually ask.
SKILLS = {
    "briefing": ("Start-of-day briefing: calendar, tasks, projects, news. Use when "
                 "he begins a work session or asks what's new.",
                 {}, "chat"),
    "calendar": ("Read the calendar. Today, tomorrow, a weekday, or when he is free.",
                 {"when": ("string", "The period in his own words"),
                  "free": ("boolean", "true if asking when he is FREE, not what is booked")},
                 "calendar"),
    "calendar_add": ("Add an event to the calendar (warns on a clash).",
                     {"title": ("string", "Event name"),
                      "when": ("string", "Date and time in his own words"),
                      "duration": ("integer", "Minutes, if stated")},
                     "calendar"),
    "calendar_edit": ("Move, rename or CANCEL an existing event.",
                      {"title": ("string", "Which event"),
                       "when": ("string", "New time if moving"),
                       "cancel": ("boolean", "true to cancel it")},
                      "calendar"),
    "todo_list": ("List his to-dos by priority and due date.", {}, "todo"),
    "todo_add": ("Add a task to his to-do list.",
                 {"title": ("string", "The task"),
                  "due": ("string", "Deadline in his own words"),
                  "urgent": ("boolean", "true if he stressed it is urgent")},
                 "todo"),
    "todo_done": ("Mark a task complete.",
                  {"title": ("string", "Which task")}, "todo"),
    "project_status": ("Progress across ALL projects.", {}, "graph"),
    "resume_project": ("Reopen one project: git state, tasks, what is next.",
                       {"project": ("string", "Project name")}, "project"),
    "note_suggestions": ("Suggestions from his notes: what to work on, schedule clashes.",
                         {}, "notes"),
    "new_note": ("Save a note.",
                 {"text": ("string", "Note content if he dictated it")}, "notes"),
    "stock": ("Vietnamese stock price or the VN-Index.",
              {"symbol": ("string", "Ticker, e.g. VNM, FPT")}, "chat"),
    "gold": ("Current gold price.", {}, "chat"),
    "news": ("Latest news on a topic.",
             {"topic": ("string", "Topic, empty for general")}, "chat"),
    "fab": ("Sales from his Fab.com store.", {}, "chat"),
    "ads": ("Meta/Facebook ad performance.",
            {"query": ("string", "What specifically he asked")}, "chat"),
    "post_content": ("Publish a video/reel to his social platforms.",
                     {"brief": ("string", "What the video is about"),
                      "video": ("string", "File path if stated")}, "chat"),
    "content_history": ("What he posted recently.", {}, "chat"),
    "automation": ("Run or list his n8n automation workflows.",
                   {"name": ("string", "Workflow name if stated")}, "automations"),
    "backup_graph": ("Back up the knowledge graph.", {}, None),
}

# Project creation isn't in the router (the typed path runs a multi-step
# confirmation flow), so it is declared here and calls the planner directly.
_EXTRA = {
    "plan_project": (
        "Plan a NEW PROJECT with him. Call this the moment he mentions starting, "
        "building or setting up a project, and again after EVERY answer he gives — "
        "pass only the fields he just answered. It replies with the next question "
        "to ask (ask exactly that, one at a time), or with the plan to read back "
        "for his approval. Never invent answers on his behalf.",
        {"name": ("string", "Project name"),
         "scope": ("string", "prototype or production"),
         "goal": ("string", "The problem it solves and who for"),
         "platform": ("string", "web, mobile, desktop, service, game"),
         "stack": ("string", "His stack preference, or 'recommend one'"),
         "timeline": ("string", "Deadline or timeline"),
         "integrations": ("string", "What it must integrate with, or 'none'"),
         "folder": ("string", "Where it should live, if he said"),
         "github": ("string", "private, public or none"),
         "clickup_space": ("string", "ClickUp space for the roadmap"),
         "approve": ("boolean", "true ONLY when he has approved the plan out loud")},
        "chat"),
    "create_project": (
        "Build the project he approved: folder, plan.md, README, git, the GitHub "
        "repo, the ClickUp roadmap and a private Slack channel. Only call this "
        "AFTER plan_project says the plan is ready and he has approved it.",
        {"approve": ("boolean", "true if he just approved it")},
        "graph"),
    "recall": (
        "Look up what PATROAM has remembered about a topic or about him.",
        {"topic": ("string", "Topic to look up")}, "graph"),
    "introduce": (
        "Introduce yourself: what you can do, which integrations and connectors "
        "are live, and where his day stands. Call this when he says just your "
        "name with no request attached, or asks what you can do.",
        {}, "chat"),
}


def _declare(name, spec):
    desc, args, _ui = spec
    props = {k: {"type": t, "description": d} for k, (t, d) in args.items()}
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props}}


def declarations():
    """Every skill, as Gemini function declarations."""
    out = [_declare(n, s) for n, s in SKILLS.items()]
    out += [_declare(n, s) for n, s in _EXTRA.items()]
    return out


def ui_hint(name, args, result=None):
    spec = SKILLS.get(name) or _EXTRA.get(name)
    hint = spec[2] if spec else None
    # A named project should focus that node rather than the whole graph.
    if hint == "project" and args.get("project"):
        return "project:" + args["project"]
    if name == "recall" and args.get("topic"):
        return "graph:" + args["topic"]
    return hint


def _project(name, args):
    """The two-phase project flow, driven from Python rather than a prompt.

    Everything the voice knows about the brief lives in project_intake, so the
    model cannot skip a question or start building early: this returns the next
    question until the brief is complete, then the plan to approve, and only
    then does it create anything."""
    from .. import planner, project_intake as intake
    approve = bool(args.pop("approve", False))
    if name == "plan_project":
        intake.update(**args)
    if approve and intake.ready():
        intake.approve()
    st = intake.status()

    if st["stage"] == "asking":
        q = st["next"]
        return {"text": "Ask him, in your own words: " + q["question"],
                "detail": f"❓ {q['question']}"
                          + (f"\n   ({q['why']})" if q.get("why") else "")
                          + (f"\n   options: {', '.join(q['options'])}" if q.get("options") else ""),
                "ui": None}
    if st["stage"] == "review":
        return {"text": "Read this plan back to him and ask if you should build it: "
                        + st["summary"].replace("\n", "; "),
                "detail": "📋 Plan for approval\n" + st["summary"], "ui": "chat"}

    if name == "plan_project":       # approved, but let the model call create
        return {"text": "The plan is approved. Call create_project now.",
                "detail": "", "ui": None}
    from .. import skills
    from .tools import _short
    rep = planner.create_project(**intake.to_kwargs())
    intake.reset()
    say, show = skills.split_reply(rep) if rep else ("", "")
    return {"text": _short(say) or "Done, Sir.", "detail": show, "ui": "graph"}


def run(name, args):
    """Execute a skill and return {text, detail, ui}.

    `text` is what Gemini says; `detail` is the fuller version for the chat
    window — links, paths and task lists are things you need to see, not hear."""
    from .. import skills
    args = args or {}
    try:
        if name in ("plan_project", "create_project"):
            return _project(name, args)
        elif name == "introduce":
            from .. import intro
            rep = intro.on_name_only()
            return {"text": rep["say"], "detail": rep.get("show", ""), "ui": "chat"}
        elif name == "recall":
            from .. import graph
            topic = (args.get("topic") or "").strip()
            facts = graph.render_for(topic, limit=12) or graph.user_summary()
            rep = {"say": facts, "show": facts}
        elif name in SKILLS:
            # Straight through the SAME dispatcher the typed chat uses.
            intent = dict(args)
            intent["skill"] = name
            rep = skills._dispatch(intent, args.get("text") or name)
        else:
            return {"text": f"I cannot do {name} yet, Sir.", "detail": "", "ui": None}
    except Exception as e:
        return {"text": f"{name} failed: {type(e).__name__}", "detail": "", "ui": None}

    say, show = skills.split_reply(rep) if rep else ("", "")
    from .tools import _summarise, _short
    # Long results get condensed for speech; the chat still shows everything.
    speak = _short(say) if len(say) <= 320 else _summarise(show or say,
                                                           "Summarise in 1-2 spoken sentences.")
    return {"text": speak or "Done, Sir.", "detail": show or "", "ui": ui_hint(name, args, rep)}
