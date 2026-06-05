"""PATROAM entry point.

  python app.py                 # desktop orb window (pywebview)
  python app.py --web           # serve PATROAM on the web (open in a browser)
  python app.py --web --daemon  # web AND local-machine voice, together
  python app.py --tk            # classic lightweight Tkinter window
  python app.py --daemon        # headless, 24/7, local wake-word only
"""

import subprocess
import sys
import threading


def _install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])


def _bootstrap():
    """Install voice dependencies on first run."""
    # Required: voice I/O and the offline TTS fallback.
    for module, pkg in [
        ("pyaudio", "pyaudio"),
        ("pyttsx3", "pyttsx3"),
        ("speech_recognition", "SpeechRecognition"),
    ]:
        try:
            __import__(module)
        except ImportError:
            print(f"Installing {pkg}…")
            _install(pkg)

    # Optional: the natural neural British voice (Edge TTS + pygame playback)
    # and the WebGL orb window (pywebview). If these can't be installed (e.g.
    # offline), PATROAM still runs with the offline voice / classic window.
    for module, pkg in [("edge_tts", "edge-tts"), ("pygame", "pygame"),
                        ("webview", "pywebview"), ("anthropic", "anthropic"),
                        ("mcp", "mcp")]:
        try:
            __import__(module)
        except ImportError:
            print(f"Installing {pkg}…")
            try:
                _install(pkg)
            except Exception as e:
                print(f"  Skipped {pkg}: {e}")


def _run_tk():
    from patroam.ui import PatroamChat
    PatroamChat().mainloop()


def _start_mcp():
    # Connect to configured MCP connectors (e.g. Meta Ads) in the background.
    try:
        from patroam.mcp_client import get_mcp
        threading.Thread(target=get_mcp().start, daemon=True).start()
    except Exception as e:
        print(f"MCP startup skipped: {e}")


def main():
    _bootstrap()
    #_start_mcp()
    args = sys.argv[1:]
    if "--web" in args:
        for module, pkg in [("fastapi", "fastapi"), ("uvicorn", "uvicorn[standard]")]:
            try:
                __import__(module)
            except ImportError:
                print(f"Installing {pkg}…")
                _install(pkg)
        from patroam.web.server import run as run_web
        host = "0.0.0.0" if "--lan" in args else "127.0.0.1"
        # `--web --daemon` runs the browser app AND local-machine voice together.
        run_web(host=host, local_voice="--daemon" in args)
    elif "--daemon" in args:
        from patroam.daemon import run_daemon
        run_daemon()
    elif "--tk" in args:
        _run_tk()
    else:
        try:
            from patroam.ui.webview_app import run as run_webview
            run_webview()
        except Exception as e:
            print(f"WebGL window unavailable ({e}); using the classic window.")
            _run_tk()


if __name__ == "__main__":
    main()
