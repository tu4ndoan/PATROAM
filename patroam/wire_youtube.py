"""One-time YouTube setup — get an OAuth refresh token and save it to secrets.json.

The content pipeline uploads Shorts with the YouTube Data API v3. That needs a
long-lived *refresh token* tied to your Google account. Google issues one only
through an interactive consent flow, so this little helper runs it for you:

    python -m patroam.wire_youtube

It opens Google's consent screen in your browser, catches the redirect on a local
loopback port, exchanges the code for tokens, and merges YOUTUBE_CLIENT_ID /
YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN into ~/.patroam/secrets.json — the
same file config.py already reads. After this, "post my new reel" uploads to
YouTube automatically.

BEFORE running, get an OAuth *client* (one-time, in Google Cloud Console):
  1. https://console.cloud.google.com/ → create/select a project.
  2. APIs & Services → Library → enable "YouTube Data API v3".
  3. APIs & Services → OAuth consent screen → External → add yourself as a Test user
     (Publishing status "Testing" is fine; test-user refresh tokens don't expire
     for this use).
  4. APIs & Services → Credentials → Create credentials → OAuth client ID →
     Application type "Desktop app" → download / copy the Client ID + Client secret.
Pass them as arguments or paste them when prompted:
    python -m patroam.wire_youtube <CLIENT_ID> <CLIENT_SECRET>
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SECRETS_PATH = os.path.join(os.path.expanduser("~"), ".patroam", "secrets.json")


def _auth_url(client_id, redirect_uri):
    """Build the consent URL. access_type=offline + prompt=consent guarantees a
    refresh_token comes back (Google omits it on repeat grants otherwise)."""
    q = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true"})
    return f"{AUTH_URL}?{q}"


def _exchange(client_id, client_secret, code, redirect_uri):
    """Swap the auth code for tokens. Returns the parsed token JSON."""
    data = urllib.parse.urlencode({
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _save_secrets(updates):
    """Merge key/values into ~/.patroam/secrets.json without dropping existing keys."""
    os.makedirs(os.path.dirname(SECRETS_PATH), exist_ok=True)
    try:
        with open(SECRETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data.update(updates)
    with open(SECRETS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return SECRETS_PATH


class _Catcher(BaseHTTPRequestHandler):
    code = None
    error = None
    done = False

    def do_GET(self):                                     # noqa: N802 (http.server API)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (q.get("code") or [None])[0]
        error = (q.get("error") or [None])[0]
        # Browsers fire speculative/favicon requests to localhost with no query.
        # Ignore those (204, keep listening) so we don't mistake them for "no code".
        if not code and not error:
            self.send_response(204)
            self.end_headers()
            return
        _Catcher.code, _Catcher.error, _Catcher.done = code, error, True
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("✅ PATROAM is connected to YouTube. You can close this tab."
               if code else f"⚠️ Authorization failed: {error}")
        self.wfile.write(f"<html><body style='font:20px system-ui;padding:3rem'>{msg}"
                         "</body></html>".encode())

    def log_message(self, *_a):                           # silence the default logging
        pass


def run(client_id="", client_secret="", port=8765):
    """Run the interactive flow. Returns the refresh token, or None on failure."""
    client_id = (client_id or os.environ.get("YOUTUBE_CLIENT_ID") or "").strip()
    client_secret = (client_secret or os.environ.get("YOUTUBE_CLIENT_SECRET") or "").strip()
    if not client_id:
        client_id = input("Paste your Google OAuth Client ID: ").strip()
    if not client_secret:
        client_secret = input("Paste your Google OAuth Client secret: ").strip()
    if not (client_id and client_secret):
        print("Need both a Client ID and Client secret. See the header of this file.")
        return None

    _Catcher.code = _Catcher.error = None                # reset any state from a prior run
    _Catcher.done = False
    redirect_uri = f"http://localhost:{port}/"
    server = HTTPServer(("localhost", port), _Catcher)
    url = _auth_url(client_id, redirect_uri)
    print("\nMake sure this redirect URI is allowed on your OAuth client:")
    print("   " + redirect_uri)
    print("\nOpening Google's consent screen in your browser...")
    print("(If it doesn't open, paste this URL manually:)\n   " + url + "\n")
    webbrowser.open(url)
    # Loop over requests, ignoring code-less probes, until the real redirect lands.
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

    path = _save_secrets({"YOUTUBE_CLIENT_ID": client_id,
                          "YOUTUBE_CLIENT_SECRET": client_secret,
                          "YOUTUBE_REFRESH_TOKEN": refresh})
    print(f"\n✅ Saved YouTube credentials to {path}")
    print("Restart PATROAM, then say \"post my new reel\" — YouTube now uploads automatically.")
    return refresh


if __name__ == "__main__":
    run(*(sys.argv[1:3]))
