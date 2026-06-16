"""Local command execution ("skills").

Before a request goes to the language model, PATROAM checks here for a concrete
action it can perform on the system itself — e.g. "open Spotify" should launch
the app, not just chat about it. `try_handle` returns a spoken response string
if it handled the request, or None to let the model answer normally.

This is the seed of PATROAM's command-execution pillar; add more skills here.
"""

import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

OPEN_VERBS = r"open|launch|start|run|fire up|bring up|pull up|boot up"
CLOSE_VERBS = r"close|quit|exit|kill|terminate"

# "play some music", "put on my liked songs", "play music on spotify", …
MUSIC_RE = re.compile(
    r"\b(play|put on|start|throw on)\b.*\b(music|song|songs|tunes|playlist|spotify|liked)\b",
    re.I)
LIKED_SONGS_URI = "spotify:collection:tracks"

# "Stop talking" — halt speech immediately and keep listening (whole utterance).
STOP_SPEAKING_RE = re.compile(
    r"^\s*(stop|stop talking|stop it|be quiet|quiet|shush|hush|shut up|enough|"
    r"that'?s enough|cancel|wait|hold on)\s*[.!]?\s*$", re.I)

# Memory voice commands (reliable, no model needed).
REMEMBER_RE = re.compile(r"^\s*(?:please\s+)?(?:remember|note|keep in mind)\b(?:\s+that)?\s+(.+)", re.I)
FORGET_RE = re.compile(r"^\s*forget\b(?:\s+that|\s+about)?\s+(.+)", re.I)
RECALL_RE = re.compile(r"\bwhat do you (?:know|remember)(?:\s+about me)?\b", re.I)

# Ad-stats command — needs both an "ad" word and a stats/question word.
ADS_RE = re.compile(r"\b(ads?|advert\w*|campaigns?)\b", re.I)
ADS_CTX_RE = re.compile(r"\b(doing|stats|statistics|performance|results?|spend|spending|"
                        r"ctr|impressions?|clicks?|reach|how|numbers?|metrics?|report)\b", re.I)

# News command ("what's up", "what's new", "news", "headlines", …).
NEWS_RE = re.compile(r"\b(what'?s up|what'?s new|what is up|news|headlines|catch me up)\b", re.I)

# Switch reply language ("reply in Vietnamese", "trả lời bằng tiếng Việt", …).
LANG_RE = re.compile(
    r"\b(reply|respond|speak|talk|answer|switch|change|use)\b.{0,24}"
    r"(vietnamese|tieng viet|tiếng việt|vietnam|english|tieng anh|tiếng anh)\b"
    r"|\b(trả lời|nói|chuyển|dùng)\b.{0,24}(tiếng việt|tieng viet|tiếng anh|tieng anh)", re.I)
_VIET_RE = re.compile(r"viet|việt", re.I)

# Re-index the knowledge base ("index my docs", "reload knowledge", …).
INGEST_RE = re.compile(r"\b(index|re-?index|ingest|reload|refresh|rebuild|update)\b"
                       r".{0,20}\b(docs?|documents?|knowledge|files?|notes?|memory base)\b", re.I)

# Knowledge-graph overview ("knowledge graph", "what's connected", …).
GRAPH_RE = re.compile(r"\b(knowledge graph|what'?s connected|show.{0,15}(connections|graph))\b", re.I)

# Explicit "connect/link A to B" → a RELATED_TO edge.
CONNECT_RE = re.compile(r"^\s*(?:connect|link)\s+(.+?)\s+(?:to|with|and)\s+(.+)$", re.I)

# "merge A into B" / "merge A and B" / "A is the same as B" — fold duplicate nodes.
MERGE_RE = re.compile(
    r"^\s*merge\s+(.+?)\s+(?:into|with|and|to)\s+(.+)$", re.I)
SAME_RE = re.compile(
    r"^\s*(.+?)\s+(?:is|are)\s+the same(?:\s+(?:as|node|thing|entity|person))?\s+(?:as\s+)?(.+)$", re.I)
# "merge duplicates", "clean up the graph", "deduplicate the graph" — auto-merge.
DEDUPE_RE = re.compile(
    r"\b(merge|combine|clean ?up|de-?duplicate|de-?dupe|fix)\b.{0,24}"
    r"\b(duplicates?|dupes?|nodes?|graph|entities)\b", re.I)
