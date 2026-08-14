"""n8n — the automation engine that runs alongside PATROAM.

n8n is a workflow automation server (webhooks, schedules, ~400 app integrations).
PATROAM starts it as a child process on launch and shuts it down on exit, so the
editor is always there without you managing anything.

No Docker: n8n is an npm package and Node is already required on this machine,
so it runs directly as `n8n start`. That avoids Docker Desktop entirely
(~3 GB + a WSL2 VM) for what is a single Node process.

The editor itself is a web app — n8n ships no native UI — but PATROAM renders it
inside its own window rather than opening a browser (see ui/web/index.html).
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import config

_PROC = None
_LOCK = threading.Lock()
_STATUS = {"state": "stopped", "detail": ""}


def _log(msg):
    try:
        with open(config.N8N_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def base_url():
    return f"http://127.0.0.1:{config.N8N_PORT}"


def executable():
    """Path to the n8n launcher, or None if it isn't installed.

    On Windows npm installs a `n8n.cmd` shim; shutil.which finds it only when the
    npm global bin is on PATH, so check the usual global prefix too."""
    exe = shutil.which("n8n")
    if exe:
        return exe
    candidates = []
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidates += [os.path.join(appdata, "npm", "n8n.cmd"),
                       os.path.join(appdata, "npm", "n8n")]
    candidates += [os.path.join(os.path.expanduser("~"), ".npm-global", "bin", "n8n")]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def installed():
    """True only if n8n can actually RUN. The npm install can leave a launcher
    behind while the native sqlite3 build failed, so check the package too —
    otherwise PATROAM reports "installed" for something that dies on start."""
    if executable() is None:
        return False
    appdata = os.environ.get("APPDATA", "")
    pkg = os.path.join(appdata, "npm", "node_modules", "n8n") if appdata else ""
    if pkg and os.path.isdir(pkg):
        # n8n's own entry point must exist; a half-installed tree won't have it.
        for probe in (os.path.join(pkg, "bin", "n8n"),
                      os.path.join(pkg, "dist", "index.js"),
                      os.path.join(pkg, "package.json")):
            if os.path.exists(probe):
                return True
        return False
    return True


def status():
    """{'state': stopped|starting|running|error|not_installed, 'detail': ..., 'url': ...}"""
    st = dict(_STATUS)
    st["url"] = base_url()
    st["installed"] = installed()
    if not st["installed"] and st["state"] == "stopped":
        st["state"] = "not_installed"
        st["detail"] = "n8n isn't installed. Run:  npm install -g n8n"
    return st


def is_up(timeout=1.5):
    """True if something is already answering on the n8n port."""
    try:
        req = urllib.request.Request(base_url() + "/rest/login", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError:
        return True                      # answering, just not with 200
    except Exception:
        return False


def _env():
    """Environment for the child process."""
    env = dict(os.environ)
    env.setdefault("N8N_PORT", str(config.N8N_PORT))
    env.setdefault("N8N_HOST", "127.0.0.1")
    env.setdefault("N8N_LISTEN_ADDRESS", "127.0.0.1")   # local only, never exposed
    env.setdefault("N8N_USER_FOLDER", config.N8N_DIR)
    env.setdefault("GENERIC_TIMEZONE", config.TIMEZONE or "Asia/Ho_Chi_Minh")
    # PATROAM embeds the editor in its own window; n8n's default frame headers
    # would refuse that, so allow same-app framing.
    env.setdefault("N8N_SECURE_COOKIE", "false")        # required over plain http
    env.setdefault("N8N_DIAGNOSTICS_ENABLED", "false")  # no telemetry
    env.setdefault("N8N_HIRING_BANNER_ENABLED", "false")
    env.setdefault("N8N_VERSION_NOTIFICATIONS_ENABLED", "false")
    env.setdefault("N8N_PERSONALIZATION_ENABLED", "false")
    return env


def start(wait=False, timeout=90):
    """Launch n8n in the background. Safe to call repeatedly."""
    global _PROC
    with _LOCK:
        if _PROC and _PROC.poll() is None:
            return True
        if is_up():                       # someone already runs it on this port
            _STATUS.update(state="running", detail="already running")
            return True
        exe = executable()
        if not exe:
            _STATUS.update(state="not_installed",
                           detail="n8n isn't installed. Run:  npm install -g n8n")
            return False
        # A previous PATROAM that was force-quit can leave n8n holding the port.
        kill_stray()
        os.makedirs(config.N8N_DIR, exist_ok=True)
        _STATUS.update(state="starting", detail="")
        try:
            kw = {}
            if sys.platform.startswith("win"):
                # No console window for the child (PATROAM may run under pythonw).
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _PROC = subprocess.Popen(
                [exe, "start"], env=_env(), cwd=config.N8N_DIR,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
            _log(f"spawned {exe} (pid {_PROC.pid}) on port {config.N8N_PORT}")
        except Exception as e:
            _STATUS.update(state="error", detail=str(e))
            _log(f"spawn failed: {e}")
            return False

    def watch():
        # n8n's first boot builds its database and takes a while.
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _PROC and _PROC.poll() is not None:
                _STATUS.update(state="error", detail=(
                    "n8n exited immediately — the install is usually incomplete "
                    "(the native sqlite3 module fails to build on Windows). "
                    "Reinstall with:  npm install -g n8n"))
                _log("child exited during startup")
                return
            if is_up():
                _STATUS.update(state="running", detail="")
                _log("n8n is up")
                return
            time.sleep(1.5)
        _STATUS.update(state="error", detail="timed out waiting for n8n")
        _log("timed out waiting for n8n")

    if wait:
        watch()
    else:
        threading.Thread(target=watch, daemon=True).start()
    return True


def stop():
    """Stop n8n when PATROAM exits.

    Kills the whole PROCESS TREE. n8n runs as npm shim → `n8n start` → task
    runner, and terminating just the child we spawned kills only the shim,
    leaving the real server orphaned and still holding the port."""
    global _PROC
    with _LOCK:
        p, _PROC = _PROC, None
    if not p or p.poll() is not None:
        _STATUS.update(state="stopped", detail="")
        return
    try:
        if sys.platform.startswith("win"):
            # /T = tree, /F = force. The shim exits instantly, so terminate()
            # alone would strand its children.
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()
    except Exception:
        pass
    _STATUS.update(state="stopped", detail="")
    _log("stopped")


def kill_stray(timeout=10):
    """Kill any n8n left over from a previous run (e.g. PATROAM was force-quit),
    so the port is free and we don't stack servers. Windows only; elsewhere the
    port check in start() is enough. Returns how many were killed."""
    if not sys.platform.startswith("win"):
        return 0
    killed = 0
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='node.exe'", "get", "ProcessId,CommandLine"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        for line in out.splitlines():
            if "n8n" not in line:
                continue
            pid = line.strip().split()[-1]
            if not pid.isdigit():
                continue
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            killed += 1
    except Exception:
        pass
    if killed:
        _log(f"killed {killed} stray n8n process(es)")
    return killed


# ── Reading the workflow list ─────────────────────────────────────────────────
# Two ways in, and the fallback is the one that always works:
#   * the public API (/api/v1) — clean, but needs a key you create by hand;
#   * the sqlite file n8n keeps its workflows in — always there, since PATROAM
#     runs n8n locally and owns its data folder.
# The old code asked /rest/workflows, which is the editor's own endpoint: it
# authenticates with a BROWSER session cookie, so from Python it answers 401 and
# the list came back empty with no explanation.
_LAST_ERROR = ""


def last_error():
    return _LAST_ERROR


def _api(path, method="GET", body=None):
    """Call the public API (needs N8N_API_KEY)."""
    url = base_url() + "/api/v1" + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if config.N8N_API_KEY:
        headers["X-N8N-API-KEY"] = config.N8N_API_KEY
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def database_path():
    return os.path.join(config.N8N_DIR, ".n8n", "database.sqlite")


def _workflows_from_db():
    """Read the workflows straight out of n8n's own database (read-only)."""
    import sqlite3
    path = database_path()
    if not os.path.exists(path):
        raise FileNotFoundError("n8n has no database yet — start it once.")
    uri = "file:" + path.replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        rows = con.execute("SELECT id, name, active, nodes FROM workflow_entity "
                           "ORDER BY updatedAt DESC").fetchall()
    finally:
        con.close()
    out = []
    for wid, name, active, nodes in rows:
        out.append({"id": wid, "name": name or "", "active": bool(active),
                    "webhook": _webhook_path(nodes)})
    return out


