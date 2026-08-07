"""ClickUp integration — push a project roadmap into ClickUp.

Stdlib-only REST client (like gold/stocks). Gated on a personal API token in
~/.patroam/secrets.json. Maps a roadmap to ClickUp: a List per project,
milestones → tasks, tasks → subtasks, subtasks → checklist items, plus a
Backlog task. Activates automatically once CLICKUP_API_TOKEN + CLICKUP_SPACE_ID
are set; otherwise the Planner still works locally (folder + README + graph).
"""

import json
import time
import urllib.request

from . import config

_API = "https://api.clickup.com/api/v2"


def available():
    return bool(config.CLICKUP_API_TOKEN and config.CLICKUP_SPACE_ID)


def _api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        _API + path, data=data, method=method,
        headers={"Authorization": config.CLICKUP_API_TOKEN,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def resolve_space(name_or_id):
    """A space id from an id, or a partial/case-insensitive space name. Falls back
    to the configured default space."""
    s = str(name_or_id or "").strip()
    if not s:
        return config.CLICKUP_SPACE_ID
    if s.isdigit():
        return s
    try:
        for t in _api("GET", "/team").get("teams", []):
            for sp in _api("GET", f"/team/{t['id']}/space").get("spaces", []):
                if s.lower() in (sp.get("name", "").lower()):
                    return sp["id"]
    except Exception:
        pass
    return config.CLICKUP_SPACE_ID


def create_list(name, space_id=None):
    space_id = space_id or config.CLICKUP_SPACE_ID
    return _api("POST", f"/space/{space_id}/list", {"name": name})


def create_task(list_id, name, description="", parent=None):
    body = {"name": name[:255]}
    if description:
        body["description"] = description
    if parent:
        body["parent"] = parent
    return _api("POST", f"/list/{list_id}/task", body)


def add_checklist(task_id, name):
    return _api("POST", f"/task/{task_id}/checklist", {"name": name})


def add_checklist_item(checklist_id, name):
    return _api("POST", f"/checklist/{checklist_id}/checklist_item", {"name": name[:255]})


def _space_lists(space_id):
    """All lists in a space — folderless + inside folders."""
    lists = []
    try:
        lists += _api("GET", f"/space/{space_id}/list").get("lists", [])
    except Exception:
        pass
    try:
        for fo in _api("GET", f"/space/{space_id}/folder").get("folders", []):
            lists += fo.get("lists", [])
    except Exception:
        pass
    return lists


def list_tasks(list_id, include_closed=False, subtasks=True):
    """Tasks in a list. `subtasks=True` matters: push_roadmap() files real work as
    SUBTASKS under milestone tasks, and ClickUp omits subtasks unless asked — so
    without it PATROAM only ever saw the milestone headers."""
    q = "true" if include_closed else "false"
    sub = "&subtasks=true" if subtasks else ""
    try:
        return _api("GET", f"/list/{list_id}/task?include_closed={q}{sub}"
                           "&order_by=updated").get("tasks", [])
    except Exception:
        return []


def is_done(task):
    """True if a task is completed. ClickUp reports this via the status *type*
    ('done' for a done-category status, 'closed' for the closed one)."""
    return ((task.get("status") or {}).get("type") or "") in ("done", "closed")


def _closed_ms(task):
    """When the task was completed, in epoch-ms (0 if unknown)."""
    try:
        return int(task.get("date_closed") or task.get("date_done") or 0)
    except (TypeError, ValueError):
        return 0


def summary(space_id=None, recent_days=7):
    """A snapshot of the user's tasks in the space: open count, what's in progress,
    what was touched most recently ('working on'), AND what was completed recently
    — read straight from ClickUp's date_closed, so it's correct even on the very
    first run. Returns None if ClickUp isn't configured."""
    if not available():
        return None
    space_id = space_id or config.CLICKUP_SPACE_ID
    rows = []
    for lst in _space_lists(space_id):
        # include_closed=True — completed tasks are the whole point of "what did I
        # finish?"; without them PATROAM always answered "you've completed nothing".
        for t in list_tasks(lst.get("id"), include_closed=True):
            st = t.get("status") or {}
            rows.append({
                "id": t.get("id"), "name": t.get("name", ""),
                "list": (t.get("list") or {}).get("name") or lst.get("name", ""),
                "status": st.get("status", ""), "type": st.get("type", ""),
                "url": t.get("url", ""), "updated": int(t.get("date_updated") or 0),
                "done": is_done(t), "closed_at": _closed_ms(t)})
    open_rows = [r for r in rows if not r["done"]]
    done_rows = [r for r in rows if r["done"]]
    prog_re = ("progress", "doing", "wip", "active", "working", "review")
    in_progress = [r for r in open_rows if any(k in r["status"].lower() for k in prog_re)]
    recent = sorted(open_rows, key=lambda r: r["updated"], reverse=True)[:5]
    cutoff = (time.time() - recent_days * 86400) * 1000
    done_recent = sorted([r for r in done_rows if r["closed_at"] >= cutoff],
                         key=lambda r: r["closed_at"], reverse=True)
    return {
        "open": len(open_rows),
        "done": len(done_rows),
        "total": len(rows),
        "in_progress": in_progress[:5],
        "recent": recent,
        "done_recent": done_recent[:8],
        "open_ids": [r["id"] for r in open_rows],
        "names": {r["id"]: r["name"] for r in rows},
        "lists": {r["id"]: r["list"] for r in rows},
    }


def push_roadmap(project_name, roadmap, space_id=None):
    """Create a ClickUp list in `space_id` and fill it from the roadmap.
    Returns {list_id, url} or None on failure."""
    if not available():
        return None
    try:
        lst = create_list(project_name, space_id)
        list_id = (lst or {}).get("id")
        if not list_id:
            return None
        board_url = None
        for ms in roadmap.get("milestones", []):
            mt = create_task(list_id, ms.get("name", "Milestone"))
            mid = mt.get("id")
            board_url = board_url or mt.get("url")
            for t in ms.get("tasks", []):
                tt = create_task(list_id, t.get("name", "Task"), parent=mid)
                tid = tt.get("id")
                subs = t.get("subtasks") or []
                if tid and subs:
                    cl = add_checklist(tid, "Steps")
                    clid = (cl.get("checklist") or {}).get("id")
                    if clid:
                        for s in subs:
                            add_checklist_item(clid, str(s))
        backlog = roadmap.get("backlog") or []
        if backlog:
            bt = create_task(list_id, "Backlog")
            bid = bt.get("id")
            board_url = board_url or bt.get("url")
            if bid:
                cl = add_checklist(bid, "Backlog")
                clid = (cl.get("checklist") or {}).get("id")
                if clid:
                    for b in backlog:
                        add_checklist_item(clid, str(b))
        return {"list_id": list_id, "url": board_url}
    except Exception:
        return None
