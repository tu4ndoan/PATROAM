"""The Planner — turn a project idea into a delivery roadmap, scaffold it,
document it (README), record it in the knowledge graph under `Projects`, and push
the tasks to ClickUp.

The conversational intake (consult → verify requirements → confirm choices) is
driven by the model via the `ask` action / Building protocol; once the user has
confirmed, the model emits `ACTION: plan {…}` which calls `create_project()` here.
"""

import json
import os
import re

from . import clickup, config, files, graph, llm

_ROADMAP_PROMPT = (
    "You are a senior software planner. Produce a concrete delivery roadmap for "
    "the project below. Return ONLY JSON of EXACTLY this shape:\n"
    '{"summary":"one line","stack":["..."],'
    '"milestones":[{"name":"M1: ...","tasks":[{"name":"...","subtasks":["...","..."]}]}],'
    '"backlog":["..."]}\n'
    "Use 3-6 milestones, each with 2-5 tasks, each task with 2-5 concrete subtasks. "
    "Backlog: 3-8 nice-to-haves. Be specific to THIS project; no placeholders.\n\n"
    "PROJECT:\n"
)


def _slug(name):
    s = re.sub(r"[^a-z0-9_]+", "_", (name or "app").lower()).strip("_") or "app"
    return s if s[0].isalpha() else "app_" + s


def build_roadmap(name, description="", kind=""):
    """LLM → structured roadmap dict (empty dict if no model / parse fails)."""
    if not llm.available():
        return {}
    raw = llm.complete(
        _ROADMAP_PROMPT + json.dumps({"project": name, "type": kind, "description": description}),
        timeout=60)
    if not raw:
        return {}
    try:
        i, j = raw.find("{"), raw.rfind("}")
        return json.loads(raw[i:j + 1]) if i >= 0 and j > i else {}
    except Exception:
        return {}


def _readme(name, description, kind, roadmap):
    lines = [f"# {name}", ""]
    if description:
        lines += [description, ""]
    if roadmap.get("summary"):
        lines += ["> " + roadmap["summary"], ""]
    if kind:
        lines += [f"**Type:** {kind}", ""]
    stack = roadmap.get("stack") or []
    if stack:
        lines += ["## Stack", *[f"- {s}" for s in stack], ""]
    lines += ["## Roadmap", ""]
    for ms in roadmap.get("milestones", []):
        lines.append(f"### {ms.get('name', 'Milestone')}")
        for t in ms.get("tasks", []):
            lines.append(f"- [ ] {t.get('name', 'Task')}")
            for s in (t.get("subtasks") or []):
                lines.append(f"  - [ ] {s}")
        lines.append("")
    backlog = roadmap.get("backlog") or []
    if backlog:
        lines += ["## Backlog", *[f"- [ ] {b}" for b in backlog], ""]
    return "\n".join(lines)


def create_project(name, kind="", description="", choices=None):
    """Plan + create a project end-to-end. Returns {say, show}."""
    name = (name or "Project").strip()
    pkg = _slug(name)
    # 1) real folder + starter structure (handles flutter/python/website/webapp/
    #    desktop, and a plain folder for anything else).
    files.scaffold_project(kind or "generic", name)
    # 2) the plan
    roadmap = build_roadmap(name, description, kind)
    # 3) README carrying the plan (overwrites the scaffold's stub)
    readme = _readme(name, description, kind, roadmap)
    readme_path = files.write_file(f"{pkg}/README.md", readme)
    # 4) knowledge graph: register under Projects + extract facts
    facts = [("USES", s) for s in (roadmap.get("stack") or [])]
    facts += [("DECIDED", str(c)) for c in (choices or [])]
    graph.add_project(pkg, description or roadmap.get("summary", ""), facts=facts, doc=pkg)
    if llm.available():
        try:
            graph.extract_into(readme, llm.complete, doc=pkg)
        except Exception:
            pass
    # 5) ClickUp
    url = clickup.push_roadmap(name, roadmap) if clickup.available() else None
    # 6) report
    nms = len(roadmap.get("milestones", []))
    nt = sum(len(m.get("tasks", [])) for m in roadmap.get("milestones", []))
    nb = len(roadmap.get("backlog", []))
    say = (f"Project {name} is set up, Sir — {nms} milestones and {nt} tasks"
           + (f", plus {nb} backlog items" if nb else "") + ".")
    show = (f"✅ Created **{name}** — {readme_path}\n"
            f"Roadmap: {nms} milestones · {nt} tasks · {nb} backlog")
    if url:
        show += f"\n🔗 ClickUp board: {url}"
    elif clickup.available():
        show += "\n⚠ ClickUp push failed — check your token / space id."
    return {"say": say, "show": show}


def project_status():
    """Where each project stands: progress (checkbox counts) + the next open task.
    Returns {say, show} or a short string if there are none."""
    names = graph.projects()
    if not names:
        return ("You have no projects tracked yet, Sir — ask me to create one and "
                "I'll plan it out.")
    lines, say_bits = [], []
    for disp in names:
        slug = _slug(disp)
        rd = os.path.join(config.WORKSPACE_DIR, slug, "README.md")
        txt = graph._read_text(rd)
        done = txt.count("[x]") + txt.count("[X]")
        total = done + txt.count("[ ]")
        nxt = ""
        for line in txt.splitlines():
            s = line.strip()
            if s.startswith("- [ ]") or s.startswith("[ ]"):
                nxt = s.split("]", 1)[1].strip()
                break
        prog = f"{done}/{total} done" if total else "no tasks yet"
        lines.append(f"• {disp} — {prog}" + (f" · next: {nxt}" if nxt else ""))
        say_bits.append(f"{disp}, {prog}")
    say = "Here's where your projects stand, Sir: " + "; ".join(say_bits) + "."
    show = "📋 Project status\n" + "\n".join(lines)
    return {"say": say, "show": show}
