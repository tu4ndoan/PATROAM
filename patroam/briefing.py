"""The daily briefing — PATROAM as Chief of Staff.

On launch (or on demand) PATROAM delivers a professional daily briefing in three
layers:
  A. Executive Summary — a short SPOKEN snapshot: what changed since last session
     (Fab sales delta), what to focus on, urgent feedback, a couple of news items.
  B. Personalized Dashboard — a structured overview shown ONLY in the chat (never
     spoken): priorities, business, projects, news, notes, recommended next action.
  C. Conversational opening — a spoken proposal (e.g. "Shall I put on your Focus
     playlist?").

Delivered through the notify hub: `say` is spoken on the orb (A + C), `show` is the
full text incl. the dashboard (A + B + C) + DM'd to Slack.
"""

import datetime
import json
import os

from . import config, llm, notify


# ── session snapshot (for "since our last session…") ──────────────────────────────
def _load_session():
    try:
        with open(config.SESSION_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_session(d):
    try:
        os.makedirs(os.path.dirname(config.SESSION_FILE), exist_ok=True)
        with open(config.SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


# ── signal gathering ──────────────────────────────────────────────────────────────
def _fab(prev):
    try:
        from . import fab
        t = fab.latest_totals()
    except Exception:
        t = None
    if not t:
        return None
    last = prev.get("fab", {})
    t["delta_sales"] = round(t["sales"] - last.get("sales", t["sales"]), 2)
    t["delta_units"] = t["units"] - last.get("units", t["units"])
    return t


def _projects():
    """Real projects: git repos in the GitHub root + ClickUp lists in the space."""
    out = []
    try:
        from . import manage
        for rec in manage.discover_projects():
            pr = manage.project_progress(rec)
            out.append({"name": rec["name"], "done": pr["done"],
                        "total": pr["total"], "next": pr["next"]})
    except Exception:
        pass
    return out


def _notes():
    try:
        from . import notes
        # title for the dashboard + body so the exec summary can spot conflicts.
        return [{"title": t, "body": (x or "").strip()[:300]} for t, x in notes.list_notes()]
    except Exception:
        return []


def _news():
    try:
        from . import news
        return news.latest_items(n=3)
    except Exception:
        return []


def _clickup(prev):
    """Current ClickUp tasks + what was completed since last session."""
    try:
        from . import clickup
        s = clickup.summary()
    except Exception:
        s = None
    if not s:
        return None
    # What ClickUp itself says was completed recently (works on the very first run,
    # and survives a reset session.json).
    names = [r["name"] for r in (s.get("done_recent") or []) if r.get("name")]
    # Plus anything that was open last session and isn't now — catches tasks closed
    # longer ago than the recent-window but still news to us.
    prev_cu = prev.get("clickup") or {}
    prev_ids = set(prev_cu.get("open_ids") or [])
    prev_names = prev_cu.get("names") or {}
    for i in (prev_ids - set(s["open_ids"])):
        nm = prev_names.get(i, i)
        if nm not in names:
            names.append(nm)
    s["completed"] = names[:6]
    return s


def _calendar():
    """Today's remaining events — so the briefing knows what the day holds."""
    try:
        from . import gcal
        if not gcal.available():
            return []
        return gcal.list_events(days=1, limit=8)
    except Exception:
        return []


def _feedback(limit=15):
    """Recent messages from the configured Slack customer-feedback channel."""
    if not config.SLACK_FEEDBACK_CHANNEL:
        return []
    try:
        from . import slack_bot
        client = slack_bot._client
        if client is None:
            return []
        res = client.conversations_history(channel=config.SLACK_FEEDBACK_CHANNEL, limit=limit)
        return [m.get("text", "") for m in res.get("messages", []) if m.get("text")]
    except Exception:
        return []


# ── composition ────────────────────────────────────────────────────────────────────
def _executive_summary(facts):
    """A short spoken narrative (layer A). LLM-composed, with a deterministic fallback."""
    lead = config.time_greeting()
    write = llm.complete if llm.available() else None
    if write:
        prompt = (
            "You are PATROAM, the user's Chief of Staff. Write a SHORT spoken executive "
            "briefing — 4 to 7 sentences, plain prose, NO markdown, NO bullets, NO headers. "
            "Cover: what changed since last session (Fab sales delta; and from clickup, how "
            "many tasks were completed and which task they were last working on — name its "
            "list); which task/project to focus on next; any urgent customer feedback; any "
            "schedule conflicts between notes; and 1-2 relevant news items. End with a single "
            "'recommended focus' sentence. Be concise and action-oriented. Do NOT add "
            "any greeting (no 'hello', no 'welcome back') — start directly with the substance. "
            "Facts (JSON):\n" + json.dumps(facts, ensure_ascii=False)[:4500])
        out = write(prompt, timeout=45)
        if out and out.strip():
            return lead + " " + out.strip()
    # Fallback: stitch a summary from the facts.
    bits = [lead]
    cal = facts.get("calendar") or []
    if cal:
        nxt = cal[0]
        bits.append(f"You have {len(cal)} event" + ("s" if len(cal) != 1 else "")
                    + f" today; next is {nxt['title']} at {nxt['when'].split('· ')[-1]}.")
    fb = facts.get("fab")
    if fb and fb.get("delta_sales"):
        bits.append(f"Since last session your Fab sales changed by ${fb['delta_sales']:+,.2f}, "
                    f"now ${fb['sales']:,.2f}.")
    cu = facts.get("clickup")
    if cu:
        if cu.get("completed"):
            bits.append(f"You completed {len(cu['completed'])} task"
                        + ("s" if len(cu['completed']) != 1 else "") + " since last session.")
        wk = (cu.get("in_progress") or cu.get("recent") or [None])[0]
        if wk:
            bits.append(f"You were last working on {wk['name']} in {wk['list']}.")
    pr = facts.get("projects") or []
    if pr:
        p = pr[0]
        bits.append(f"You have {len(pr)} active project" + ("s" if len(pr) != 1 else "")
                    + (f"; next up on {p['name']}: {p['next']}." if p.get("next") else "."))
    if facts.get("feedback"):
        bits.append(f"There are {len(facts['feedback'])} new customer messages to review.")
    return " ".join(bits)


def _dashboard(facts):
    """The structured, chat-only overview (layer B)."""
    L = ["━━━━━━━━━━━━━━", "Daily Briefing", "━━━━━━━━━━━━━━", ""]
    cal = facts.get("calendar") or []
    if cal:
        L += ["📅 TODAY'S SCHEDULE"] + [
            f"• {e['when']} — {e['title']}" + (f" ({e['location']})" if e.get("location") else "")
            for e in cal] + [""]
    pr = facts.get("projects") or []
    cu = facts.get("clickup")
    # Priorities: ClickUp in-progress tasks first, then project next steps.
    pri = [t["name"] for t in (cu.get("in_progress") if cu else [])]
    pri += [p["next"] for p in pr if p.get("next")]
    pri = pri[:4]
    if pri:
        L += ["🎯 TOP PRIORITIES"] + [f"{i + 1}. {x}" for i, x in enumerate(pri)] + [""]
    fb = facts.get("fab")
    if fb:
        d = (f"  (▲ ${fb['delta_sales']:+,.2f})" if fb.get("delta_sales") else "")
        L += ["📈 BUSINESS",
              f"• Fab: {fb['units']} units · ${fb['sales']:,.2f}{d}",
              f"• Top seller: {fb.get('top', '—')}", ""]
    if pr:
        L += ["🎮 PROJECTS"] + [
            f"• {p['name']} — {p['done']}/{p['total']} done"
            + (f" · next: {p['next']}" if p.get("next") else "") for p in pr] + [""]
    if cu:
        L += ["✅ TASKS (ClickUp)"]
        if cu.get("in_progress"):
            L += ["  In progress:"] + [f"  • {t['name']} — {t['list']}" for t in cu["in_progress"][:4]]
        elif cu.get("recent"):
            L += ["  Recently worked on:"] + [f"  • {t['name']} — {t['list']}" for t in cu["recent"][:3]]
        L += [f"  Open tasks: {cu['open']}"]
        if cu.get("completed"):
            L += [f"  ✓ Completed since last session: {', '.join(cu['completed'])}"]
        L += [""]
    news = facts.get("news") or []
    if news:
        L += ["📰 NEWS"] + [f"• {it['title']}" + (f"  {it['link']}" if it.get("link") else "")
                            for it in news] + [""]
    notes = facts.get("notes") or []
    if notes:
        L += ["📝 NOTES & REMINDERS"] + [f"• {n['title']}" for n in notes[:6]] + [""]
    fbk = facts.get("feedback") or []
    if fbk:
        L += ["⚠ CUSTOMER FEEDBACK"] + [f"• {m[:90]}" for m in fbk[:3]] + [""]
    # Recommended action: resume the ClickUp task you were last working on, else a
    # project's next step, else the first priority.
    if cu and (cu.get("in_progress") or cu.get("recent")):
        t = (cu.get("in_progress") or cu.get("recent"))[0]
        rec = f"Continue \"{t['name']}\" in {t['list']}"
    elif pr and pr[0].get("next"):
        rec = pr[0]["next"]
    else:
        rec = pri[0] if pri else "Review your notes"
    L += ["⚡ RECOMMENDED NEXT ACTION", "→ " + rec]
    return "\n".join(L)


def _opening():
    return "Shall I put on your Focus playlist, Sir?" if config.SPOTIFY_FOCUS_URL else ""


def gather():
    """Assemble the 3-layer briefing → {say, show, offer}, or None if nothing to report."""
    prev = _load_session()
    # Seven independent network round-trips. Sequentially they took ~10 s and the
    # briefing felt like a hang on startup; they don't depend on each other, so
    # fetch them at once and wait for the slowest.
    from concurrent.futures import ThreadPoolExecutor
    jobs = {
        "fab": lambda: _fab(prev),
        "projects": _projects,
        "clickup": lambda: _clickup(prev),
        "notes": _notes,
        "news": _news,
        "feedback": _feedback,
        "calendar": _calendar,
    }
    facts = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futures = {k: ex.submit(fn) for k, fn in jobs.items()}
        for k, fut in futures.items():
            try:
                facts[k] = fut.result(timeout=25)
            except Exception:
                facts[k] = None
    # Snapshot for next session's deltas.
    snap = {"ts": datetime.datetime.now().isoformat()}
    if facts["fab"]:
        snap["fab"] = {"units": facts["fab"]["units"], "sales": facts["fab"]["sales"]}
    if facts["clickup"]:
        snap["clickup"] = {"open_ids": facts["clickup"]["open_ids"],
                           "names": facts["clickup"]["names"]}
    _save_session(snap)

    if not (facts["fab"] or facts["projects"] or facts["clickup"] or facts["notes"]
            or facts["news"]):
        return None

    summary = _executive_summary(facts)   # A — spoken + shown
    dashboard = _dashboard(facts)         # B — shown only (NOT spoken)
    opening = _opening()                   # C — spoken + shown
    # Executive summary (A) + opening (C) are SPOKEN only. The chat shows just the
    # structured dashboard (B).
    say = summary + ((" " + opening) if opening else "")
    show = dashboard
    return {"say": say, "show": show, "offer": "focus" if opening else None}


def broadcast_launch():
    """Build the briefing and push it to every active channel (orb + Slack).
    Returns the briefing dict (so the caller can set up the focus-playlist offer)."""
    if not config.LAUNCH_BRIEFING:
        return None
    rep = gather()
    if rep:
        notify.broadcast({"say": rep["say"], "show": rep["show"]})
    return rep
