"""Google Calendar — read, create, move and cancel events.

Stdlib-only REST client against Calendar API v3 (same house style as clickup.py
and gold.py — no google-api-python-client dependency). Gated on OAuth
credentials in ~/.patroam/secrets.json:

    GCAL_CLIENT_ID / GCAL_CLIENT_SECRET / GCAL_REFRESH_TOKEN

Run `python -m patroam.wire_gcal` once to obtain them. The YouTube OAuth client
in the same Google Cloud project can be reused — the helper offers that.

All times are handled in the local timezone (config.TIMEZONE if set, else the
machine's), because "tomorrow at 3pm" means *your* 3pm.
"""

import datetime
import json
import re
import urllib.parse
import urllib.request

from . import config

_API = "https://www.googleapis.com/calendar/v3"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Cached access token: (token_string, expiry_epoch)
_TOKEN = [None, 0.0]


def available():
    return bool(config.GCAL_CLIENT_ID and config.GCAL_CLIENT_SECRET
                and config.GCAL_REFRESH_TOKEN)


def _access_token():
    """A valid access token, refreshed on demand (cached until ~1 min before expiry)."""
    import time
    tok, exp = _TOKEN
    if tok and time.time() < exp:
        return tok
    data = urllib.parse.urlencode({
        "client_id": config.GCAL_CLIENT_ID,
        "client_secret": config.GCAL_CLIENT_SECRET,
        "refresh_token": config.GCAL_REFRESH_TOKEN,
        "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode())
    tok = payload.get("access_token")
    _TOKEN[0] = tok
    _TOKEN[1] = time.time() + int(payload.get("expires_in", 3600)) - 60
    return tok


# Why the last call failed, in words a human can act on. Swallowing errors made
# a very fixable problem ("enable the Calendar API") look like a mystery bug.
_LAST_ERROR = [""]


def last_error():
    return _LAST_ERROR[0]


def _explain(err):
    """Turn an API/HTTP failure into an actionable sentence."""
    import urllib.error
    if isinstance(err, urllib.error.HTTPError):
        try:
            body = json.loads(err.read().decode())
            msg = ((body.get("error") or {}).get("message")
                   or (body.get("error_description") or "")) or str(err)
        except Exception:
            msg = str(err)
        if err.code == 403 and "has not been used in project" in msg:
            proj = ""
            for tok in msg.split():
                if tok.rstrip(".").isdigit():
                    proj = tok.rstrip(".")
                    break
            return ("The Google Calendar API isn't enabled for your Google Cloud "
                    "project yet. Enable it here, wait a minute, then try again:\n"
                    "https://console.developers.google.com/apis/api/"
                    "calendar-json.googleapis.com/overview"
                    + (f"?project={proj}" if proj else ""))
        if err.code in (401, 403) and ("invalid" in msg.lower() or "credential" in msg.lower()):
            return ("Google rejected the credentials. Re-authorise with: "
                    "python -m patroam.wire_gcal\n" + msg)
        return f"Google Calendar API error {err.code}: {msg}"
    return f"{type(err).__name__}: {err}"


def _api(method, path, body=None, **params):
    url = _API + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": "Bearer " + _access_token(),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        _LAST_ERROR[0] = ""
        return json.loads(raw) if raw else {}
    except Exception as e:
        _LAST_ERROR[0] = _explain(e)
        raise


# ── time helpers ──────────────────────────────────────────────────────────────
def _tz():
    """The local timezone (aware), honouring config.TIMEZONE when set."""
    name = getattr(config, "TIMEZONE", "") or ""
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.datetime.now().astimezone().tzinfo


def _aware(dt):
    """Attach the local timezone to a naive datetime."""
    return dt if dt.tzinfo else dt.replace(tzinfo=_tz())


def _rfc3339(dt):
    return _aware(dt).isoformat()


def _parse(s):
    """Parse an RFC3339 / 'YYYY-MM-DD' string from the API into a datetime."""
    if not s:
        return None
    try:
        if len(s) == 10:                       # all-day event: date only
            return datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=_tz())
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _row(ev):
    """Flatten an API event into the shape the UI/voice layers use."""
    st, en = ev.get("start") or {}, ev.get("end") or {}
    allday = "date" in st
    s = _parse(st.get("dateTime") or st.get("date"))
    e = _parse(en.get("dateTime") or en.get("date"))
    return {
        "id": ev.get("id", ""),
        "title": ev.get("summary", "(no title)"),
        "start": s.isoformat() if s else "",
        "end": e.isoformat() if e else "",
        "all_day": allday,
        "location": ev.get("location", ""),
        "description": ev.get("description", ""),
        "url": ev.get("htmlLink", ""),
        "when": _human(s, e, allday),
    }


def _human(s, e, allday=False):
    """'Tue 12 Aug · 15:00–16:00' — a phrase that reads well aloud and on screen."""
    if not s:
        return ""
    now = datetime.datetime.now(tz=_tz())
    day = s.strftime("%a %d %b")
    if s.date() == now.date():
        day = "Today"
    elif s.date() == (now + datetime.timedelta(days=1)).date():
        day = "Tomorrow"
    if allday:
        return day + " · all day"
    out = f"{day} · {s.strftime('%H:%M')}"
    if e:
        out += f"–{e.strftime('%H:%M')}"
    return out


# ── reading ───────────────────────────────────────────────────────────────────
# Small caches. Voice replies need to land in well under a second, and the
# calendar list barely changes — re-fetching it per question cost ~900 ms.
_CAL_CACHE = {"at": 0.0, "data": None}
_CAL_TTL = 300.0          # seconds


def calendars(force=False):
    """Every calendar on the account — primary, birthdays, holidays, shared ones.
    Reading only "primary" silently hid birthdays and any secondary calendar."""
    if not available():
        return []
    import time as _t
    if not force and _CAL_CACHE["data"] is not None and \
            (_t.time() - _CAL_CACHE["at"]) < _CAL_TTL:
        return _CAL_CACHE["data"]
    try:
        out = [{"id": c.get("id", ""), "name": c.get("summary", ""),
                "primary": bool(c.get("primary"))}
               for c in _api("GET", "/users/me/calendarList").get("items", [])
               if c.get("id") and c.get("selected", True)]
    except Exception:
        return _CAL_CACHE["data"] or []
    _CAL_CACHE["at"], _CAL_CACHE["data"] = _t.time(), out
    return out


def _events_in(calendar_id, lo, hi, limit):
    try:
        res = _api("GET", f"/calendars/{urllib.parse.quote(calendar_id)}/events",
                   timeMin=_rfc3339(lo), timeMax=_rfc3339(hi),
                   singleEvents="true", orderBy="startTime", maxResults=limit)
    except Exception:
        return []
    return [e for e in res.get("items", []) if e.get("status") != "cancelled"]


def list_events(days=7, calendar_id=None, limit=20, start=None):
    """Upcoming events over the next `days` (default a week).

    Reads EVERY calendar by default (birthdays and holidays live on their own
    calendars, so "primary" alone misses them). Pass calendar_id to narrow."""
    if not available():
        return []
    lo = _aware(start or datetime.datetime.now())
    hi = lo + datetime.timedelta(days=days)
    if calendar_id:
        rows = []
        for e in _events_in(calendar_id, lo, hi, limit):
            r = _row(e)
            r["calendar_id"] = calendar_id      # so the UI can delete the right one
            rows.append(r)
    else:
        cals = calendars() or [{"id": "primary", "name": "", "primary": True}]
        # One HTTP call per calendar, run in PARALLEL: sequentially this was
        # ~2.1 s for three calendars, which is far too slow to answer by voice.
        from concurrent.futures import ThreadPoolExecutor
        rows = []
        with ThreadPoolExecutor(max_workers=min(6, len(cals))) as ex:
            futures = {ex.submit(_events_in, c["id"], lo, hi, limit): c for c in cals}
            for fut, c in futures.items():
                try:
                    events = fut.result(timeout=20)
                except Exception:
                    continue
                for e in events:
                    r = _row(e)
                    r["calendar_id"] = c["id"]
                    if not c.get("primary"):
                        r["calendar"] = c["name"]
                    rows.append(r)
        rows.sort(key=lambda r: r["start"] or "")
    return rows[:limit]


def agenda(day=None, calendar_id="primary"):
    """Everything on one day (default today)."""
    d = day or datetime.datetime.now(tz=_tz())
    start = _aware(datetime.datetime.combine(d.date(), datetime.time.min))
    return list_events(days=1, calendar_id=calendar_id, start=start)


def find_event(query, days=60, calendar_id="primary"):
    """The next event whose title matches `query` (case-insensitive substring)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for ev in list_events(days=days, calendar_id=calendar_id, limit=50):
        if q in ev["title"].lower():
            return ev
    return None


# ── writing ───────────────────────────────────────────────────────────────────
def conflicts(start, end, ignore_id=None):
    """Existing events that overlap [start, end) — so PATROAM can warn before
    double-booking instead of silently stacking two things on the same hour.
    All-day entries (holidays, birthdays) don't count as a clash."""
    if not available():
        return []
    s, e = _aware(start), _aware(end)
    out = []
    # Widen the window slightly so events starting just before `s` are seen.
    for ev in list_events(days=2, start=s - datetime.timedelta(days=1), limit=100):
        if ev["all_day"] or ev["id"] == ignore_id:
            continue
        es, ee = _parse(ev["start"]), _parse(ev["end"])
        if es and ee and es < e and ee > s:        # half-open overlap
            out.append(ev)
    return out


def create_event(title, start, end=None, duration_minutes=60, description="",
                 location="", all_day=False, calendar_id="primary"):
    """Add an event. `start`/`end` are datetimes. Returns the created row."""
    if not available():
        return None
    body = {"summary": (title or "Untitled").strip()[:255]}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if all_day:
        d = _aware(start).date()
        body["start"] = {"date": d.isoformat()}
        body["end"] = {"date": (d + datetime.timedelta(days=1)).isoformat()}
    else:
        s = _aware(start)
        e = _aware(end) if end else s + datetime.timedelta(minutes=duration_minutes)
        body["start"] = {"dateTime": _rfc3339(s)}
        body["end"] = {"dateTime": _rfc3339(e)}
    try:
        return _row(_api("POST", f"/calendars/{urllib.parse.quote(calendar_id)}/events", body))
    except Exception:
        return None


def update_event(event_id, title=None, start=None, end=None, description=None,
                 location=None, calendar_id="primary"):
    """Change an existing event (only the fields you pass). Returns the new row."""
    if not available() or not event_id:
        return None
    patch = {}
    if title is not None:
        patch["summary"] = title.strip()[:255]
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    if start is not None:
        patch["start"] = {"dateTime": _rfc3339(start)}
        # Google rejects a start past the existing end — move the end along too.
        patch["end"] = {"dateTime": _rfc3339(end or _aware(start) + datetime.timedelta(hours=1))}
    elif end is not None:
        patch["end"] = {"dateTime": _rfc3339(end)}
    if not patch:
        return None
    try:
        return _row(_api("PATCH",
                         f"/calendars/{urllib.parse.quote(calendar_id)}/events/"
                         f"{urllib.parse.quote(event_id)}", patch))
    except Exception:
        return None


def delete_event(event_id, calendar_id="primary"):
    """Cancel an event. Returns True on success."""
    if not available() or not event_id:
        return False
    try:
        _api("DELETE", f"/calendars/{urllib.parse.quote(calendar_id)}/events/"
                       f"{urllib.parse.quote(event_id)}")
        return True
    except Exception:
        return False


# ── Google Tasks ──────────────────────────────────────────────────────────────
# Tasks appear in the Google Calendar UI but are a SEPARATE API — the Calendar
# endpoints never return them, which is why they looked missing.
_TASKS_API = "https://tasks.googleapis.com/tasks/v1"


def _tapi(method, path, body=None, **params):
    url = _TASKS_API + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": "Bearer " + _access_token(),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        _LAST_ERROR[0] = ""
        return json.loads(raw) if raw else {}
    except Exception as e:
        _LAST_ERROR[0] = _explain(e)
        raise


_TL_CACHE = {"at": 0.0, "data": None}


def task_lists(force=False):
    """The user's Google Tasks lists (cached — they rarely change)."""
    if not available():
        return []
    import time as _t
    if not force and _TL_CACHE["data"] is not None and \
            (_t.time() - _TL_CACHE["at"]) < _CAL_TTL:
        return _TL_CACHE["data"]
    try:
        out = [{"id": t.get("id"), "name": t.get("title", "")}
               for t in _tapi("GET", "/users/@me/lists").get("items", [])]
    except Exception:
        return _TL_CACHE["data"] or []
    _TL_CACHE["at"], _TL_CACHE["data"] = _t.time(), out
    return out


# A "!" / "!!" / "high" marker in the title bumps a task up the list. Google
# Tasks has no priority field, so the title is the only place to put one.
_PRIORITY_RE = re.compile(r"(?:^|\s)(!{1,3}|\bp[123]\b|\b(?:urgent|gấp|quan trọng)\b)",
                          re.IGNORECASE)


def _due_stamp(due):
    """A Tasks due date as Google stores it: midnight UTC of the intended day.

    Sending LOCAL midnight silently shifted every due date a day earlier — at
    UTC+7, "13 Aug 00:00 +07:00" is "12 Aug 17:00Z", and Tasks keeps only the
    date part of that instant."""
    d = _aware(due).date()
    return datetime.datetime(d.year, d.month, d.day,
                             tzinfo=datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _priority(title):
    """0 = normal, 1 = important, 2 = urgent — read from markers in the title."""
    m = _PRIORITY_RE.search(title or "")
    if not m:
        return 0
    tag = m.group(1).lower()
    if tag in ("!!!", "!!", "p1", "urgent", "gấp"):
        return 2
    return 1


def list_tasks(include_done=False, limit=25):
    """Google Tasks across every list, ordered the way you'd actually work them:
    overdue first, then by priority, then by due date, undated last."""
    if not available():
        return []
    today = datetime.datetime.now(tz=_tz()).date()
    out = []
    for tl in task_lists():
        try:
            res = _tapi("GET", f"/lists/{urllib.parse.quote(tl['id'])}/tasks",
                        showCompleted="true" if include_done else "false",
                        showHidden="true" if include_done else "false",
                        maxResults=100)
        except Exception:
            continue
        for t in res.get("items", []):
            if t.get("deleted"):
                continue
            due = _parse(t.get("due"))
            done = (t.get("status") == "completed")
            comp = _parse(t.get("completed"))
            title = t.get("title", "").strip() or "(untitled)"
            out.append({
                "id": t.get("id", ""), "title": title,
                "list": tl["name"], "list_id": tl["id"],
                "due": due.isoformat() if due else "",
                "when": _human(due, None, allday=True) if due else "",
                "overdue": bool(due and not done and due.date() < today),
                "today": bool(due and due.date() == today),
                "priority": _priority(title),
                "notes": t.get("notes", ""),
                "done": done,
                "completed_at": comp.isoformat() if comp else "",
            })
    out.sort(key=lambda r: (
        not r["overdue"],           # overdue first
        -r["priority"],             # then urgent/important
        not r["due"],               # dated before undated
        r["due"],                   # soonest first
    ))
    return out[:limit]


def tasks_snapshot(limit=50):
    """Everything the TODO panel needs in one round-trip: open tasks in working
    order, plus what was completed recently (so PATROAM can report progress)."""
    rows = list_tasks(include_done=True, limit=200)
    open_t = [r for r in rows if not r["done"]]
    done_t = sorted([r for r in rows if r["done"]],
                    key=lambda r: r["completed_at"], reverse=True)
    return {
        "open": open_t[:limit],
        "done": done_t[:15],
        "counts": {"open": len(open_t), "done": len(done_t),
                   "overdue": len([r for r in open_t if r["overdue"]]),
                   "today": len([r for r in open_t if r["today"]])},
        "lists": task_lists(),
    }


def create_task(title, due=None, notes="", list_id=None):
    """Add a Google Task. `due` is a datetime (Google stores the DATE only)."""
    if not available() or not (title or "").strip():
        return None
    if not list_id:
        lists = task_lists()
        if not lists:
            return None
        list_id = lists[0]["id"]
    body = {"title": title.strip()[:1024]}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = _due_stamp(due)
    try:
        t = _tapi("POST", f"/lists/{urllib.parse.quote(list_id)}/tasks", body)
    except Exception:
        return None
    d = _parse(t.get("due"))
    return {"id": t.get("id", ""), "title": t.get("title", ""),
            "due": d.isoformat() if d else "", "when": _human(d, None, True) if d else "",
            "done": False}


def complete_task(task_id, list_id=None):
    """Tick a Google Task off. Returns True on success."""
    if not available() or not task_id:
        return False
    if not list_id:
        for t in list_tasks(limit=100):
            if t["id"] == task_id:
                list_id = t["list_id"]
                break
    if not list_id:
        return False
    try:
        _tapi("PATCH", f"/lists/{urllib.parse.quote(list_id)}/tasks/"
                       f"{urllib.parse.quote(task_id)}", {"status": "completed"})
        return True
    except Exception:
        return False


def reopen_task(task_id, list_id=None):
    """Un-tick a task (mistakes happen). Returns True on success."""
    if not available() or not task_id:
        return False
    list_id = list_id or _list_of(task_id, done=True)
    if not list_id:
        return False
    try:
        # Clearing `completed` as well as the status is required, otherwise the
        # API keeps the completion timestamp and re-hides it.
        _tapi("PATCH", f"/lists/{urllib.parse.quote(list_id)}/tasks/"
                       f"{urllib.parse.quote(task_id)}",
              {"status": "needsAction", "completed": None})
        return True
    except Exception:
        return False


def delete_task(task_id, list_id=None):
    """Remove a task entirely. Returns True on success."""
    if not available() or not task_id:
        return False
    list_id = list_id or _list_of(task_id, done=True)
    if not list_id:
        return False
    try:
        _tapi("DELETE", f"/lists/{urllib.parse.quote(list_id)}/tasks/"
                        f"{urllib.parse.quote(task_id)}")
        return True
    except Exception:
        return False


def update_task(task_id, title=None, due=None, notes=None, list_id=None):
    """Edit a task's title / due date / notes. Returns True on success."""
    if not available() or not task_id:
        return False
    list_id = list_id or _list_of(task_id, done=True)
    if not list_id:
        return False
    patch = {}
    if title is not None:
        patch["title"] = title.strip()[:1024]
    if notes is not None:
        patch["notes"] = notes
    if due is not None:
        patch["due"] = _due_stamp(due) if due else None
    if not patch:
        return False
    try:
        _tapi("PATCH", f"/lists/{urllib.parse.quote(list_id)}/tasks/"
                       f"{urllib.parse.quote(task_id)}", patch)
        return True
    except Exception:
        return False


def _list_of(task_id, done=False):
    """Which list holds `task_id` (Tasks operations are per-list)."""
    for t in list_tasks(include_done=done, limit=200):
        if t["id"] == task_id:
            return t["list_id"]
    return None


def find_task(query):
    """The first open task whose title matches `query`."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for t in list_tasks(limit=100):
        if q in t["title"].lower():
            return t
    return None


def free_slots(day=None, work_start=9, work_end=18, minutes=60, calendar_id="primary"):
    """Gaps of at least `minutes` in the working day — for "when am I free?"."""
    d = (day or datetime.datetime.now(tz=_tz())).date()
    lo = _aware(datetime.datetime.combine(d, datetime.time(work_start)))
    hi = _aware(datetime.datetime.combine(d, datetime.time(work_end)))
    busy = []
    for ev in list_events(days=1, calendar_id=calendar_id, start=lo, limit=50):
        s, e = _parse(ev["start"]), _parse(ev["end"])
        if s and e and not ev["all_day"]:
            busy.append((s, e))
    busy.sort()
    slots, cur = [], lo
    for s, e in busy:
        if (s - cur).total_seconds() >= minutes * 60:
            slots.append({"start": cur.isoformat(), "end": s.isoformat(),
                          "when": _human(cur, s)})
        cur = max(cur, e)
    if (hi - cur).total_seconds() >= minutes * 60:
        slots.append({"start": cur.isoformat(), "end": hi.isoformat(), "when": _human(cur, hi)})
    return slots
