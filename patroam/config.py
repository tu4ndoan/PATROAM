"""Central configuration for PATROAM.

Anything that might change per-machine or per-user lives here so the rest of
the package never hardcodes paths, URLs, or the agent's persona.
"""

import json
import os
import random
import re
import tempfile
from datetime import datetime


def _load_secrets():
    """Load secrets from ~/.patroam/secrets.json into the environment (without
    overriding real env vars). Keeps API keys/tokens OUT of tracked source."""
    path = os.path.join(os.path.expanduser("~"), ".patroam", "secrets.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if v not in (None, ""):
                os.environ.setdefault(k, str(v))


_load_secrets()

# ── Backend ──────────────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("PATROAM_OLLAMA_URL", "http://localhost:11434")

# Preferred model to start on. Matched flexibly against the available models
# (exact, case-insensitive, punctuation-insensitive, then substring), so "llama3"
# picks "Llama3:latest" and "gemma4.31b" still finds "gemma4:31b".
# Override with PATROAM_MODEL (e.g. "claude-opus-4-8"; Claude needs ANTHROPIC_API_KEY).
# If none of these are installed, PATROAM falls back to any local model you have.
DEFAULT_MODEL = os.environ.get("PATROAM_MODEL", "gemma4:31b-cloud")

# Preferred CLOUD vision model (Claude reads images very well). Used when an
# ANTHROPIC_API_KEY is configured; otherwise PATROAM falls back to a local
# vision model. Set "" to never use cloud vision.
VISION_MODEL = os.environ.get("PATROAM_VISION_MODEL", "claude-sonnet-4-6")

# Model PATROAM switches to for CODING questions (must be installed/available;
# falls back to the normal model if not). Set "" to disable code routing.
CODE_MODEL = os.environ.get("PATROAM_CODE_MODEL", "minimax-m3:cloud")

# Local (Ollama) vision models PATROAM can use, matched by name substring.
LOCAL_VISION_HINTS = ("qwen2.5vl", "qwen2-vl", "qwenvl", "llava", "bakllava",
                      "moondream", "minicpm-v", "granite3.2-vision")


def choose_vision_model(available=None):
    """Pick the best AVAILABLE vision model for an image request:
    1) Claude vision if an API key is set (best quality),
    2) else a locally-installed vision model (offline, free, e.g. qwen2.5vl),
    3) else None (no vision available).
    `available` is the list of model names the provider currently offers."""
    if VISION_MODEL and VISION_MODEL.lower().startswith("claude") \
            and os.environ.get("ANTHROPIC_API_KEY"):
        return VISION_MODEL
    for m in (available or []):
        ml = m.lower()
        if "embed" not in ml and any(h in ml for h in LOCAL_VISION_HINTS):
            return m
    if VISION_MODEL and not VISION_MODEL.lower().startswith("claude"):
        return VISION_MODEL
    return None

# ── Meta Ads (direct API — for the "how are my ads doing" command) ──────────────
# A Meta access token with `ads_read` (a non-expiring System User token is best)
# and your ad-account id (numeric, no "act_" prefix). No OAuth needed.
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID", "")
META_API_VERSION = os.environ.get("META_API_VERSION", "v21.0")

# ── News (NewsAPI — for the "what's up" command) ────────────────────────────────
# Free key from https://newsapi.org. Country is a NewsAPI top-headlines code.
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "c1af580a08f24022bfa1c29743b48f71")
NEWS_COUNTRY = os.environ.get("PATROAM_NEWS_COUNTRY", "us")

# ── Your trusted news sources (RSS/Atom feeds) ──────────────────────────────────
# PATROAM reads these feeds, speaks the headlines you care about, and puts the
# clickable links in the chat. Edit this list, OR drop a ~/.patroam/news.json like:
#   {"feeds": ["https://...rss"], "interests": ["ai", "vietnam", "oncology"]}
NEWS_MAX = int(os.environ.get("PATROAM_NEWS_MAX", "6"))
_DEFAULT_FEEDS = [
    "https://baochinhphu.vn/rss"
]
# Topics/keywords you personally care about (used to rank & pick headlines).
_DEFAULT_INTERESTS = ["technology"]