# "clear/reset/wipe the knowledge graph" — start the graph fresh.
CLEAR_GRAPH_RE = re.compile(
    r"\b(clear|reset|wipe|empty|erase|delete)\b.{0,24}\b(knowledge graph|graph)\b", re.I)
# Modifiers that mean "remove my personal memory too".
CLEAR_ALL_RE = re.compile(
    r"\b(everything|including (my )?memory|memory too|personal|all of it|completely)\b", re.I)

# Relationship verbs → canonical relation. Order matters (most specific first);
# the generic "is/are" attribute pattern is LAST so it never shadows the others.
_REL_PATTERNS = [
    (r"depends? on|relies on|requires|needs", "DEPENDS_ON"),
    (r"is part of|are part of|belongs? to|is in|lives in", "PART_OF"),
    (r"is owned by|owned by", "OWNED_BY"),
    (r"owns|has", "OWNS"),
    (r"implements|provides|exposes", "IMPLEMENTS"),
    (r"is blocked by|blocked by|waiting on", "BLOCKED_BY"),
    (r"uses|use|is built (?:on|with)|built (?:on|with)|runs on|written in|powered by", "USES"),
    (r"works? on|works? for|created|made|built|leads|manages|maintains", "WORKS_ON"),
    (r"is related to|relates? to|connected to|links? to", "RELATED_TO"),
    (r"likes?|loves?|enjoys?|prefers?", "LIKES"),
    (r"hates?|dislikes?", "DISLIKES"),
    (r"knows?", "KNOWS"),
    (r"is|are|am|was|were|seems?|looks?", "IS"),
]
_REL_RE = [(re.compile(rf"^(.+?)\s+(?:{p})\s+(.+)$", re.I), rel) for p, rel in _REL_PATTERNS]

# Filler words trimmed from an extracted object so "very handsome" → "handsome".
_OBJ_FILLER = re.compile(
    r"^(?:a|an|the|very|really|so|quite|just|also|pretty|kind of|sort of|"
    r"my|our|your|his|her|their|its)\s+", re.I)

# Questions / non-statements we should NOT silently learn from.
_NOT_STATEMENT = re.compile(
    r"^\s*(who|what|when|where|why|how|which|is|are|was|were|do|does|did|can|"
    r"could|would|will|should|tell me|show|find|open|close|play|search)\b", re.I)


def _clean_obj(o):
    o = o.strip().rstrip(".!?")
    while True:
        n = _OBJ_FILLER.sub("", o)
        if n == o:
            break
        o = n
    return o.strip()


def extract_triple(text):
    """Parse 'A <verb> B' into (subject, RELATION, object), or None."""
    t = (text or "").strip().rstrip(".!?")
    # Expand a few contractions so "I'm a doctor" / "they're cool" parse.
    t = re.sub(r"\bi'm\b", "i am", t, flags=re.I)
    t = re.sub(r"(\w)'re\b", r"\1 are", t, flags=re.I)
    for rx, rel in _REL_RE:
        m = rx.match(t)
        if m:
            s, o = m.group(1).strip(), _clean_obj(m.group(2))
            if s and o and len(s) < 80 and len(o) < 80:
                return s, rel, o
    return None


def learn_triple(text):
    """Like extract_triple, but only for declarative statements (not questions /
    commands) — used to passively learn graph facts from normal conversation."""
    t = (text or "").strip()
    if not t or t.endswith("?") or _NOT_STATEMENT.match(t):
        return None
    return extract_triple(t)

# Words to strip from a spoken app name ("open the spotify app please" -> spotify)
_FILLER = {
    "the", "a", "my", "app", "apps", "application", "please", "for", "me",
    "up", "program", "window", "again", "now",
}

# Apps best launched by their registered URI scheme (often Store apps).
URI_MAP = {
    "spotify": "spotify:",
    "whatsapp": "whatsapp:",
    "settings": "ms-settings:",
    "store": "ms-windows-store:",
    "microsoft store": "ms-windows-store:",
    "maps": "bingmaps:",
    "calendar": "outlookcal:",
    "mail": "outlookmail:",
}

# If no app is found, these names open as websites instead.
SITES = {
    "youtube": "https://youtube.com",
    "google": "https://google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "reddit": "https://reddit.com",
    "chatgpt": "https://chat.openai.com",
}


