"""File-system actions — PATROAM actually creates files, folders, and projects.

Everything is sandboxed to config.WORKSPACE_DIR: a path that would resolve
outside the workspace is rejected (an LLM should never write just anywhere).
Text files are written directly; .pdf is rendered via reportlab if available.
"""

import os

from . import config


def _safe(path):
    """Resolve `path` under the workspace; return None if it escapes."""
    base = os.path.abspath(config.WORKSPACE_DIR)
    full = os.path.abspath(os.path.join(base, str(path or "").strip()))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def make_dir(path):
    full = _safe(path)
    if not full:
        return None
    os.makedirs(full, exist_ok=True)
    return full


def write_file(path, content=""):
    full = _safe(path)
    if not full:
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
    base = make_dir(name)
    if not base:
        return []
    made = []
    for rel, content in (files or {}).items():
        p = write_file(os.path.join(name, rel), content)
        if p:
            made.append(p)
    return made or [base]


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
