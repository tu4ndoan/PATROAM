"""The working brain behind the voice — and which model plays it.

Gemini Live is the ears and the mouth: it hears you, works out what you want,
and speaks. It is billed per second of audio, so its replies stay terse.

The substance — reading the calendar, summarising a project, listing tasks — is
composed here. Which model does that is YOUR choice (config.WORKER_MODEL, set
from the dropdown in the title bar), because the trade-off is real:

    groq    ~370 ms to first token, free tier, data leaves the machine
    ollama  whatever you picked in the model list — same engine as the rest of
            PATROAM, no extra account, ~3 s
    gemini  one provider for everything, spends the same credits as the voice

They all expose complete()/stream(), so callers never care which is active.
"""

import json
import urllib.error
import urllib.request

from .. import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Cloudflare rejects urllib's default agent with 403 (error 1010).
_UA = "PATROAM/1.0"


class GroqLLM:
    """Streaming chat over Groq's OpenAI-compatible endpoint."""

    name = "groq"

    def __init__(self, model=None, api_key=None):
        self.model = model or config.GROQ_MODEL
        self.api_key = api_key or config.GROQ_API_KEY
        self.last_error = ""

    def available(self):
        return bool(self.api_key)

    def _headers(self):
        return {"Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json", "User-Agent": _UA}

    def complete(self, prompt, system=None, timeout=20, max_tokens=400):
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        body = json.dumps({"model": self.model, "messages": msgs,
                           "max_tokens": max_tokens}).encode()
        try:
            req = urllib.request.Request(GROQ_URL, data=body, headers=self._headers())
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            self.last_error = ""
            return (d["choices"][0]["message"]["content"] or "").strip()
        except urllib.error.HTTPError as e:
            self.last_error = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
        return ""


class GeminiLLM:
    """Plain (non-Live) Gemini, for when you'd rather keep one provider."""

    name = "gemini"

    def __init__(self, model=None, api_key=None):
        self.model = model or config.GEMINI_TEXT_MODEL
        self.api_key = api_key or config.GEMINI_API_KEY
        self.last_error = ""

    def available(self):
        return bool(self.api_key)

    def complete(self, prompt, system=None, timeout=20, max_tokens=400):
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        try:
            req = urllib.request.Request(
                f"{GEMINI_URL}/{self.model}:generateContent",
                data=json.dumps(body).encode(),
                headers={"x-goog-api-key": self.api_key,
                         "Content-Type": "application/json", "User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            self.last_error = ""
            parts = (d["candidates"][0].get("content") or {}).get("parts", [])
            return " ".join(p.get("text", "") for p in parts).strip()
        except urllib.error.HTTPError as e:
            self.last_error = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
        return ""


class OllamaLLM:
    """Whatever model is selected in PATROAM's main model list."""

    name = "ollama"

    def __init__(self):
        self.last_error = ""

    def available(self):
        from .. import llm
        return llm.available()

    def complete(self, prompt, system=None, timeout=20, max_tokens=400):
        from .. import llm
        try:
            return (llm.complete(prompt, system=system, timeout=timeout) or "").strip()
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return ""


_BACKENDS = {"groq": GroqLLM, "gemini": GeminiLLM, "ollama": OllamaLLM}


def options():
    """[{id, label, available}] for the dropdown — greys out what isn't set up."""
    out = []
    for key, label in (("groq", "Groq · llama-3.3-70b (nhanh nhất, free)"),
                       ("ollama", "Ollama · model đang chọn ở trên"),
                       ("gemini", "Gemini flash (dùng chung credit với giọng nói)")):
        try:
            ok = _BACKENDS[key]().available()
        except Exception:
            ok = False
        out.append({"id": key, "label": label, "available": ok})
    return out


def worker():
    """The model currently chosen to compose answers."""
    cls = _BACKENDS.get(config.WORKER_MODEL) or OllamaLLM
    inst = cls()
    if not inst.available():          # chosen backend isn't configured → fall back
        for alt in ("groq", "ollama", "gemini"):
            cand = _BACKENDS[alt]()
            if cand.available():
                return cand
    return inst