# ── Parsing ────────────────────────────────────────────────────────────────────
def _clean(raw):
    raw = raw.lower().strip().rstrip(".!?")
    raw = re.sub(r"[^a-z0-9.+&' -]", " ", raw)
    toks = [t for t in raw.split() if t not in _FILLER]
    return " ".join(toks).strip()


def parse_command(text):
    """Return (action, app) where action is 'open'/'close', or (None, None)."""
    m = re.search(rf"\b({OPEN_VERBS})\b\s+(.+)", text, re.I)
    if m:
        app = _clean(m.group(2))
        if app:
            return "open", app
    m = re.search(rf"\b({CLOSE_VERBS})\b\s+(.+)", text, re.I)
    if m:
        app = _clean(m.group(2))
        if app:
            return "close", app
    return None, None


# ── App resolution (Windows-focused, with mac/linux fallbacks) ──────────────────
def _start_menu_dirs():
    dirs = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(os.path.join(base, "Microsoft", "Windows",
                                     "Start Menu", "Programs"))
    return [d for d in dirs if os.path.isdir(d)]


def _find_shortcuts(name):
    """Search the Start Menu for a .lnk matching `name` (exact, then partial)."""
    name_l = name.lower()
    exact = partial = None
    for d in _start_menu_dirs():
        for root, _, files in os.walk(d):
            for f in files:
                if not f.lower().endswith(".lnk"):
                    continue
                stem = f[:-4].lower()
                if stem == name_l and exact is None:
                    exact = os.path.join(root, f)
                elif name_l in stem and partial is None:
                    partial = os.path.join(root, f)
    return exact, partial


def _app_paths_exe(name):
    """Look up an executable via the Windows 'App Paths' registry (what the
    Run dialog / `start <name>` uses)."""
    try:
        import winreg
    except ImportError:
        return None
    key = name if name.lower().endswith(".exe") else name + ".exe"
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + key
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub) as k:
                val, _ = winreg.QueryValueEx(k, None)
            path = (val or "").strip('"')
            if path and os.path.exists(path):
                return path
        except OSError:
            continue
    return None


def resolve(name):
    """Decide how to open `name`. Returns (kind, target) or None."""
    if IS_WIN:
        exact, partial = _find_shortcuts(name)
        if exact:
            return "shortcut", exact
        if name in URI_MAP:
            return "uri", URI_MAP[name]
        exe = _app_paths_exe(name)
        if exe:
            return "exe", exe
        which = shutil.which(name) or shutil.which(name + ".exe")
        if which:
            return "exe", which
        if partial:
            return "shortcut", partial
        if name in SITES:
            return "site", SITES[name]
        return None
    if IS_MAC:
        return "mac", name
    which = shutil.which(name)
    if which:
        return "exe", which
    if name in SITES:
        return "site", SITES[name]
    return "xdg", name


def _launch(kind, target):
    if kind in ("shortcut", "uri"):
        os.startfile(target)  # noqa: S606 (Windows)
        return True
    if kind == "exe":
        subprocess.Popen([target])
        return True
    if kind == "site":
        webbrowser.open(target)
        return True
    if kind == "mac":
        return subprocess.run(["open", "-a", target]).returncode == 0
    if kind == "xdg":
        subprocess.Popen(["xdg-open", target])
        return True
    return False


def open_app(name):
    target = resolve(name)
    if not target:
        return False
    try:
        return _launch(*target)
    except Exception:
        return False


def close_app(name):
    img = name if name.lower().endswith(".exe") else name + ".exe"
    try:
        if IS_WIN:
            r = subprocess.run(["taskkill", "/f", "/im", img],
                               capture_output=True)
            return r.returncode == 0
        return subprocess.run(["pkill", "-i", "-f", name]).returncode == 0
    except Exception:
        return False


# ── Entry point ────────────────────────────────────────────────────────────────
def _media_play():
    """Send the OS 'play' command (Spotify resumes / starts the loaded context)."""
    try:
        if IS_WIN:
            import ctypes
            VK_PLAY_PAUSE = 0xB3
            ctypes.windll.user32.keybd_event(VK_PLAY_PAUSE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_PLAY_PAUSE, 0, 2, 0)  # key up
        elif IS_MAC:
            subprocess.run(["osascript", "-e", 'tell application "Spotify" to play'])
        else:
            subprocess.run(["playerctl", "play"])
    except Exception:
        pass


