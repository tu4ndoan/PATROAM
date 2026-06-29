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
# Top-level container nodes (siblings of the You memory node).
PROJECTS = "Projects"
NOTES = "Notes"
_FIRST_PERSON = {"i", "me", "my", "myself", "mine"}


def _norm(entity):
    e = (entity or "").strip()
    if e.lower() in _FIRST_PERSON:
        return USER
    # Canonicalise display so "Pham_Nhat_Vuong" and "Pham Nhat Vuong" don't
    # become two separate nodes: underscores → spaces, collapse whitespace.
    return re.sub(r"\s+", " ", e.replace("_", " ")).strip()


def _canon(name):
    """A loose key for spotting the SAME entity written differently
    (case / underscores / hyphens / spacing)."""
    return re.sub(r"[\s_\-]+", " ", (name or "").lower()).strip()


def _load():
    global _CACHE
    if _CACHE is None:
        try:
            with open(config.GRAPH_FILE, encoding="utf-8") as f:
                _CACHE = json.load(f)
        except Exception:
            _CACHE = {"triples": []}
        _CACHE.setdefault("triples", [])
        _CACHE.setdefault("colors", {})   # {canonical name: "#rrggbb"} custom node colors
    return _CACHE


def _save():
    try:
        os.makedirs(os.path.dirname(config.GRAPH_FILE), exist_ok=True)
        with open(config.GRAPH_FILE, "w", encoding="utf-8") as f:
            json.dump(_load(), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add(subject, relation, obj, confidence=1.0, doc=None):
    """Add a triple. `doc` is the source document (for grouping/clustering in the
    visualizer); None means it came from you/conversation."""
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
            if doc:
                t["doc"] = doc
            _save()
            return True
    new = {"s": s, "r": r, "o": o, "confidence": confidence, "ts": time.time()}
    if doc:
        new["doc"] = doc
    triples.append(new)
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


# ── merging duplicate nodes ───────────────────────────────────────────────────────
def _dedupe(g):
    """Drop self-loops and collapse identical triples (keeping the latest)."""
    seen, out = {}, []
    for t in g["triples"]:
        if t["s"].lower() == t["o"].lower():
            continue                         # a node related to itself: meaningless
        key = (t["s"].lower(), t["r"], t["o"].lower())
        if key in seen:
            if t.get("ts", 0) >= seen[key].get("ts", 0):
                seen[key]["confidence"] = max(seen[key].get("confidence", 1.0),
                                              t.get("confidence", 1.0))
            continue
        seen[key] = t
        out.append(t)
    g["triples"] = out


def merge(source, target):
    """Merge the `source` node into `target`: every connection of `source` (as
    subject OR object) is re-pointed to `target`, then duplicates/self-loops are
    cleaned. No connections are lost. Source is matched LOOSELY (any spelling /
    case / underscores), so old variants already in the graph are caught.
    Returns the number of triples updated."""
    src_c, tgt = _canon(source), _norm(target)
    if not src_c or not tgt:
        return 0
    g = _load()
    changed = 0
    for t in g["triples"]:
        hit = False
        if _canon(t["s"]) == src_c and t["s"] != tgt:
            t["s"], hit = tgt, True
        if _canon(t["o"]) == src_c and t["o"] != tgt:
            t["o"], hit = tgt, True
        if hit:
            changed += 1
    if changed:
        _dedupe(g)
        _save()
    return changed


def rename(old, new):
    """Rename a node everywhere (subject & object), then dedupe. Loose match on
    `old` (any spelling). Returns triples changed."""
    oc, newname = _canon(old), _norm(new)
    if not oc or not newname:
        return 0
    g = _load()
    changed = 0
    for t in g["triples"]:
        hit = False
        if _canon(t["s"]) == oc:
            t["s"], hit = newname, True
        if _canon(t["o"]) == oc:
            t["o"], hit = newname, True
        if hit:
            changed += 1
    if changed:
        _dedupe(g)
        _save()
    return changed


def _entities():
    seen, out = set(), []
    for t in _load()["triples"]:
        for n in (t["s"], t["o"]):
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


def _pick_canonical(variants):
    """Choose the nicest display form among duplicates: prefer spaced (no
    underscore) names, then proper-case ('Trump') over ALL-CAPS or lowercase,
    then the longer form."""
    pool = [v for v in variants if "_" not in v] or variants

    def score(v):
        letters = [c for c in v if c.isalpha()]
        has_upper = any(c.isupper() for c in letters)
        all_upper = bool(letters) and all(c.isupper() for c in letters)
        return (has_upper and not all_upper, has_upper, len(v))

    return max(pool, key=score)


def merge_duplicates():
    """Auto-merge nodes that are the same entity in different syntax (case /
    underscores / spacing). Returns a list of (kept, [merged_away, …])."""
    groups = {}
    for n in _entities():
        groups.setdefault(_canon(n), []).append(n)
    merges = []
    for variants in groups.values():
        if len(variants) < 2:
            continue
        keep = _pick_canonical(variants)
        for v in variants:
            if v != keep:
                merge(v, keep)
        merges.append((keep, [v for v in variants if v != keep]))
    return merges


def clear(keep_user=True):
    """Wipe the knowledge graph. By default KEEPS your personal memory (facts on
    the 'You' node) and removes everything else (e.g. garbled document facts).
    Pass keep_user=False to wipe absolutely everything. Returns count removed."""
    g = _load()
    before = len(g["triples"])
    if keep_user:
        u = USER.lower()
        g["triples"] = [t for t in g["triples"]
                        if t["s"].lower() == u or t["o"].lower() == u]
    else:
        g["triples"] = []
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


def set_color(name, color):
    """Persist a custom colour (hex '#rrggbb') for a node, so it survives restarts.
    Keyed by the canonical name so case/spacing variants share the colour."""
    c = _canon(name)
    if not c:
        return False
    g = _load()
    if color:
        g["colors"][c] = color
    else:
        g["colors"].pop(c, None)   # empty colour = reset to default
    _save()
    return True


def get_colors():
    """The custom node-colour map {canonical name: '#rrggbb'} for the visualizer."""
    return dict(_load().get("colors", {}))


def node_docs(name):
    """The source documents a node came from (for showing the doc's images when
    you click the node). Loose match on the node name."""
    c = _canon(name)
    out = []
    for t in _load()["triples"]:
        d = t.get("doc")
        if d and d not in out and (_canon(t["s"]) == c or _canon(t["o"]) == c):
            out.append(d)
    return out


# ── Projects & Notes (top-level containers) + backups ─────────────────────────────
def link_under(parent, child, relation="HAS"):
    """Attach `child` under a top-level container node (Projects / Notes)."""
    return add(parent, relation, child)


def add_project(name, description="", facts=None, doc=None):
    """Register a project under the Projects node, with an optional description and
    extra (relation, object) facts (e.g. choices, stack). Tagged to `doc`."""
    name = _norm(name)
    if not name:
        return False
    add(PROJECTS, "HAS_PROJECT", name, doc=doc)
    if description:
        add(name, "DESCRIBED_AS", description.strip()[:200], confidence=0.9, doc=doc)
    for rel, obj in (facts or []):
        add(name, rel, obj, doc=doc)
    return True


def add_note_entry(title, text="", facts=None, doc=None):
    """Register a note under the Notes node (title + free text + optional facts)."""
    title = _norm(title)
    if not title:
        return False
    add(NOTES, "HAS_NOTE", title, doc=doc)
    if text:
        add(title, "NOTE", text.strip()[:500], doc=doc)
    for rel, obj in (facts or []):
        add(title, rel, obj, doc=doc)
    return True


def projects():
    """Names of projects registered under the Projects node."""
    p = PROJECTS.lower()
    return [t["o"] for t in _load()["triples"]
            if t["s"].lower() == p and t["r"] == "HAS_PROJECT"]


def backup():
    """Write a timestamped copy of the graph to BACKUP_DIR and prune old ones.
    Returns the backup path, or None on failure."""
    import datetime
    import shutil
    _save()                                   # flush current state to disk first
    try:
        os.makedirs(config.BACKUP_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(config.BACKUP_DIR, f"graph-{ts}.json")
        shutil.copy2(config.GRAPH_FILE, dst)
    except Exception:
        return None
    _prune_backups()
    return dst


def _prune_backups(keep=None):
    import glob
    keep = keep or config.BACKUP_KEEP
    files = sorted(glob.glob(os.path.join(config.BACKUP_DIR, "graph-*.json")))
    for f in files[:-keep] if keep > 0 else []:
        try:
            os.remove(f)
        except Exception:
            pass


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def index_projects():
    """Scan the workspace for <project>/README.md, link each under Projects, and
    LLM-extract its facts. Returns triples added. Safe/idempotent."""
    from . import llm
    fn = llm.complete if llm.available() else None
    ws = config.WORKSPACE_DIR
    if not os.path.isdir(ws):
        return 0
    added = 0
    for name in os.listdir(ws):
        rd = os.path.join(ws, name, "README.md")
        if not os.path.isfile(rd):
            continue
        link_under(PROJECTS, name, "HAS_PROJECT")
        txt = _read_text(rd)
        if fn and txt.strip():
            added += extract_into(txt, fn, doc=name)
    return added


def index_notes():
    """Link each file in NOTES_DIR under Notes and LLM-extract its facts."""
    from . import llm
    fn = llm.complete if llm.available() else None
    nd = config.NOTES_DIR
    if not os.path.isdir(nd):
        return 0
    added = 0
    for f in os.listdir(nd):
        if os.path.splitext(f)[1].lower() not in (".md", ".txt"):
            continue
        title = os.path.splitext(f)[0]
        txt = _read_text(os.path.join(nd, f))
        add_note_entry(title, txt, doc=title)
        if fn and txt.strip():
            added += extract_into(txt, fn, doc=title)
    return added


def all_triples():
    """Every stored triple — for the inspector / visualizer (read-only copy)."""
    return [{"s": t["s"], "r": t["r"], "o": t["o"],
             "confidence": t.get("confidence", 1.0),
             "doc": t.get("doc")} for t in _load()["triples"]]


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


def extract_into(text, complete, max_chars=6000, doc=None):
    """Use `complete(prompt)->str` to pull triples from `text` into the graph.
    `doc` tags them with their source document (for clustering). Returns count."""
    if not text or not text.strip() or complete is None:
        return 0
    raw = complete(_EXTRACT_PROMPT + text[:max_chars])
    data = _parse_json(raw)
    added = 0
    for t in (data.get("triples") or []):
        if not isinstance(t, dict):
            continue
        if add(t.get("subject", ""), t.get("relation", "RELATED_TO"), t.get("object", ""),
               confidence=0.8, doc=doc):
            added += 1
    return added
