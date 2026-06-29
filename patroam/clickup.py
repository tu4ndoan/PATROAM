"""ClickUp integration — push a project roadmap into ClickUp.

Stdlib-only REST client (like gold/stocks). Gated on a personal API token in
~/.patroam/secrets.json. Maps a roadmap to ClickUp: a List per project,
milestones → tasks, tasks → subtasks, subtasks → checklist items, plus a
Backlog task. Activates automatically once CLICKUP_API_TOKEN + CLICKUP_SPACE_ID
are set; otherwise the Planner still works locally (folder + README + graph).
"""

import json
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


def push_roadmap(project_name, roadmap):
    """Create a ClickUp list for the project and fill it from the roadmap.
    Returns a URL to open the board, or None on failure."""
    if not available():
        return None
    try:
        lst = create_list(project_name)
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
        return board_url
    except Exception:
        return None
