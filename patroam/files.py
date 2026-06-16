"""File-system actions — PATROAM actually creates files, folders, and projects.

Everything is sandboxed to config.WORKSPACE_DIR: a path that would resolve
outside the workspace is rejected (an LLM should never write just anywhere).
Text files are written directly; .pdf is rendered via reportlab if available.
"""

import os
import re

from . import config

# Fenced code blocks in a model reply: ```lang\n…code…``` .
_FENCE = re.compile(r"```([\w+.\-]*)[ \t]*\n(.*?)```", re.S)
_LANG_EXT = {
    "python": "py", "py": "py", "cpp": "cpp", "c++": "cpp", "cxx": "cpp", "cc": "cpp",
    "c": "c", "h": "h", "hpp": "hpp", "javascript": "js", "js": "js", "node": "js",
    "typescript": "ts", "ts": "ts", "java": "java", "go": "go", "golang": "go",
    "rust": "rs", "rs": "rs", "swift": "swift", "kotlin": "kt", "kt": "kt",
    "dart": "dart", "flutter": "dart", "html": "html",
    "css": "css", "json": "json", "yaml": "yaml", "yml": "yaml", "bash": "sh",
    "sh": "sh", "shell": "sh", "sql": "sql", "markdown": "md", "md": "md",
    "text": "txt", "txt": "txt", "": "txt",
}
_DEFAULT_NAME = {"py": "script", "cpp": "main", "c": "main", "h": "header", "js": "script",
                 "ts": "script", "java": "Main", "go": "main", "rs": "main", "html": "index",
                 "css": "style", "json": "data", "sh": "script", "sql": "query", "txt": "notes"}
_NAME_IN_REQ = re.compile(
    r"\b([\w\-]{1,40}\.(?:py|cpp|cc|cxx|c|hpp|h|js|ts|java|go|rs|swift|kt|html|css|json|ya?ml|sh|sql|md|txt))\b",
    re.I)


def _safe(path):
    """Resolve `path` under the workspace; return None if it escapes."""
    base = os.path.abspath(config.WORKSPACE_DIR)
    full = os.path.abspath(os.path.join(base, str(path or "").strip()))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def _is_root(full):
    return full == os.path.abspath(config.WORKSPACE_DIR)


def make_dir(path):
    if not str(path or "").strip():
        return None
    full = _safe(path)
    if not full or _is_root(full):          # never operate on the workspace root itself
        return None
    os.makedirs(full, exist_ok=True)
    return full


def write_file(path, content=""):
    if not str(path or "").strip():
        return None
    full = _safe(path)
    if not full or _is_root(full):
        return None
    os.makedirs(os.path.dirname(full) or full, exist_ok=True)
    content = content if isinstance(content, str) else str(content)
    if full.lower().endswith(".pdf"):
        _write_pdf(full, content)
    else:
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    return full


def create_project(name, files=None):
    """Scaffold a project: a folder plus (optionally) many files at once.
    `files` is {relative_path: content}. Returns the list of paths created."""
    if not str(name or "").strip():
        return []
    base = make_dir(name)
    if not base:
        return []
    made = []
    for rel, content in (files or {}).items():
        p = write_file(os.path.join(name, rel), content)
        if p:
            made.append(p)
    return made or [base]


def _sniff_ext(lang, code):
    e = _LANG_EXT.get((lang or "").lower().strip())
    if e:
        return e
    c = (code or "").lower()
    if "#include" in c or "int main" in c or "std::" in c:
        return "cpp"
    if "def " in c or "import " in c or "print(" in c:
        return "py"
    if "function " in c or "=>" in c or "console.log" in c or "const " in c:
        return "js"
    if "<html" in c or "<!doctype" in c:
        return "html"
    return "txt"


def _unique_name(name):
    """Avoid clobbering: foo.py → foo_2.py if foo.py already exists."""
    if not (_safe(name) and os.path.exists(_safe(name))):
        return name
    root, ext = os.path.splitext(name)
    i = 2
    while True:
        cand = f"{root}_{i}{ext}"
        full = _safe(cand)
        if not full or not os.path.exists(full):
            return cand
        i += 1


