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
    s = (subject or "").strip()
    r = (relation or "").strip().upper().replace(" ", "_")
    o = (obj or "").strip()
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