def play_music():
    """Open Spotify on the user's Liked Songs and start playback.

    No Spotify login/API needed: we deep-link to the Liked Songs view, then send
    the media Play key once Spotify has had a moment to come up.
    """
    def worker():
        try:
            if IS_WIN:
                os.startfile(LIKED_SONGS_URI)
            elif IS_MAC:
                subprocess.run(["open", LIKED_SONGS_URI])
            else:
                subprocess.Popen(["xdg-open", LIKED_SONGS_URI])
        except Exception:
            open_app("spotify")  # fall back to just opening the app
        time.sleep(3.0)          # let Spotify launch / navigate
        _media_play()

    threading.Thread(target=worker, daemon=True).start()
    return True


def _addr():
    """An honorific now and then — most of the time, nothing."""
    return random.choice([", Master", ", Sir"]) if random.random() < 0.3 else ""


def is_stop_speaking(text):
    """True if the whole utterance is a 'stop' command (halt speech/generation)."""
    return bool(STOP_SPEAKING_RE.match(text or ""))


def split_reply(reply):
    """Normalise a skill reply into (spoken_text, shown_text).

    A skill may return a plain string (spoken == shown) or a dict
    {"say": ..., "show": ...} when the chat should show MORE than is spoken
    (e.g. news: speak titles, show clickable links)."""
    if isinstance(reply, dict):
        say = reply.get("say", "") or reply.get("show", "")
        show = reply.get("show", "") or say
        return say, show
    return reply, reply


# Questions that ask PATROAM what it knows → open the graph fullscreen.
INFO_QUERY_RE = re.compile(
    r"\b(what do you know about|tell me about|what (?:is|are|was|were)|what'?s|"
    r"who (?:is|are|was)|who'?s|explain|describe|info(?:rmation)? (?:on|about)|"
    r"do you know (?:about|anything about)|what can you tell me about|"
    r"show me .*\bgraph|knowledge graph)\b", re.I)


def is_info_query(text):
    """True if the user is asking PATROAM what it knows (a good moment to open
    the knowledge graph fullscreen and focus the relevant node)."""
    return bool(INFO_QUERY_RE.search(text or ""))


# Asking PATROAM to look at the screen → capture a screenshot for the vision model.
SCREEN_RE = re.compile(
    r"\b(look at|see|read|check|describe|analy[sz]e|capture|what'?s on|whats on|"
    r"what do you see|can you see|take a look)\b.{0,18}\b(screen|display|monitor|this|here)\b"
    r"|\bscreenshot\b|\bmy screen\b", re.I)


def wants_screen(text):
    """True if the user wants PATROAM to look at their screen."""
    return bool(SCREEN_RE.search(text or ""))


# Asking PATROAM to produce a file → save the code block(s) from the reply.
CREATE_FILE_RE = re.compile(
    r"\b(write|create|make|generate|build|draft|code|save)\b[^.?!]{0,50}?"
    r"\b(script|program|file|module|app|class|function|snippet|code)\b"
    r"|\bsave\b[^.?!]{0,25}\b(it|this|that|code|to\s+(?:a\s+)?file)\b"
    r"|\b[\w\-]{1,40}\.(?:py|cpp|cc|cxx|c|hpp|h|js|ts|java|go|rs|swift|kt|html|css|json|ya?ml|sh|sql|txt)\b",
    re.I)


def wants_file(text):
    """True if the user is asking PATROAM to generate/save a file."""
    return bool(CREATE_FILE_RE.search(text or ""))


# Coding questions → PATROAM switches to its coding model.
CODE_QUERY_RE = re.compile(
    r"\b(cod(?:e|ing)|programming|script|function|method|algorithm|debug|refactor|"
    r"compile|syntax|stack ?trace|traceback|regex|unit ?test|leetcode|recursion)\b"
    r"|\b(python|c\+\+|cpp|c#|csharp|javascript|typescript|java|kotlin|swift|rust|"
    r"golang|sql|html|css|bash|powershell|node\.?js)\b"
    r"|\b(write|create|make|generate|fix|implement|optimi[sz]e|review|explain|build)\b"
    r"[^.?!]{0,30}\b(code|program|script|function|app|class|api|bug|error)\b", re.I)


