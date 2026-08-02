"""The Planner — professional project planning, creation, and status.

Planning (driven by the model's consult) yields a rich `plan.md`; creation then
scaffolds the folder in your GitHub root (git init, you push manually), pushes a
ClickUp list in the space you choose, records a node under `Projects` in the graph,
opens a private Slack dev-log channel, and registers everything so the project can
be resumed later (see manage.py).
"""

import datetime
import json
import os
import subprocess

from . import clickup, config, graph, llm, registry

_PLAN_PROMPT = (
    "You are a senior software architect and delivery lead. Produce a PROFESSIONAL "
    "project plan as JSON ONLY, in EXACTLY this shape:\n"
    '{"summary":"one line","scope":"prototype|production","timeline":"e.g. 6 weeks",'
    '"stack":["..."],"stack_rationale":"why this stack fits the requirements",'
    '"nonfunctional":{"seo":"...","performance":"...","scaling":"...","security":"..."},'
    '"risks":["short risk / what won\'t work"],'
    '"milestones":[{"name":"M1: ...","tasks":[{"name":"...","subtasks":["...","..."]}]}],'
    '"backlog":["..."]}\n'
    "Tailor DEPTH to scope: a prototype stays lean (skip heavy scaling/security); a "
    "production build covers SEO, performance, scaling and security properly. Use 3-6 "
    "milestones, each 2-5 tasks with 2-5 concrete subtasks. Be specific to THIS "
    "project — no placeholders.\n\nPROJECT:\n"
)

_GITIGNORE = ("node_modules/\n__pycache__/\n*.pyc\n.env\n.DS_Store\ndist/\nbuild/\n"
              ".idea/\n.vscode/\n*.log\n")


