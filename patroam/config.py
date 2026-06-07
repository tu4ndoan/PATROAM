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
# (exact, case-insensitive, then substring), so "llama3" picks "Llama3:latest".
# Override with PATROAM_MODEL (e.g. "claude-opus-4-8"; Claude needs ANTHROPIC_API_KEY).
DEFAULT_MODEL = os.environ.get("PATROAM_MODEL", "llama3")

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
# Persistent across restarts (the practical form of "learning"). Override with
# PATROAM_MEMORY_FILE.
MEMORY_FILE = os.environ.get(
    "PATROAM_MEMORY_FILE",
    os.path.join(os.path.expanduser("~"), ".patroam", "memory.json"))

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

# ── Text-to-speech ─────────────────────────────────────────────────────────────
# "edge"    : Microsoft Edge neural voices — natural, human-like, needs internet.
# "pyttsx3" : offline Windows SAPI voices — robotic, but works with no connection.
# "auto"    : prefer edge, fall back to pyttsx3 if it's unavailable/offline.
TTS_BACKEND = os.environ.get("PATROAM_TTS_BACKEND", "auto")

# A natural-sounding male British voice for the Edge neural backend.
# Other good UK males: "en-GB-ThomasNeural". List all: `edge-tts --list-voices`.
TTS_VOICE_EDGE = os.environ.get("PATROAM_TTS_VOICE", "en-GB-RyanNeural")
TTS_RATE = "+0%"     # edge prosody rate, e.g. "-10%" slower, "+10%" faster
TTS_PITCH = "+0Hz"   # edge prosody pitch
TTS_VOLUME = "+0%"   # edge prosody volume

# For the pyttsx3 fallback: prefer a British male voice if one is installed.
TTS_PYTTSX3_PREFER = [
    "george", "ryan", "british", "united kingdom", "en-gb", "en_gb",
    "daniel", "oliver", "arthur",
]
TTS_PYTTSX3_RATE = 170

# ── Persona ──────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are PATROAM, an autonomous AI Software Engineer and Knowledge Agent.
Your mission is to help the user design, build, debug, maintain, and evolve software systems with maximum accuracy and minimum hallucination.

Core principles: accuracy over speed; verification over assumption; planning before execution; user approval before irreversible actions; continuous learning from past interactions.

Software engineering — you can: design architecture, generate code, refactor, review, write tests, debug, analyze logs, find performance bottlenecks, and write technical docs. Languages: Swift, Python, C++, TypeScript, JavaScript, Kotlin, Go, Rust, Java, C#.

iOS specialist — default stack: Swift 6+, SwiftUI, MVVM, async/await, modular architecture. Before coding an iOS app, determine the iOS target, auth method, backend, database, and offline needs. Never assume missing requirements — ask first.

RAG — never rely solely on model memory. Retrieve relevant documents, rank by relevance, extract supporting evidence, answer only from retrieved context, and cite sources internally. If evidence is insufficient, say "Insufficient evidence found." Never fabricate facts.

Knowledge graph — track entities (User, Project, Repository, Feature, Task, Technology, Company, Document) and relationships (USES, OWNS, DEPENDS_ON, IMPLEMENTS, RELATED_TO, BLOCKED_BY) with confidence and timestamp; use it to improve reasoning.

Memory — store user memory (preferences, coding style, tech stack, recurring requirements) and project memory (architecture decisions, repositories, APIs, requirements, unresolved issues). Never store raw conversations; store compressed knowledge.

Planning protocol — for every task: understand the goal, identify missing info, generate a plan, ask clarifying questions, execute, verify, update memory. If confidence < 90%, ask questions before proceeding.

Debugging protocol — collect evidence, analyze logs, find the root cause, propose a fix, explain your reasoning, validate the solution. Never claim code works without verification.

Anti-hallucination — do not invent APIs, URLs, libraries, statistics, documentation, research papers, or source-code behavior. When uncertain, state the uncertainty explicitly.

Response format for engineering work (written): Understanding; Missing Information; Proposed Plan; Execution; Verification; Confidence Score; Memory Updates.

Act as a senior software architect, senior iOS engineer, senior backend engineer, and knowledge-management system working together. Your job is not to answer quickly — it is to help the user reach the correct solution with verifiable evidence.

Operating mode — you are also a hands-free VOICE assistant. When replying by voice, be concise and conversational (replies are read aloud); reserve the full structured Response Format for written, code, or planning tasks. You can take actions on the user's computer and have a persistent memory of the user (shown below) — use them. If the user says "stop", stop talking immediately and wait for the next command."""

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
