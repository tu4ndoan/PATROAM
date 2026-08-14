"""One-time Google Calendar setup — get an OAuth refresh token into secrets.json.

    python -m patroam.wire_gcal

Opens Google's consent screen, catches the loopback redirect, and merges
GCAL_CLIENT_ID / GCAL_CLIENT_SECRET / GCAL_REFRESH_TOKEN into
~/.patroam/secrets.json (the file config.py already reads).

If you already wired YouTube (`python -m patroam.wire_youtube`), this reuses that
OAuth client automatically — same Google Cloud project, so there is nothing new
to create. You only need to:

  1. https://console.cloud.google.com/ → select that project.
  2. APIs & Services → Library → enable "Google Calendar API".
  3. Make sure the OAuth client has this redirect URI allowed:
       http://localhost:8766/
  Then run this script and approve the calendar permission.

Starting fresh instead? Create an OAuth client first (APIs & Services →
Credentials → Create credentials → OAuth client ID → "Desktop app"), then:
    python -m patroam.wire_gcal <CLIENT_ID> <CLIENT_SECRET>
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

# Calendars AND Google Tasks — tasks are a separate API, and without this scope
# PATROAM can't see the to-dos that show up in the Calendar UI.
SCOPE = ("https://www.googleapis.com/auth/calendar"
         " https://www.googleapis.com/auth/tasks")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SECRETS_PATH = os.path.join(os.path.expanduser("~"), ".patroam", "secrets.json")


def _load_secrets():
    try:
        with open(SECRETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_secrets(updates):
    """Merge key/values into secrets.json without dropping existing keys."""
    os.makedirs(os.path.dirname(SECRETS_PATH), exist_ok=True)
    data = _load_secrets()
    data.update(updates)
    with open(SECRETS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return SECRETS_PATH


def _auth_url(client_id, redirect_uri):
    q = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true"})
    return f"{AUTH_URL}?{q}"


def _exchange(client_id, client_secret, code, redirect_uri):
    data = urllib.parse.urlencode({
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


class _Catcher(BaseHTTPRequestHandler):
    code = None
    error = None
    done = False

    def do_GET(self):                                     # noqa: N802 (http.server API)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (q.get("code") or [None])[0]
        error = (q.get("error") or [None])[0]
        # Ignore favicon/speculative probes that carry no query.
        if not code and not error:
            self.send_response(204)
            self.end_headers()
            return
        _Catcher.code, _Catcher.error, _Catcher.done = code, error, True
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("✅ PATROAM is connected to Google Calendar. You can close this tab."
               if code else f"⚠️ Authorization failed: {error}")
        self.wfile.write(f"<html><body style='font:20px system-ui;padding:3rem'>{msg}"
                         "</body></html>".encode())

    def log_message(self, *_a):
        pass


def run(client_id="", client_secret="", port=8766):
    """Run the interactive flow. Returns the refresh token, or None on failure."""
    sec = _load_secrets()
    client_id = (client_id or os.environ.get("GCAL_CLIENT_ID")
                 or sec.get("GCAL_CLIENT_ID") or "").strip()
    client_secret = (client_secret or os.environ.get("GCAL_CLIENT_SECRET")
                     or sec.get("GCAL_CLIENT_SECRET") or "").strip()
    # Fall back to the YouTube OAuth client — same Google Cloud project.
    reused = False
    if not (client_id and client_secret):
        yt_id = (sec.get("YOUTUBE_CLIENT_ID") or "").strip()
        yt_sec = (sec.get("YOUTUBE_CLIENT_SECRET") or "").strip()
        if yt_id and yt_sec:
            client_id, client_secret, reused = yt_id, yt_sec, True
            print("Reusing your existing Google OAuth client (from the YouTube setup).")
    if not client_id:
        client_id = input("Paste your Google OAuth Client ID: ").strip()
    if not client_secret:
        client_secret = input("Paste your Google OAuth Client secret: ").strip()
    if not (client_id and client_secret):
        print("Need both a Client ID and Client secret. See the header of this file.")
        return None

    _Catcher.code = _Catcher.error = None
    _Catcher.done = False
    redirect_uri = f"http://localhost:{port}/"
    server = HTTPServer(("localhost", port), _Catcher)
    url = _auth_url(client_id, redirect_uri)
    print("\nEnable BOTH APIs for this project first:")
    print("   https://console.cloud.google.com/apis/library/calendar-json.googleapis.com")
    print("   https://console.cloud.google.com/apis/library/tasks.googleapis.com")
    print("\nAnd allow this redirect URI on the OAuth client:")
    print("   " + redirect_uri)
    if reused:
        print("   (the YouTube setup used port 8765 — port 8766 must be added too)")
    print("\nOpening Google's consent screen...")
    print("(If it doesn't open, paste this URL manually:)\n   " + url + "\n")
    webbrowser.open(url)
    while not _Catcher.done:
        server.handle_request()
    server.server_close()

    if _Catcher.error or not _Catcher.code:
        print(f"Authorization failed: {_Catcher.error or 'no code returned'}")
        return None
    try:
        tok = _exchange(client_id, client_secret, _Catcher.code, redirect_uri)
    except Exception as e:
        print(f"Token exchange failed: {e}")
        return None
    refresh = tok.get("refresh_token")
    if not refresh:
        print("Google didn't return a refresh token. Remove PATROAM's access at "
              "https://myaccount.google.com/permissions and run this again.")
        return None

    path = _save_secrets({"GCAL_CLIENT_ID": client_id,
                          "GCAL_CLIENT_SECRET": client_secret,
                          "GCAL_REFRESH_TOKEN": refresh})
    print(f"\n✅ Saved Google Calendar credentials to {path}")
    print('Restart PATROAM, then try: "what\'s on my calendar tomorrow?" or '
          '"schedule a meeting with Long friday at 3pm".')
    return refresh


if __name__ == "__main__":
    run(*(sys.argv[1:3]))
