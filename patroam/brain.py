"""A headless text brain — text in, text out — reusing the same Agent, skills,
and model routing as the desktop orb, but with no voice or UI.

Used by the Slack bot (and any future text channel) so chatting with PATROAM
from your phone gets the full assistant: live data skills (news, gold, fab,
ads), the LLM persona with tool-calling, knowledge-graph memory, file creation,
and coding-model routing.
"""

import threading

from . import config, skills


class Brain:
    def __init__(self, provider=None):
        from .agent import Agent
        from .providers import make_provider, pick_default
        self.agent = Agent(provider or make_provider())
        models = self.agent.provider.list_models()
        if models and not self.agent.model:
            self.agent.set_model(pick_default(models))
        self._lock = threading.Lock()   # serialize turns (one shared history)

    def respond(self, text):
        """Return PATROAM's full text reply to `text` (blocking)."""
        text = (text or "").strip()
        if not text:
            return ""
        with self._lock:
            return self._respond(text)

    def _respond(self, text):
        # 1) Deterministic live-data skills (news / gold / fab / ads).
        data = skills.data_handle(text)
        if data is not None:
            if not data:
                return ""
            _, show = skills.split_reply(data)
            return show

        # 2) No model → deterministic commands only.
        if not self.agent.model:
            return skills.command_handle(text) or \
                "I have no language model available right now, Sir."

        # 3) LLM-first, with coding-model routing.
        model = None
        if config.CODE_MODEL and skills.is_coding_query(text) \
                and config.CODE_MODEL in self.agent.provider.list_models():
            model = config.CODE_MODEL

        out = {"text": ""}
        done = threading.Event()
        self.agent.send(text, lambda t: None,
                        lambda full: (out.__setitem__("text", full), done.set()),
                        lambda err: (out.__setitem__("text", f"(error: {err})"), done.set()),
                        model=model)
        done.wait(150)
        reply = (out["text"] or "").strip()

        # Fallback: model understood but didn't emit the tool call for a command.
        if not self.agent.acted:
            cmd = skills.command_handle(text)
            if cmd:
                reply = (reply + "\n\n" + cmd).strip() if reply else cmd

        # Surface any files PATROAM created (paths are clickable on the computer).
        if self.agent.files_made:
            reply += "\n\nFiles created:\n" + "\n".join(self.agent.files_made)

        return reply or "…"
