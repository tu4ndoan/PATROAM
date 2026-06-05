"""Generic MCP client — lets PATROAM use external connectors (Meta Ads, etc.).

Connects to the MCP servers listed in config (see config.load_mcp_servers),
discovers their tools, and exposes them to the model through the same ACTION
tool-calling protocol as the built-in skills. Data-returning tools (like ad
stats) flow back into the conversation via the Agent's tool-result loop.

The MCP SDK is async; PATROAM is threaded. We run one asyncio loop on a
background thread, keep the connections open with an AsyncExitStack, and provide
sync wrappers (start / call_tool) that marshal onto that loop.

Everything is optional and lazy: no mcp.json → no servers → PATROAM is unchanged.
"""

import asyncio
import json
import os
import threading

from . import config


class MCPClient:
    def __init__(self):
        self._loop = None
        self._thread = None
        self._stack = None
        self._sessions = {}   # tool_name -> ClientSession
        self._tools = {}      # tool_name -> {"description", "schema", "server"}
        self._started = False

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self, timeout=30):
        """Connect to all configured servers (idempotent, blocking up to timeout)."""
        if self._started:
            return bool(self._tools)
        self._started = True
        servers = config.load_mcp_servers()
        if not servers:
            return False
        try:
            import mcp  # noqa: F401
        except ImportError:
            print("[mcp] SDK not installed (pip install mcp); skipping connectors.")
            return False

        # OAuth servers may need the user to authorize in a browser — allow time.
        if any(self._is_oauth(s) for s in servers):
            timeout = max(timeout, 300)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        fut = asyncio.run_coroutine_threadsafe(self._connect_all(servers), self._loop)
        try:
            fut.result(timeout=timeout)
        except Exception as e:
            print(f"[mcp] connection error: {e}")
        if self._tools:
            print(f"[mcp] {len(self._tools)} tool(s) ready: {', '.join(self._tools)}")
        return bool(self._tools)

    async def _connect_all(self, servers):
        from contextlib import AsyncExitStack
        self._stack = AsyncExitStack()
        for s in servers:
            try:
                await self._connect(s)
            except Exception as e:
                msg = f"{e!r}"
                if "Dynamic registration" in msg or "invalid_client_metadata" in msg:
                    print(f"[mcp] '{s.get('name', '?')}' requires a pre-registered "
                          "OAuth client — add \"client_id\" (and \"client_secret\" if "
                          "given) to its entry in mcp.json.")
                else:
                    print(f"[mcp] server '{s.get('name', '?')}' failed: {e}")

    @staticmethod
    def _is_oauth(s):
        return bool(s.get("oauth") or s.get("auth") == "oauth")

    def _auth_for(self, s):
        if not self._is_oauth(s):
            return None
        from .mcp_oauth import make_oauth_provider
        return make_oauth_provider(
            s["url"], s.get("name", "mcp"), s.get("scope"),
            s.get("client_id"), s.get("client_secret"))

    async def _connect(self, s):
        from mcp import ClientSession
        transport = (s.get("transport") or ("http" if s.get("url") else "stdio")).lower()
        if transport in ("http", "streamable-http", "streamable_http"):
            from mcp.client.streamable_http import streamablehttp_client
            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(s["url"], headers=s.get("headers"), auth=self._auth_for(s)))
        elif transport == "sse":
            from mcp.client.sse import sse_client
            read, write = await self._stack.enter_async_context(
                sse_client(s["url"], headers=s.get("headers"), auth=self._auth_for(s)))
        else:
            from mcp.client.stdio import stdio_client, StdioServerParameters
            params = StdioServerParameters(
                command=s["command"], args=s.get("args", []),
                env={**os.environ, **(s.get("env") or {})})
            read, write = await self._stack.enter_async_context(stdio_client(params))

        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        for t in (await session.list_tools()).tools:
            self._sessions[t.name] = session
            self._tools[t.name] = {
                "description": (t.description or "").strip(),
                "schema": getattr(t, "inputSchema", None),
                "server": s.get("name", "mcp"),
            }

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
        fut = asyncio.run_coroutine_threadsafe(self._call(name, args or {}), self._loop)
        try:
            return fut.result(timeout=timeout)
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
