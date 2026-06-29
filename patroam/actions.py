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
          "write_file", "make_dir", "create_project", "relate", "unrelate", "merge"}

# Actions that create files/folders — their return is a path (or list of paths)
# for the UI to show as a clickable link, NOT a tool-result to feed back to the model.
FILE_ACTIONS = {"write_file", "make_dir", "create_project", "scaffold"}

_ACTION_HEAD = re.compile(r"ACTION:[ \t]*([a-zA-Z_]\w*)")


def _balanced_json(text, i):
    """From index `i` (a '{'), return the JSON object as a parseable string,
    scanning balanced braces ACROSS newlines, ignoring braces inside string
    values (code!), and escaping literal newlines/tabs inside strings so
    json.loads accepts it. Returns None if no balanced object is found."""
    out, depth, in_str, esc = [], 0, False, False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                out.append(c); esc = False
            elif c == "\\":
                out.append(c); esc = True
            elif c == '"':
                out.append(c); in_str = False
            elif c == "\n":
                out.append("\\n")
            elif c == "\r":
                out.append("\\r")
            elif c == "\t":
                out.append("\\t")
            else:
                out.append(c)
        else:
            out.append(c)
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return "".join(out)
    return None


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
        "in the user's workspace. ANY file type works: source code (.py, .cpp, .c, "
        ".h, .js, .ts, .java, .go, .rs, .swift, .html, .css), data (.json, .csv, "
        ".yaml), or documents (.txt, .md, .pdf). When the user asks you to make/"
        "generate a script or file, WRITE it with this action; content is the full "
        "file body. Then say briefly what you created and where.\n"
        '- make_dir {"path": "..."} — create a folder.\n'
        '- create_project {"name": "...", "files": {"relative/path": "content", …}} '
        "— scaffold a whole project: a folder with the CORRECT structure/hierarchy "
        "for the type (Flutter iOS app, Python app, website, web app, desktop app) "
        "— include all needed files (e.g. pubspec.yaml/lib/main.dart for Flutter; "
        "main.py/requirements.txt/tests/ for Python; index.html/css/js for a site).\n"
        '- ask {"question": "...", "options": ["A", "B"]} — ASK the user to decide '
        "before you act. The options show as clickable buttons. Use this to confirm "
        "requirements and choices for any non-trivial build/coding task BEFORE writing "
        "code (e.g. \"Do you want Provider or Riverpod, Sir?\").\n"
        '- run {"command": "...", "cwd": "..."} — run a shell command in the workspace '
        "and get its output: run tests (\"pytest\", \"flutter test\", \"npm test\"), "
        "scaffolders (\"flutter create app\"), or builds. cwd is relative to the "
        "workspace.\n"
        '- scaffold {"type": "flutter|python|website|webapp|desktop", "name": "..."} '
        "— create a REAL starter project with the correct structure for that type. "
        "This is the RELIABLE way to create a project: emit this ONE action and "
        "PATROAM builds the whole folder/files. Use it once the user confirms what "
        "they want; then customize by writing extra/edited files as path + code "
        "blocks. Do NOT hand-write all project files yourself.\n"
        '- plan {"name": "...", "kind": "flutter|python|website|webapp|desktop|generic", '
        '"description": "...", "choices": ["..."]} — PLAN & CREATE a whole project: '
        "PATROAM generates a delivery roadmap (milestones → tasks → subtasks + a "
        "backlog), scaffolds the folder, writes a README with the plan, records it in "
        "the knowledge graph under Projects, and pushes the tasks to ClickUp. Emit this "
        "ONCE, only AFTER you've consulted the user and they've confirmed the goal, "
        "requirements and choices (use `ask` for those questions first). `choices` are "
        "the decisions they made (e.g. \"Riverpod\", \"Postgres\").\n"
        '- relate {"subject": "...", "relation": "USES|OWNS|DEPENDS_ON|IMPLEMENTS|'
        'IS|LIKES|WORKS_ON|RELATED_TO|BLOCKED_BY", "object": "..."} — add/update a '
        "relationship in the knowledge graph (e.g. \"Trump IS handsome\", \"Orion USES "
        "Postgres\"). Use it WHENEVER the user states a fact, attribute, opinion or "
        "connection about an entity, so the graph stays current.\n"
        '- unrelate {"subject": "...", "object": "...", "relation": "..."} — remove a '
        "connection when the user retracts or corrects something (e.g. they say "
        "\"forget that Trump is handsome\"). Omit relation to remove any link between them.\n"
        '- merge {"from": "...", "into": "..."} — merge two nodes that are the same '
        "entity written differently (e.g. \"Pham_Nhat_Vuong\" and \"Pham Nhat Vuong\"); "
        "all connections move to the kept node.\n"
        "Maintain the knowledge graph as you chat: create nodes/links for new facts, "
        "remove links the user takes back, and merge duplicate nodes.\n"
        "Rules: keep your spoken reply natural, warm and brief. NEVER read the "
        "ACTION lines aloud or mention them. Only include ACTION lines when needed.\n"
        "IMPORTANT — to create a project or multiple files, do NOT put file bodies "
        "in JSON. Instead output EACH file as its relative path on its own line, "
        "immediately followed by a fenced code block. Prefix paths with the project "
        "folder. PATROAM saves every one. Example:\n"
        "myapp/pubspec.yaml\n```yaml\n…\n```\n"
        "myapp/lib/main.dart\n```dart\n…\n```\n"
        "Then keep your spoken reply a one-line summary of what you made. All paths "
        "are relative to the user's PATROAM workspace."
    )
    mcp = get_mcp().tools_prompt()
    return base + "\n" + mcp if mcp else base


