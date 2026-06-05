"""FastAPI backend for PATROAM on the web.

Endpoints:
  GET  /api/models   -> available Ollama models
  WS   /ws           -> chat: client sends {type:text|model|wake}, server streams
                        {type:models|token|reply|error}
  /                  -> the orb web app (static files)

Each WebSocket connection gets its own Agent (own conversation history). Voice
in/out lives in the browser; skills (open apps, play music) run on this host.
"""

import asyncio
import os

from .. import config, skills
from ..agent import Agent
from ..providers import make_provider, pick_default

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app(local_voice=False):
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="PATROAM")
    provider = make_provider()
    clients = set()          # open browser WebSockets
    state = {"loop": None, "voice": None}

    @app.get("/api/models")
    def list_models():
        return {"models": provider.list_models()}

    async def broadcast(msg):
        for ws in list(clients):
            try:
                await ws.send_json(msg)
            except Exception:
                clients.discard(ws)

    @app.on_event("startup")
    async def _startup():
        if not local_voice:
            return
        state["loop"] = asyncio.get_running_loop()

        def mirror(kind, val):
            # Reflect local-machine voice activity on any open browser orb.
            if kind == "state":
                msg = {"type": "state", "state": val}
            elif kind == "status":
                msg = {"type": "status", "text": val}
            else:
                return  # don't mirror replies (avoids double speech in the browser)
            loop = state["loop"]
            if loop:
                loop.call_soon_threadsafe(lambda: asyncio.create_task(broadcast(msg)))

        from ..daemon import start_local_voice
        state["voice"] = start_local_voice(provider=provider, on_event=mirror)

    @app.on_event("shutdown")
    async def _shutdown():
        if state["voice"]:
            state["voice"].stop()

    async def _respond(ws, agent, loop, text):
        text = (text or "").strip()
        if not text:
            return
        # Local command first (e.g. "open Spotify", "play some music").
        reply = skills.try_handle(text)
        if reply is not None:
            await ws.send_json({"type": "reply", "text": reply})
            return
        if not agent.model:
            await ws.send_json({"type": "error", "text": "No model selected. Is Ollama running?"})
            return

        q: asyncio.Queue = asyncio.Queue()
        push = lambda kind, val: loop.call_soon_threadsafe(q.put_nowait, (kind, val))
        agent.send(text,
                   lambda t: push("token", t),
                   lambda f: push("done", f),
                   lambda e: push("error", e))
        while True:
            kind, val = await q.get()
            if kind == "token":
                await ws.send_json({"type": "token", "text": val})
            elif kind == "done":
                await ws.send_json({"type": "reply", "text": val})
                break
            else:
                await ws.send_json({"type": "error", "text": val})
                break

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        clients.add(ws)
        agent = Agent(provider)
        models = provider.list_models()
        if models:
            agent.set_model(pick_default(models))
        await ws.send_json({"type": "models", "models": models, "current": agent.model})
        loop = asyncio.get_event_loop()
        try:
            while True:
                data = await ws.receive_json()
                kind = data.get("type")
                if kind == "text":
                    await _respond(ws, agent, loop, data.get("text", ""))
                elif kind == "model":
                    agent.set_model(data.get("name", ""))
                elif kind == "wake":
                    # Bare wake word — greet based on the time of day.
                    await ws.send_json({"type": "reply", "text": config.time_greeting()})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            clients.discard(ws)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def run(host="127.0.0.1", port=8800, local_voice=False):
    import uvicorn
    url = f"http://{host}:{port}"
    print(f"PATROAM is on the web → {url}")
    print("Open that in Chrome/Edge. Allow the microphone for voice.")
    if local_voice:
        print("Local voice is also on — say \"hey patroam\" at this machine.")
    uvicorn.run(create_app(local_voice=local_voice), host=host, port=port,
                log_level="warning")
