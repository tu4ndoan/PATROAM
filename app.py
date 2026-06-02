"""PATROAM entry point.

  python app.py            # WebGL orb window (3D, interactable)
  python app.py --tk       # classic lightweight Tkinter window
  python app.py --daemon   # run headless, 24/7, wake-word only ("hey patroam")
"""

import subprocess
import sys


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
                        ("webview", "pywebview")]:
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


def main():
    _bootstrap()
    args = sys.argv[1:]
    if "--daemon" in args:
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
