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


# True once the store was READ successfully (or confirmed absent). While False —
# e.g. the file exists but was locked/corrupt when we tried to read it — _save()
# refuses to write, so a failed load can never wipe the real graph on disk.
_LOAD_OK = False
# Explicit permission to write an empty store (only clear() sets this) — guards
# against any other path accidentally flushing an empty cache over real data.
_ALLOW_EMPTY_WRITE = False


def _load():
    global _CACHE, _LOAD_OK
    if _CACHE is None:
        data = None
        if os.path.exists(config.GRAPH_FILE):
            # Retry a few times: on Windows another PATROAM process shutting down
            # can hold the file for a moment (sharing violation).
            for _ in range(4):
                try:
                    with open(config.GRAPH_FILE, encoding="utf-8") as f:
                        data = json.load(f)
                    _LOAD_OK = True
                    break
                except Exception:
                    time.sleep(0.15)
            if data is None:
                # File exists but can't be read → work from an empty cache in
                # memory, but NEVER save over the real file (_LOAD_OK stays False).
                _LOAD_OK = False
                data = {"triples": []}
        else:
            _LOAD_OK = True                # genuinely new store — writing is fine
            data = {"triples": []}
        _CACHE = data
        _CACHE.setdefault("triples", [])
        _CACHE.setdefault("colors", {})   # {canonical name: "#rrggbb"} custom node colors
        _CACHE.setdefault("layout", {})   # {canonical name: {x,y,z,pinned}} saved node positions
    return _CACHE


def _save():
    if not _LOAD_OK:
        return                             # never overwrite a store we failed to read
    try:
        data = _load()
        # Catastrophic-shrink guard: an EMPTY cache never overwrites a real store
        # unless clear() explicitly allowed it.
        if not data.get("triples") and not _ALLOW_EMPTY_WRITE:
            try:
                if (os.path.exists(config.GRAPH_FILE)
                        and os.path.getsize(config.GRAPH_FILE) > 200):
                    return
            except Exception:
                pass
        os.makedirs(os.path.dirname(config.GRAPH_FILE), exist_ok=True)
        # Atomic write (tmp + replace) so a crash mid-write can't corrupt the file.
        tmp = config.GRAPH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config.GRAPH_FILE)
    except Exception:
        pass


# ── new-node confirmation ─────────────────────────────────────────────────────
# Facts the model PROPOSED that would introduce an entity the graph has never
# seen. They wait here until you approve them, so a bad extraction can't quietly
# invent nodes (this is how "Project USES Stripe" and "PATROAM USES Three.js"
# got in). Trusted callers — real repos on disk, a note you just wrote, a link
# you added by hand — bypass this entirely.
_PENDING = []


def _known_entities():
    out = set()
    for t in _load()["triples"]:
        out.add(_canon(t["s"]))
        out.add(_canon(t["o"]))
    return out


def pending():
    """Proposed facts awaiting your approval (newest last)."""
    return [dict(p) for p in _PENDING]


def pending_nodes():
    """Just the NEW entity names waiting for approval, deduped in order."""
    known, out = _known_entities(), []
    for p in _PENDING:
        for n in (p["s"], p["o"]):
            if _canon(n) not in known and n not in out:
                out.append(n)
    return out


def approve_pending(names=None):
    """Commit pending facts. `names` limits it to those touching those entities
    (case/spacing-insensitive); None approves everything. Returns count added."""
    global _PENDING
    if not _PENDING:
        return 0
    keep, take = [], []
    want = {_canon(n) for n in (names or [])}
    for p in _PENDING:
        if not want or _canon(p["s"]) in want or _canon(p["o"]) in want:
            take.append(p)
        else:
            keep.append(p)
    _PENDING = keep
    n = 0
    for p in take:
        if add(p["s"], p["r"], p["o"], confidence=p.get("confidence", 1.0),
               doc=p.get("doc"), trusted=True):
            n += 1
    return n


def reject_pending(names=None):
    """Discard pending facts (all, or only those touching `names`)."""
    global _PENDING
    before = len(_PENDING)
    if names is None:
        _PENDING = []
        return before
    want = {_canon(n) for n in names}
    _PENDING = [p for p in _PENDING
                if not (_canon(p["s"]) in want or _canon(p["o"]) in want)]
    return before - len(_PENDING)