def save_code_from_reply(reply, request=""):
    """Extract fenced code block(s) from a model reply and save each to a file in
    the workspace. Filename comes from the request (if it names one), else a
    sensible default per language. Returns the list of created paths.

    This is how PATROAM reliably 'generates files' even when a weak model writes
    the code in a markdown block instead of emitting a write_file action."""
    blocks = _FENCE.findall(reply or "")
    if not blocks:
        return []
    named = _NAME_IN_REQ.search(request or "")
    made = []
    for i, (lang, code) in enumerate(blocks):
        if not code.strip():
            continue
        ext = _sniff_ext(lang, code)
        if named and i == 0:
            name = named.group(1)
        else:
            base = _DEFAULT_NAME.get(ext, "file")
            name = f"{base}.{ext}" if len(blocks) == 1 else f"{base}_{i + 1}.{ext}"
        p = write_file(_unique_name(name), code.rstrip("\n") + "\n")
        if p:
            made.append(p)
    return made


def run_command(command, cwd=None, timeout=180):
    """Run a shell command (tests, scaffolders, builds) inside the workspace and
    return its combined output. Sandboxed: cwd can't escape the workspace."""
    command = (command or "").strip()
    if not command:
        return "(no command)"
    base = _safe(cwd or "") or os.path.abspath(config.WORKSPACE_DIR)
    os.makedirs(base, exist_ok=True)
    try:
        import subprocess
        r = subprocess.run(command, shell=True, cwd=base, capture_output=True,
                           text=True, timeout=timeout)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return f"$ {command}\n(exit {r.returncode})\n{out[-4000:]}"
    except subprocess.TimeoutExpired:
        return f"$ {command}\n(timed out after {timeout}s)"
    except Exception as e:
        return f"$ {command}\n(failed: {e})"


# A file written as: a path line (optionally "File:", bold, heading, backticks)
# immediately followed by a fenced code block. Robust for whole projects — no JSON.
_FILE_BLOCK = re.compile(
    r"(?:^|\n)[ \t>#*`]*(?:file[:\-]?\s*)?`?"
    r"((?:[\w.\-]+[\\/])*[\w.\-]+\.[A-Za-z0-9]{1,8})`?[ \t:*]*\r?\n+"
    r"```[\w+.\-]*[ \t]*\r?\n(.*?)```",
    re.I | re.S)
_NAMED_IN_REQ2 = re.compile(r"\b(?:called|named|name it|call it)\s+([\w \-]{2,40})", re.I)
_SKIP_WORDS = {"create", "make", "build", "a", "an", "the", "app", "application",
               "please", "new", "me", "write", "generate", "flutter", "python",
               "website", "web", "webapp", "desktop", "ios", "android", "project",
               "program", "script", "for", "with", "that", "and", "to", "my"}


def _project_name(request):
    m = _NAMED_IN_REQ2.search(request or "")
    if m:
        return (re.sub(r"[^\w\-]+", "_", m.group(1).strip()).strip("_")[:40] or "app")
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", request or "")
    keep = [w.lower() for w in words if w.lower() not in _SKIP_WORDS][:3]
    return "_".join(keep) or "app"


def save_project_from_reply(reply, request=""):
    """Save every 'path + code block' the model emitted, into the workspace with
    the right hierarchy. Returns created paths (empty if no file blocks found).
    This is how PATROAM scaffolds projects reliably — no JSON to break."""
    blocks = _FILE_BLOCK.findall(reply or "")
    if not blocks:
        return []
    paths = [p.strip().replace("\\", "/") for p, _ in blocks]
    tops = {p.split("/")[0] for p in paths if "/" in p}
    # If the model already nested everything under one folder, keep it; else add one.
    prefix = "" if (len(tops) == 1 and all("/" in p for p in paths)) else _project_name(request) + "/"
    made, seen = [], set()
    for raw_path, content in blocks:
        path = prefix + raw_path.strip().replace("\\", "/")
        if path in seen or not content.strip():
            continue
        seen.add(path)
        p = write_file(path, content.rstrip("\n") + "\n")
        if p:
            made.append(p)
    return made


