"""PATROAM's long-term memory — the practical form of "learning".

A small JSON store of durable facts about the user (and a profile dict). It's
rendered into the system prompt every turn so PATROAM always "knows" the user,
and PATROAM writes to it over time (via the `remember` action or the "remember…"
voice command), so it gets more personalised the more you use it.

Persists to ~/.patroam/memory.json so it survives restarts.
"""

import json
import os
import threading

from . import config


class Memory:
    MAX_FACTS = 200          # hard cap on stored facts
    RENDER_FACTS = 60        # how many recent facts to put in the prompt

    def __init__(self, path=None):
        self.path = path or config.MEMORY_FILE
        self._lock = threading.Lock()
        self.data = {"profile": {}, "facts": []}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except Exception:
            pass
        self.data.setdefault("profile", {})
        self.data.setdefault("facts", [])

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ── writes ──────────────────────────────────────────────────────────────
    def add_fact(self, text):
        text = (text or "").strip()
        if not text:
            return False
        with self._lock:
            facts = self.data["facts"]
            if any(text.lower() == f.lower() for f in facts):
                return True   # already known
            facts.append(text)
            self.data["facts"] = facts[-self.MAX_FACTS:]
            self._save()
        return True

    def forget(self, substr):
        substr = (substr or "").strip().lower()
        if not substr:
            return 0
        with self._lock:
            before = len(self.data["facts"])
            self.data["facts"] = [f for f in self.data["facts"] if substr not in f.lower()]
            removed = before - len(self.data["facts"])
            if removed:
                self._save()
        return removed

    def set(self, key, value):
        with self._lock:
            self.data["profile"][key] = value
            self._save()

    def clear(self):
        with self._lock:
            self.data = {"profile": {}, "facts": []}
            self._save()

    # ── reads ───────────────────────────────────────────────────────────────
    def render(self):
        """A block to inject into the system prompt."""
        profile = self.data.get("profile", {})
        facts = self.data.get("facts", [])
        if not profile and not facts:
            return "You have no saved memories about the user yet. Learn about them as you talk."
        lines = ["What you remember about the user (use it to personalise your help):"]
        for k, v in profile.items():
            lines.append(f"- {k}: {v}")
        for fact in facts[-self.RENDER_FACTS:]:
            lines.append(f"- {fact}")
        return "\n".join(lines)

    def summary(self):
        """A short spoken read-back of what's stored."""
        facts = self.data.get("facts", [])
        if not facts and not self.data.get("profile"):
            return "I don't have anything saved about you yet, Sir."
        items = [f"{k} is {v}" for k, v in self.data.get("profile", {}).items()]
        items += facts[-10:]
        return "Here's what I remember: " + "; ".join(items) + "."


_GLOBAL = None


def get_memory():
    """Shared process-wide memory instance."""
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = Memory()
    return _GLOBAL
