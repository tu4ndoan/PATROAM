"""OAuth 2.0 for MCP connectors (e.g. Meta's official Meta Ads MCP server).

Implements the three pieces the MCP SDK's OAuthClientProvider needs:
  * token storage  — persists tokens + dynamic-client registration to disk, so
                     you authorize once and it refreshes silently afterwards;
  * redirect handler — opens your browser to the authorization page;
  * callback handler — a tiny localhost server that catches the redirect (the
                     `?code=…`) and hands it back to the SDK.

Tokens live under <mcp config dir>/mcp_oauth/<server>.json.
"""

import asyncio
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from . import config

CALLBACK_PORT = 8723
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"


def _oauth_dir():
    d = os.path.join(os.path.dirname(config.MCP_FILE), "mcp_oauth")
    os.makedirs(d, exist_ok=True)
    return d


class FileTokenStorage:
    """Persists OAuth tokens + client registration to a JSON file per server."""

    def __init__(self, name):
        from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
        self._Token = OAuthToken
        self._Client = OAuthClientInformationFull
        self.path = os.path.join(_oauth_dir(), f"{name}.json")
        self._data = self._read()

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
        except Exception:
            pass

    async def get_tokens(self):
        d = self._data.get("tokens")
        return self._Token.model_validate(d) if d else None

    async def set_tokens(self, tokens):
        self._data["tokens"] = tokens.model_dump(mode="json")
        self._write()

    async def get_client_info(self):
        d = self._data.get("client")
        return self._Client.model_validate(d) if d else None

    async def set_client_info(self, client_info):
        self._data["client"] = client_info.model_dump(mode="json")
        self._write()

    def seed_client(self, client_id, client_secret=None, scope=None):
        """Pre-register a client_id so the SDK skips dynamic registration (which
        Meta's official server does not support)."""
        client = {
            "client_id": client_id,
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post" if client_secret else "none",
            "scope": scope,
        }
        if client_secret:
            client["client_secret"] = client_secret
        self._data["client"] = client
        self._write()


# How the authorization page gets shown. The UI replaces this with a PATROAM
# window (set_opener) so you approve access inside the app instead of being
# thrown out to a browser; without a UI it falls back to the default browser.
_OPENER = {"open": None}


def set_opener(fn):
    """`fn(url)` shows the authorization page; it may return a handle with
    .destroy() so the window can be closed once the code comes back."""
    _OPENER["open"] = fn


_window = {"handle": None}


async def _redirect_handler(url):
    print("\n[mcp] PATROAM needs you to authorize access.")
    print(f"[mcp] If nothing opens, visit this URL:\n{url}\n")
    opener = _OPENER["open"]
    if opener:
        try:
            _window["handle"] = opener(url)
            return
        except Exception as e:
            print(f"[mcp] in-app auth window failed ({e}); using the browser.")
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _close_window():
    """Shut the auth window once the redirect has been captured."""
    h = _window.pop("handle", None)
    _window["handle"] = None
    if h is not None:
        try:
            h.destroy()
        except Exception:
            pass


async def _callback_handler():
    """Run a one-shot localhost server to capture the OAuth redirect."""
    loop = asyncio.get_event_loop()
    fut = loop.create_future()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            code = q.get("code", [None])[0]
            state = q.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;background:#0a0612;"
                b"color:#e8ecf7;text-align:center;padding-top:80px'>"
                b"<h2>\xe2\x9c\x93 PATROAM is authorized.</h2>"
                b"<p>You can close this tab.</p></body></html>")
            if code and not fut.done():
                loop.call_soon_threadsafe(fut.set_result, (code, state))

        def log_message(self, *a):
            pass

    srv = HTTPServer(("localhost", CALLBACK_PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        return await fut
    finally:
        srv.shutdown()
        _close_window()


def token_path(name):
    return os.path.join(_oauth_dir(), f"{name}.json")


def authorized(name):
    """True once this server has a token stored (so the panel can say so)."""
    try:
        with open(token_path(name), encoding="utf-8") as f:
            return bool(json.load(f).get("tokens"))
    except Exception:
        return False


def forget(name):
    """Sign out of a server — the next connect asks for authorization again."""
    try:
        os.remove(token_path(name))
        return True
    except OSError:
        return False


def make_oauth_provider(server_url, name, scope=None, client_id=None, client_secret=None):
    """Build an httpx.Auth that performs the MCP OAuth flow, or None if the SDK
    doesn't support it.

    If `client_id` is given, the client is pre-registered (the SDK skips dynamic
    registration — required for servers like Meta's that don't support it).
    """
    try:
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata
    except Exception:
        return None

    storage = FileTokenStorage(name)
    if client_id:
        storage.seed_client(client_id, client_secret, scope)

    metadata = OAuthClientMetadata(
        client_name="PATROAM",
        redirect_uris=[REDIRECT_URI],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post" if client_secret else "none",
        scope=scope,
    )
    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=_redirect_handler,
        callback_handler=_callback_handler,
    )