# ── deterministic project templates (reliable scaffolding, no model needed) ───────
_PYTHON_TEMPLATE = {
    "main.py": "def main():\n    print(\"Hello from __NAME__!\")\n\n\nif __name__ == \"__main__\":\n    main()\n",
    "requirements.txt": "",
    "tests/test_main.py": "from main import main\n\n\ndef test_runs(capsys):\n    main()\n    assert \"__NAME__\" in capsys.readouterr().out\n",
    "README.md": "# __NAME__\n\nA Python app.\n\n## Run\n    python main.py\n## Test\n    pytest\n",
    ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
}
_WEBSITE_TEMPLATE = {
    "index.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\"/>\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n<title>__NAME__</title>\n<link rel=\"stylesheet\" href=\"css/style.css\"/>\n</head>\n<body>\n<h1>__NAME__</h1>\n<p>Welcome to your new site.</p>\n<script src=\"js/script.js\"></script>\n</body>\n</html>\n",
    "css/style.css": "body{font-family:system-ui,sans-serif;margin:2rem;color:#222}\nh1{color:#4f46e5}\n",
    "js/script.js": "console.log('__NAME__ loaded');\n",
    "README.md": "# __NAME__\n\nA website. Open index.html in a browser.\n",
}
_WEBAPP_TEMPLATE = {
    "index.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\"/>\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n<title>__NAME__</title>\n<link rel=\"stylesheet\" href=\"style.css\"/>\n</head>\n<body>\n<div id=\"app\"></div>\n<script src=\"app.js\"></script>\n</body>\n</html>\n",
    "app.js": "const app=document.getElementById('app');\napp.innerHTML='<h1>__NAME__</h1><button id=\"b\">Click</button><p id=\"o\"></p>';\nlet n=0;\ndocument.getElementById('b').onclick=()=>{document.getElementById('o').textContent='Clicks: '+(++n);};\n",
    "style.css": "body{font-family:system-ui,sans-serif;margin:2rem;color:#222}\nh1{color:#4f46e5}\nbutton{padding:8px 14px;cursor:pointer}\n",
    "README.md": "# __NAME__\n\nA simple web app. Open index.html (or serve with: python -m http.server).\n",
}
_DESKTOP_TEMPLATE = {
    "app.py": "import tkinter as tk\n\n\ndef main():\n    root = tk.Tk()\n    root.title(\"__NAME__\")\n    tk.Label(root, text=\"Hello from __NAME__\").pack(padx=40, pady=40)\n    root.mainloop()\n\n\nif __name__ == \"__main__\":\n    main()\n",
    "requirements.txt": "",
    "README.md": "# __NAME__\n\nA desktop app (Tkinter).\n    python app.py\n",
}
_FLUTTER_TEMPLATE = {
    "pubspec.yaml": "name: __PKG__\ndescription: A new Flutter project.\npublish_to: 'none'\nversion: 1.0.0+1\n\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n\ndependencies:\n  flutter:\n    sdk: flutter\n  cupertino_icons: ^1.0.6\n\ndev_dependencies:\n  flutter_test:\n    sdk: flutter\n\nflutter:\n  uses-material-design: true\n",
    "lib/main.dart": "import 'package:flutter/material.dart';\n\nvoid main() => runApp(const MyApp());\n\nclass MyApp extends StatelessWidget {\n  const MyApp({super.key});\n  @override\n  Widget build(BuildContext context) {\n    return MaterialApp(\n      title: '__NAME__',\n      theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.indigo),\n      home: const HomePage(),\n    );\n  }\n}\n\nclass HomePage extends StatelessWidget {\n  const HomePage({super.key});\n  @override\n  Widget build(BuildContext context) {\n    return Scaffold(\n      appBar: AppBar(title: const Text('__NAME__')),\n      body: const Center(child: Text('Welcome to __NAME__')),\n    );\n  }\n}\n",
    "test/widget_test.dart": "import 'package:flutter_test/flutter_test.dart';\nimport 'package:__PKG__/main.dart';\n\nvoid main() {\n  testWidgets('app builds', (tester) async {\n    await tester.pumpWidget(const MyApp());\n    expect(find.text('Welcome to __NAME__'), findsOneWidget);\n  });\n}\n",
    "README.md": "# __NAME__\n\nA Flutter app.\n    flutter pub get\n    flutter run\n",
    ".gitignore": ".dart_tool/\nbuild/\n",
}


