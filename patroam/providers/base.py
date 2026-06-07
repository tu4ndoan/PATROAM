"""The Provider interface every model backend implements.

This is the seam that makes PATROAM "run on any model": the agent only ever
talks to a Provider, never to Ollama/OpenAI/Anthropic directly. Adding GPT or
Opus later means adding one file here, not changing the agent or UI.
"""

from abc import ABC, abstractmethod
from typing import Callable, List, Dict


class Provider(ABC):
    name: str = "base"

    @abstractmethod
    def list_models(self) -> List[str]:
        """Return the model names this backend currently offers (may be empty)."""

    @abstractmethod
    def stream_chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        on_token: Callable[[str], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
        cancel=None,
    ) -> None:
        """Stream a chat completion.

        Implementations should run in a background thread and invoke the
        callbacks as tokens arrive / the stream finishes / an error occurs.
        `messages` is a list of {"role", "content"} dicts. `cancel`, if given, is
        a threading.Event — the loop should stop and close when it is set.
        """
