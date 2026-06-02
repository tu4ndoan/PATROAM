"""Model providers. Each provider adapts a backend (Ollama, OpenAI, …) to the
common Provider interface so the agent is model-agnostic."""

from .base import Provider
from .ollama import OllamaProvider

__all__ = ["Provider", "OllamaProvider"]