def is_coding_query(text):
    """True if the user is asking a coding/programming question."""
    return bool(CODE_QUERY_RE.search(text or "") or wants_file(text))


# Detect a two-way choice in a reply ("do you want A or B?") → render as buttons,
# even if the model asked in prose instead of emitting an ask action.
_CHOICE_RE = re.compile(
    r"\b(?:want|prefer|like|use|choose|pick|go with|which)\b[^?\n]*?\b"
    r"([A-Za-z][\w.+#/-]{0,28})\s*,?\s+or\s+,?\s*([A-Za-z][\w.+#/-]{0,28})[^?\n]*\?",
    re.I)


_PTYPES = [
    (re.compile(r"\b(flutter|dart)\b", re.I), "flutter"),
    (re.compile(r"\b(web ?app|web application|react|vue|svelte|spa)\b", re.I), "webapp"),
    (re.compile(r"\b(website|web ?site|landing page|static site|web page)\b", re.I), "website"),
    (re.compile(r"\b(desktop|tkinter|pyqt|electron)\b", re.I), "desktop"),
    (re.compile(r"\bpython\b", re.I), "python"),
]
# A go-ahead to actually build it.
GO_RE = re.compile(
    r"\b(create|build|scaffold|make|generate|start|go ahead|do it|let'?s go|"
    r"here we go|where'?s? (?:the|my) project|set ?up)\b", re.I)


def project_type(text):
    """Detect a project type mentioned in the text (flutter/python/website/…)."""
    for rx, t in _PTYPES:
        if rx.search(text or ""):
            return t
    return None


def extract_choices(reply):
    """Return [optionA, optionB] if the reply offers a clear two-way choice, else None."""
    m = _CHOICE_RE.search(reply or "")
    if not m:
        return None
    a, b = m.group(1).strip(" .,"), m.group(2).strip(" .,")
    if a and b and a.lower() != b.lower():
        return [a, b]
    return None


def try_handle(text):
    """Deterministic handling: data fetch first, then a system command.
    Returns a reply string/dict, "" if handled silently, or None to fall through
    to the model. (Kept for the daemon / web / Tk frontends.)"""
    r = data_handle(text)
    return r if r is not None else command_handle(text)


def data_handle(text):
    """Things that FETCH live data — run before the model (it can't be trusted to
    actually fetch, and we don't want it talking over the result). News, ads."""
    # Ad stats (direct Meta API — reliable on any model).
    if ADS_RE.search(text) and ADS_CTX_RE.search(text):
        from . import meta_ads
        return meta_ads.summary(text)
    # Latest news from your trusted feeds (speaks titles, shows clickable links).
    if NEWS_RE.search(text):
        from . import news
        return news.latest(text)
    return None