def _slug(name):
    import re
    s = re.sub(r"[^a-z0-9_]+", "_", (name or "app").lower()).strip("_") or "app"
    return s if s[0].isalpha() else "app_" + s


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _git_init(d):
    """git init + initial commit (no remote, no push). Best-effort."""
    try:
        if os.path.isdir(os.path.join(d, ".git")):
            return True
        subprocess.run(["git", "init"], cwd=d, capture_output=True, timeout=20)
        subprocess.run(["git", "add", "-A"], cwd=d, capture_output=True, timeout=20)
        subprocess.run(["git", "commit", "-m", "Initial commit (PATROAM plan)"],
                       cwd=d, capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def build_plan(name, kind="", description="", choices=None, prototype=None):
    """LLM → a full professional plan dict (empty dict if no model / parse fails)."""
    if not llm.available():
        return {}
    payload = {"project": name, "type": kind, "description": description,
               "decisions": choices or [], "prototype": prototype}
    raw = llm.complete(_PLAN_PROMPT + json.dumps(payload, ensure_ascii=False), timeout=90)
    if not raw:
        return {}
    try:
        i, j = raw.find("{"), raw.rfind("}")
        return json.loads(raw[i:j + 1]) if i >= 0 and j > i else {}
    except Exception:
        return {}


def _plan_md(plan, name, kind, description, choices):
    nf = plan.get("nonfunctional") or {}
    L = [f"# {name} — Project Plan", ""]
    if description:
        L += [description, ""]
    if plan.get("summary"):
        L += ["> " + plan["summary"], ""]
    L += [f"- **Scope:** {plan.get('scope', '—')}",
          f"- **Timeline:** {plan.get('timeline', '—')}",
          f"- **Type:** {kind or '—'}"]
    if choices:
        L += [f"- **Decisions:** {', '.join(str(c) for c in choices)}"]
    L += [""]
    stack = plan.get("stack") or []
    if stack:
        L += ["## Tech Stack", *[f"- {s}" for s in stack]]
        if plan.get("stack_rationale"):
            L += ["", f"_Rationale:_ {plan['stack_rationale']}"]
        L += [""]
    if nf:
        L += ["## Non-functional Requirements"]
        for key in ("seo", "performance", "scaling", "security"):
            if nf.get(key):
                L += [f"- **{key.upper() if key == 'seo' else key.capitalize()}:** {nf[key]}"]
        L += [""]
    L += ["## Roadmap", ""]
    for ms in plan.get("milestones", []):
        L.append(f"### {ms.get('name', 'Milestone')}")
        for t in ms.get("tasks", []):
            L.append(f"- [ ] {t.get('name', 'Task')}")
            for s in (t.get("subtasks") or []):
                L.append(f"  - [ ] {s}")
        L.append("")
    if plan.get("backlog"):
        L += ["## Backlog", *[f"- [ ] {b}" for b in plan["backlog"]], ""]
    if plan.get("risks"):
        L += ["## Risks & What Won't Work", *[f"- {r}" for r in plan["risks"]], ""]
    return "\n".join(L)


def _readme(name, description, plan):
    L = [f"# {name}", ""]
    if description:
        L += [description, ""]
    if plan.get("summary"):
        L += ["> " + plan["summary"], ""]
    L += ["See [plan.md](plan.md) for the full delivery plan, roadmap and decisions."]
    return "\n".join(L) + "\n"


def create_project(name, kind="", description="", choices=None, folder=None,
                   clickup_space=None, slack=True, prototype=None):
    """Create a project end-to-end. Returns {say, show}."""
    name = (name or "Project").strip()
    slug = _slug(name)
    base = os.path.expanduser(str(folder).strip()) if (folder and str(folder).strip()) \
        else config.GITHUB_ROOT
    proj_dir = os.path.join(base, slug)
    os.makedirs(proj_dir, exist_ok=True)

    # 1) plan.md + README + .gitignore
    plan = build_plan(name, kind, description, choices, prototype)
    plan_md = _plan_md(plan, name, kind, description, choices)
    _write(os.path.join(proj_dir, "plan.md"), plan_md)
    _write(os.path.join(proj_dir, "README.md"), _readme(name, description, plan))
    gi = os.path.join(proj_dir, ".gitignore")
    if not os.path.exists(gi):
        _write(gi, _GITIGNORE)

    # 2) git init (no push — you push manually)
    git_ok = _git_init(proj_dir)

    # 3) ClickUp list in the chosen space
    cu = None
    if clickup.available():
        space_id = clickup.resolve_space(clickup_space)
        cu = clickup.push_roadmap(
            name, {"milestones": plan.get("milestones", []),
                   "backlog": plan.get("backlog", [])}, space_id=space_id)

    # 4) knowledge graph
    facts = [("USES", s) for s in (plan.get("stack") or [])]
    facts += [("DECIDED", str(c)) for c in (choices or [])]
    if plan.get("scope"):
        facts.append(("SCOPE", plan["scope"]))
    graph.add_project(slug, description or plan.get("summary", ""), facts=facts, doc=slug)
    if llm.available():
        try:
            graph.extract_into(plan_md, llm.complete, doc=slug)
        except Exception:
            pass

    # 5) private Slack dev-log channel
    ch = None
    if slack:
        try:
            from . import slack_bot
            intro = (f"📋 *{name}* dev-log\n{plan.get('summary', '')}\n"
                     f"Scope: {plan.get('scope', '—')} · Timeline: {plan.get('timeline', '—')}")
            ch = slack_bot.create_devlog_channel(slug, intro)
        except Exception:
            ch = None

    # 6) registry — the single source of truth for resuming later
    registry.register(
        name, folder=proj_dir, kind=kind, plan=os.path.join(proj_dir, "plan.md"),
        clickup_list_id=(cu or {}).get("list_id"), clickup_url=(cu or {}).get("url"),
        slack_channel_id=(ch or {}).get("id"), slack_channel=(ch or {}).get("name"),
        created=datetime.date.today().isoformat())

    # 7) report
    nms = len(plan.get("milestones", []))
    nt = sum(len(m.get("tasks", [])) for m in plan.get("milestones", []))
    nb = len(plan.get("backlog", []))
    say = (f"Project {name} is set up, Sir — plan written, {nms} milestones and {nt} tasks"
           + (f", plus {nb} backlog items" if nb else "") + ".")
    show = (f"✅ Created **{name}**\n"
            f"📁 {proj_dir}" + ("  · git initialised" if git_ok else "") + "\n"
            f"📝 plan.md + README.md\n"
            f"Roadmap: {nms} milestones · {nt} tasks · {nb} backlog")
    if cu and cu.get("url"):
        show += f"\n🔗 ClickUp: {cu['url']}"
    elif clickup.available():
        show += "\n⚠ ClickUp push failed — check token/space."
    if ch:
        show += f"\n💬 Slack: #{ch['name']} (private)"
    return {"say": say, "show": show}


def project_status():
    """Where each real project stands (git repos in your GitHub root + ClickUp lists)."""
    from . import manage
    projs = manage.discover_projects()
    if not projs:
        return ("You have no projects yet, Sir — ask me to create one and I'll plan it out.")
    lines, say_bits = [], []
    for rec in projs:
        pr = manage.project_progress(rec)
        prog = f"{pr['done']}/{pr['total']} done" if pr["total"] else "—"
        lines.append(f"• {rec['name']} — {prog}" + (f" · next: {pr['next']}" if pr["next"] else ""))
        say_bits.append(f"{rec['name']}, {prog}")
    return {"say": "Here's where your projects stand, Sir: " + "; ".join(say_bits) + ".",
            "show": "📋 Project status\n" + "\n".join(lines)}
