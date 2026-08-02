"""Project management — resume a project by pulling its live state from GitHub
(git), ClickUp, Slack, and its plan.md, so PATROAM can answer "where were we on X?"
with what you were working on, open issues, and the best next action.
"""

import json
import os
import subprocess

from . import clickup, config, llm, registry


def _git(folder):
    if not folder or not os.path.isdir(os.path.join(folder, ".git")):
        return {}

    def run(*a):
        try:
            return subprocess.run(["git", *a], cwd=folder, capture_output=True,
                                  text=True, timeout=15).stdout.strip()
        except Exception:
            return ""
    dirty = run("status", "--porcelain")
    return {"branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "last_commit": run("log", "-1", "--pretty=%s (%cr)"),
            "dirty": len([x for x in dirty.splitlines() if x.strip()])}


def _find(name):
    """(folder, record) for a project — from the registry, else by searching the
    GitHub root for a folder whose name matches."""
    rec = registry.get(name) or {}
    if rec.get("folder") and os.path.isdir(rec["folder"]):
        return rec["folder"], rec
    root = config.GITHUB_ROOT
    if os.path.isdir(root):
        key = "".join(c for c in name.lower() if c.isalnum())
        for d in os.listdir(root):
            if key and key in "".join(c for c in d.lower() if c.isalnum()):
                return os.path.join(root, d), rec
    return rec.get("folder"), rec


def _clickup_state(rec):
    lid = rec.get("clickup_list_id")
    if not lid or not clickup.available():
        return {}
    try:
        tasks = clickup.list_tasks(lid)
    except Exception:
        return {}
    open_t = [t for t in tasks if (t.get("status") or {}).get("type") not in ("done", "closed")]
    inprog = [t for t in open_t if "progress" in ((t.get("status") or {}).get("status", "").lower())]
    nxt = inprog or open_t
    return {"open": len(open_t), "working": nxt[0].get("name") if nxt else None}


def _slack_recent(rec, limit=5):
    cid = rec.get("slack_channel_id")
    if not cid:
        return []
    try:
        from . import slack_bot
        if slack_bot._client is None:
            return []
        res = slack_bot._client.conversations_history(channel=cid, limit=limit)
        return [m.get("text", "") for m in res.get("messages", []) if m.get("text")][:limit]
    except Exception:
        return []


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _norm(n):
    return "".join(c for c in (n or "").lower() if c.isalnum())


def discover_projects():
    """Real projects = git repos in the GitHub root + lists in the ClickUp space.
    Merged by normalised name. Returns [{name, folder?, clickup_list_id?}]."""
    out = {}
    root = config.GITHUB_ROOT
    if os.path.isdir(root):
        for d in sorted(os.listdir(root)):
            p = os.path.join(root, d)
            if os.path.isdir(os.path.join(p, ".git")):     # a real repo
                out[_norm(d)] = {"name": d, "folder": p}
    try:
        if clickup.available():
            for lst in clickup._space_lists(config.CLICKUP_SPACE_ID):
                nm = lst.get("name", "")
                rec = out.setdefault(_norm(nm), {"name": nm})
                rec["clickup_list_id"] = lst.get("id")
                rec.setdefault("name", nm)
    except Exception:
        pass
    return list(out.values())


def project_progress(rec):
    """{done, total, next} — from ClickUp task status if the project has a list,
    else from plan.md/README checkboxes."""
    lid = rec.get("clickup_list_id")
    if lid and clickup.available():
        try:
            tasks = clickup.list_tasks(lid, include_closed=True)
            done = len([t for t in tasks if (t.get("status") or {}).get("type") in ("done", "closed")])
            open_t = [t for t in tasks if (t.get("status") or {}).get("type") not in ("done", "closed")]
            nxt = open_t[0].get("name") if open_t else ""
            return {"done": done, "total": len(tasks), "next": nxt}
        except Exception:
            pass
    folder = rec.get("folder")
    txt = (_read(os.path.join(folder, "plan.md")) or _read(os.path.join(folder, "README.md"))) if folder else ""
    done = txt.count("[x]") + txt.count("[X]")
    total = done + txt.count("[ ]")
    nxt = ""
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("- [ ]") or s.startswith("[ ]"):
            nxt = s.split("]", 1)[1].strip()
            break
    return {"done": done, "total": total, "next": nxt}


def _plan_next(folder):
    try:
        with open(os.path.join(folder or "", "plan.md"), encoding="utf-8", errors="ignore") as f:
            txt = f.read()
    except Exception:
        return ""
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("- [ ]") or s.startswith("[ ]"):
            return s.split("]", 1)[1].strip()
    return ""


def resume(name):
    """Pull a project's state together into a focused resume briefing → {say, show}."""
    folder, rec = _find(name)
    if not folder and not rec:
        return (f"I don't have a project matching '{name}', Sir. Say \"create a "
                "project\" and I'll plan it with you.")
    disp = rec.get("name") or name
    git = _git(folder) if folder else {}
    cu = _clickup_state(rec)
    slack = _slack_recent(rec)
    nxt = _plan_next(folder) if folder else ""

    say = [f"Resuming {disp}, Sir."]
    show = [f"🗂 {disp}"]
    if folder:
        show.append(f"📁 {folder}")
    if git:
        show.append("git: " + (git.get("branch") or "?")
                    + " · last: " + (git.get("last_commit") or "—")
                    + (f" · {git['dirty']} uncommitted" if git.get("dirty") else ""))
        if git.get("last_commit"):
            say.append(f"Your last commit was {git['last_commit']}.")
    if cu.get("working"):
        show.append(f"ClickUp: working on '{cu['working']}' · {cu.get('open', 0)} open")
        say.append(f"You were working on {cu['working']}.")
    elif cu:
        show.append(f"ClickUp: {cu.get('open', 0)} open tasks")
    if nxt:
        show.append(f"Next in plan: {nxt}")
        say.append(f"Next up: {nxt}.")
    if slack:
        show.append("Recent dev-log:\n" + "\n".join("  • " + m[:80] for m in slack))

    # One-line recommended next action (LLM), if a model is available.
    if llm.available():
        ctx = {"project": disp, "git": git, "clickup": cu, "plan_next": nxt, "slack": slack[:3]}
        rec_line = llm.complete(
            "In ONE short sentence, recommend the single best next action to make "
            "progress on this project. Context JSON:\n" + json.dumps(ctx, ensure_ascii=False),
            timeout=25) or ""
        if rec_line.strip():
            show.append("→ " + rec_line.strip())
            say.append(rec_line.strip())
    return {"say": " ".join(say), "show": "\n".join(show)}
