"""Chat with PATROAM from Slack (e.g. the Slack app on your phone).

Uses Slack **Socket Mode**: PATROAM opens an outbound WebSocket to Slack, so it
runs on your own computer with no public URL, no port-forwarding, and no cloud.
Direct-message the bot (or @mention it in a channel) and it replies with the
full PATROAM brain. It also subscribes to the notify hub, so proactive alerts
(like the news watch) get DM'd to you.

Setup (one time):
  1. https://api.slack.com/apps → Create New App → From scratch.
  2. Socket Mode → enable it; generate an App-Level token (scope connections:write)
     → that's your SLACK_APP_TOKEN (xapp-…).
  3. OAuth & Permissions → Bot Token Scopes: app_mentions:read, chat:write,
     im:read, im:write, im:history. Install to workspace → Bot token (xoxb-…) is
     your SLACK_BOT_TOKEN.
  4. Event Subscriptions → Subscribe to bot events: message.im, app_mention.
  5. Put both tokens in ~/.patroam/secrets.json. (Optional: SLACK_DM_CHANNEL = a
     channel id the bot is in, for proactive news alerts.)
"""

import re

from . import config, notify

_brain = None
_client = None
_handler = None   # keep the Socket Mode handler alive (don't let it get GC'd)


def enabled():
    return config.slack_enabled()


def _reply_to(text):
    global _brain
    if _brain is None:
        from .brain import Brain
        _brain = Brain()
    try:
        return _brain.respond(text)
    except Exception as e:
        return f"Sorry, Sir — I hit an error: {e}"


def create_devlog_channel(project_name, intro=""):
    """Create a PRIVATE #devlog-<project> channel, invite the user, post an intro.
    Returns {id, name} or None. Needs bot scopes groups:write (+ groups:read)."""
    if _client is None:
        return None
    import re
    ch = "devlog-" + re.sub(r"[^a-z0-9]+", "-", (project_name or "project").lower()).strip("-")
    ch = ch[:79]
    try:
        res = _client.conversations_create(name=ch, is_private=True)
        cid = res["channel"]["id"]
    except Exception:
        return None
    if config.SLACK_USER_ID:
        try:
            _client.conversations_invite(channel=cid, users=config.SLACK_USER_ID)
        except Exception:
            pass
    if intro:
        try:
            _client.chat_postMessage(channel=cid, text=intro)
        except Exception:
            pass
    return {"id": cid, "name": ch}


def post(channel_id, text):
    """Post a message to a channel (e.g. a dev-log issue). Best-effort."""
    if _client is None or not channel_id:
        return False
    try:
        _client.chat_postMessage(channel=channel_id, text=text)
        return True
    except Exception:
        return False


def _notify(payload):
    """Push a proactive message (news, etc.) to your Slack DM/channel."""
    ch = config.SLACK_DM_CHANNEL
    if not (_client and ch):
        return
    try:
        _client.chat_postMessage(channel=ch, text=payload.get("show") or payload.get("say") or "")
    except Exception:
        pass


def start():
    """Connect to Slack in a background thread. Returns True if it started."""
    if not enabled():
        return False
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        return False

    global _client
    app = App(token=config.SLACK_BOT_TOKEN)
    _client = app.client

    @app.event("message")
    def _on_message(event, say):
        # Ignore the bot's own messages, edits, joins, etc.
        if event.get("bot_id") or event.get("subtype"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        say(_reply_to(text) or "…")

    @app.event("app_mention")
    def _on_mention(event, say):
        text = re.sub(r"<@[^>]+>", "", event.get("text") or "").strip()
        say((_reply_to(text) if text else "Yes, Sir?") or "…")

    notify.subscribe(_notify)
    global _handler
    _handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    # connect() is non-blocking and (unlike start()) installs NO SIGINT handler,
    # so it's safe off the main thread. Its own background threads keep it alive.
    _handler.connect()
    return True
