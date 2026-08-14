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


_BOOT_MARK = os.path.join(os.path.expanduser("~"), ".patroam", ".bootstrap-ok")


def _bootstrap():
    """Ensure dependencies are installed — but only CHECK once. Re-importing heavy
    packages (anthropic, edge_tts, pygame, mcp…) on every launch cost ~25s and made
    startup hang; after a clean run we drop a marker and skip the checks entirely."""
    if os.path.exists(_BOOT_MARK):
        return
    ok = True
    # Required: voice I/O and the offline TTS fallback.
    required = [("pyaudio", "pyaudio"), ("pyttsx3", "pyttsx3"),
                ("speech_recognition", "SpeechRecognition")]
    # Optional: neural voice (edge-tts+pygame), the orb window, vision, PDF, etc.
    optional = [("edge_tts", "edge-tts"), ("pygame", "pygame"),
                ("webview", "pywebview"), ("anthropic", "anthropic"),
                ("mcp", "mcp"), ("reportlab", "reportlab"), ("fitz", "pymupdf"),
                ("pypdf", "pypdf"), ("PIL", "Pillow"), ("playwright", "playwright"),
                ("browser_cookie3", "browser-cookie3")]
    for module, pkg in required + optional:
        try:
            __import__(module)
        except ImportError:
            print(f"Installing {pkg}…")
            try:
                _install(pkg)
            except Exception as e:
                print(f"  Skipped {pkg}: {e}")
                ok = False
    # Mark success so subsequent launches skip the slow re-import checks. Delete
    # ~/.patroam/.bootstrap-ok to force a re-check (e.g. after reinstalling Python).
    if ok:
        try:
            os.makedirs(os.path.dirname(_BOOT_MARK), exist_ok=True)
            open(_BOOT_MARK, "w").close()
        except Exception:
            pass


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
    # Connect to configured MCP connectors in the background. Never on the
    # startup path itself: an OAuth server can sit waiting on a sign-in for
    # minutes, and nothing else may wait for it.
    try:
        from patroam.mcp_client import get_mcp
        get_mcp().start_background()
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


def _backup_graph_on_launch():
    """Snapshot the knowledge graph at startup so a bad session can't lose it."""
    try:
        from patroam import graph
        p = graph.backup()
        _log(f"graph backed up: {p}")
    except Exception as e:
        _log(f"graph backup skipped: {e}")
    # Notes live in their own panel now, so the old "Notes" hub and the note
    # bodies dumped in as triples are cleared out of the graph. After the
    # backup, never before — this deletes triples.
    try:
        from patroam import graph
        gone = graph.drop_notes_node()
        if gone:
            _log(f"removed {gone} Notes triples from the graph")
    except Exception as e:
        _log(f"notes cleanup skipped: {e}")


def _start_n8n():
    """Bring the automation engine up with PATROAM (background; never blocks)."""
    try:
        from patroam import config, n8n
        if not config.N8N_ENABLED:
            return
        if not n8n.installed():
            _log("n8n not installed (npm install -g n8n) — skipping")
            return
        threading.Thread(target=n8n.start, daemon=True).start()
        _log(f"n8n starting on port {config.N8N_PORT}")
    except Exception as e:
        _log(f"n8n skipped: {e}")


def _start_integrations():
    """Background integrations that work in any run mode: the automatic news
    watch, and (if tokens are configured) the Slack bot for phone access."""
    try:
        from patroam import news_watch
        if news_watch.start():
            _log("news watch started")
    except Exception as e:
        _log(f"news watch skipped: {e}")
    try:
        from patroam import config as _cfg
        if _cfg.slack_enabled():
            try:
                import slack_bolt  # noqa: F401
            except ImportError:
                print("Installing slack-bolt…")
                try:
                    _install("slack_bolt")
                except Exception as e:
                    _log(f"slack-bolt install failed: {e}")
            from patroam import slack_bot
            _log(f"slack started: {slack_bot.start()}")
    except Exception as e:
        _log(f"slack skipped: {e}")


def main():
    _log(f"start argv={sys.argv[1:]} exe={sys.executable}")
    if not _acquire_single_instance():
        _log("another instance already running — exiting")
        print("PATROAM is already running — not starting a second instance "
              "(this prevents two voices speaking at once).")
        return
    _bootstrap()
    _start_mcp()
    #_start_daemon()  # never auto-start the headless daemon; the desktop orb
    #                   already listens. Running both = overlapping voices.
    _start_rag()
    _backup_graph_on_launch()
    _start_n8n()
    _start_integrations()
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
