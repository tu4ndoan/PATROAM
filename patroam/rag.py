"""RAG — retrieval over the user's own documents.

Drop files in config.KNOWLEDGE_DIR and call ingest(): PATROAM chunks them,
embeds each chunk (via Ollama embeddings if EMBED_MODEL is available, else a
keyword fallback), and stores an index. retrieve()/context_for() return the most
relevant passages for a query, which the Agent injects into context so answers
are grounded in the user's documents rather than model memory.
"""

import json
import math
import os
import re
import urllib.request

from . import config

_WORD = re.compile(r"[a-z0-9]+")
_TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".py", ".js", ".ts", ".json",
             ".csv", ".html", ".htm", ".yaml", ".yml", ".log", ".pdf"}
_CACHE = None


# ── reading & chunking ──────────────────────────────────────────────────────────
def _read_file(path):
    if path.lower().endswith(".pdf"):
        try:
            import pypdf
            return "\n".join((pg.extract_text() or "") for pg in pypdf.PdfReader(path).pages)
        except Exception:
            return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _chunk(text, size=800, overlap=120):
    text = text.strip()
    chunks, i, n = [], 0, len(text)
    while i < n:
        end = min(n, i + size)
        nl = text.rfind("\n", i + size // 2, end)   # prefer a line break
        if nl > i:
            end = nl
        piece = text[i:end].strip()
        if piece:
            chunks.append(piece)
        i = end - overlap if end - overlap > i else end
    return chunks


# ── embeddings (Ollama, optional) ───────────────────────────────────────────────
def _embed(text):
    if not config.EMBED_MODEL:
        return None
    try:
        payload = json.dumps({"model": config.EMBED_MODEL, "prompt": text}).encode()
        req = urllib.request.Request(
            f"{config.OLLAMA_URL}/api/embeddings", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("embedding")
    except Exception:
        return None


# ── vector database (ChromaDB, optional) ────────────────────────────────────────
def _use_chroma():
    if not config.EMBED_MODEL:
        return False
    try:
        import chromadb  # noqa: F401
        return True
    except Exception:
        return False


def _chroma_col():
    import chromadb
    client = chromadb.PersistentClient(path=config.CHROMA_DIR)
    return client.get_or_create_collection("patroam")


# ── index ────────────────────────────────────────────────────────────────────────
def ensure_dir():
    os.makedirs(config.KNOWLEDGE_DIR, exist_ok=True)
    readme = os.path.join(config.KNOWLEDGE_DIR, "README.txt")
    if not os.listdir(config.KNOWLEDGE_DIR):
        try:
            with open(readme, "w", encoding="utf-8") as f:
                f.write("Drop documents here (.txt .md .py .json .csv .html .pdf …), "
                        "then tell PATROAM: \"index my docs\".")
        except Exception:
            pass


def _build_graph(docs, llm):
    """Extract a knowledge graph from documents using the active model.
    `docs` is [(source, full_text)]. Returns triples added."""
    from . import graph, llm as _llm
    fn = llm if callable(llm) else (_llm.complete if _llm.available() else None)
    if fn is None:
        return 0
    added = 0
    for _src, full in docs:
        # Cover the document in a few windows so long files aren't truncated away,
        # but cap total windows per doc to bound time/cost.
        for w in range(0, min(len(full), 6000 * 4), 6000):
            added += graph.extract_into(full[w:w + 6000], fn)
    return added


def ingest(llm=None):
    """(Re)build the index from KNOWLEDGE_DIR, and (if a model is available)
    extract a knowledge graph from the documents. Returns (chunks, files, triples).

    `llm` is an optional (prompt, system=None)->str completion function; when
    omitted, the active model registered via patroam.llm is used if present."""
    global _CACHE
    ensure_dir()
    files = []
    for dp, _, fns in os.walk(config.KNOWLEDGE_DIR):
        for fn in fns:
            if fn == "README.txt":
                continue
            if os.path.splitext(fn)[1].lower() in _TEXT_EXT:
                files.append(os.path.join(dp, fn))

    # Read each file once: keep the full text (for graph extraction) and chunk it.
    pieces = []   # (text, source)
    docs = []     # (source, full_text)
    for path in files:
        rel = os.path.relpath(path, config.KNOWLEDGE_DIR)
        full = _read_file(path)
        docs.append((rel, full))
        for piece in _chunk(full):
            pieces.append((piece, rel))

    # Build the knowledge graph from the documents (LLM extraction).
    triples = _build_graph(docs, llm)

    # Preferred path: a real vector database (ChromaDB) with Ollama embeddings.
    if pieces and _use_chroma():
        embs = [_embed(t) for t, _ in pieces]
        if all(e is not None for e in embs):
            try:
                import chromadb
                client = chromadb.PersistentClient(path=config.CHROMA_DIR)
                try:
                    client.delete_collection("patroam")
                except Exception:
                    pass
                col = client.get_or_create_collection("patroam")
                col.add(
                    ids=[str(i) for i in range(len(pieces))],
                    documents=[t for t, _ in pieces],
                    embeddings=embs,
                    metadatas=[{"source": s} for _, s in pieces],
                )
                _CACHE = {"chunks": [], "backend": "chroma"}
                with open(config.RAG_INDEX_FILE, "w", encoding="utf-8") as f:
                    json.dump(_CACHE, f)
                return len(pieces), len(files), triples
            except Exception:
                pass   # fall through to the JSON store

    # Fallback: JSON index (with embeddings if available, else keyword search).
    chunks = [{"text": t, "source": s, "emb": _embed(t)} for t, s in pieces]
    _CACHE = {"chunks": chunks}
    try:
        with open(config.RAG_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f)
    except Exception:
        pass
    return len(chunks), len(files), triples


def _load():
    global _CACHE
    if _CACHE is None:
        try:
            with open(config.RAG_INDEX_FILE, encoding="utf-8") as f:
                _CACHE = json.load(f)
        except Exception:
            _CACHE = {"chunks": []}
    return _CACHE


def available():
    if _use_chroma():
        try:
            if _chroma_col().count() > 0:
                return True
        except Exception:
            pass
    return bool(_load().get("chunks"))


# ── retrieval ────────────────────────────────────────────────────────────────────
def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def retrieve(query, k=None):
    k = k or config.RAG_TOP_K
    # Vector DB first.
    if _use_chroma():
        try:
            col = _chroma_col()
            if col.count() > 0:
                qe = _embed(query)
                if qe:
                    res = col.query(query_embeddings=[qe], n_results=k)
                    docs = (res.get("documents") or [[]])[0]
                    metas = (res.get("metadatas") or [[]])[0]
                    return [{"text": d, "source": (m or {}).get("source", "")}
                            for d, m in zip(docs, metas)]
        except Exception:
            pass
    chunks = _load().get("chunks", [])
    if not chunks:
        return []
    qemb = _embed(query) if any(c.get("emb") for c in chunks) else None
    if qemb:
        scored = [(_cosine(qemb, c.get("emb")), c) for c in chunks]
    else:
        qwords = set(_WORD.findall(query.lower()))
        scored = [(_score_lex(qwords, c["text"]), c) for c in chunks]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for s, c in scored[:k] if s > 0]


def _score_lex(qwords, text):
    if not qwords:
        return 0.0
    words = set(_WORD.findall(text.lower()))
    return sum(1 for w in qwords if w in words) / len(qwords)


def stats():
    """Snapshot of the current index for the inspector: backend, chunk count, sources."""
    if _use_chroma():
        try:
            col = _chroma_col()
            cnt = col.count()
            if cnt:
                got = col.get()
                metas = got.get("metadatas") or []
                sources = sorted({(m or {}).get("source", "") for m in metas if m})
                return {"backend": "ChromaDB (vector DB)", "chunks": cnt,
                        "sources": [s for s in sources if s]}
        except Exception:
            pass
    chunks = _load().get("chunks", [])
    sources = sorted({c.get("source", "") for c in chunks})
    if not chunks:
        backend = "empty — no documents indexed yet"
    elif any(c.get("emb") for c in chunks):
        backend = "JSON index + embeddings"
    else:
        backend = "JSON index (keyword search)"
    return {"backend": backend, "chunks": len(chunks),
            "sources": [s for s in sources if s]}


def search(query, k=None):
    """Like retrieve() but keeps the relevance score — used by the inspector to
    prove retrieval works. Returns [{text, source, score}]."""
    k = k or config.RAG_TOP_K
    if _use_chroma():
        try:
            col = _chroma_col()
            if col.count() > 0:
                qe = _embed(query)
                if qe:
                    res = col.query(query_embeddings=[qe], n_results=k)
                    docs = (res.get("documents") or [[]])[0]
                    metas = (res.get("metadatas") or [[]])[0]
                    dists = (res.get("distances") or [[]])[0]
                    out = []
                    for i, d in enumerate(docs):
                        dist = dists[i] if i < len(dists) else None
                        score = round(max(0.0, 1 - dist), 3) if dist is not None else None
                        src = (metas[i] or {}).get("source", "") if i < len(metas) else ""
                        out.append({"text": d, "source": src, "score": score})
                    return out
        except Exception:
            pass
    chunks = _load().get("chunks", [])
    if not chunks:
        return []
    qemb = _embed(query) if any(c.get("emb") for c in chunks) else None
    if qemb:
        scored = [(_cosine(qemb, c.get("emb")), c) for c in chunks]
    else:
        qwords = set(_WORD.findall(query.lower()))
        scored = [(_score_lex(qwords, c["text"]), c) for c in chunks]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"text": c["text"], "source": c.get("source", ""), "score": round(s, 3)}
            for s, c in scored[:k] if s > 0]


def context_for(query, k=None, max_chars=2200):
    """A context block of the most relevant passages, or '' if none."""
    hits = retrieve(query, k)
    parts, total = [], 0
    for c in hits:
        block = f"[Source: {c['source']}]\n{c['text']}"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    if not parts:
        return ""
    return ("Relevant passages retrieved from the user's knowledge base. Answer their "
            "question using ONLY this evidence and cite the source filename. If it does "
            "not contain the answer, say \"Insufficient evidence found.\":\n\n"
            + "\n\n---\n\n".join(parts))
