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

from .memory import get_memory

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

# Re-index the knowledge base ("index my docs", "reload knowledge", …).
INGEST_RE = re.compile(r"\b(index|re-?index|ingest|reload|refresh|rebuild|update)\b"
                       r".{0,20}\b(docs?|documents?|knowledge|files?|notes?|memory base)\b", re.I)

# Knowledge-graph overview ("knowledge graph", "what's connected", …).
GRAPH_RE = re.compile(r"\b(knowledge graph|what'?s connected|show.{0,15}(connections|graph))\b", re.I)

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


def try_handle(text):
    """Handle `text` as a system command.

    Returns: a spoken reply string, "" if handled but nothing should be spoken
    (e.g. "stop"), or None if not a command (fall through to the model).
    """
    # "Stop" — handled silently; the caller has already interrupted any speech.
    if STOP_SPEAKING_RE.match(text):
        return ""

    # Memory commands first.
    m = REMEMBER_RE.match(text)
    if m:
        get_memory().add_fact(m.group(1).strip().rstrip(".!?"))
        return f"Noted{_addr()}. I'll remember that."
    m = FORGET_RE.match(text)
    if m:
        n = get_memory().forget(m.group(1).strip())
        return f"Done{_addr()}." if n else "I didn't have anything matching that."
    if RECALL_RE.search(text):
        return get_memory().summary()

    # Knowledge-graph overview.
    if GRAPH_RE.search(text):
        from . import graph
        return graph.summary()

    # Re-index the user's documents for RAG.
    if INGEST_RE.search(text):
        from . import rag
        n, m = rag.ingest()
        if not n:
            return ("I didn't find any documents in your knowledge folder "
                    "(~/.patroam/knowledge). Drop some in and ask again.")
        return (f"Indexed {n} passage{'s' if n != 1 else ''} from "
                f"{m} document{'s' if m != 1 else ''}{_addr()}.")

    # Ad stats (direct Meta API — reliable on any model).
    if ADS_RE.search(text) and ADS_CTX_RE.search(text):
        from . import meta_ads
        return meta_ads.summary(text)

    # Latest news (NewsAPI).
    if NEWS_RE.search(text):
        from . import news
        return news.headlines(text)

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
