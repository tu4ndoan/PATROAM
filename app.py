"""PATROAM entry point.

  python app.py                 # desktop orb window (pywebview)
  python app.py --web           # serve PATROAM on the web (open in a browser)
  python app.py --web --daemon  # web AND local-machine voice, together
  python app.py --tk            # classic lightweight Tkinter window
  python app.py --daemon        # headless, 24/7, local wake-word only
"""

import os
import subprocess
import sys
import threading


def _log(msg):
    """Append a line to ~/.patroam/startup.log. pythonw (used at login) has no
    console, so this is the only way to see what happened when PATROAM starts."""
    try:
        import datetime
        d = os.path.join(os.path.expanduser("~"), ".patroam")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "startup.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except Exception:
        pass


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
                        ("mcp", "mcp"), ("reportlab", "reportlab"),
                        ("fitz", "pymupdf"),   # best .pdf text extraction (Unicode)
                        ("pypdf", "pypdf"),    # .pdf fallback reader
                        ("PIL", "Pillow")]:    # screen capture for the vision model
        try:
            __import__(module)
        except ImportError:
            print(f"Installing {pkg}…")
            try:
                _install(pkg)
            except Exception as e:
                print(f"  Skipped {pkg}: {e}")


_LOCK_SOCK = None


def _acquire_single_instance():
    """Guarantee only ONE PATROAM runs at a time, so instances never talk over
    each other (the cause of hearing 2–3 voices at once). We grab an OS-level
    lock by binding a fixed loopback port; if it's taken, another PATROAM is
    already running and this one should bow out. Returns True if we hold it."""
    global _LOCK_SOCK
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 49517))
        s.listen(1)
        _LOCK_SOCK = s          # keep the socket open for the whole process life
        return True
    except OSError:
        s.close()
        return False


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


def _start_rag():
    # Ensure the knowledge folder exists and (re)index it in the background if
    # there are documents but no index yet.
    def work():
        try:
            from patroam import config, rag
            import os
            rag.ensure_dir()
            has_docs = any(
                f != "README.txt"
                for _, _, fs in os.walk(config.KNOWLEDGE_DIR) for f in fs)
            if has_docs and not os.path.exists(config.RAG_INDEX_FILE):
                n, m, t = rag.ingest()
                if n:
                    print(f"[rag] indexed {n} passages from {m} documents"
                          + (f", extracted {t} graph facts" if t else ""))
        except Exception as e:
            print(f"RAG startup skipped: {e}")
    threading.Thread(target=work, daemon=True).start()


def main():
    _log(f"start argv={sys.argv[1:]} exe={sys.executable}")
    if not _acquire_single_instance():
        _log("another instance already running — exiting")
        print("PATROAM is already running — not starting a second instance "
              "(this prevents two voices speaking at once).")
        return
    _bootstrap()
    #_start_mcp()
    #_start_daemon()  # never auto-start the headless daemon; the desktop orb
    #                   already listens. Running both = overlapping voices.
    _start_rag()
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
            _log("launching desktop orb (pywebview)")
            run_webview()
        except Exception as e:
            import traceback
            _log("pywebview FAILED, falling back to Tk:\n" + traceback.format_exc())
            print(f"WebGL window unavailable ({e}); using the classic window.")
            _run_tk()


if __name__ == "__main__":
    main()
