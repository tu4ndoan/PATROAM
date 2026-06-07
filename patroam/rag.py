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


def ingest():
    """(Re)build the index from KNOWLEDGE_DIR. Returns (chunks, files)."""
    global _CACHE
    ensure_dir()
    files = []
    for dp, _, fns in os.walk(config.KNOWLEDGE_DIR):
        for fn in fns:
            if fn == "README.txt":
                continue
            if os.path.splitext(fn)[1].lower() in _TEXT_EXT:
                files.append(os.path.join(dp, fn))

    # Gather chunks (without embeddings yet).
    pieces = []   # (text, source)
    for path in files:
        rel = os.path.relpath(path, config.KNOWLEDGE_DIR)
        for piece in _chunk(_read_file(path)):
            pieces.append((piece, rel))

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
                return len(pieces), len(files)
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
    return len(chunks), len(files)


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
