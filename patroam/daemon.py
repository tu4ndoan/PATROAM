"""Headless always-on PATROAM.

Runs with no GUI: starts the wake-word listener, and on "hey patroam <command>"
streams a reply from the model and speaks it. This is the 24/7 mode.
"""

import time

from . import config, skills
from .agent import Agent
from .providers import OllamaProvider
from .voice.listener import WakeWordListener
from .voice.tts import TTSWorker


def run_daemon():
    provider = OllamaProvider()
    models = provider.list_models()
    if not models:
        print("No Ollama models found. Start Ollama and pull a model, e.g.:")
        print("  ollama serve")
        print("  ollama pull llama3")
        return

    agent = Agent(provider, model=models[0], system_prompt=config.SYSTEM_PROMPT)
    tts = TTSWorker()
    tts.start()

    print(f"PATROAM is online.  Model: {agent.model}")
    print("Say \"hey patroam\" followed by a command.  Ctrl+C to quit.\n")

    def speak(text):
        # Mute the mic while speaking so PATROAM doesn't hear itself, then
        # resume listening for the next command once it finishes.
        listener.pause()
        tts.speak(text, on_finish=listener.resume)

    def on_command(text):
        print(f"\n> {text}")

        # Act on it locally first (e.g. "open Spotify"); else ask the model.
        handled = skills.try_handle(text)
        if handled is not None:
            print(handled)
            speak(handled)
            return

        def on_token(t):
            print(t, end="", flush=True)

        def on_done(full):
            print()
            speak(full)

        def on_error(e):
            print(f"[error] {e}")

        agent.send(text, on_token, on_done, on_error)

    listener = WakeWordListener(
        on_command=on_command,
        on_status=lambda s: print(f"[patroam] {s}"),
        on_wake=lambda: print("[patroam] 🔔 awake — listening for commands"),
        on_sleep=lambda: print("[patroam] 💤 session ended"),
    )
    listener.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        listener.stop()
        tts.stop()
