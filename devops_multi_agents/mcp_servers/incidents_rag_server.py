"""
MCP server – Incident RAG tool.

Exposes three tools to MCP clients (agents):

  search_similar_incidents(query, top_k)
      → retrieves the top_k most similar incidents from the ChromaDB knowledge
        base ranked by cosine similarity.

  add_incident_solution(incident_id, title, description, solution, category, severity)
      → inserts a new incident+solution pair into the knowledge base so future
        occurrences can be matched automatically.

  list_knowledge_base()
      → returns all seeded entry titles for inspection / debugging.

Run as a standalone process (stdio transport):
    python -m devops_multi_agents.mcp_servers.incidents_rag_server

The IncidentResponseAgent spawns this server via subprocess and communicates
over stdin/stdout using the MCP protocol.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Lazy import – fail gracefully if the `mcp` package is missing
# ---------------------------------------------------------------------------
try:
    from mcp.server.fastmcp import FastMCP

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from devops_multi_agents.mcp_servers.rag_knowledge_base import IncidentKnowledgeBase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] incidents_rag_server - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("incidents_rag_server")

# ---------------------------------------------------------------------------
# Singleton knowledge base (loaded once when the server starts)
# ---------------------------------------------------------------------------
_kb: IncidentKnowledgeBase | None = None


def _get_kb() -> IncidentKnowledgeBase:
    global _kb
    if _kb is None:
        _kb = IncidentKnowledgeBase()
    return _kb


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

if MCP_AVAILABLE:
    mcp = FastMCP(
        name="incidents-rag-server",
        instructions=(
            "Provides semantic search over a DevOps incident knowledge base. "
            "Use `search_similar_incidents` to find matching incidents and "
            "retrieve their remediation playbooks."
        ),
    )

    # ── Tool 1: search ───────────────────────────────────────────────────────
    @mcp.tool()
    def search_similar_incidents(query: str, top_k: int = 3) -> str:
        """
        Search the incident knowledge base for entries semantically similar
        to `query`.

        Args:
            query:  Free-text description of the incident (error messages,
                    service names, symptoms, etc.).
            top_k:  Maximum number of results to return (default 3).

        Returns:
            JSON-encoded list of matches, each with fields:
            rank, similarity (0-1), id, title, category, severity,
            solution, auto_commands, fix_commands.
        """
        kb = _get_kb()
        hits = kb.search(query, top_k=max(1, min(top_k, 10)))
        logger.info("search_similar_incidents: query=%r → %d hits", query[:80], len(hits))
        return json.dumps(hits, ensure_ascii=False)

    # ── Tool 2: add ──────────────────────────────────────────────────────────
    @mcp.tool()
    def add_incident_solution(
        incident_id: str,
        title: str,
        description: str,
        solution: str,
        category: str = "custom",
        severity: str = "medium",
    ) -> str:
        """
        Persist a new incident+solution pair in the knowledge base so it can
        be recalled in future runs.

        Args:
            incident_id:  Unique identifier (e.g. MongoDB ObjectId as string).
            title:        Short incident title.
            description:  Detailed description of the failure symptoms.
            solution:     Step-by-step remediation playbook.
            category:     One of: kubernetes, docker, infrastructure, security,
                          network, deployment, custom.
            severity:     One of: low, medium, high, critical.

        Returns:
            JSON with {"success": true/false}.
        """
        kb = _get_kb()
        ok = kb.add_incident(
            incident_id=incident_id,
            title=title,
            description=description,
            solution=solution,
            category=category,
            severity=severity,
        )
        logger.info("add_incident_solution: id=%s ok=%s", incident_id, ok)
        return json.dumps({"success": ok, "incident_id": incident_id})

    # ── Tool 3: list ─────────────────────────────────────────────────────────
    @mcp.tool()
    def list_knowledge_base() -> str:
        """
        List all entries currently stored in the knowledge base.

        Returns:
            JSON with {"count": N, "entries": [{"id":…,"title":…,"category":…}]}.
        """
        from devops_multi_agents.mcp_servers.rag_knowledge_base import SEED_INCIDENTS

        kb = _get_kb()
        entries = [
            {"id": inc["id"], "title": inc["title"], "category": inc["category"]}
            for inc in SEED_INCIDENTS
        ]
        return json.dumps({"count": kb.count(), "entries": entries}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    if not MCP_AVAILABLE:
        logger.error(
            "The `mcp` package is not installed. "
            "Run: pip install mcp"
        )
        sys.exit(1)

    logger.info("Starting Incidents RAG MCP server (stdio transport)…")
    # Pre-warm the knowledge base before accepting connections
    _get_kb()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
