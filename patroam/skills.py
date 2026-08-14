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
    '- "calendar": READING the calendar — what\'s on, am I free, what\'s next, '
    'my schedule. Add {"when":"..."} with their own words for the day/range '
    '("today", "tomorrow", "friday", "next week"), and {"free":true} if they are '
    "asking when they are FREE rather than what's booked.\n"
    '- "calendar_add": ADD/SCHEDULE/BOOK a new event. Add {"title":"..."} (what '
    'it is), {"when":"..."} (their words for date+time, verbatim), and optionally '
    '{"duration":minutes} and {"location":"..."}.\n'
    '- "calendar_edit": MOVE/RESCHEDULE/RENAME or CANCEL an existing event. Add '
    '{"title":"..."} naming which event, {"when":"..."} for the new time if moving, '
    'and {"cancel":true} if they want it deleted.\n'
    '- "todo_add": add a TO-DO / task / reminder to their task list ("add a task", '
    '"remind me to X", "thêm việc", "todo mua sữa"). Add {"title":"..."} with the '
    'task itself, {"due":"..."} if they gave a deadline in their own words, and '
    '{"urgent":true} if they stressed it is urgent/important.\n'
    '- "todo_done": mark a task COMPLETE ("done with X", "xong việc X", "tick off "\n'
    '"the report"). Add {"title":"..."} naming which task.\n'
    '- "todo_list": asking what is on their to-do list / what tasks are left / '
    'what they finished ("what do I have to do", "còn việc gì", "task nào chưa xong").\n'
    '- "automation": run / trigger one of their n8n automation workflows, or ask '
    'what automations exist. Add {"name":"..."} naming the workflow if they said '
    'one ("chạy workflow đăng bài", "run the order printing automation").\n'
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
    '"what\'s on my calendar tomorrow" -> {"skill":"calendar","when":"tomorrow"}; '
    '"lịch của tôi hôm nay thế nào" -> {"skill":"calendar","when":"today"}; '
    '"when am I free on friday" -> {"skill":"calendar","when":"friday","free":true}; '
    '"schedule a meeting with Long friday at 3pm" -> '
    '{"skill":"calendar_add","title":"Meeting with Long","when":"friday at 3pm"}; '
    '"đặt lịch gặp nha sĩ thứ 3 lúc 10 giờ sáng" -> '
    '{"skill":"calendar_add","title":"Gặp nha sĩ","when":"thứ 3 lúc 10 giờ sáng"}; '
    '"move the dentist appointment to 4pm" -> '
    '{"skill":"calendar_edit","title":"dentist","when":"4pm"}; '
    '"cancel the standup tomorrow" -> '
    '{"skill":"calendar_edit","title":"standup","cancel":true}; '
    '"remind me to buy milk tomorrow" -> '
    '{"skill":"todo_add","title":"Buy milk","due":"tomorrow"}; '
    '"thêm việc mua sách vào todo" -> {"skill":"todo_add","title":"Mua sách"}; '
    '"todo gấp: gửi CV cho công ty unreal" -> '
    '{"skill":"todo_add","title":"Gửi CV cho công ty unreal","urgent":true}; '
    '"xong việc mua sách rồi" -> {"skill":"todo_done","title":"mua sách"}; '
    '"i finished the ads update" -> {"skill":"todo_done","title":"ads update"}; '
    '"còn việc gì chưa làm" -> {"skill":"todo_list"}; '
    '"what\'s on my to do list" -> {"skill":"todo_list"}; '
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
    if skill == "todo_add":
        return _todo_add(intent.get("title") or text, intent.get("due") or "",
                         bool(intent.get("urgent")))
    if skill == "todo_done":
        return _todo_done(intent.get("title") or "")
    if skill == "todo_list":
        return _todo_list()
    if skill == "automation":
        return _automation(intent.get("name") or "")
    if skill == "calendar":
        return _calendar_read(intent.get("when") or "", bool(intent.get("free")))
    if skill == "calendar_add":
        return _calendar_add(intent.get("title") or "", intent.get("when") or "",
                             intent.get("duration"), intent.get("location") or "")
    if skill == "calendar_edit":
        return _calendar_edit(intent.get("title") or "", intent.get("when") or "",
                              bool(intent.get("cancel")))
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
        rep = notes.save_note("", text)
        rep["ui"] = "notes"                        # and show it in the Notes panel
        return rep
    # No content → just open the panel and let him write.
    return {"say": "Opening your notes, Sir.", "show": "📝 Notes", "ui": "notes"}


