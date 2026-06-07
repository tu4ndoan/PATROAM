"""Proof that PATROAM's RAG and knowledge graph actually work.

Run:  python verify_rag_graph.py

Uses a throwaway sandbox (temp dirs) so it never touches your real ~/.patroam data.
It indexes a sample document, retrieves from it, records graph relationships, and
shows that BOTH end up in the exact system prompt the model receives.
"""

import os
import shutil
import tempfile

# Sandbox — point all stores at a temp dir so this demo is isolated.
_tmp = tempfile.mkdtemp(prefix="patroam_verify_")
os.environ["PATROAM_KNOWLEDGE_DIR"] = os.path.join(_tmp, "knowledge")
os.environ["PATROAM_RAG_INDEX"] = os.path.join(_tmp, "rag_index.json")
os.environ["PATROAM_GRAPH_FILE"] = os.path.join(_tmp, "graph.json")
os.environ["PATROAM_CHROMA_DIR"] = os.path.join(_tmp, "chroma")
os.makedirs(os.environ["PATROAM_KNOWLEDGE_DIR"], exist_ok=True)

from patroam import actions, config, graph, rag      # noqa: E402
from patroam.agent import Agent                       # noqa: E402

print("=" * 64)
print("  PATROAM — RAG + Knowledge Graph verification")
print("=" * 64)

# ── 1) RAG: index a document, then retrieve from it ─────────────────────────
with open(os.path.join(config.KNOWLEDGE_DIR, "orion.md"), "w", encoding="utf-8") as f:
    f.write("# Orion\n"
            "The Orion app stores its data in SQLite and uses JWT for authentication.\n"
            "It targets iOS 17. The team standup is on Mondays.\n")

n, m, _ = rag.ingest()
backend = ("ChromaDB (real vector DB)" if rag._use_chroma()
           else "JSON + " + ("Ollama embeddings" if config.EMBED_MODEL else "keyword search"))
print(f"\n[RAG]  indexed {n} passage(s) from {m} document(s)")
print(f"[RAG]  backend: {backend}")

q = "what database does Orion use?"
hits = rag.retrieve(q)
print(f"[RAG]  question: {q!r}")
for h in hits[:2]:
    print(f"        -> from '{h['source']}': {h['text'][:75]!r}")
rag_ok = any("SQLite" in h["text"] for h in hits)
print(f"[RAG]  found the answer passage: {rag_ok}")

miss = rag.retrieve("what is the company's stock price?")
print(f"[RAG]  unrelated question returns nothing relevant: {not miss}")

# ── 2) Knowledge graph: record relationships, then read them back ────────────
actions.run("relate", {"subject": "Orion", "relation": "USES", "object": "SQLite"})
actions.run("relate", {"subject": "Orion", "relation": "DEPENDS_ON", "object": "AuthService"})
actions.run("relate", {"subject": "Tuan", "relation": "OWNS", "object": "Orion"})
print("\n[GRAPH] recorded 3 relationships")
print("[GRAPH] " + graph.summary())
facts = graph.render_for("tell me about Orion")
graph_ok = "Orion uses SQLite" in facts
print(f"[GRAPH] relevant facts surface for a query about Orion: {graph_ok}")

# ── 3) Proof the model actually receives both ───────────────────────────────
captured = {}


class _Probe:
    """A fake provider that just captures the system prompt PATROAM builds."""
    def stream_chat(self, model, messages, on_token, on_done, on_error, cancel=None):
        captured["system"] = messages[0]["content"]
        on_done("(a real model would answer here)")


Agent(_Probe()).send("what does Orion use and where is its data stored?",
                     lambda t: None, lambda f: None, lambda e: None)
sysp = captured.get("system", "")
inj_rag = "SQLite" in sysp and "Source:" in sysp
inj_graph = "Orion uses SQLite" in sysp
print("\n[AGENT] the system prompt sent to the model contains:")
print(f"        - retrieved document evidence (with source): {inj_rag}")
print(f"        - knowledge-graph facts:                      {inj_graph}")

print("\n" + "=" * 64)
ok = rag_ok and graph_ok and inj_rag and inj_graph
print("  RESULT:", "PASS — RAG and the knowledge graph are working." if ok
      else "  Something is off — see the output above.")
print("=" * 64)

shutil.rmtree(_tmp, ignore_errors=True)