def split(full):
    """Split a raw reply into (spoken_text, [(name, args), …]).

    Robust to multi-line JSON, code with braces, and literal newlines inside
    string values — so create_project / write_file actually receive their files.
    """
    idx = full.find("ACTION:")
    spoken = full[:idx] if idx >= 0 else full
    acts = []
    for m in _ACTION_HEAD.finditer(full):
        name = m.group(1)
        j = m.end()
        while j < len(full) and full[j] in " \t":   # the '{' must follow the name
            j += 1
        args = {}
        if j < len(full) and full[j] == "{":
            raw = _balanced_json(full, j)
            if raw:
                try:
                    args = json.loads(raw)
                except Exception:
                    args = {}
        acts.append((name, args))
    return spoken.strip(), acts


def run(name, args):
    """Execute one parsed action.

    Returns None for fire-and-forget local actions (no follow-up needed), or a
    result STRING for data-returning tools (MCP) — the Agent feeds that back so
    the model can answer with it.
    """
    args = args or {}
    # Ask the user to choose (renders option buttons; pauses for their answer).
    if name == "ask":
        return {"question": args.get("question", ""), "options": args.get("options") or []}
    # Run a shell command (tests / scaffolders / builds) in the workspace.
    if name == "run":
        return files.run_command(args.get("command", ""), args.get("cwd"))
    # Scaffold a real project of a given type (deterministic, reliable).
    if name == "scaffold":
        return files.scaffold_project(args.get("type") or args.get("kind"), args.get("name"))
    # Plan & create a full project (roadmap + scaffold + README + graph + ClickUp).
    if name == "plan":
        from . import planner
        rep = planner.create_project(args.get("name", ""), args.get("kind", ""),
                                     args.get("description", ""), args.get("choices"))
        return rep.get("show") if isinstance(rep, dict) else rep
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
            return files.write_file(args.get("path", ""), args.get("content", ""))
        elif name == "make_dir":
            return files.make_dir(args.get("path", ""))
        elif name == "create_project":
            return files.create_project(args.get("name", ""), args.get("files"))
        elif name == "relate":
            graph.add(args.get("subject", ""), args.get("relation", ""), args.get("object", ""))
        elif name == "unrelate":
            graph.remove_triple(args.get("subject", ""), args.get("object", ""),
                                args.get("relation"))
        elif name == "merge":
            graph.merge(args.get("from", ""), args.get("into", ""))
        return None
    # External MCP tool → return its result for the model to use.
    mcp = get_mcp()
    if mcp.has_tool(name):
        return mcp.call_tool(name, args)
    return None
