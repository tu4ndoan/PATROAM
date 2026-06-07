"""Anthropic (Claude) provider — runs PATROAM on Claude models like Opus.

Uses the official `anthropic` SDK and streams replies. Requires an API key in
the ANTHROPIC_API_KEY environment variable. If the key or SDK is missing,
`list_models()` returns [] so the model simply doesn't appear in the picker.

Note: "Opus 3" (claude-3-opus) was retired by Anthropic; the current Opus is
`claude-opus-4-8`, which this provider defaults to.
"""

import os
import threading

from .base import Provider


class AnthropicProvider(Provider):
    name = "anthropic"

    # Current Claude models (Opus 3 is retired → use Opus 4.8).
    MODELS = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]

    def __init__(self):
        self._client = None

    def _ready(self):
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def list_models(self):
        if not self._ready():
            return []
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return []
        return list(self.MODELS)

    @staticmethod
    def _split(messages):
        """Claude takes the system prompt as a separate argument, not as a
        message. Pull any system messages out and keep the rest in order."""
        system_parts, msgs = [], []
        for m in messages:
            if m.get("role") == "system":
                if m.get("content"):
                    system_parts.append(m["content"])
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        return "\n\n".join(system_parts), msgs

    def stream_chat(self, model, messages, on_token, on_done, on_error, cancel=None):
        def worker():
            try:
                client = self._get_client()
                system, msgs = self._split(messages)
                kwargs = {"system": system} if system else {}
                full = []
                with client.messages.stream(
                    model=model or self.MODELS[0],
                    max_tokens=1024,           # spoken replies are short
                    messages=msgs,
                    **kwargs,
                ) as stream:
                    for text in stream.text_stream:
                        if cancel is not None and cancel.is_set():
                            return          # aborted: close the stream, no on_done
                        full.append(text)
                        on_token(text)
                on_done("".join(full))
            except Exception as e:
                on_error(str(e))

        threading.Thread(target=worker, daemon=True).start()
