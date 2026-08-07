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
    """(folder, record) for a project — the registry entry merged with what we can
    discover live (repo folder + ClickUp list), so a project registered without a
    ClickUp list still resolves to one, and vice versa."""
    rec = dict(registry.get(name) or {})
    key = _norm(name)
    # Merge in the live record for this project (adds clickup_list_id / folder).
    try:
        for d in discover_projects():
            if _same_project(key, _norm(d.get("name", ""))) or \
               _same_project(key, _norm(d.get("clickup_name", ""))):
                for k, v in d.items():
                    rec.setdefault(k, v)
                break
    except Exception:
        pass
    if rec.get("folder") and os.path.isdir(rec["folder"]):
        return rec["folder"], rec
    root = config.GITHUB_ROOT
    if os.path.isdir(root):
        for d in os.listdir(root):
            if key and key in _norm(d):
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
    open_t = [t for t in tasks if not clickup.is_done(t)]
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


def _same_project(a, b):
    """Whether a repo folder and a ClickUp list name refer to the same project.
    ClickUp lists are usually titled more fully than the folder — the repo
    'tu4ndoan' is the list 'tu4ndoan — Personal Website' — so a prefix match on
    the normalised names counts, not just an exact one."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.startswith(short)


def discover_projects():
    """Real projects = git repos in the GitHub root + lists in the ClickUp space,
    merged into one record per project. Returns [{name, folder?, clickup_list_id?}].
    """
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
                key = _norm(nm)
                # Attach to the matching repo if there is one, so a project never
                # shows up twice (once as the folder, once as the ClickUp list).
                hit = next((k for k in out if _same_project(k, key)), None)
                rec = out[hit] if hit else out.setdefault(key, {"name": nm})
                rec["clickup_list_id"] = lst.get("id")
                rec["clickup_name"] = nm
                rec.setdefault("name", nm)
    except Exception:
        pass
    return list(out.values())


def recent_commits(folder, limit=8):
    """Recent git commits for the project → [{subject, author, when, hash}]."""
    if not folder or not os.path.isdir(os.path.join(folder, ".git")):
        return []
    try:
        sep = "\x1f"          # unit separator — safe inside commit subjects
        out = subprocess.run(
            ["git", "log", f"-{int(limit)}", f"--pretty=%h{sep}%s{sep}%an{sep}%cr"],
            cwd=folder, capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace").stdout
    except Exception:
        return []
    rows = []
    for line in (out or "").splitlines():
        parts = line.split(sep)
        if len(parts) == 4:
            rows.append({"hash": parts[0], "subject": parts[1],
                         "author": parts[2], "when": parts[3]})
    return rows


def project_view(name):
    """Everything the UI needs for a project panel: folder, git state, recent
    commits, and live ClickUp tasks (open + recently completed)."""
    folder, rec = _find(name)
    disp = rec.get("name") or name
    view = {"name": disp, "folder": folder or "", "git": {}, "code": {},
            "commits": [], "tasks": {"open": [], "done": [], "counts": {}},
            "progress": {}, "found": bool(folder or rec.get("clickup_list_id"))}
    if folder:
        view["git"] = _git(folder)
        view["commits"] = recent_commits(folder)
        try:
            from . import codebase
            view["code"] = codebase.analyze(folder)
        except Exception:
            view["code"] = {}
    lid = rec.get("clickup_list_id")
    if lid and clickup.available():
        try:
            tasks = clickup.list_tasks(lid, include_closed=True)
        except Exception:
            tasks = []
        def row(t):
            st = t.get("status") or {}
            return {"name": t.get("name", ""), "status": st.get("status", ""),
                    "url": t.get("url", ""), "closed_at": clickup._closed_ms(t)}
        done = [row(t) for t in tasks if clickup.is_done(t)]
        open_t = [row(t) for t in tasks if not clickup.is_done(t)]
        done.sort(key=lambda r: r["closed_at"], reverse=True)
        view["tasks"] = {"open": open_t[:12], "done": done[:8],
                         "counts": {"open": len(open_t), "done": len(done),
                                    "total": len(tasks)}}
        view["clickup_url"] = f"https://app.clickup.com/{config.CLICKUP_TEAM_ID}/v/li/{lid}" \
            if getattr(config, "CLICKUP_TEAM_ID", "") else ""
    view["progress"] = project_progress(rec)
    return view


def project_progress(rec):
    """{done, total, next} — from ClickUp task status if the project has a list,
    else from plan.md/README checkboxes."""
    lid = rec.get("clickup_list_id")
    if lid and clickup.available():
        try:
            tasks = clickup.list_tasks(lid, include_closed=True)
            done = len([t for t in tasks if clickup.is_done(t)])
            open_t = [t for t in tasks if not clickup.is_done(t)]
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