def _project_status():
    from . import planner
    return planner.project_status()


# ── n8n automations ───────────────────────────────────────────────────────────
def _automation(name=""):
    """List automation workflows, or trigger one by name."""
    from . import n8n
    st = n8n.status()
    running = st["state"] == "running"
    # Listing works from n8n's own database, so the workflows are readable even
    # while the engine is down — only TRIGGERING one needs it running.
    wfs = n8n.workflows()
    if not running and (name or not wfs):
        detail = st.get("detail") or st["state"]
        return {"say": _vi(f"n8n chưa chạy: {detail}", f"n8n isn't running: {detail}"),
                "show": "⚙ n8n — " + detail, "ui": "automations"}
    if not wfs:
        why = n8n.last_error()
        return {"say": _vi("Em chưa thấy workflow nào. Anh mở tab Automations để tạo nhé.",
                           "No workflows yet, Sir. Open the Automations tab to build one."),
                "show": "⚙ " + _vi("Chưa có workflow nào.", "No workflows yet.")
                        + (f"\n({why})" if why else ""),
                "ui": "automations"}
    if not name:
        act = [w for w in wfs if w["active"]]
        return {"say": _vi(f"Anh có {len(wfs)} workflow, {len(act)} đang bật.",
                           f"You have {len(wfs)} workflows, {len(act)} active."),
                "show": "⚙ " + _vi("Automations", "Automations") + "\n"
                        + "\n".join(("  ● " if w["active"] else "  ○ ") + w["name"] for w in wfs)
                        + ("" if running else "\n" + _vi("(n8n đang tắt — bật để chạy)",
                                                         "(n8n is stopped — start it to run these)")),
                "ui": "automations"}
    want = name.strip().lower()
    hit = next((w for w in wfs if want in w["name"].lower()), None)
    if not hit:
        return {"say": _vi(f"Em không tìm thấy workflow “{name}”.",
                           f"No workflow matching “{name}”, Sir."),
                "show": "⚙ " + "\n".join("  • " + w["name"] for w in wfs), "ui": "automations"}
    # Prefer the path the workflow actually listens on; the slugified name is
    # only a guess and fails on anything not named after its webhook.
    ok, resp = n8n.run_webhook(hit.get("webhook") or hit["name"].lower().replace(" ", "-"))
    if ok:
        return {"say": _vi(f"Đã chạy {hit['name']}.", f"Ran {hit['name']}."),
                "show": f"⚙ {hit['name']} — " + _vi("đã chạy", "triggered"), "ui": "automations"}
    return {"say": _vi(f"Em không chạy được {hit['name']}.", f"Couldn't trigger {hit['name']}, Sir."),
            "show": f"⚙ {hit['name']}\n{resp}", "ui": "automations"}


# ── TODO (Google Tasks) ───────────────────────────────────────────────────────
def _todo_unavailable():
    return {"say": _vi("Google Tasks chưa kết nối. Anh chạy python -m patroam.wire_gcal nhé.",
                       "Google Tasks isn't connected yet, Sir. Run "
                       "python -m patroam.wire_gcal once."),
            "show": "☑ Google Tasks not connected.\nRun:  python -m patroam.wire_gcal"}


def _todo_line(t):
    bits = "  " + ("‼ " if t["priority"] > 1 else "! " if t["priority"] else "") + t["title"]
    if t["when"]:
        bits += "  — " + t["when"].replace(" · all day", "")
    if t["overdue"]:
        bits += "  (overdue)"
    return bits