def _load_news():
    feeds, interests = list(_DEFAULT_FEEDS), list(_DEFAULT_INTERESTS)
    path = os.path.join(os.path.expanduser("~"), ".patroam", "news.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("feeds"), list) and d["feeds"]:
            feeds = [u for u in d["feeds"] if isinstance(u, str)]
        if isinstance(d.get("interests"), list):
            interests = [k for k in d["interests"] if isinstance(k, str)]
    except Exception:
        pass
    return feeds, interests


NEWS_FEEDS, NEWS_INTERESTS = _load_news()

# ── Automatic news watch (poll feeds, alert on anything new) ──────────────────────
# PATROAM checks your feeds every NEWS_WATCH_INTERVAL seconds and proactively
# reports new items (spoken by the orb and/or DM'd to your phone via Slack).
NEWS_WATCH = os.environ.get("PATROAM_NEWS_WATCH", "1") not in ("0", "false", "False", "")
NEWS_WATCH_INTERVAL = int(os.environ.get("PATROAM_NEWS_WATCH_INTERVAL", "300"))   # 5 min
NEWS_WATCH_MAX = int(os.environ.get("PATROAM_NEWS_WATCH_MAX", "5"))
# Only alert on items matching your interests (avoids spam). 0 = any new item.
NEWS_WATCH_INTERESTS_ONLY = os.environ.get(
    "PATROAM_NEWS_WATCH_INTERESTS_ONLY", "1") not in ("0", "false", "False", "")
NEWS_SEEN_FILE = os.path.join(os.path.expanduser("~"), ".patroam", "news_seen.json")

# ── Launch briefing (project progress + notes review + "what's up") on startup ────
LAUNCH_BRIEFING = os.environ.get("PATROAM_LAUNCH_BRIEFING", "1") not in ("0", "false", "False", "")
# Also brief each time you wake PATROAM with the wake word (you can barge-in to skip).
BRIEF_ON_WAKE = os.environ.get("PATROAM_BRIEF_ON_WAKE", "1") not in ("0", "false", "False", "")
# Min seconds between wake-briefings (0 = every wake). Within the gap you just get a greeting.
BRIEF_ON_WAKE_COOLDOWN = int(os.environ.get("PATROAM_BRIEF_WAKE_COOLDOWN", "0"))
# Snapshot of last session's state, for "since our last session…" deltas.
SESSION_FILE = os.path.join(os.path.expanduser("~"), ".patroam", "session.json")
# A Spotify playlist URL for the briefing's "shall I put on your Focus playlist?" offer.
SPOTIFY_FOCUS_URL = os.environ.get("SPOTIFY_FOCUS_URL", "")
# Slack channel id to scan for customer feedback/complaints in the briefing (optional).
SLACK_FEEDBACK_CHANNEL = os.environ.get("SLACK_FEEDBACK_CHANNEL", "")

# ── Slack (chat with PATROAM from your phone) ─────────────────────────────────────
# Socket Mode: PATROAM connects OUT to Slack, so it runs on your computer with no
# public URL/port-forwarding. Put the tokens in ~/.patroam/secrets.json. See
# patroam/slack_bot.py for the one-time Slack-app setup.
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")      # xoxb-…
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")      # xapp-…
# Optional: where to post PROACTIVE alerts (news). A channel id the bot is in, or
# your DM channel id. Replies to your messages go back automatically regardless.
SLACK_DM_CHANNEL = os.environ.get("SLACK_DM_CHANNEL", "")


def slack_enabled():
    return bool(SLACK_BOT_TOKEN and SLACK_APP_TOKEN)


# ── Media extracted from documents (images inside PDFs etc.) ──────────────────────
MEDIA_DIR = os.environ.get(
    "PATROAM_MEDIA_DIR", os.path.join(os.path.expanduser("~"), ".patroam", "media"))
MEDIA_INDEX_FILE = os.path.join(MEDIA_DIR, "index.json")

# ── Gold price + Fab sales ──────────────────────────────────────────────────────
# Optional goldapi.io key (in secrets) for the gold price; without one, a free
# keyless source is used. "what's up", "gold price", or "fab sales" use these.
GOLD_API_KEY = os.environ.get("GOLD_API_KEY", "")
FAB_SALES_URL = os.environ.get("PATROAM_FAB_URL",
                               "https://www.fab.com/portal/analytics/reports/sales")
# Download endpoint — opened in Brave (your logged-in session) to fetch a fresh
# CSV, which PATROAM then reads. FAB_REPORT_DAYS sets the date range.
FAB_DOWNLOAD_URL = os.environ.get("PATROAM_FAB_DOWNLOAD_URL",
                                  "https://www.fab.com/i/portal/sales/reports/download")
FAB_REPORT_DAYS = int(os.environ.get("PATROAM_FAB_REPORT_DAYS", "365"))

# ── Content pipeline (one video → tailored posts on 5 platforms) ──────────────────
# "post my reel" / "publish my video": PATROAM writes a hook + caption + hashtags
# TAILORED to each platform, then publishes to every platform whose credentials are
# set below. Platforms without credentials fall back to ASSISTED mode (PATROAM opens
# the upload page and copies the caption to your clipboard so you paste it yourself)
# — so the pipeline is useful today and each platform turns fully automatic the
# moment you drop its token into ~/.patroam/secrets.json.
#
# Where PATROAM looks for the edited video (newest file wins) and logs each package.
CONTENT_DIR = os.environ.get(
    "PATROAM_CONTENT_DIR", os.path.join(os.path.expanduser("~"), ".patroam", "content"))
CONTENT_LOG = os.path.join(CONTENT_DIR, "posts.json")
# Video extensions the pipeline recognises when auto-picking the latest clip.
CONTENT_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")
# Your niche + persona — steers the caption/hook copywriting. Edit to taste.
CONTENT_NICHE = os.environ.get(
    "PATROAM_CONTENT_NICHE",
    "I create and sell 3D game assets — animation packs and environments — for game "
    "developers on Fab.com (Epic Games' marketplace). My audience is aspiring 3D "
    "artists and indie devs who want to earn recurring income from their craft.")
# Which platforms the pipeline targets, in posting order. Trim to taste.
CONTENT_PLATFORMS = [p.strip().lower() for p in os.environ.get(
    "PATROAM_CONTENT_PLATFORMS", "tiktok,instagram,youtube,threads,x").split(",") if p.strip()]

# Per-platform credentials (all optional — missing ones use assisted mode).
# TikTok — Content Posting API (open.tiktokapis.com). OAuth user access token with
# the `video.publish` scope.
TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
# Instagram Reels — Graph API. A long-lived token + the IG *user* id. Reels upload
# needs the video reachable at a PUBLIC url (Graph API pulls it), see CONTENT_PUBLIC_BASE.
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
IG_USER_ID = os.environ.get("IG_USER_ID", "")
# Threads — Threads Graph API. Token + Threads user id. Video also needs a PUBLIC url.
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID = os.environ.get("THREADS_USER_ID", "")
# YouTube Shorts — Data API v3. An OAuth *refresh* token + the client id/secret it
# was issued for; PATROAM exchanges it for an access token and does a resumable upload
# of the LOCAL file (no public url needed).
YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
# X (Twitter) — API v2 + v1.1 media upload. OAuth 1.0a user-context keys (needs a
# paid tier that allows posting). Uploads the LOCAL file.
X_API_KEY = os.environ.get("X_API_KEY", "")
X_API_SECRET = os.environ.get("X_API_SECRET", "")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET", "")
# Optional: a public base URL that maps to CONTENT_DIR (e.g. an ngrok/S3/CDN mount),
# so Instagram/Threads — which pull the video from a url — can be fully automated.
# If empty, those two use assisted mode even when their tokens are present.
CONTENT_PUBLIC_BASE = os.environ.get("PATROAM_CONTENT_PUBLIC_BASE", "")

# Upload-page URLs used in ASSISTED mode (opened in Brave with the caption copied).
CONTENT_UPLOAD_URLS = {
    "tiktok": "https://www.tiktok.com/upload",
    "instagram": "https://www.instagram.com/",
    "youtube": "https://studio.youtube.com/",
    "threads": "https://www.threads.net/",
    "x": "https://x.com/compose/post",
}

# ── ClickUp (Planner agent pushes project roadmaps here) ──────────────────────────
# Personal API token (ClickUp → Settings → Apps) + the default Space id (the
# Planner asks which space each time, falling back to this).
CLICKUP_API_TOKEN = os.environ.get("CLICKUP_API_TOKEN", "")
CLICKUP_SPACE_ID = os.environ.get("CLICKUP_SPACE_ID", "")

# ── Projects (professional planning / creation / management) ──────────────────────
# Where your code projects live (the Planner creates new project folders here and
# reads them for "where were we?"). git init only — you push manually.
GITHUB_ROOT = os.environ.get(
    "PATROAM_GITHUB_ROOT", os.path.join(os.path.expanduser("~"), "Documents", "GitHub"))
# Registry mapping each project → its folder, git remote, ClickUp list, Slack channel.
PROJECTS_REGISTRY = os.path.join(os.path.expanduser("~"), ".patroam", "projects.json")
# Your Slack user id — so PATROAM can invite you to the private dev-log channels it
# creates (find it in Slack: profile → ⋯ → Copy member ID, starts with "U").
SLACK_USER_ID = os.environ.get("SLACK_USER_ID", "")

# ── Vietnamese stocks (SSI FastConnect Data — official API) ───────────────────────
# Register at https://iboard.ssi.com.vn → FastConnect Data to get a ConsumerID +
# Secret, then put them in ~/.patroam/secrets.json. Powers "stock price VNM",
# "VN-Index", "giá cổ phiếu", and folds the index into "what's up".
SSI_CONSUMER_ID = os.environ.get("SSI_CONSUMER_ID", "")
SSI_CONSUMER_SECRET = os.environ.get("SSI_CONSUMER_SECRET", "")
SSI_DATA_URL = os.environ.get("SSI_DATA_URL", "https://fc-data.ssi.com.vn/")

# ── Voice / wake word ──────────────────────────────────────────────────────────
# Any of these phrases wakes PATROAM. Include the common speech-to-text
# mishearings (e.g. "patron" for "patroam", "pea/pee" for the letter P).
# NOTE: keep this roughly in sync with WAKE_PHRASES in web/static/app.js.
WAKE_PHRASES = [
    "patroam", "hey patroam",
    "patrom", "patroum", "patroan", "patriam", "patron", "petroam", "patram",
    "hey bro", "hey dude",
    "hey agent p", "agent p", "hey agent pea", "agent pea",
    "hey p", "hey pea", "hey pee", "hey peep",
]
# How close a heard phrase must be to a wake phrase to count (0–1).
WAKE_WORD_FUZZ = 0.78

# Silence (seconds) before the recognizer emits a captured chunk. Kept short so
# chunks arrive quickly — smart endpointing (below) decides if you're FINISHED.
PAUSE_THRESHOLD = float(os.environ.get("PATROAM_PAUSE_THRESHOLD", "0.6"))
# Hard cap (seconds) on a single captured chunk, so it can't listen forever.
PHRASE_TIME_LIMIT = float(os.environ.get("PATROAM_PHRASE_LIMIT", "30"))
# Minimum seconds of speech before a phrase is considered (filters stray clicks).
PHRASE_THRESHOLD = float(os.environ.get("PATROAM_PHRASE_THRESHOLD", "0.3"))

# ── Smart endpointing (deciding when your command is COMPLETE) ───────────────────
# After each chunk, PATROAM waits a grace period for you to continue. The grace
# adapts to whether the phrase looks finished — so trailing off on "and…/to…"
# keeps it listening, while a finished sentence is acted on quickly.
ENDPOINT_COMPLETE_GRACE = float(os.environ.get("PATROAM_GRACE_COMPLETE", "0.9"))
ENDPOINT_AMBIGUOUS_GRACE = float(os.environ.get("PATROAM_GRACE_AMBIGUOUS", "1.6"))
ENDPOINT_INCOMPLETE_GRACE = float(os.environ.get("PATROAM_GRACE_INCOMPLETE", "2.4"))
# Absolute cap (seconds) on how long one command may accumulate.
ENDPOINT_MAX_WAIT = float(os.environ.get("PATROAM_ENDPOINT_MAX", "15"))
# Consult the LLM to judge completeness for genuinely ambiguous (short) phrases.
# Set PATROAM_ENDPOINT_LLM=0 to keep it purely heuristic (instant, offline).
ENDPOINT_USE_LLM = os.environ.get("PATROAM_ENDPOINT_LLM", "1") not in ("0", "false", "False", "")
ENDPOINT_LLM_TIMEOUT = float(os.environ.get("PATROAM_ENDPOINT_LLM_TIMEOUT", "2.5"))

# Conversation session: after the wake word, PATROAM stays awake and treats every
# following utterance as a command (no need to repeat "hey patroam"). The session
# ends after this many seconds of silence, or when a stop phrase is heard.
# Set to 0 (or None) to stay awake until a stop phrase only.
SESSION_TIMEOUT = 30

# Spoken phrases that end the conversation session and return to wake-word mode.
STOP_PHRASES = [
    "stop listening", "go to sleep", "go back to sleep", "goodbye patroam",
    "that's all", "that is all", "never mind", "stop patroam",
    "thank you that's all",
]

# Temp file for recorded audio. Uses the OS temp dir so this works on Windows
# (the original /tmp/... path only existed on Unix).
VOICE_TMP_WAV = os.path.join(tempfile.gettempdir(), "patroam_voice_input.wav")
TTS_TMP_MP3 = os.path.join(tempfile.gettempdir(), "patroam_tts.mp3")

# ── Memory ─────────────────────────────────────────────────────────────────────
# PATROAM's memory about the user now lives in the knowledge graph (graph.py,
# under the "You" entity) — there is no separate memory.json store.

# ── Workspace ──────────────────────────────────────────────────────────────────
# Where PATROAM creates files/folders/projects. All file actions are sandboxed to
# this directory (paths that would escape it are rejected).
WORKSPACE_DIR = os.environ.get(
    "PATROAM_WORKSPACE", os.path.join(os.path.expanduser("~"), "PATROAM"))

# ── RAG (retrieval over your own documents) ─────────────────────────────────────
# Drop documents in KNOWLEDGE_DIR, then say "index my docs". PATROAM retrieves the
# relevant passages and answers from them. Uses Ollama embeddings if EMBED_MODEL is
# pulled (e.g. `ollama pull nomic-embed-text`); otherwise falls back to keyword search.
KNOWLEDGE_DIR = os.environ.get(
    "PATROAM_KNOWLEDGE_DIR", os.path.join(os.path.expanduser("~"), ".patroam", "knowledge"))
RAG_INDEX_FILE = os.environ.get(
    "PATROAM_RAG_INDEX", os.path.join(os.path.expanduser("~"), ".patroam", "rag_index.json"))
EMBED_MODEL = os.environ.get("PATROAM_EMBED_MODEL", "nomic-embed-text")
RAG_TOP_K = int(os.environ.get("PATROAM_RAG_TOPK", "4"))
# Vector database: if `chromadb` is installed (and an embed model is available),
# RAG uses a real persistent vector DB here; otherwise it falls back to a JSON index.
CHROMA_DIR = os.environ.get(
    "PATROAM_CHROMA_DIR", os.path.join(os.path.expanduser("~"), ".patroam", "chroma"))

# ── Knowledge graph (entities + relationships) ──────────────────────────────────
GRAPH_FILE = os.environ.get(
    "PATROAM_GRAPH_FILE", os.path.join(os.path.expanduser("~"), ".patroam", "graph.json"))
# Timestamped knowledge-graph backups (auto on launch; keep the most recent N).
BACKUP_DIR = os.environ.get(
    "PATROAM_BACKUP_DIR", os.path.join(os.path.expanduser("~"), ".patroam", "backups"))
BACKUP_KEEP = int(os.environ.get("PATROAM_BACKUP_KEEP", "20"))

# ── Notes (the Note-taker writes here; indexed into the graph under "Notes") ─────
NOTES_DIR = os.environ.get(
    "PATROAM_NOTES_DIR", os.path.join(os.path.expanduser("~"), ".patroam", "notes"))

# ── MCP connectors ──────────────────────────────────────────────────────────────
# A JSON file listing MCP servers to connect to, giving PATROAM external tools
# (e.g. the Meta Ads connector). Format: {"servers": [ {server}, ... ]} where each
# server is either stdio: {"name","command","args"?,"env"?} or remote:
# {"name","url","transport":"http"|"sse","headers"?}. Missing file = no MCP.
MCP_FILE = os.environ.get(
    "PATROAM_MCP_FILE",
    os.path.join(os.path.expanduser("~"), ".patroam", "mcp.json"))


def load_mcp_servers():
    try:
        with open(MCP_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("servers", [])
    return data if isinstance(data, list) else []

# ── Language ───────────────────────────────────────────────────────────────────
# The language PATROAM replies in. Set permanently with PATROAM_LANGUAGE, or
# switch live by saying "reply in Vietnamese" / "trả lời bằng tiếng Việt".
RESPONSE_LANGUAGE = os.environ.get("PATROAM_LANGUAGE", "English")

# Edge neural voice to use per language (a natural voice in that language).
TTS_VOICE_BY_LANG = {
    "english": "en-GB-RyanNeural",
    "vietnamese": "vi-VN-NamMinhNeural",   # male; female alt: vi-VN-HoaiMyNeural
}


def language_directive():
    """A system-prompt line telling the model which language to answer in
    (empty for English). Read fresh each turn so it can change at runtime."""
    lang = (RESPONSE_LANGUAGE or "English").strip()
    if lang.lower() in ("english", "en", ""):
        return ""
    return (f"LANGUAGE: Always reply to the user in {lang}, even if they write in "
            "another language. Keep code, identifiers, URLs and proper nouns as-is.")


def set_language(name):
    """Switch reply language at runtime and pick a matching voice. Returns the
    (language, voice) now in effect."""
    global RESPONSE_LANGUAGE, TTS_VOICE_EDGE
    RESPONSE_LANGUAGE = (name or "English").strip().title()
    voice = TTS_VOICE_BY_LANG.get(RESPONSE_LANGUAGE.lower())
    if voice:
        TTS_VOICE_EDGE = voice
    return RESPONSE_LANGUAGE, TTS_VOICE_EDGE


# ── Text-to-speech ─────────────────────────────────────────────────────────────
# "edge"    : Microsoft Edge neural voices — natural, human-like, needs internet.
# "pyttsx3" : offline Windows SAPI voices — robotic, but works with no connection.
# "auto"    : prefer edge, fall back to pyttsx3 if it's unavailable/offline.
TTS_BACKEND = os.environ.get("PATROAM_TTS_BACKEND", "auto")

# A natural-sounding male British voice for the Edge neural backend.
# Other good UK males: "en-GB-ThomasNeural". List all: `edge-tts --list-voices`.
TTS_VOICE_EDGE = (os.environ.get("PATROAM_TTS_VOICE")
                  or TTS_VOICE_BY_LANG.get(RESPONSE_LANGUAGE.lower(), "en-GB-RyanNeural"))
TTS_RATE = "+0%"     # edge prosody rate, e.g. "-10%" slower, "+10%" faster
TTS_PITCH = "+0Hz"   # edge prosody pitch
TTS_VOLUME = "+0%"   # edge prosody volume

# For the pyttsx3 fallback: prefer a British male voice if one is installed.
TTS_PYTTSX3_PREFER = [
    "george", "ryan", "british", "united kingdom", "en-gb", "en_gb",
    "daniel", "oliver", "arthur",
]
TTS_PYTTSX3_RATE = 170

# ── Speech length ──────────────────────────────────────────────────────────────
# PATROAM SPEAKS only a short summary of a long model reply; the full text (with
# code & links) still appears in the chat. The model is told to lead with a
# one-line summary, and only the first SPEAK_SUMMARY_CHARS of speech are voiced.
SPEAK_SUMMARY = os.environ.get("PATROAM_SPEAK_SUMMARY", "1") not in ("0", "false", "False", "")
SPEAK_SUMMARY_CHARS = int(os.environ.get("PATROAM_SPEAK_SUMMARY_CHARS", "240"))

# ── Persona ──────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are PATROAM, the user's personal AI assistant — warm, natural, and genuinely conversational — who is also a capable software engineer and knowledge agent.

Understand what the user MEANS from ordinary conversation; never require exact command phrasing or keywords. Infer intent, ask a brief question only if truly unclear, and reply like a thoughtful human. When an intent maps to one of your tools/actions (open an app, remember something, record a relationship, switch language, create files…), quietly emit the ACTION for it AND give a short natural reply — don't make the user phrase it a special way.
Your deeper mission is to help the user design, build, debug, maintain, and evolve software systems with maximum accuracy and minimum hallucination.

Core principles: accuracy over speed; verification over assumption; planning before execution; user approval before irreversible actions; continuous learning from past interactions.

Software engineering — you can: design architecture, generate code, refactor, review, write tests, debug, analyze logs, find performance bottlenecks, and write technical docs. Languages: Swift, Python, C++, TypeScript, JavaScript, Kotlin, Go, Rust, Java, C#.

iOS specialist — default stack: Swift 6+, SwiftUI, MVVM, async/await, modular architecture. Before coding an iOS app, determine the iOS target, auth method, backend, database, and offline needs. Never assume missing requirements — ask first.

RAG — never rely solely on model memory. Retrieve relevant documents, rank by relevance, extract supporting evidence, answer only from retrieved context, and cite sources internally. If evidence is insufficient, say "Insufficient evidence found." Never fabricate facts.

Knowledge graph — track entities (User, Project, Repository, Feature, Task, Technology, Company, Document) and relationships (USES, OWNS, DEPENDS_ON, IMPLEMENTS, RELATED_TO, BLOCKED_BY) with confidence and timestamp; use it to improve reasoning.

Memory — store user memory (preferences, coding style, tech stack, recurring requirements) and project memory (architecture decisions, repositories, APIs, requirements, unresolved issues). Never store raw conversations; store compressed knowledge.

Planning protocol — for every task: understand the goal, identify missing info, generate a plan, ask clarifying questions, execute, verify, update memory. If confidence < 90%, ask questions before proceeding.

Debugging protocol — collect evidence, analyze logs, find the root cause, propose a fix, explain your reasoning, validate the solution. Never claim code works without verification.

Anti-hallucination — do not invent APIs, URLs, libraries, statistics, documentation, research papers, or source-code behavior. When uncertain, state the uncertainty explicitly.

Structured format (OPTIONAL, written engineering work ONLY): the internal sections Understanding / Missing Information / Proposed Plan / Execution / Verification / Confidence Score / Memory Updates are a private thinking checklist. NEVER output these section headers, and NEVER say them aloud. By DEFAULT — and ALWAYS for questions, knowledge/recall, and casual chat — just give the answer directly and conversationally, with no preamble and no headings.

Project planning & creation protocol — when the user wants to build/create a project (app, website, service, feature set), act as a senior architect + delivery lead across TWO explicit phases. NEVER dump a solution or create anything until planning is done and confirmed.

PLANNING PHASE — consult, one question at a time, using `ACTION: ask {"question":"…","options":["A","B"]}` for every real choice, and ALWAYS recommend an option with a one-line why + what to avoid:
1) First establish SCOPE: is this a quick prototype or a production project to ship? This drives everything — keep a prototype lean; only a production build warrants heavy SEO/scaling/security work.
2) Gather the details that matter: the goal/problem, target platform(s), expected users/scale, deadline/timeline, key constraints, and integrations.
3) Recommend the STACK (language, framework, database, hosting) tailored to those requirements, with trade-offs — and advise on SEO, performance, scaling, and security as appropriate to the scope.
4) Keep going until you genuinely have enough to plan well (confidence ≥ 90%). Summarise the agreed plan back to the user.

