"""A tiny registry for the active model's synchronous completion function.

The Agent registers its `complete(prompt, system)` here on startup, so subsystems
that aren't wired to a Provider (RAG ingestion, skills, startup tasks) can still
ask the current model for a one-shot completion — e.g. to extract a knowledge
graph from a document. Mirrors the get_memory()/get_mcp() singleton pattern.
"""

_COMPLETE = None


def set_completer(fn):
    """Register the active completion function: (prompt, system=None) -> str|None."""
    global _COMPLETE
    _COMPLETE = fn


def available():
    return _COMPLETE is not None


def complete(prompt, system=None):
    """One-shot completion using the active model, or None if unavailable/failed."""
    if _COMPLETE is None:
        return None
    try:
        return _COMPLETE(prompt, system)
    except Exception:
        return None