def _todo_add(title, due="", urgent=False):
    """Create a task from speech."""
    from . import gcal
    if not gcal.available():
        return _todo_unavailable()
    title = (title or "").strip()
    if not title:
        return {"say": _vi("Việc gì ạ?", "What's the task, Sir?"),
                "show": "☑ " + _vi("Nói tên việc cần thêm.", "Name the task.")}
    if urgent and not gcal._PRIORITY_RE.search(title):
        title = "!! " + title                       # marker the sorter understands
    when = _parse_when(due, want_range=True) if (due or "").strip() else None
    t = gcal.create_task(title, due=when)
    if not t:
        return {"say": _vi("Em không thêm được việc đó.", "I couldn't add that task, Sir."),
                "show": "☑ " + (gcal.last_error() or "failed")}
    tail = (" — " + t["when"].replace(" · all day", "")) if t["when"] else ""
    return {"say": _vi(f"Đã thêm: {t['title']}{tail}.", f"Added: {t['title']}{tail}."),
            "show": "☑ " + _vi("Đã thêm", "Added") + f" — {t['title']}{tail}",
            "ui": "todo"}


def _todo_done(title):
    """Tick a task off by name."""
    from . import gcal
    if not gcal.available():
        return _todo_unavailable()
    t = gcal.find_task(title)
    if not t:
        return {"say": _vi(f"Em không tìm thấy việc nào tên “{title}”.",
                           f"I couldn't find a task called {title}, Sir."),
                "show": "☑ " + _vi(f"Không có việc nào khớp “{title}”.",
                                   f"No open task matching “{title}”."),
                "ui": "todo"}
    if not gcal.complete_task(t["id"], t["list_id"]):
        return {"say": _vi("Em không tick được việc đó.", "I couldn't complete that, Sir."),
                "show": "☑ " + (gcal.last_error() or "failed"), "ui": "todo"}
    left = [x for x in gcal.list_tasks(limit=50)]
    nxt = (" " + _vi(f"Tiếp theo: {left[0]['title']}.", f"Next up: {left[0]['title']}.")) if left else ""
    return {"say": _vi(f"Xong việc {t['title']}.", f"Marked {t['title']} done.") + nxt,
            "show": "☑ " + _vi("Hoàn thành", "Completed") + f" — {t['title']}\n"
                    + _vi(f"Còn lại {len(left)} việc.", f"{len(left)} still open."),
            "ui": "todo"}


def _todo_list():
    """What's done and what's left, in the order it should be worked."""
    from . import gcal
    if not gcal.available():
        return _todo_unavailable()
    snap = gcal.tasks_snapshot()
    open_t, done_t, c = snap["open"], snap["done"], snap["counts"]
    if not open_t and not done_t:
        if snap.get("error") or gcal.last_error():
            return {"say": _vi("Em không đọc được danh sách việc.",
                               "I couldn't read your task list, Sir."),
                    "show": "☑ " + (snap.get("error") or gcal.last_error()), "ui": "todo"}
        return {"say": _vi("Anh không còn việc nào cả.", "Your task list is clear, Sir."),
                "show": "☑ " + _vi("Không còn việc nào.", "Nothing to do."), "ui": "todo"}
    # Spoken: recent wins first, then what matters now.
    say = []
    import datetime as _dt
    cutoff = (_dt.datetime.now(tz=gcal._tz()) - _dt.timedelta(days=2)).isoformat()
    just = [d for d in done_t if d["completed_at"] and d["completed_at"] >= cutoff]
    if just:
        say.append(_vi(f"Anh vừa xong {len(just)} việc: " + "; ".join(d["title"] for d in just[:3]) + ".",
                       f"You recently finished {len(just)}: " + "; ".join(d["title"] for d in just[:3]) + "."))
    if c.get("overdue"):
        say.append(_vi(f"{c['overdue']} việc đã quá hạn.", f"{c['overdue']} overdue."))
    if open_t:
        top = open_t[:3]
        say.append(_vi("Ưu tiên bây giờ: " + "; ".join(t["title"] for t in top) + ".",
                       "Up next: " + "; ".join(t["title"] for t in top) + "."))
        if len(open_t) > 3:
            say.append(_vi(f"Còn {len(open_t) - 3} việc nữa.", f"{len(open_t) - 3} more after that."))
    # Shown: the full ordered list.
    L = ["☑ " + _vi("VIỆC CẦN LÀM", "TO DO") + f" — {c.get('open', 0)} open"
         + (f" · {c['overdue']} overdue" if c.get("overdue") else "")]
    for name, sel in (("Overdue", lambda t: t["overdue"]),
                      ("Today", lambda t: not t["overdue"] and t["today"]),
                      ("Upcoming", lambda t: not t["overdue"] and not t["today"] and t["due"]),
                      ("No due date", lambda t: not t["due"])):
        rows = [t for t in open_t if sel(t)]
        if rows:
            L += ["", name + f" ({len(rows)})"] + [_todo_line(t) for t in rows[:8]]
    if just:
        L += ["", _vi("Vừa hoàn thành", "Just completed")] + ["  ✓ " + d["title"] for d in just[:5]]
    return {"say": " ".join(say), "show": "\n".join(L), "ui": "todo"}


