"""Router provider — the realization of "run on any model".

Aggregates every available backend so a single model picker can list local
Ollama models *and* Claude models together. Requests are routed to the right
backend by the chosen model name (claude-* → Anthropic, everything else →
Ollama).
"""

import re

from .anthropic import AnthropicProvider
from .base import Provider
from .ollama import OllamaProvider
from .. import config


class RouterProvider(Provider):
    name = "router"

    def __init__(self):
        self.ollama = OllamaProvider()
        self.anthropic = AnthropicProvider()

    def list_models(self):
        return list(self.ollama.list_models()) + list(self.anthropic.list_models())

    def _route(self, model):
        return self.anthropic if (model or "").lower().startswith("claude") else self.ollama

    def stream_chat(self, model, messages, on_token, on_done, on_error, cancel=None):
        self._route(model).stream_chat(model, messages, on_token, on_done, on_error, cancel=cancel)


def make_provider():
    """The default provider for all PATROAM frontends."""
    return RouterProvider()


def _loose(s):
    """Key for punctuation-insensitive matching, so a model written 'gemma4.31b'
    still matches the installed 'gemma4:31b' (dot vs colon is an easy typo)."""
    return re.sub(r"[\s._:\-]+", "", (s or "").lower())


def pick_default(models):
    """Choose the model to start on: config.DEFAULT_MODEL matched flexibly against
    the available models (exact → case-insensitive → punctuation-insensitive →
    substring). If it isn't installed, prefer a LOCAL model over a cloud one —
    a fresh clone shouldn't start on a `*-cloud` model it has no account for."""
    if not models:
        return None
    want = (config.DEFAULT_MODEL or "").strip()
    if want:
        for m in models:                       # exact
            if m == want:
                return m
        wl = want.lower()
        for m in models:                       # case-insensitive
            if m.lower() == wl:
                return m
        wk = _loose(want)
        for m in models:                       # punctuation-insensitive
            if _loose(m) == wk:
                return m
        for m in models:                       # substring / prefix (e.g. "llama3")
            if wl in m.lower():
                return m
    # Wanted model isn't here: prefer something that runs locally. Ollama cloud
    # models end in "-cloud" or ":cloud" (gemma4:31b-cloud, deepseek-v3:cloud).
    for m in models:
        ml = m.lower()
        if not re.search(r"[-:_]cloud$", ml) and not ml.startswith("claude"):
            return m
    return models[0]
