"""
Synchronous wrapper around the async MCP client.

Spawns the incidents_rag_server as a subprocess and communicates with it
over stdio using the official `mcp` Python SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False

# Absolute path to the MCP server script
_SERVER_MODULE = "devops_multi_agents.mcp_servers.incidents_rag_server"


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

async def _async_search(query: str, top_k: int) -> list[dict[str, Any]]:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", _SERVER_MODULE],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_similar_incidents",
                {"query": query, "top_k": top_k},
            )
            # result.content is a list of TextContent objects
            text = result.content[0].text if result.content else "[]"
            return json.loads(text)


async def _async_add(
    incident_id: str,
    title: str,
    description: str,
    solution: str,
    category: str,
    severity: str,
) -> bool:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", _SERVER_MODULE],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "add_incident_solution",
                {
                    "incident_id": incident_id,
                    "title": title,
                    "description": description,
                    "solution": solution,
                    "category": category,
                    "severity": severity,
                },
            )
            text = result.content[0].text if result.content else "{}"
            return json.loads(text).get("success", False)


# ---------------------------------------------------------------------------
# Public synchronous API
# ---------------------------------------------------------------------------

class RagMcpClient:
    """
    Synchronous façade that spawns the MCP server per-call and tears it
    down cleanly.  Each method runs a fresh asyncio event loop because the
    agent itself is synchronous.
    """

    def __init__(self) -> None:
        if not MCP_SDK_AVAILABLE:
            logger.warning(
                "mcp SDK not installed – MCP calls will be no-ops. "
                "Install with: pip install mcp"
            )

    # ------------------------------------------------------------------
    def search_similar_incidents(
        self, query: str, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """
        Query the RAG knowledge base for incidents similar to `query`.

        Returns a list of matches (may be empty if MCP SDK is unavailable
        or no matches found above the configured threshold).
        """
        if not MCP_SDK_AVAILABLE:
            logger.warning("MCP SDK unavailable – skipping RAG search.")
            return []
        try:
            return asyncio.run(_async_search(query, top_k))
        except Exception as exc:
            logger.error("MCP search failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    def add_incident_solution(
        self,
        incident_id: str,
        title: str,
        description: str,
        solution: str,
        category: str = "custom",
        severity: str = "medium",
    ) -> bool:
        """Persist a resolved incident in the knowledge base for future recall."""
        if not MCP_SDK_AVAILABLE:
            return False
        try:
            return asyncio.run(
                _async_add(incident_id, title, description, solution, category, severity)
            )
        except Exception as exc:
            logger.error("MCP add_incident failed: %s", exc)
            return False
