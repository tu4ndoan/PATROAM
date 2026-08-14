"""The Note-taker — quick capture into a Notes folder, indexed into the graph.

Notes are plain Markdown files in config.NOTES_DIR. Saving a note also injects it
into the knowledge graph under the `Notes` node (and LLM-extracts any facts), so
PATROAM can reason over them — surface suggestions and spot schedule conflicts.
"""

import datetime
import os
import re

from . import config, graph, llm


def _slug(s):
    s = re.sub(r"[^A-Za-z0-9 _-]+", "", (s or "")).strip().replace(" ", "-").lower()
    return s[:60] or datetime.datetime.now().strftime("note-%Y%m%d-%H%M%S")


def _title_from(text):
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return first[:60]


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _body(content):
    """The note text without the leading '# title' header line."""
    lines = (content or "").splitlines()
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def save_note(title, text):
    """Write a note to the Notes folder and index it into the graph.
    De-dupes: identical content isn't saved twice, and the same title overwrites
    (no timestamped duplicates). Returns {say, show, ok, path}."""
    text = (text or "").strip()
    given = (title or "").strip()
    title = given or _title_from(text) or \
        datetime.datetime.now().strftime("Note %Y-%m-%d %H:%M")
    if not given:
        # The title came from the first line, so don't store that line twice —
        # the Notes panel shows the title above the body.
        lines = text.splitlines()
        if lines and lines[0].strip() == title:
            text = "\n".join(lines[1:]).strip()
    os.makedirs(config.NOTES_DIR, exist_ok=True)
    # Same title → SAME file (overwrite), so re-saving a note never creates
    # timestamped duplicates. Different titles are kept as separate notes.
    base = _slug(title)
    path = os.path.join(config.NOTES_DIR, base + ".md")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{text}\n")
    except Exception as e:
        return {"say": f"I couldn't save the note, Sir: {e}", "show": str(e), "ok": False}
    # Inject into the knowledge graph under the Notes node.
    try:
        graph.add_note_entry(title, text, doc=title)
        if text and llm.available():
            graph.extract_into(text, llm.complete, doc=title)
    except Exception:
        pass
    return {"say": f"Saved your note: {title}.",
            "show": "📝 Saved note → " + path, "ok": True, "path": path}


def title_of(content, fallback=""):
    """The real note title from the leading '# …' line (keeps Vietnamese diacritics);
    falls back to the slug filename only if there's no header."""
    for line in (content or "").splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return fallback


def list_notes():
    """[(title, text)] for every note file — title from the '# ' header, not the slug."""
    nd = config.NOTES_DIR
    if not os.path.isdir(nd):
        return []
    out = []
    for f in sorted(os.listdir(nd)):
        if os.path.splitext(f)[1].lower() not in (".md", ".txt"):
            continue
        content = _read(os.path.join(nd, f))
        out.append((title_of(content, os.path.splitext(f)[0]), content))
    return out


def _files():
    nd = config.NOTES_DIR
    if not os.path.isdir(nd):
        return []
    return [os.path.join(nd, f) for f in os.listdir(nd)
            if os.path.splitext(f)[1].lower() in (".md", ".txt")]


def snapshot(limit=200):
    """Every note, newest first — what the Notes panel lists."""
    rows = []
    for path in _files():
        content = _read(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        body = _body(content)
        rows.append({
            "id": os.path.basename(path),
            "title": title_of(content, os.path.splitext(os.path.basename(path))[0]),
            "body": body,
            "preview": " ".join(body.split())[:120],
            "when": datetime.datetime.fromtimestamp(mtime).strftime("%d %b · %H:%M") if mtime else "",
            "stamp": mtime,
            "path": path,
        })
    rows.sort(key=lambda r: r["stamp"], reverse=True)
    return {"notes": rows[:limit], "counts": {"total": len(rows)},
            "folder": config.NOTES_DIR}


def delete_note(note_id):
    """Delete one note file (by its file name)."""
    name = os.path.basename(note_id or "")
    if not name:
        return False
    path = os.path.join(config.NOTES_DIR, name)
    # Never follow a path out of the notes folder.
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(config.NOTES_DIR):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def rename_note(note_id, new_title):
    """Retitle a note in place, keeping its body."""
    name = os.path.basename(note_id or "")
    path = os.path.join(config.NOTES_DIR, name)
    if not name or not os.path.exists(path) or not (new_title or "").strip():
        return False
    body = _body(_read(path))
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {new_title.strip()}\n\n{body}\n")
        return True
    except OSError:
        return False


_REVIEW_PROMPT = (
    "You are reviewing the user's personal notes. In 2-5 short bullet points: "
    "(1) suggest what to do / look at / fix next, and (2) flag any CONFLICTS or "
    "connections between notes (e.g. two commitments on the same day, a dependency, "
    "a deadline clash). Be concise and specific. If nothing notable, say so briefly.\n\n"
    "NOTES:\n"
)


def review():
    """LLM review of all notes → {say, show}, or a short string if none."""
    notes = list_notes()
    if not notes:
        return None
    if not llm.available():
        return {"say": f"You have {len(notes)} notes, Sir.",
                "show": "🗒 Notes:\n" + "\n".join("• " + t for t, _ in notes)}
    body = "\n---\n".join(f"[{t}]\n{x}" for t, x in notes)
    out = llm.complete(_REVIEW_PROMPT + body[:6000], timeout=45) or ""
    out = out.strip()
    if not out:
        return {"say": f"You have {len(notes)} notes, Sir.",
                "show": "🗒 Notes:\n" + "\n".join("• " + t for t, _ in notes)}
    return {"say": "I went through your notes, Sir. " + out.splitlines()[0],
            "show": "🗒 Notes review\n" + out}