def _scaffold_write(folder, template, subs):
    made = []
    for rel, content in template.items():
        for k, v in subs.items():
            content = content.replace(k, v)
        p = write_file(os.path.join(folder, rel), content)
        if p:
            made.append(p)
    return made


def _list_files(base, limit=300):
    out = []
    for dp, _, fns in os.walk(base or ""):
        for fn in fns:
            out.append(os.path.join(dp, fn))
            if len(out) >= limit:
                return out
    return out


def scaffold_project(kind, name):
    """Create a real, correctly-structured starter project of `kind`
    (flutter / python / website / webapp / desktop). Uses the Flutter SDK if
    installed, else built-in templates. Returns the created file paths."""
    kind = (kind or "").lower()
    raw = (name or "app").strip()
    pkg = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_") or "app"
    if not pkg[0].isalpha():
        pkg = "app_" + pkg
    disp = raw or pkg
    if "flutter" in kind or "dart" in kind:
        import shutil
        if shutil.which("flutter"):
            run_command(f"flutter create {pkg}")
            base = _safe(pkg)
            return _list_files(base) if base else []
        return _scaffold_write(pkg, _FLUTTER_TEMPLATE, {"__PKG__": pkg, "__NAME__": disp})
    if "python" in kind or kind in ("py", "cli", "script"):
        return _scaffold_write(pkg, _PYTHON_TEMPLATE, {"__NAME__": disp})
    if "desktop" in kind:
        return _scaffold_write(pkg, _DESKTOP_TEMPLATE, {"__NAME__": disp})
    if "website" in kind or "site" in kind or ("web" in kind and "app" not in kind):
        return _scaffold_write(pkg, _WEBSITE_TEMPLATE, {"__NAME__": disp})
    if "web" in kind:
        return _scaffold_write(pkg, _WEBAPP_TEMPLATE, {"__NAME__": disp})
    return _scaffold_write(pkg, {"README.md": "# __NAME__\n"}, {"__NAME__": disp})


def guess_project_name(reply, request):
    """Best-effort project name from the model's reply or the request."""
    for src in (reply, request):
        m = re.search(r"\b([A-Z][A-Za-z0-9]{1,30})\s+(?:flutter|app|application|project|website|web ?app|site)\b", src or "")
        if m and m.group(1).lower() not in ("the", "a", "an", "new", "ios", "my", "your", "this"):
            return m.group(1)
    return _project_name(request)


def scaffold_from_reply(kind, name, reply=""):
    """Deterministically scaffold a real project of `kind`, then overwrite each
    template file with the model's matching code block (yaml→pubspec.yaml,
    dart→lib/main.dart, …) when the model provided one. Returns created paths."""
    made = scaffold_project(kind, name)
    if not made:
        return []
    blocks = _FENCE.findall(reply or "")
    if blocks:
        base = os.path.abspath(config.WORKSPACE_DIR)
        by_ext = {}
        for p in made:                                   # primary file per extension
            e = os.path.splitext(p)[1].lstrip(".").lower()
            by_ext.setdefault(e, p)
        for lang, code in blocks:
            if not code.strip():
                continue
            target = by_ext.get(_sniff_ext(lang, code))
            if target:
                write_file(os.path.relpath(target, base), code.rstrip("\n") + "\n")
    return made


def _write_pdf(full, text):
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.pdfgen.canvas import Canvas
    except ImportError:
        # No PDF lib — save the text alongside so nothing is lost.
        with open(os.path.splitext(full)[0] + ".txt", "w", encoding="utf-8") as f:
            f.write(text)
        return
    c = Canvas(full, pagesize=LETTER)
    width, height = LETTER
    x, y = inch, height - inch
    for raw in text.split("\n"):
        line = raw
        while len(line) > 95:
            c.drawString(x, y, line[:95]); y -= 14; line = line[95:]
            if y < inch:
                c.showPage(); y = height - inch
        c.drawString(x, y, line); y -= 14
        if y < inch:
            c.showPage(); y = height - inch
    c.save()
