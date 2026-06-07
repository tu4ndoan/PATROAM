"""Knowledge graph — entities and how they relate.

Stores (subject, relation, object) triples with confidence + timestamp, so
PATROAM can reason over connections (which project uses which technology, who owns
what, what depends on what). The model records relationships with the `relate`
action; relevant triples are injected into context for future questions.

Entities: User, Project, Repository, Feature, Task, Technology, Company, Document.
Relations: USES, OWNS, DEPENDS_ON, IMPLEMENTS, RELATED_TO, BLOCKED_BY (free-form
allowed). Persisted to config.GRAPH_FILE.
"""

import json
import os
import re
import time

from . import config

_WORD = re.compile(r"[a-z0-9]+")
_CACHE = None
MAX_TRIPLES = 1000

# The user is a first-class entity in the graph — this is where "memory about
# you" lives now (no separate memory.json). First-person words map onto it so
# "I like pizza" becomes (You)-[LIKES]->(pizza).
USER = "You"
_FIRST_PERSON = {"i", "me", "my", "myself", "mine"}


def _norm(entity):
    e = (entity or "").strip()
    return USER if e.lower() in _FIRST_PERSON else e


def _load():
    global _CACHE
    if _CACHE is None:
        try:
            with open(config.GRAPH_FILE, encoding="utf-8") as f:
                _CACHE = json.load(f)
        except Exception:
            _CACHE = {"triples": []}
        _CACHE.setdefault("triples", [])
    return _CACHE


def _save():
    try:
        os.makedirs(os.path.dirname(config.GRAPH_FILE), exist_ok=True)
        with open(config.GRAPH_FILE, "w", encoding="utf-8") as f:
            json.dump(_load(), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add(subject, relation, obj, confidence=1.0):
    s = _norm(subject)
    r = (relation or "").strip().upper().replace(" ", "_")
    o = _norm(obj)
    if not s or not r or not o:
        return False
    triples = _load()["triples"]
    for t in triples:
        if t["s"].lower() == s.lower() and t["r"] == r and t["o"].lower() == o.lower():
            t["confidence"] = confidence
            t["ts"] = time.time()
            _save()
            return True
    triples.append({"s": s, "r": r, "o": o, "confidence": confidence, "ts": time.time()})
    _load()["triples"] = triples[-MAX_TRIPLES:]
    _save()
    return True


def remove_triple(subject, obj, relation=None):
    """Remove a specific connection (e.g. 'forget that Trump is handsome').
    If `relation` is None, removes any link between subject and object.
    Matching is case-insensitive. Returns the number of triples removed."""
    s = _norm(subject).lower()
    o = _norm(obj).lower()
    r = (relation or "").strip().upper().replace(" ", "_")
    if not s or not o:
        return 0
    g = _load()
    before = len(g["triples"])
    g["triples"] = [t for t in g["triples"] if not (
        t["s"].lower() == s and t["o"].lower() == o and (not r or t["r"] == r))]
    removed = before - len(g["triples"])
    if removed:
        _save()
    return removed


def forget(entity):
    e = (entity or "").strip().lower()
    if not e:
        return 0
    g = _load()
    before = len(g["triples"])
    g["triples"] = [t for t in g["triples"] if e not in t["s"].lower() and e not in t["o"].lower()]
    removed = before - len(g["triples"])
    if removed:
        _save()
    return removed


def _phrase(t):
    return f"{t['s']} {t['r'].replace('_', ' ').lower()} {t['o']}"


def render_for(text, limit=15):
    """Triples whose subject/object words appear in `text` — for context injection."""
    toks = set(_WORD.findall((text or "").lower()))
    if not toks:
        return ""
    hits = []
    for t in _load()["triples"]:
        ent = set(_WORD.findall(f"{t['s']} {t['o']}".lower()))
        if ent & toks:
            hits.append(t)
    if not hits:
        return ""
    lines = [f"- {_phrase(t)}" for t in hits[:limit]]
    return "Relevant facts from your knowledge graph:\n" + "\n".join(lines)


def summary(limit=15):
    triples = _load()["triples"]
    if not triples:
        return "My knowledge graph is empty so far."
    return "Here's what I know is connected: " + "; ".join(_phrase(t) for t in triples[-limit:]) + "."


# ── memory about the user (lives in the graph, not a separate store) ───────────────
def add_note(text):
    """Remember a free-text fact about the user (one that isn't a clean triple)."""
    text = (text or "").strip()
    return add(USER, "NOTE", text) if text else False


def _user_facts():
    u = USER.lower()
    return [t for t in _load()["triples"] if t["s"].lower() == u or t["o"].lower() == u]


def _fact_phrase(t):
    return t["o"] if t["r"] == "NOTE" else _phrase(t)


def render_profile(limit=60):
    """Always-on block for the system prompt: what PATROAM knows about the user."""
    facts = _user_facts()
    if not facts:
        return "You have no saved facts about the user yet. Learn about them as you chat."
    lines = ["What you remember about the user (use it to personalise your help):"]
    lines += [f"- {_fact_phrase(t)}" for t in facts[-limit:]]
    return "\n".join(lines)


def user_summary(limit=12):
    """Spoken read-back for 'what do you know about me'."""
    facts = _user_facts()
    if not facts:
        return "I don't have anything saved about you yet, Sir."
    return "Here's what I remember about you: " + "; ".join(
        _fact_phrase(t) for t in facts[-limit:]) + "."


def all_triples():
    """Every stored triple — for the inspector / visualizer (read-only copy)."""
    return [{"s": t["s"], "r": t["r"], "o": t["o"],
             "confidence": t.get("confidence", 1.0)} for t in _load()["triples"]]


# ── LLM extraction (documents → triples) ──────────────────────────────────────────
_EXTRACT_PROMPT = (
    "You are an information-extraction engine. From the text below, extract a "
    "knowledge graph of the explicit relationships between entities (people, "
    "projects, companies, technologies, products, places, documents).\n"
    "Return ONLY valid JSON, no prose, in exactly this shape:\n"
    '{"triples":[{"subject":"...","relation":"USES","object":"..."}]}\n'
    "Use SHORT canonical entity names (drop articles like 'the'). Prefer these "
    "relations when they fit: USES, OWNS, DEPENDS_ON, IMPLEMENTS, PART_OF, "
    "CREATED_BY, WORKS_FOR, RELATED_TO, BLOCKED_BY. Only include relationships "
    "the text actually states. If there are none, return {\"triples\":[]}.\n\n"
    "TEXT:\n"
)


def _parse_json(raw):
    """Best-effort JSON extraction from a model reply (tolerates surrounding prose)."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    i, j = raw.find("{"), raw.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(raw[i:j + 1])
        except Exception:
            return {}
    return {}


def extract_into(text, complete, max_chars=6000):
    """Use `complete(prompt)->str` to pull triples from `text` into the graph.
    Returns the number of triples added."""
    if not text or not text.strip() or complete is None:
        return 0
    raw = complete(_EXTRACT_PROMPT + text[:max_chars])
    data = _parse_json(raw)
    added = 0
    for t in (data.get("triples") or []):
        if not isinstance(t, dict):
            continue
        if add(t.get("subject", ""), t.get("relation", "RELATED_TO"), t.get("object", ""),
               confidence=0.8):
            added += 1
    return added
