"""Ollama provider — talks to a local Ollama server over its HTTP API."""

import json
import threading
import urllib.request

from .base import Provider
from .. import config


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, url: str = None):
        self.url = url or config.OLLAMA_URL

    def list_models(self):
        try:
            req = urllib.request.Request(f"{self.url}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def stream_chat(self, model, messages, on_token, on_done, on_error):
        def worker():
            payload = json.dumps({
                "model": model,
                "messages": messages,
                "stream": True,
            }).encode()
            try:
                req = urllib.request.Request(
                    f"{self.url}/api/chat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    full = ""
                    for line in resp:
                        line = line.strip()
                        if not line:
                            continue
                        obj = json.loads(line)
                        token = obj.get("message", {}).get("content", "")
                        full += token
                        on_token(token)
                        if obj.get("done"):
                            break
                    on_done(full)
            except Exception as e:
                on_error(str(e))

        threading.Thread(target=worker, daemon=True).start()
