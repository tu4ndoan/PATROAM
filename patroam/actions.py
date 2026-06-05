"""Model-driven actions ("tool-calling"), portable across any model.

Native tool-calling APIs differ per backend (and small local models support
them poorly), so PATROAM uses a simple, universal protocol instead: the model
ends its reply with `ACTION: <name> <json>` lines. The Agent parses those out,
runs them, and strips them from what gets spoken/shown. This works the same on
Claude, Llama, or anything else.

This is what lets PATROAM *decide* to remember something or open an app on its
own, mid-conversation — rather than only matching fixed phrases.
"""

import json
import re

from . import skills
from .mcp_client import get_mcp
from .memory import get_memory

_LOCAL = {"remember", "forget", "open_app", "close_app", "play_music"}

_ACTION_RE = re.compile(r"^ACTION:\s*([a-zA-Z_]\w*)\s*(\{.*\})?\s*$", re.M)


def tools_prompt():
    """Instructions appended to the system prompt describing available actions."""
    base = (
        "You can take actions on the user's behalf. When you need to, append "
        "lines at the VERY END of your reply — after your spoken answer — one per "
        "line, each formatted exactly as:\n"
        "ACTION: <name> <json-args>\n"
        "Available actions:\n"
        '- remember {"text": "..."} — save a durable fact about the user '
        "(their name, preferences, projects, important context) so you recall it "
        "in future conversations. Use this proactively whenever they share "
        "something worth keeping.\n"
        '- forget {"text": "..."} — remove saved facts matching the text.\n'
        '- open_app {"name": "..."} — open an application on their computer.\n'
        '- close_app {"name": "..."} — close an application.\n'
        "- play_music {} — open Spotify and play their Liked Songs.\n"
        "Rules: keep your spoken reply natural, warm and brief. NEVER read the "
        "ACTION lines aloud or mention them. Only include ACTION lines when an "
        "action is actually needed."
    )
    mcp = get_mcp().tools_prompt()
    return base + "\n" + mcp if mcp else base


def split(full):
    """Split a raw reply into (spoken_text, [(name, args), …])."""
    idx = full.find("ACTION:")
    spoken = full[:idx] if idx >= 0 else full
    actions = []
    for m in _ACTION_RE.finditer(full):
        name = m.group(1)
        raw = m.group(2)
        try:
            args = json.loads(raw) if raw else {}
        except Exception:
            args = {}
        actions.append((name, args))
    return spoken.strip(), actions


def run(name, args):
    """Execute one parsed action.

    Returns None for fire-and-forget local actions (no follow-up needed), or a
    result STRING for data-returning tools (MCP) — the Agent feeds that back so
    the model can answer with it.
    """
    args = args or {}
    if name in _LOCAL:
        mem = get_memory()
        if name == "remember":
            mem.add_fact(args.get("text", ""))
        elif name == "forget":
            mem.forget(args.get("text", ""))
        elif name == "open_app":
            skills.open_app(args.get("name", ""))
        elif name == "close_app":
            skills.close_app(args.get("name", ""))
        elif name == "play_music":
            skills.play_music()
        return None
    # External MCP tool → return its result for the model to use.
    mcp = get_mcp()
    if mcp.has_tool(name):
        return mcp.call_tool(name, args)
    return None