# ── Google Calendar ───────────────────────────────────────────────────────────
def _cal_unavailable():
    return {"say": "Google Calendar isn't connected yet, Sir. Run "
                   "python -m patroam.wire_gcal once and I'll take it from there.",
            "show": "📅 Google Calendar not connected.\n"
                    "Run:  python -m patroam.wire_gcal"}


def _cal_error(lead):
    """Report WHY a calendar call failed — the API tells us plainly, so pass it on
    instead of a generic 'failed' that leaves nothing to act on."""
    from . import gcal
    why = gcal.last_error()
    return {"say": lead + ((" " + why.splitlines()[0]) if why else ""),
            "show": "📅 " + lead + (("\n" + why) if why else "")}


def _parse_when(phrase, want_range=False):
    """Turn a spoken time phrase into a datetime — via the model, so English and
    Vietnamese ("thứ 3 lúc 10 giờ sáng") work without hand-written patterns.
    Returns a datetime, or None. `want_range` asks for a whole-day answer."""
    import datetime as _dt
    from . import llm
    phrase = (phrase or "").strip()
    now = _dt.datetime.now()
    if not phrase:
        return now
    if not llm.available():
        return now
    prompt = (
        "Convert the time expression to an absolute local datetime.\n"
        f"NOW is {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')}).\n"
        "Return ONLY JSON: {\"iso\":\"YYYY-MM-DDTHH:MM\"}. "
        "If no clock time is given, use "
        + ("00:00" if want_range else "09:00") + ". "
        "Weekday names mean the NEXT such day (today counts if still ahead).\n"
        # ensure_ascii=False: keep Vietnamese readable ("thứ 3 lúc 10 giờ sáng"),
        # not escaped to \uXXXX, so the model parses it reliably.
        "Expression: " + json.dumps(phrase, ensure_ascii=False))
    raw = llm.complete(prompt, timeout=15) or ""
    try:
        i, j = raw.find("{"), raw.rfind("}")
        iso = (json.loads(raw[i:j + 1]) if i >= 0 and j > i else {}).get("iso", "")
        return _dt.datetime.fromisoformat(iso) if iso else None
    except Exception:
        return None


def _fmt_events(evs, header):
    if not evs:
        return None
    lines = [header]
    for e in evs:
        bit = f"  • {e['when']} — {e['title']}"
        if e.get("location"):
            bit += f" ({e['location']})"
        lines.append(bit)
    return "\n".join(lines)


