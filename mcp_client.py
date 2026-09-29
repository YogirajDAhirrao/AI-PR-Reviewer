"""
Thin wrapper around the MCP Python SDK for connecting to a remote
Streamable HTTP MCP server (e.g. GitHub's official remote server).
"""
from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"


class MCPConnection:
    """A long-lived connection to one MCP server."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self.url = url
        self.headers = headers or {}
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def connect(self) -> "MCPConnection":
        read, write, _ = await self._stack.enter_async_context(
            streamablehttp_client(self.url, headers=self.headers)
        )
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def list_tools(self):
        assert self.session, "call connect() first"
        return (await self.session.list_tools()).tools

    async def call(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Call a tool by name. Returns (text_result, is_error)."""
        assert self.session, "call connect() first"
        result = await self.session.call_tool(name, args)
        text = "\n".join(p.text for p in result.content if getattr(p, "type", None) == "text")
        return text, bool(result.isError)

    async def close(self):
        await self._stack.aclose()


def github_connection(pat: str | None = None) -> MCPConnection:
    """Build (but do not open) a connection to the GitHub remote MCP server."""
    token = pat or os.environ["GITHUB_PAT"]
    return MCPConnection(GITHUB_MCP_URL, headers={"Authorization": f"Bearer {token}"})
