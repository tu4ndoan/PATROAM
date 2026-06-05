"""Router provider — the realization of "run on any model".

Aggregates every available backend so a single model picker can list local
Ollama models *and* Claude models together. Requests are routed to the right
backend by the chosen model name (claude-* → Anthropic, everything else →
Ollama).
"""

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

    def stream_chat(self, model, messages, on_token, on_done, on_error):
        self._route(model).stream_chat(model, messages, on_token, on_done, on_error)


def make_provider():
    """The default provider for all PATROAM frontends."""
    return RouterProvider()


def pick_default(models):
    """Choose the model to start on: PATROAM_MODEL if set and available, else
    the first model offered."""
    if not models:
        return None
    if config.DEFAULT_MODEL and config.DEFAULT_MODEL in models:
        return config.DEFAULT_MODEL
    return models[0]
