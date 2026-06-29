"""A tiny pub/sub hub for proactive messages PATROAM initiates itself.

Background tasks (e.g. the news watcher) call broadcast(); whatever output
channels are active subscribe to receive it:
  * the desktop orb subscribes and speaks + shows the message in chat,
  * the Slack bot subscribes and DMs it to your phone.

This keeps the producers (news_watch) decoupled from the sinks (UI / Slack).
"""

_subs = []


def subscribe(fn):
    """Register a sink: fn(payload) where payload = {"say": str, "show": str}."""
    if fn not in _subs:
        _subs.append(fn)
    return fn


def unsubscribe(fn):
    if fn in _subs:
        _subs.remove(fn)


def _normalize(payload):
    if isinstance(payload, dict):
        say = payload.get("say") or payload.get("show") or ""
        show = payload.get("show") or payload.get("say") or ""
        return {"say": say, "show": show}
    s = str(payload or "")
    return {"say": s, "show": s}


def broadcast(payload):
    """Deliver a proactive message to every active output channel."""
    p = _normalize(payload)
    for fn in list(_subs):
        try:
            fn(p)
        except Exception:
            pass
