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

from . import files, graph, skills
from .mcp_client import get_mcp
_LOCAL = {"remember", "forget", "open_app", "close_app", "play_music",
          "write_file", "make_dir", "create_project", "relate", "unrelate"}

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
        '- write_file {"path": "...", "content": "..."} — create/overwrite a file '
        "in the user's workspace (code or documents: .py, .md, .txt, .json, .html, "
        ".pdf, …). Content is the full file body.\n"
        '- make_dir {"path": "..."} — create a folder.\n'
        '- create_project {"name": "...", "files": {"relative/path": "content", …}} '
        "— scaffold a whole project (a folder with many files) in one action.\n"
        '- relate {"subject": "...", "relation": "USES|OWNS|DEPENDS_ON|IMPLEMENTS|'
        'IS|LIKES|WORKS_ON|RELATED_TO|BLOCKED_BY", "object": "..."} — add/update a '
        "relationship in the knowledge graph (e.g. \"Trump IS handsome\", \"Orion USES "
        "Postgres\"). Use it WHENEVER the user states a fact, attribute, opinion or "
        "connection about an entity, so the graph stays current.\n"
        '- unrelate {"subject": "...", "object": "...", "relation": "..."} — remove a '
        "connection when the user retracts or corrects something (e.g. they say "
        "\"forget that Trump is handsome\"). Omit relation to remove any link between them.\n"
        "Maintain the knowledge graph as you chat: create nodes/links for new facts, "
        "and remove links the user takes back.\n"
        "Rules: keep your spoken reply natural, warm and brief. NEVER read the "
        "ACTION lines aloud or mention them. Only include ACTION lines when needed.\n"
        "IMPORTANT — when asked to create code, files, an app, or any lengthy output, "
        "WRITE it to files with write_file / create_project instead of dumping it in "
        "your reply. Then keep your reply a short summary of what you made and where "
        "(e.g. \"Done — created todo-app with main.py, README.md and requirements.txt "
        "in your PATROAM folder.\"). All paths are relative to the user's PATROAM "
        "workspace."
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
        if name == "remember":
            # Remember a fact about the user, in the knowledge graph.
            text = args.get("text", "")
            tr = skills.extract_triple(text)
            graph.add(*tr) if tr else graph.add_note(text)
        elif name == "forget":
            graph.forget(args.get("text", ""))
        elif name == "open_app":
            skills.open_app(args.get("name", ""))
        elif name == "close_app":
            skills.close_app(args.get("name", ""))
        elif name == "play_music":
            skills.play_music()
        elif name == "write_file":
            files.write_file(args.get("path", ""), args.get("content", ""))
        elif name == "make_dir":
            files.make_dir(args.get("path", ""))
        elif name == "create_project":
            files.create_project(args.get("name", ""), args.get("files"))
        elif name == "relate":
            graph.add(args.get("subject", ""), args.get("relation", ""), args.get("object", ""))
        elif name == "unrelate":
            graph.remove_triple(args.get("subject", ""), args.get("object", ""),
                                args.get("relation"))
        return None
    # External MCP tool → return its result for the model to use.
    mcp = get_mcp()
    if mcp.has_tool(name):
        return mcp.call_tool(name, args)
    return None