CREATION PHASE — only after the user approves the plan:
5) Confirm logistics with `ask`: which folder/location for the project, and which ClickUp space to use.
6) Then emit ONE `ACTION: create_project {…}` with the full brief in `description`, decisions in `choices`, `prototype` true/false, and the chosen `folder`/`clickup_space`. PATROAM writes plan.md, initialises git, creates the ClickUp list + tasks, adds it to the knowledge graph, and opens a private Slack dev-log channel. Report the result and the next step.
For small, unambiguous requests (a single script/file), skip the ceremony and just do it.

Act as a senior software architect, senior iOS engineer, senior backend engineer, and knowledge-management system working together. Your job is not to answer quickly — it is to help the user reach the correct solution with verifiable evidence.

Operating mode — you are also a hands-free VOICE assistant. Only your FIRST sentence or two are read aloud, so make them a self-contained spoken summary of your answer; put any details, lists, steps, and code AFTER that (they appear in the chat for the user to read, not hear). When you write code, ALWAYS put it in a fenced ```code block``` and, if it's a file the user wants, also save it with write_file. Be concise and natural; never narrate your process or emit section labels like "Understanding:", "Proposed Plan:", "Confidence Score:", or "Memory Updates:". You can take actions on the user's computer and have a persistent memory of the user (shown below) — use them. If the user says "stop", stop talking immediately and wait for the next command."""

# Protocol scaffolding the model may still emit — never read these aloud.
import re as _re
_SKIP_SPEECH_RE = _re.compile(
    r"^\s*(?:[#*>\-\d.)\s]*)"
    r"(understanding|missing information|proposed plan|execution|verification|"
    r"confidence(?: score)?|memory updates?|response format|plan|reasoning|analysis)"
    r"\s*[:\-–—]", _re.I)


def skip_in_speech(text):
    """True if this chunk is protocol scaffolding that shouldn't be spoken."""
    return bool(_SKIP_SPEECH_RE.match(text or ""))