def _webhook_path(nodes_json):
    """The webhook path a workflow listens on, so PATROAM can trigger it by
    name instead of guessing the slug from the title."""
    try:
        nodes = json.loads(nodes_json) if isinstance(nodes_json, str) else (nodes_json or [])
    except Exception:
        return ""
    for n in nodes:
        if "webhook" in (n.get("type") or "").lower():
            p = (n.get("parameters") or {}).get("path")
            if p:
                return str(p)
    return ""


def workflows():
    """[{id, name, active, webhook}] — from the API if a key is set, else the
    local database. Failures are recorded in last_error() instead of vanishing."""
    global _LAST_ERROR
    if config.N8N_API_KEY:
        try:
            data = _api("/workflows")
            items = data.get("data", data if isinstance(data, list) else [])
            _LAST_ERROR = ""
            return [{"id": w.get("id"), "name": w.get("name", ""),
                     "active": bool(w.get("active")),
                     "webhook": _webhook_path(w.get("nodes"))} for w in items]
        except Exception as e:
            _LAST_ERROR = f"API: {e}"      # fall through to the database
    try:
        out = _workflows_from_db()
        _LAST_ERROR = ""
        return out
    except Exception as e:
        _LAST_ERROR = f"{type(e).__name__}: {e}"
        return []


def run_webhook(path, payload=None, method="POST"):
    """Trigger a workflow through its webhook path → (ok, response_text)."""
    url = f"{base_url()}/webhook/{path.lstrip('/')}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, r.read().decode("utf-8", "replace")[:2000]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}"
    except Exception as e:
        return False, str(e)