def add(subject, relation, obj, confidence=1.0, doc=None, trusted=False):
    """Add a triple. `doc` is the source document (for grouping/clustering in the
    visualizer); None means it came from you/conversation.

    When the fact would introduce a brand-new entity and it wasn't `trusted`,
    it is held in `pending()` for confirmation instead of being written."""
    s = _norm(subject)
    r = (relation or "").strip().upper().replace(" ", "_")
    o = _norm(obj)
    if not s or not r or not o:
        return False
    if (not trusted) and config.CONFIRM_NEW_NODES:
        known = _known_entities()
        if _canon(s) not in known or _canon(o) not in known:
            item = {"s": s, "r": r, "o": o, "confidence": confidence, "doc": doc}
            if not any(p["s"] == s and p["r"] == r and p["o"] == o for p in _PENDING):
                _PENDING.append(item)
            return False        # not written yet — awaiting approval
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
        # drop saved positions of nodes that no longer exist
        live = {_canon(n) for t in g["triples"] for n in (t["s"], t["o"])}
        g["layout"] = {k: v for k, v in g.get("layout", {}).items() if k in live}
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
        # carry the saved position over to the new name
        lay = g.get("layout", {})
        if oc in lay:
            lay[_canon(newname)] = lay.pop(oc)
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
    global _ALLOW_EMPTY_WRITE
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
        _ALLOW_EMPTY_WRITE = True          # an explicit wipe IS allowed to persist
        try:
            _save()
        finally:
            _ALLOW_EMPTY_WRITE = False
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
    return add(USER, "NOTE", text, trusted=True) if text else False


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


def get_layout():
    """Saved node positions {canonical name: {x, y, z, pinned}} so the graph keeps
    the layout you arranged across restarts."""
    return dict(_load().get("layout", {}))


def save_layout(positions):
    """Persist node positions from the visualizer. `positions` is
    {display name: {x, y, z, pinned}}; a value of None drops that node's saved
    position (falls back to auto-layout). Keyed canonically. Returns True on save."""
    if not isinstance(positions, dict):
        return False
    g = _load()
    lay = g.setdefault("layout", {})
    for name, pos in positions.items():
        c = _canon(name)
        if not c:
            continue
        if pos is None:
            lay.pop(c, None)
            continue
        try:
            lay[c] = {
                "x": float(pos.get("x", 0.0)),
                "y": float(pos.get("y", 0.0)),
                "z": float(pos.get("z", 0.0)),
                "pinned": bool(pos.get("pinned", False)),
            }
        except (TypeError, ValueError):
            continue
    _save()
    return True


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
    return add(parent, relation, child, trusted=True)


def add_project(name, description="", facts=None, doc=None):
    """Register a project under the Projects node, with an optional description and
    extra (relation, object) facts (e.g. choices, stack). Tagged to `doc`."""
    name = _norm(name)
    if not name:
        return False
    add(PROJECTS, "HAS_PROJECT", name, doc=doc, trusted=True)
    if description:
        add(name, "DESCRIBED_AS", description.strip()[:200], confidence=0.9, doc=doc, trusted=True)
    for rel, obj in (facts or []):
        add(name, rel, obj, doc=doc, trusted=True)
    return True


def add_note_entry(title, text="", facts=None, doc=None):
    """Record a note's own facts in the graph.

    Notes used to hang off a "Notes" container node, with the whole body dumped
    in as a triple. They have their own panel now, so the graph keeps only what
    is actually knowledge — the facts extracted from a note — and no longer
    grows a Notes hub nobody navigated through."""
    title = _norm(title)
    if not title:
        return False
    for rel, obj in (facts or []):
        add(title, rel, obj, doc=doc, trusted=True)
    return True


def drop_notes_node():
    """Remove the old Notes hub and its note-body triples (one-time cleanup)."""
    g = _load()
    if not _LOAD_OK:
        return 0
    n = NOTES.lower()
    before = len(g["triples"])
    g["triples"] = [t for t in g["triples"]
                    if not (t["s"].lower() == n and t["r"] == "HAS_NOTE")
                    and t["r"] != "NOTE"]
    gone = before - len(g["triples"])
    if gone:
        # Drop any node left with no edges at all (the note titles themselves).
        linked = {_canon(t["s"]) for t in g["triples"]} | {_canon(t["o"]) for t in g["triples"]}
        for key in ("layout", "colors"):
            if isinstance(g.get(key), dict):
                g[key] = {k: v for k, v in g[key].items() if _canon(k) in linked}
        _save()
    return gone