def command_handle(text):
    """System commands & graph/memory edits. In the LLM-first flow these run as a
    FALLBACK when the model understood but didn't emit the matching tool call —
    so on a weak model the command still happens.

    Returns a spoken reply string, "" if handled silently, or None if not a
    command.
    """
    # "Stop" — handled silently; the caller has already interrupted any speech.
    if STOP_SPEAKING_RE.match(text):
        return ""

    # Switch reply language on the fly (affects the model's text and the voice).
    if LANG_RE.search(text):
        from . import config
        if _VIET_RE.search(text):
            config.set_language("Vietnamese")
            return "Vâng, từ bây giờ tôi sẽ trả lời bằng tiếng Việt."
        config.set_language("English")
        return "Of course — I'll reply in English from now on."

    # "connect A to B" → a knowledge-graph edge.
    m = CONNECT_RE.match(text)
    if m:
        from . import graph
        s, o = m.group(1).strip().rstrip(".!?"), m.group(2).strip().rstrip(".!?")
        graph.add(s, "RELATED_TO", o)
        return f"Connected {s} and {o} in your knowledge graph{_addr()}."

    # Memory commands — everything is remembered in the knowledge graph.
    m = REMEMBER_RE.match(text)
    if m:
        from . import graph
        body = m.group(1).strip().rstrip(".!?")
        # A relationship/attribute becomes a triple; anything else, a free-text note.
        tr = extract_triple(body)
        if tr:
            graph.add(*tr)
            s, rel, o = graph._norm(tr[0]), tr[1].replace("_", " ").lower(), graph._norm(tr[2])
            return f"Noted{_addr()} — added to your knowledge graph: {s} {rel} {o}."
        graph.add_note(body)
        return f"Noted{_addr()}. I'll remember that."
    m = FORGET_RE.match(text)
    if m:
        from . import graph
        body = m.group(1).strip().rstrip(".!?")
        # "forget that Trump is handsome" → drop that specific graph connection.
        tr = extract_triple(body)
        if tr and graph.remove_triple(tr[0], tr[2], tr[1]):
            s, rel, o = graph._norm(tr[0]), tr[1].replace("_", " ").lower(), graph._norm(tr[2])
            return f"Forgotten{_addr()} — removed from your knowledge graph: {s} {rel} {o}."
        # Otherwise remove matching facts/notes/entities from the graph. Try the
        # whole phrase first, then fall back to its most significant word.
        removed = graph.forget(body)
        if not removed:
            for w in sorted(re.findall(r"[a-z0-9]{4,}", body.lower()), key=len, reverse=True):
                removed = graph.forget(w)
                if removed:
                    break
        return f"Done{_addr()}." if removed else "I didn't have anything matching that."
    if RECALL_RE.search(text):
        from . import graph
        return graph.user_summary()

    # Clear/reset the knowledge graph ("clear the knowledge graph"). Keeps your
    # personal memory unless you say "everything / including memory".
    if CLEAR_GRAPH_RE.search(text):
        from . import graph
        keep = not CLEAR_ALL_RE.search(text)
        removed = graph.clear(keep_user=keep)
        if not removed:
            return f"The knowledge graph is already empty{_addr()}."
        scope = "your personal memory is kept" if keep else "personal memory included"
        return (f"Cleared {removed} fact{'s' if removed != 1 else ''} from your "
                f"knowledge graph{_addr()} — {scope}.")

    # Auto-merge all duplicate nodes ("merge duplicates", "clean up the graph").
    if DEDUPE_RE.search(text):
        from . import graph
        merges = graph.merge_duplicates()
        if not merges:
            return f"No duplicate nodes to merge{_addr()} — your graph looks clean."
        n = sum(len(m[1]) for m in merges)
        examples = "; ".join(f"{kept}" for kept, _ in merges[:4])
        return (f"Merged {n} duplicate node{'s' if n != 1 else ''} into "
                f"{len(merges)} entit{'ies' if len(merges) != 1 else 'y'}{_addr()}: {examples}.")

    # Merge two specific nodes ("merge A into B", "A is the same as B").
    m = MERGE_RE.match(text) or SAME_RE.match(text)
    if m:
        from . import graph
        a, b = m.group(1).strip().rstrip(".!?"), m.group(2).strip().rstrip(".!?")
        moved = graph.merge(a, b)
        if moved:
            return f"Merged {a} into {b}{_addr()} — moved {moved} connection{'s' if moved != 1 else ''}."
        return f"I couldn't find connections for {a} to merge{_addr()}."

    # Knowledge-graph overview.
    if GRAPH_RE.search(text):
        from . import graph
        return graph.summary()

    # Re-index the user's documents for RAG.
    if INGEST_RE.search(text):
        from . import rag
        n, m, tr = rag.ingest()
        if not n:
            return ("I didn't find any documents in your knowledge folder "
                    "(~/.patroam/knowledge). Drop some in and ask again.")
        facts = f", and built {tr} fact{'s' if tr != 1 else ''} into your knowledge graph" if tr else ""
        return (f"Indexed {n} passage{'s' if n != 1 else ''} from "
                f"{m} document{'s' if m != 1 else ''}{facts}{_addr()}.")

    if MUSIC_RE.search(text):
        play_music()
        return f"Putting on your Liked Songs{_addr()}."

    action, app = parse_command(text)
    if not action:
        return None
    title = app.title()
    a = _addr()
    if action == "open":
        if open_app(app):
            return f"Opening {title}{a}."
        return f"I couldn't find {title} on this system{a}."
    if close_app(app):
        return f"Closing {title}{a}."
    return f"I couldn't close {title}{a} — perhaps it isn't running."
