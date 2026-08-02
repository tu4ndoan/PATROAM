"""Local command execution ("skills").

Before a request goes to the language model, PATROAM checks here for a concrete
action it can perform on the system itself — e.g. "open Spotify" should launch
the app, not just chat about it. `try_handle` returns a spoken response string
if it handled the request, or None to let the model answer normally.

This is the seed of PATROAM's command-execution pillar; add more skills here.
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

from . import config

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

# Post/publish a video to social media (the content pipeline).
POST_CONTENT_RE = re.compile(
    r"\b(post|publish|upload|share)\b.{0,30}\b(reel|reels|short|shorts|video|clip|content|"
    r"tiktok|instagram|threads)\b"
    r"|\b(đăng|xu[aấ]t b[aả]n|t[aả]i l[eê]n)\b.{0,30}\b(reel|video|clip|b[aà]i)\b", re.I)


_AFFIRM_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|please|please do|go ahead|do it|"
    r"yes please|sounds good|absolutely|definitely|y)\b", re.I)


def is_affirmative(text):
    """A simple yes to a yes/no offer (e.g. the briefing's Focus-playlist prompt)."""
    return bool(_AFFIRM_RE.match(text or ""))


def graph_view_mode(text):
    """Detect a 'change the knowledge-graph view' command → 'flat' | 'sphere' |
    'toggle', or None. e.g. 'make the graph flat', 'change the graph visual'."""
    t = (text or "").lower()
    if not re.search(r"\bgraph\b|knowledge graph|\bkg\b", t):
        return None
    if re.search(r"\b(flat|2d|plane|planar|static|stop spin\w*|don'?t spin|no spin)\b", t):
        return "flat"
    if re.search(r"\b(3d|sphere|spherical|spin\w*|orb|globe|rotat\w*)\b", t):
        return "sphere"
    if re.search(r"\b(change|switch|toggle|flip|different)\b", t):
        return "toggle"
    return None

# Fab sales → open the Fab analytics page in Brave.
FAB_RE = re.compile(r"\bfab\s+(?:sales|report|analytics|portal|store|earnings|revenue)\b"
                    r"|\bfab sales\b|fab\.com|\bmy fab\b", re.I)
# Gold price (USD + VND), incl. Vietnamese "giá vàng".
GOLD_RE = re.compile(r"\bgold\b[^?\n]{0,15}\b(price|rate|cost|spot|worth|value|usd|vnd)\b"
                     r"|\b(price|rate|cost)\b[^?\n]{0,15}\bgold\b|\bgi[aá]\s*v[aà]ng\b", re.I)

# Vietnamese stocks (SSI): "stock/share price", "ticker", "VN-Index", "cổ phiếu".
STOCK_RE = re.compile(
    r"\b(stocks?|shares?|ticker|equit\w*|trading\s+at|share\s+price|vn[\- ]?index|"
    r"vnindex|hnx[\- ]?index|upcom|vn30|c[oổ]\s*phi[eế]u|ch[uứ]ng kho[aá]n|"
    r"gi[aá]\s+c[oổ])\b", re.I)
_VNINDEX_RE = re.compile(r"vn[\- ]?index|vnindex", re.I)
_HNXINDEX_RE = re.compile(r"hnx[\- ]?index", re.I)
_VN30_RE = re.compile(r"\bvn[\- ]?30\b", re.I)
# Words that look like 3-letter tickers but aren't.
_NOT_TICKER = {"THE", "AND", "FAB", "USD", "VND", "WHO", "WHY", "HOW", "ARE", "YOU",
               "VND", "API", "SSI", "NOW", "GET", "FOR", "OUT", "NEW"}


def stock_symbol(text):
    """Pull a stock ticker (or index code) out of a request, or None."""
    if _VNINDEX_RE.search(text):
        return "VNINDEX"
    if _HNXINDEX_RE.search(text):
        return "HNXINDEX"
    if _VN30_RE.search(text):
        return "VN30"
    # "stock/share/ticker/cổ phiếu/mã VNM"
    m = re.search(r"(?:stock|share|ticker|c[oổ]\s*phi[eế]u|m[aã]|of|for)\s+"
                  r"(?:price\s+|of\s+|for\s+)?([A-Za-z]{3})\b", text, re.I)
    if m and m.group(1).upper() not in _NOT_TICKER:
        return m.group(1).upper()
    # A standalone 3-letter UPPERCASE token (most HOSE/HNX tickers).
    m = re.search(r"\b([A-Z]{3})\b", text)
    if m and m.group(1).upper() not in _NOT_TICKER:
        return m.group(1).upper()
    return None


def open_url_in_brave(url):
    """Open `url` in Brave specifically; fall back to the default browser."""
    paths = [
        os.path.join(os.environ.get("ProgramFiles", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ]
    exe = next((p for p in paths if p and os.path.exists(p)), None) or shutil.which("brave") or shutil.which("brave-browser")
    try:
        if exe:
            subprocess.Popen([exe, url])
        else:
            webbrowser.open(url)
        return True
    except Exception:
        try:
            webbrowser.open(url)
            return True
        except Exception:
            return False

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


# ── live-data skills (each returns the exact {say, show} to speak/show) ────────────
def _need_ssi():
    return ("Add your SSI FastConnect credentials first, Sir — put "
            "SSI_CONSUMER_ID and SSI_CONSUMER_SECRET in secrets.json.")


def _fab():
    """Fab sales → fresh CSV via Brave (past Cloudflare), then read it."""
    from . import fab
    rep = fab.download_and_read()
    if rep:
        return rep
    open_url_in_brave(config.FAB_SALES_URL)
    return ("I opened your Fab sales in Brave, Sir — once the CSV downloads, "
            "ask me again and I'll read you the numbers.")


def _stock(text, symbol=None):
    from . import stocks
    if not stocks.available():
        return _need_ssi()
    sym = (symbol or "").upper().strip() or stock_symbol(text)
    if sym in ("VNINDEX", "HNXINDEX", "VN30"):
        return stocks.index(sym)
    if sym:
        return stocks.quote(sym)
    return "Which stock would you like, Sir? Tell me the ticker — for example, VNM."


def _index(name=None):
    from . import stocks
    if not stocks.available():
        return _need_ssi()
    return stocks.index((name or "VNINDEX").upper().strip())


def _briefing(text):
    """"What's up" → gold + VN-Index + Fab sales + top headlines."""
    from . import gold, news, fab, stocks
    g = gold.price(text)
    idx = stocks.index("VNINDEX") if stocks.available() else None
    idx_say = idx["say"] if isinstance(idx, dict) else ""
    idx_show = idx["show"] if isinstance(idx, dict) else ""
    rep = fab.report()
    n = news.latest(text, 3)
    n_say, n_show = (n.get("say", ""), n.get("show", "")) if isinstance(n, dict) else (n, n)
    if rep:
        fab_say, fab_show = rep["say"], rep["show"]
    else:
        open_url_in_brave(config.FAB_SALES_URL)
        fab_say = "I've opened your Fab sales in Brave to download the latest report."
        fab_show = f"🔗 Fab sales: {config.FAB_SALES_URL}"
    say = f"{g} {idx_say} {fab_say} {n_say}".strip()
    show = "\n\n".join(x for x in [g, idx_show, fab_show, n_show] if x)
    return {"say": say, "show": show}


# ── LLM intent router (understand the request in any wording) ──────────────────────
_ROUTER_PROMPT = (
    "You are PATROAM's intent router. Decide which skill (if any) handles the "
    "user's message, and extract its parameters. Reply with ONLY a JSON object.\n\n"
    "Skills:\n"
    '- "stock": a Vietnamese stock/share price. Add {"symbol":"TICKER"} '
    "(3-letter HOSE/HNX ticker, uppercase).\n"
    '- "index": a market index value. Add {"name":"VNINDEX"|"HNXINDEX"|"VN30"}.\n'
    '- "gold": the current price of gold.\n'
    '- "fab": the user\'s Fab.com store sales/revenue.\n'
    '- "ads": the user\'s Meta/Facebook ad performance. Add {"query":"..."}.\n'
    '- "news": latest news/headlines. Add {"topic":"..."} (empty for general).\n'
    '- "briefing": a full daily briefing / the user is STARTING A WORK SESSION. Use '
    'for "time to work", "let\'s get back to work", "let\'s start", "begin", "catch '
    'me up", "brief me", "what\'s up", "what should I do" — anything signalling they '
    "are sitting down to work and want orienting.\n"
    '- "new_note": take/write/save a note. Add {"text":"..."} if they dictated the '
    "note content, otherwise omit it.\n"
    '- "project_status": asking about ALL projects\' progress in general, where they '
    "left off across everything, or whether they're on schedule.\n"
    '- "resume_project": open/continue/resume a SPECIFIC named project — "let\'s work '
    'on project iC", "where were we on hanabie", "open the X project". Add {"project":"NAME"}.\n'
    '- "note_suggestions": asking what their notes say, or for suggestions / '
    "conflicts from their notes.\n"
    '- "post_content": publish/post/share a video (reel, short, clip) to social '
    "media — TikTok, Instagram, YouTube, Threads, X. The user edited a video and "
    'wants it posted. Add {"brief":"..."} if they described what the video is about, '
    'and {"video":"..."} if they named a file/path. e.g. "post my new reel", "publish '
    'this video about the environment pack", "đăng reel mới lên hết các nền tảng".\n'
    '- "content_history": asking what they posted / their recent posts / post history.\n'
    '- "backup_graph": back up / save a copy of the knowledge graph.\n'
    '- "none": anything else — general chat, coding, BUILDING apps/projects, '
    "questions.\n\n"
    'Examples: "how is FPT doing today" -> {"skill":"stock","symbol":"FPT"}; '
    '"giá vàng" -> {"skill":"gold"}; "any war news" -> {"skill":"news","topic":"war"}; '
    '"how were my sales on fab" -> {"skill":"fab"}; '
    '"take a note: buy milk" -> {"skill":"new_note","text":"buy milk"}; '
    '"where did I leave off" -> {"skill":"project_status"}; '
    '"let\'s work on project iC, where were we" -> {"skill":"resume_project","project":"iC"}; '
    '"time to work" -> {"skill":"briefing"}; '
    '"let\'s get back to work" -> {"skill":"briefing"}; '
    '"back up my knowledge graph" -> {"skill":"backup_graph"}; '
    '"post my new reel about the animation pack" -> {"skill":"post_content","brief":"the animation pack"}; '
    '"đăng video này lên hết đi" -> {"skill":"post_content"}; '
    '"what have I posted lately" -> {"skill":"content_history"}; '
    '"what should I work on from my notes" -> {"skill":"note_suggestions"}; '
    '"build me a flutter app" -> {"skill":"none"}; '
    '"what stock API should I use" -> {"skill":"none"}.\n\n'
    "User message: "
)


def _route_intent(text):
    """Ask the active model to classify the request. Returns an intent dict, or
    None if no model is available (→ caller uses the regex backstop)."""
    from . import llm
    if not llm.available():
        return None
    raw = llm.complete(_ROUTER_PROMPT + json.dumps(text), timeout=8)
    if not raw:
        return None
    try:
        i, j = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[i:j + 1]) if i >= 0 and j > i else {}
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("skill") else None


def _dispatch(intent, text):
    skill = intent.get("skill")
    if skill == "stock":
        return _stock(text, intent.get("symbol"))
    if skill == "index":
        return _index(intent.get("name"))
    if skill == "gold":
        from . import gold
        return gold.price(text)
    if skill == "fab":
        return _fab()
    if skill == "ads":
        from . import meta_ads
        return meta_ads.summary(intent.get("query") or text)
    if skill == "news":
        from . import news
        return news.latest(intent.get("topic") or "", config.NEWS_MAX)
    if skill == "briefing":
        from . import briefing
        return briefing.gather() or _briefing(text)   # full daily briefing; markets fallback
    # New skills (full implementations land in later phases — stubs prove routing).
    if skill == "new_note":
        return _new_note(intent.get("text") or "")
    if skill == "project_status":
        return _project_status()
    if skill == "resume_project":
        from . import manage
        return manage.resume(intent.get("project") or text)
    if skill == "note_suggestions":
        return _note_suggestions()
    if skill == "post_content":
        from . import content
        return content.publish(intent.get("brief") or "", intent.get("video") or "")
    if skill == "content_history":
        from . import content
        rep = content.last_posts()
        return rep or "You haven't posted anything through me yet, Sir."
    if skill == "backup_graph":
        return _backup_graph()
    return None   # "none" or unknown → not a data request


# ── Planner / Notes / backup skills (Phase 1 stubs; filled in later phases) ─────────
def _new_note(text):
    from . import notes
    text = (text or "").strip()
    if text:                                       # dictated note → save straight away
        return notes.save_note("", text)
    # No content → ask the UI to pop the note window (Controller handles "ui").
    return {"say": "Opening a new note, Sir.", "show": "📝 New note", "ui": "new_note"}


def _project_status():
    from . import planner
    return planner.project_status()


def _note_suggestions():
    from . import notes
    rep = notes.review()
    if rep is None:
        return "You have no notes yet, Sir. Say \"take a note\" to start one."
    return rep


def _backup_graph():
    from . import graph
    path = graph.backup()
    if path:
        return {"say": "I've backed up your knowledge graph, Sir.",
                "show": "💾 Knowledge graph backed up to:\n" + path}
    return "I couldn't back up the knowledge graph, Sir."


def _regex_data_handle(text):
    """Deterministic keyword routing — the offline / fallback path used when the
    model is unavailable or didn't recognise a clear data command."""
    if FAB_RE.search(text):
        return _fab()
    if GOLD_RE.search(text):
        from . import gold
        return gold.price(text)
    if STOCK_RE.search(text):
        sym = stock_symbol(text)
        price_intent = bool(re.search(
            r"price|trading|quote|worth|value|index|gi[aá]|bao nhi[eê]u", text, re.I))
        if sym or price_intent:
            return _stock(text, sym)
    if ADS_RE.search(text) and ADS_CTX_RE.search(text):
        from . import meta_ads
        return meta_ads.summary(text)
    if POST_CONTENT_RE.search(text):
        from . import content
        return content.publish("", "")
    if NEWS_RE.search(text):
        return _briefing(text)
    return None


def data_handle(text):
    """Route a request to a live-data skill. The MODEL decides the intent and
    parameters (so any wording works); the skill then produces the exact answer
    (precise numbers, clickable links — no model drift). Regex matching is kept
    only as an offline backstop when the model is unavailable or unsure."""
    intent = _route_intent(text)
    if intent:
        rep = _dispatch(intent, text)
        if rep is not None:
            return rep
        if intent.get("skill") == "none":
            return None        # the model is confident this isn't live data
    return _regex_data_handle(text)


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