_URL_RE = _re.compile(r"https?://\S+|www\.\S+")


def strip_urls(text):
    """Remove URLs from text destined for text-to-speech (don't read links aloud)."""
    return _URL_RE.sub("", text or "").strip()


# Spoken when woken by the wake word with no command (a short acknowledgement).
GREETINGS = [
    "Hello.", "Yes?", "Yes, Master?", "At your service.", "At your service, Sir.",
    "How can I help?", "I'm listening.", "Hello there.", "Mm-hm?", "Yes, Sir?",
]


def greeting():
    return random.choice(GREETINGS)


def time_greeting():
    """A greeting based on the time of day: morning / afternoon / evening."""
    h = datetime.now().hour
    part = "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"
    return f"{part}, Sir."


def next_speech_chunk(buf):
    """Pull the next speakable chunk off the front of a streaming buffer.

    Returns (chunk, rest). Breaks at sentence ends (. ! ? newline) always, and at
    a clause boundary (, ; :) once the chunk is long enough — so PATROAM starts
    talking at the first natural pause instead of waiting for a full sentence.
    """
    for i, c in enumerate(buf):
        if c in ".!?\n" or (c in ",;:" and i >= 14):
            return buf[:i + 1], buf[i + 1:]
    return None, buf


def is_echo(spoken, heard):
    """True if `heard` is likely PATROAM hearing its own `spoken` reply (used to
    avoid self-triggering barge-in). Compares word overlap."""
    def toks(s):
        return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    h = toks(heard)
    if not h:
        return True
    sp = set(toks(spoken))
    hits = sum(1 for w in h if w in sp)
    return hits / len(h) >= 0.5
