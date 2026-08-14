"""Generic MCP client — lets PATROAM use external connectors (Meta Ads, etc.).

Connects to the MCP servers listed in config (see config.load_mcp_servers),
discovers their tools, and exposes them to the model through the same ACTION
tool-calling protocol as the built-in skills. Data-returning tools (like ad
stats) flow back into the conversation via the Agent's tool-result loop.

The MCP SDK is async; PATROAM is threaded. We run one asyncio loop on a
background thread, keep the connections open, and provide sync wrappers
(start / call_tool) that marshal onto that loop.

Each server gets its OWN exit stack, so one can be added or removed live from
the MCP panel without tearing down the others — a shared stack unwinds
everything at once, which made "remove this one server" impossible.

Everything is optional and lazy: no mcp.json → no servers → PATROAM is unchanged.
"""

import asyncio
import json
import logging
import os
import threading

from . import config


def _quiet_sdk_logs():
    """Stop the MCP SDK dumping tracebacks over the console.

    Its OAuth code calls `logger.exception("OAuth flow error")` on any failure,
    so a connector that simply needs a Client ID printed a full traceback at
    every launch. PATROAM reports the reason itself — in the Connectors panel
    and as one line here — so the SDK's copy is pure noise."""
    for name in ("mcp.client.auth", "mcp.client.auth.oauth2", "mcp"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.CRITICAL)
        lg.propagate = False


def _name_of(s):
    return (s.get("name") or s.get("url") or s.get("command") or "mcp").strip()


# Prefilled templates for the "add a connector" panel — a starting point, not a
# guarantee: endpoints move, so every field stays editable before you add it.
#   auth "oauth" → the server asks you to sign in, in a PATROAM window
#   auth "key"   → paste a token; it is stored in secrets.json, never in mcp.json
CATALOG = [
    {"id": "notion", "label": "Notion", "blurb": "Pages, databases, search",
     "url": "https://mcp.notion.com/mcp", "transport": "http", "auth": "oauth"},
    {"id": "linear", "label": "Linear", "blurb": "Issues, projects, cycles",
     "url": "https://mcp.linear.app/mcp", "transport": "http", "auth": "oauth"},
    {"id": "sentry", "label": "Sentry", "blurb": "Errors and releases",
     "url": "https://mcp.sentry.dev/mcp", "transport": "http", "auth": "oauth"},
    {"id": "github", "label": "GitHub", "blurb": "Repos, issues, pull requests",
     "url": "https://api.githubcopilot.com/mcp/", "transport": "http", "auth": "key",
     "secret": "MCP_GITHUB_TOKEN", "header": "Authorization",
     "prefix": "Bearer ", "hint": "A GitHub personal access token (ghp_…)"},
    {"id": "stripe", "label": "Stripe", "blurb": "Payments, customers, invoices",
     "url": "https://mcp.stripe.com", "transport": "http", "auth": "key",
     "secret": "MCP_STRIPE_KEY", "header": "Authorization",
     "prefix": "Bearer ", "hint": "A Stripe restricted key (rk_…)"},
    {"id": "context7", "label": "Context7", "blurb": "Up-to-date library docs",
     "url": "https://mcp.context7.com/mcp", "transport": "http", "auth": "none"},
    {"id": "filesystem", "label": "Filesystem", "blurb": "Read/write a folder you choose",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem",
                                "C:/Users/RAZER/Documents/GitHub"],
     "transport": "stdio", "auth": "none"},
    {"id": "playwright", "label": "Playwright", "blurb": "Drive a real browser",
     "command": "npx", "args": ["-y", "@playwright/mcp@latest"],
     "transport": "stdio", "auth": "none"},
]


def catalog():
    """The template list, marked with what is already installed."""
    have = {_name_of(s).lower() for s in config.load_mcp_servers()}
    return [dict(c, added=c["label"].lower() in have) for c in CATALOG]


