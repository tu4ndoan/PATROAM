"""Automatic news watch: poll your trusted feeds on an interval and proactively
report anything NEW that matches your interests.

On the first pass it silently records what's already out there (so it doesn't
dump the whole feed at you), then on every later pass it reports only items it
hasn't seen before — spoken by the orb and/or DM'd to your phone via Slack
through the notify hub.
"""

import json
import os
import threading
import time

from . import config, news, notify

_started = False


def _load_seen():
    try:
        with open(config.NEWS_SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_seen(seen):
    try:
        os.makedirs(os.path.dirname(config.NEWS_SEEN_FILE), exist_ok=True)
        # Bound the file so it can't grow forever.
        with open(config.NEWS_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen)[-2000:], f)
    except Exception:
        pass


def _key(item):
    return item.get("link") or item.get("title", "")


def _report(items):
    top = items[:config.NEWS_WATCH_MAX]
    say = "New headlines, Sir. " + " ".join(
        f"{i + 1}. {it['title']}." for i, it in enumerate(top))
    show = "🗞 New since my last check:\n" + "\n".join(
        f"{i + 1}. {it['title']}" + (f"\n   {it['link']}" if it.get("link") else "")
        + (f"   ({it['source']})" if it.get("source") else "")
        for i, it in enumerate(top))
    notify.broadcast({"say": say, "show": show})


def _loop():
    seen = _load_seen()
    first = not seen
    while True:
        try:
            items = news.latest_items(n=50)
            if config.NEWS_WATCH_INTERESTS_ONLY:
                fresh = [it for it in items
                         if _key(it) not in seen and it.get("_score", 0) > 0]
            else:
                fresh = [it for it in items if _key(it) not in seen]
            for it in items:
                seen.add(_key(it))
            _save_seen(seen)
            if fresh and not first:
                _report(fresh)
            first = False
        except Exception:
            pass
        time.sleep(max(60, config.NEWS_WATCH_INTERVAL))


def start():
    """Begin watching in a background thread (idempotent)."""
    global _started
    if _started or not config.NEWS_WATCH or not news.available():
        return False
    _started = True
    threading.Thread(target=_loop, daemon=True).start()
    return True
