"""Model providers. Each provider adapts a backend (Ollama, Anthropic/Claude, …)
to the common Provider interface so the agent is model-agnostic."""

from .anthropic import AnthropicProvider
from .base import Provider
from .ollama import OllamaProvider
from .router import RouterProvider, make_provider, pick_default

__all__ = [
    "Provider", "OllamaProvider", "AnthropicProvider",
    "RouterProvider", "make_provider", "pick_default",
]