def projects():
    """Names of projects registered under the Projects node."""
    p = PROJECTS.lower()
    return [t["o"] for t in _load()["triples"]
            if t["s"].lower() == p and t["r"] == "HAS_PROJECT"]


def backup():
    """Write a timestamped copy of the graph to BACKUP_DIR and prune old ones.
    Returns the backup path, or None if skipped/failed.

    IMPORTANT: copies the ON-DISK file as-is — no flush first. A flush here once
    wiped the store: when the launch-time read failed (file briefly locked by the
    previous instance), the empty in-memory cache overwrote the real graph, and
    the backup then archived the wiped file. Also skips empty/corrupt stores so
    a bad launch can't rotate the good backups away."""
    import datetime
    import shutil
    try:
        if (not os.path.exists(config.GRAPH_FILE)
                or os.path.getsize(config.GRAPH_FILE) < 200):
            return None                       # nothing worth backing up
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


def sync_projects():
    """Make the Projects node reflect the REAL projects (git repos in the GitHub
    root + ClickUp lists), dropping stale/dummy entries. Returns the real names."""
    from . import manage
    real = manage.discover_projects()
    real_norm = {_canon(r["name"]) for r in real}
    g = _load()
    p = PROJECTS.lower()
    linked = [t["o"] for t in g["triples"] if t["s"].lower() == p and t["r"] == "HAS_PROJECT"]
    stale = {_canon(n) for n in linked if _canon(n) not in real_norm}
    if stale:
        # Drop stale HAS_PROJECT links AND those dummy project nodes' own facts —
        # precise (exact canonical match), never substring, so nothing else is hit.
        g["triples"] = [t for t in g["triples"] if not (
            (t["s"].lower() == p and t["r"] == "HAS_PROJECT" and _canon(t["o"]) in stale)
            or _canon(t["s"]) in stale)]
    for r in real:
        add(PROJECTS, "HAS_PROJECT", r["name"], trusted=True)
    _save()
    return [r["name"] for r in real]


def index_codebase(max_dirs=5, max_files=5):
    """Put each project's SHAPE on the graph: its main directories and key files.

    Node names are namespaced ("tu4ndoan/src", not "src") on purpose — every
    Next.js project has an `app/` and a README.md, so bare names would fuse all
    projects into one meaningless hub instead of clustering per project.

    Tech stack is deliberately NOT graphed (it lives in the project view); this
    keeps the graph about structure. Re-running replaces the previous structure
    triples, so renamed/deleted files don't linger. Returns triples added."""
    from . import codebase, manage
    g = _load()
    added = 0
    for rec in manage.discover_projects():
        folder, name = rec.get("folder"), rec.get("name")
        if not folder or not name:
            continue
        info = codebase.analyze(folder)
        if not info:
            continue
        pref = _norm(name) + "/"
        # Drop this project's previous structure facts (idempotent re-index).
        pc = _canon(name)
        g["triples"] = [t for t in g["triples"] if not (
            t["r"] in ("HAS_MODULE", "KEY_FILE") and _canon(t["s"]) == pc)]
        for d in info.get("structure", [])[:max_dirs]:
            if add(name, "HAS_MODULE", pref + d["name"], doc=name, trusted=True):
                added += 1
        for f in info.get("key_files", [])[:max_files]:
            if add(name, "KEY_FILE", pref + f["name"], doc=name, trusted=True):
                added += 1
    _save()
    return added


def index_notes():
    """Link each file in NOTES_DIR under Notes and LLM-extract its facts."""
    from . import llm
    fn = llm.complete if llm.available() else None
    nd = config.NOTES_DIR
    if not os.path.isdir(nd):
        return 0
    from . import notes as _notes
    added = 0
    for f in os.listdir(nd):
        if os.path.splitext(f)[1].lower() not in (".md", ".txt"):
            continue
        txt = _read_text(os.path.join(nd, f))
        title = _notes.title_of(txt, os.path.splitext(f)[0])   # real title (keeps Vietnamese)
        body = _notes._body(txt)
        # Only the FACTS inside a note reach the graph — the note itself lives
        # in the Notes panel, which is where you actually read it.
        if fn and (body or txt).strip():
            added += extract_into(body or txt, fn, doc=title)
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