class MCPClient:
    def __init__(self):
        self._loop = None
        self._thread = None
        self._tasks = {}      # server name -> the task holding its connection open
        self._stops = {}      # server name -> asyncio.Event that ends that task
        self._sessions = {}   # tool_name -> ClientSession
        self._tools = {}      # tool_name -> {"description", "schema", "server"}
        self._status = {}     # server name -> {"state", "tools", "error"}
        self._started = False

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def _ensure_loop(self):
        """One asyncio loop on a background thread, created on first use."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()
        return self._loop

    def _run(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop()).result(timeout=timeout)

    def start(self, timeout=30):
        """Connect to all configured servers (idempotent, blocking up to timeout)."""
        if self._started:
            return bool(self._tools)
        self._started = True
        servers = config.load_mcp_servers()
        if not servers:
            return False
        if not self.sdk_available():
            print("[mcp] SDK not installed (pip install mcp); skipping connectors.")
            for s in servers:
                self._status[_name_of(s)] = {"state": "error", "tools": 0,
                                             "error": "mcp SDK not installed"}
            return False
        _quiet_sdk_logs()
        for s in servers:
            name = _name_of(s)
            if s.get("disabled"):
                self._status[name] = {"state": "off", "tools": 0, "error": ""}
                continue
            # OAuth servers wait on a browser round-trip; the rest should be quick.
            st = self.connect(s, timeout=300 if self._is_oauth(s) else timeout)
            if st["state"] == "error":
                print(f"[mcp] {name}: {st['error']}")
        if self._tools:
            print(f"[mcp] {len(self._tools)} tool(s) ready: {', '.join(self._tools)}")
        return bool(self._tools)

    def start_background(self):
        """Connect without blocking the caller (used at app launch)."""
        threading.Thread(target=self.start, daemon=True).start()

    @staticmethod
    def sdk_available():
        try:
            import mcp  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _is_oauth(s):
        return bool(s.get("oauth") or s.get("auth") == "oauth")

    def _auth_for(self, s):
        if not self._is_oauth(s):
            return None
        from .mcp_oauth import make_oauth_provider
        return make_oauth_provider(
            s["url"], _name_of(s), s.get("scope"),
            s.get("client_id"), s.get("client_secret"))

    # ── connecting ────────────────────────────────────────────────────────────
    def connect(self, spec, timeout=30):
        """Connect ONE server (by spec or by name) and report how it went."""
        if isinstance(spec, str):
            spec = next((s for s in config.load_mcp_servers()
                         if _name_of(s) == spec), None)
            if spec is None:
                return {"state": "error", "tools": 0, "error": "no such server"}
        name = _name_of(spec)
        if not self.sdk_available():
            st = {"state": "error", "tools": 0, "error": "mcp SDK not installed"}
            self._status[name] = st
            return st
        _quiet_sdk_logs()
        self.disconnect(name)                       # reconnect = clean slate
        self._status[name] = {"state": "connecting", "tools": 0, "error": ""}
        try:
            n = self._run(self._open(config.expand_secrets(spec)), timeout)
            st = {"state": "ready" if n else "empty", "tools": n, "error": ""}
        except Exception as e:
            st = {"state": "error", "tools": 0, "error": self._explain(e)}
            self.disconnect(name)     # a half-built connection must not linger
        self._status[name] = st
        return st

    @classmethod
    def _explain(cls, e):
        # anyio wraps failures in ExceptionGroups: "unhandled errors in a
        # TaskGroup" tells you nothing, so dig out the exception underneath.
        inner = getattr(e, "exceptions", None)
        while inner:
            e = inner[0]
            inner = getattr(e, "exceptions", None)
        msg = f"{e}" or repr(e)
        if "Dynamic registration" in msg or "invalid_client_metadata" in msg:
            return ("This server needs a pre-registered OAuth client — add a "
                    "Client ID (and secret, if it gave you one).")
        if isinstance(e, (TimeoutError, asyncio.TimeoutError)):
            return "Timed out — the server never answered."
        return f"{type(e).__name__}: {msg}"[:300]

    async def _open(self, s):
        """Start the server's own task and wait until its tools are listed."""
        name = _name_of(s)
        ready = asyncio.get_event_loop().create_future()
        stop = asyncio.Event()
        self._stops[name] = stop
        self._tasks[name] = asyncio.ensure_future(self._serve(s, ready, stop))
        return await ready

    async def _serve(self, s, ready, stop):
        """Hold one server open until asked to stop.

        The connection is opened AND closed inside this single task: the MCP
        transports use anyio task groups, which refuse to be exited from a
        different task than entered them. Closing from elsewhere threw, the
        cleanup was skipped, and a stdio server's child process stayed alive.
        """
        from contextlib import AsyncExitStack
        from mcp import ClientSession
        name = _name_of(s)
        try:
            async with AsyncExitStack() as stack:
                transport = (s.get("transport")
                             or ("http" if s.get("url") else "stdio")).lower()
                if transport in ("http", "streamable-http", "streamable_http"):
                    from mcp.client.streamable_http import streamablehttp_client
                    read, write, _ = await stack.enter_async_context(
                        streamablehttp_client(s["url"], headers=s.get("headers"),
                                              auth=self._auth_for(s)))
                elif transport == "sse":
                    from mcp.client.sse import sse_client
                    read, write = await stack.enter_async_context(
                        sse_client(s["url"], headers=s.get("headers"),
                                   auth=self._auth_for(s)))
                else:
                    from mcp.client.stdio import stdio_client, StdioServerParameters
                    params = StdioServerParameters(
                        command=s["command"], args=s.get("args", []),
                        env={**os.environ, **(s.get("env") or {})})
                    read, write = await stack.enter_async_context(stdio_client(params))

                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                found = 0
                for t in (await session.list_tools()).tools:
                    self._sessions[t.name] = session
                    self._tools[t.name] = {
                        "description": (t.description or "").strip(),
                        "schema": getattr(t, "inputSchema", None),
                        "server": name,
                    }
                    found += 1
                if not ready.done():
                    ready.set_result(found)
                await stop.wait()
        except Exception as e:
            if not ready.done():
                ready.set_exception(e)
            else:
                # It connected and then dropped — say so rather than pretending.
                self._forget_tools(name)
                self._status[name] = {"state": "error", "tools": 0,
                                      "error": self._explain(e)}

    def _forget_tools(self, name):
        for tool, meta in list(self._tools.items()):
            if meta.get("server") == name:
                self._tools.pop(tool, None)
                self._sessions.pop(tool, None)

    def disconnect(self, name):
        """Close one server and forget its tools."""
        self._forget_tools(name)
        stop = self._stops.pop(name, None)
        task = self._tasks.pop(name, None)
        if stop is not None and self._loop:
            self._loop.call_soon_threadsafe(stop.set)
        if task is not None and self._loop:
            try:
                # Let the task unwind its own stack — that is what stops the
                # child process. Waiting also keeps a reconnect from racing it.
                self._run(asyncio.wait({task}, timeout=10), 12)
            except Exception:
                pass          # a server that died on its own can't close cleanly
        if name in self._status:
            self._status[name] = {"state": "off", "tools": 0, "error": ""}
        return True

    # ── the server list (what the MCP panel edits) ────────────────────────────
    def servers(self):
        """Configured servers + live status, with secrets left as ${NAME}."""
        from .mcp_oauth import authorized
        out = []
        for s in config.load_mcp_servers():
            name = _name_of(s)
            st = self._status.get(name) or {
                "state": "off" if s.get("disabled") else "idle", "tools": 0, "error": ""}
            out.append({
                "name": name,
                "url": s.get("url", ""),
                "command": " ".join([s.get("command", "")] + list(s.get("args") or [])).strip(),
                "transport": (s.get("transport")
                              or ("http" if s.get("url") else "stdio")).lower(),
                "oauth": self._is_oauth(s),
                "authorized": self._is_oauth(s) and authorized(name),
                "disabled": bool(s.get("disabled")),
                "state": st["state"], "tools": st.get("tools", 0),
                "error": st.get("error", ""),
                "tool_names": [t for t, m in self._tools.items() if m["server"] == name],
            })
        return out

    def add_server(self, spec, secrets=None, connect=True):
        """Add (or replace) a server, storing any secret separately.

        `secrets` maps a secret NAME to its value: the value goes to
        secrets.json and the spec keeps only ${NAME}, so the server list stays
        safe to read and copy."""
        for key, value in (secrets or {}).items():
            config.save_secret(key, value)
        name = _name_of(spec)
        if not name or name == "mcp":
            return {"ok": False, "error": "The server needs a name."}
        if not (spec.get("url") or spec.get("command")):
            return {"ok": False, "error": "Give it a URL or a command to run."}
        servers = [s for s in config.load_mcp_servers() if _name_of(s) != name]
        servers.append(spec)
        config.save_mcp_servers(servers)
        st = self.connect(spec, timeout=300 if self._is_oauth(spec) else 30) if connect else {}
        return {"ok": st.get("state") in ("ready", "empty", ""), "status": st, "name": name}

    def remove_server(self, name):
        self.disconnect(name)
        self._status.pop(name, None)
        config.save_mcp_servers([s for s in config.load_mcp_servers()
                                 if _name_of(s) != name])
        return {"ok": True}

    def set_disabled(self, name, disabled):
        servers = config.load_mcp_servers()
        for s in servers:
            if _name_of(s) == name:
                s["disabled"] = bool(disabled)
        config.save_mcp_servers(servers)
        if disabled:
            self.disconnect(name)
            return {"ok": True, "status": self._status.get(name)}
        return {"ok": True, "status": self.connect(name)}

    # ── exposure to the model ──────────────────────────────────────────────────
    def has_tool(self, name):
        return name in self._tools

    def tool_names(self):
        return list(self._tools)

    def tools_prompt(self):
        if not self._tools:
            return ""
        lines = ["Connected tools (call with the ACTION protocol, JSON args). Use "
                 "these to fetch live data the user asks for, then answer from the result:"]
        for name, meta in self._tools.items():
            args = ""
            schema = meta.get("schema") or {}
            props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
            if props:
                args = ", ".join(list(props)[:8])
            desc = meta["description"].split("\n")[0][:160]
            lines.append(f"- {name} {{{args}}} — {desc}")
        return "\n".join(lines)

    # ── calling ────────────────────────────────────────────────────────────────
    def call_tool(self, name, args, timeout=60):
        if name not in self._sessions or not self._loop:
            return None
        try:
            return self._run(self._call(name, args or {}), timeout)
        except Exception as e:
            return f"(tool error: {e})"

    async def _call(self, name, args):
        res = await self._sessions[name].call_tool(name, args)
        parts = []
        for c in (res.content or []):
            text = getattr(c, "text", None)
            if text:
                parts.append(text)
        out = "\n".join(parts).strip()
        if not out and getattr(res, "structuredContent", None):
            out = json.dumps(res.structuredContent)
        if getattr(res, "isError", False):
            out = "(error) " + out
        return out[:6000]   # cap so a huge result can't blow the context window


_GLOBAL = None


def get_mcp():
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = MCPClient()
    return _GLOBAL