def _calendar_read(when, free=False):
    """What's on the calendar, or when the user is free."""
    from . import gcal
    if not gcal.available():
        return _cal_unavailable()
    day = _parse_when(when, want_range=True) or __import__("datetime").datetime.now()
    if free:
        slots = gcal.free_slots(day, config.WORK_START_HOUR, config.WORK_END_HOUR)
        if not slots:
            return {"say": f"You're fully booked {when or 'today'}, Sir.",
                    "show": "📅 No free slots.", "ui": "calendar"}
        say = "You're free " + "; ".join(s["when"] for s in slots[:3]) + "."
        return {"say": say, "show": "📅 Free slots\n" +
                "\n".join("  • " + s["when"] for s in slots), "ui": "calendar"}
    # A single day if they named one, else the week ahead.
    wide = not when or when.lower() in ("this week", "next week", "week", "tuần này", "tuần sau")
    evs = gcal.list_events(days=7 if wide else 1, start=day)
    tasks = gcal.list_tasks()          # Google Tasks — a separate API from events
    if not evs and not tasks:
        # Empty could mean "free" OR "the call failed" — don't report a silent
        # failure as an empty calendar.
        if gcal.last_error():
            return _cal_error("I couldn't read your calendar, Sir.")
        return {"say": f"Nothing on your calendar {when or 'today'}, Sir.",
                "show": "📅 Nothing scheduled.", "ui": "calendar"}
    show = _fmt_events(evs, f"📅 {when or 'Upcoming'}") or ""
    if tasks:
        show += ("\n\n" if show else "") + "☑ Tasks\n" + "\n".join(
            f"  • {t['title']}" + (f" — {t['when']}" if t["when"] else "")
            for t in tasks[:8])
    bits = []
    if evs:
        head = "; ".join(f"{e['title']} {e['when'].lower()}" for e in evs[:3])
        more = len(evs) - 3
        bits.append(_vi("Anh có " + head + (f", và {more} việc nữa" if more > 0 else ""),
                        "You have " + head + (f", and {more} more" if more > 0 else "")))
    if tasks:
        bits.append(_vi(f"{len(tasks)} việc đang mở, tiếp theo là " + tasks[0]["title"],
                        f"{len(tasks)} task" + ("s" if len(tasks) != 1 else "")
                        + " open, next is " + tasks[0]["title"]))
    return {"say": ". ".join(bits) + ".", "show": show, "ui": "calendar"}


def _calendar_add(title, when, duration=None, location=""):
    """Schedule a new event."""
    from . import gcal
    if not gcal.available():
        return _cal_unavailable()
    try:
        mins = int(duration) if duration else 60
    except (TypeError, ValueError):
        mins = 60
    # Missing pieces must be REMEMBERED, not just asked about: previously PATROAM
    # said "Name the event." and kept no state, so the user's answer fell through
    # to the chat model and the event was never created.
    if not title:
        _PENDING_ADD.clear()
        _PENDING_ADD.update({"when": when, "minutes": mins, "location": location})
        return {"say": _vi("Sự kiện tên là gì ạ?", "What should I call it, Sir?"),
                "show": "📅 " + _vi("Đặt tên cho sự kiện:", "Name the event:"),
                "offer": "cal_slot"}
    # An empty time must be ASKED for, never silently defaulted to "now" — that
    # quietly books the event at whatever o'clock it happens to be.
    start = _parse_when(when) if (when or "").strip() else None
    if not start:
        _PENDING_ADD.clear()
        _PENDING_ADD.update({"title": title, "minutes": mins, "location": location})
        return {"say": _vi(f"“{title}” vào lúc nào ạ?", f"When is “{title}”, Sir?"),
                "show": "📅 " + _vi(f"“{title}” — vào ngày giờ nào?",
                                    f"“{title}” — what date and time?"),
                "offer": "cal_slot"}
    # Warn BEFORE booking: silently stacking a second thing on an occupied hour
    # is worse than asking.
    import datetime as _dt
    clash = gcal.conflicts(start, start + _dt.timedelta(minutes=mins))
    if clash:
        _PENDING_EVENT.clear()
        _PENDING_EVENT.update({"title": title, "start": start, "minutes": mins,
                               "location": location})
        names = "; ".join(f"{c['title']} ({c['when']})" for c in clash[:3])
        w = gcal._human(start, start + _dt.timedelta(minutes=mins))
        return {"say": _vi(f"Trùng lịch với “{clash[0]['title']}” cùng giờ rồi anh. Vẫn thêm chứ ạ?",
                           f"That clashes with {clash[0]['title']} at the same time, Sir. "
                           "Shall I add it anyway?"),
                "show": "⚠️ " + _vi("Trùng lịch — anh đã có:", "Conflict — you already have:")
                        + f"\n  • {names}\n\n"
                        + _vi(f"Vẫn thêm “{title}” lúc {w} chứ?", f"Add “{title}” at {w} anyway?"),
                "offer": "cal_add"}
    return _do_add(title, start, mins, location)


