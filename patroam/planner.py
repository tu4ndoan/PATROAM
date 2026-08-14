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
    """git init + initial commit (no remote yet). Best-effort."""
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


def _github_repo(d, slug, visibility="private"):
    """Create the GitHub repo and push the first commit → {url} or {error}.

    Uses the `gh` CLI, which is already signed in on this machine — no token to
    store. Only runs when you asked for a repo; "none" skips it entirely."""
    import shutil
    if (visibility or "none").lower() in ("none", "no", ""):
        return None
    gh = shutil.which("gh")
    if not gh:
        return {"error": "GitHub CLI not found — install gh, or create the repo yourself."}
    flag = "--public" if str(visibility).lower().startswith("pub") else "--private"
    try:
        r = subprocess.run([gh, "repo", "create", slug, flag, "--source", ".",
                            "--remote", "origin", "--push"],
                           cwd=d, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        return {"error": out.splitlines()[-1][:200] if out else "gh repo create failed"}
    url = next((w for w in out.split() if w.startswith("https://github.com/")), "")
    return {"url": url or out[:120], "visibility": flag.lstrip("-")}


def _plan_writer():
    """Whichever model can actually write the plan right now.

    The chat model registers itself in `llm`, but during a VOICE session there
    may be no chat model at all — and an empty plan is what left ClickUp with a
    list and no tasks. The realtime worker (Groq / Gemini / Ollama) is the same
    model the voice already uses, so fall back to it rather than giving up."""
    if llm.available():
        return lambda prompt, timeout: llm.complete(prompt, timeout=timeout)
    try:
        from .realtime.llm import worker
        w = worker()
        if w and w.available():
            return lambda prompt, timeout: w.complete(prompt, timeout=timeout,
                                                      max_tokens=3000)
    except Exception:
        pass
    return None


def build_plan(name, kind="", description="", choices=None, prototype=None):
    """LLM → a full professional plan dict (empty dict if no model / parse fails)."""
    write = _plan_writer()
    if not write:
        return {}
    payload = {"project": name, "type": kind, "description": description,
               "decisions": choices or [], "prototype": prototype}
    raw = write(_PLAN_PROMPT + json.dumps(payload, ensure_ascii=False), 90)
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
                   clickup_space=None, slack=True, prototype=None, github="private"):
    """Create a project end-to-end. Returns {say, show}.

    `github` is "private" / "public" / "none" — the repo is created and the
    first commit pushed with the `gh` CLI when you asked for one."""
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

    # 2) git init, then the GitHub repo (only if it was asked for)
    git_ok = _git_init(proj_dir)
    gh = _github_repo(proj_dir, slug, github) if git_ok else None

    # 3) ClickUp list in the chosen space. An empty plan would create a list
    #    with nothing in it, which reads as "ClickUp is broken" — so don't.
    cu = None
    cu_note = ""
    milestones = plan.get("milestones", [])
    if not clickup.available():
        cu_note = "⚠ ClickUp not connected (CLICKUP_API_TOKEN)."
    elif not (milestones or plan.get("backlog")):
        cu_note = "⚠ No roadmap to push — the planning model wasn't available."
    else:
        space_id = clickup.resolve_space(clickup_space)
        cu = clickup.push_roadmap(
            name, {"milestones": milestones,
                   "backlog": plan.get("backlog", [])}, space_id=space_id)
        if not cu:
            cu_note = "⚠ ClickUp push failed — check the token and the space."

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
        github_url=(gh or {}).get("url"),
        created=datetime.date.today().isoformat())

    # 7) report
    nms = len(plan.get("milestones", []))
    nt = sum(len(m.get("tasks", [])) for m in plan.get("milestones", []))
    nb = len(plan.get("backlog", []))
    say = (f"Project {name} is set up, Sir — plan written, {nms} milestones and {nt} tasks"
           + (f", plus {nb} backlog items" if nb else "")
           + (", and the GitHub repo is up" if (gh or {}).get("url") else "") + ".")
    show = (f"✅ Created **{name}**\n"
            f"📁 {proj_dir}" + ("  · git initialised" if git_ok else "") + "\n"
            f"📝 plan.md + README.md\n"
            f"Roadmap: {nms} milestones · {nt} tasks · {nb} backlog")
    if gh and gh.get("url"):
        show += f"\n🐙 GitHub ({gh.get('visibility', 'private')}): {gh['url']}"
    elif gh and gh.get("error"):
        show += f"\n⚠ GitHub: {gh['error']}"
    if cu and cu.get("url"):
        show += f"\n🔗 ClickUp: {cu['url']}"
    elif cu:
        show += "\n🔗 ClickUp list created"
    if cu_note:
        show += "\n" + cu_note
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