# An event held back because it clashed; a following "yes" books it.
_PENDING_EVENT = {}
# A half-specified event waiting for its missing title or time.
_PENDING_ADD = {}


def _vi(vietnamese, english):
    """Reply in whichever language PATROAM is currently set to."""
    return vietnamese if (config.RESPONSE_LANGUAGE or "").lower().startswith("viet") \
        else english


def supply_event_slot(text):
    """The user just answered "what's it called?" / "when?" — fill the gap and
    finish booking. Returns a reply dict, or None if nothing was pending."""
    if not _PENDING_ADD:
        return None
    p = dict(_PENDING_ADD)
    _PENDING_ADD.clear()
    answer = (text or "").strip()
    if not answer:
        return None
    if not p.get("title"):
        p["title"] = answer.strip(' "“”')
    else:
        p["when"] = answer
    return _calendar_add(p.get("title", ""), p.get("when", ""),
                         p.get("minutes"), p.get("location", ""))


def cancel_event_slot():
    _PENDING_ADD.clear()


def _do_add(title, start, mins, location=""):
    from . import gcal
    ev = gcal.create_event(title, start, duration_minutes=mins, location=location)
    if not ev:
        return _cal_error("I couldn't add that to your calendar, Sir.")
    return {"say": _vi(f"Đã thêm {ev['title']}, {ev['when'].lower()}.",
                       f"Added {ev['title']}, {ev['when'].lower()}."),
            "show": "📅 " + _vi("Đã thêm", "Added") + f" — {ev['title']}\n  {ev['when']}"
                    + (f"\n  {ev['location']}" if ev.get("location") else "")}


def confirm_pending_event():
    """Book the event that was held back for a conflict. Returns a reply, or None."""
    if not _PENDING_EVENT:
        return None
    p = dict(_PENDING_EVENT)
    _PENDING_EVENT.clear()
    return _do_add(p["title"], p["start"], p["minutes"], p.get("location", ""))


def cancel_pending_event():
    _PENDING_EVENT.clear()


def _calendar_edit(title, when, cancel=False):
    """Move, rename or cancel an existing event."""
    from . import gcal
    if not gcal.available():
        return _cal_unavailable()
    ev = gcal.find_event(title)
    if not ev:
        return {"say": f"I couldn't find an event called {title}, Sir.",
                "show": f"📅 No upcoming event matching '{title}'."}
    if cancel:
        ok = gcal.delete_event(ev["id"])
        if not ok:
            return _cal_error("I couldn't cancel that, Sir.")
        return {"say": f"Cancelled {ev['title']}.",
                "show": f"📅 Cancelled — {ev['title']} ({ev['when']})"}
    start = _parse_when(when) if when else None
    if not start:
        return {"say": f"When should I move {ev['title']} to, Sir?",
                "show": f"📅 {ev['title']} is {ev['when']}. Give me a new time."}
    new = gcal.update_event(ev["id"], start=start)
    if not new:
        return _cal_error("I couldn't move that, Sir.")
    return {"say": f"Moved {new['title']} to {new['when'].lower()}.",
            "show": f"📅 Moved — {new['title']}\n  was {ev['when']}\n  now {new['when']}"}


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
