"""
Neo4j connection wrapper for the Layer 4 graph-reasoning service.

A thin async wrapper around the official ``neo4j`` AsyncDriver: lifecycle
(``connect`` / ``disconnect``) and a generic ``query`` helper that returns plain
dicts. Layer 4 reasoning Cypher lives in ``reasoning_engine.py`` and is executed
through ``query`` — this class stays a pure connection/transport layer.

The reasoning queries traverse the **existing** clinical knowledge graph
(``Biomarker -[HAS_THRESHOLD]-> Threshold -[INDICATES]-> Disease``); there are no
InferenceRule nodes.

Configuration comes from the environment (loaded from the project ``.env``):
    NEO4J_URI       default bolt://localhost:7687
    NEO4J_USER      default neo4j
    NEO4J_PASSWORD  (no default — set it in .env)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Best-effort: make the project .env available to os.getenv (no override of real env).
try:
    from dotenv import load_dotenv

    _ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
    load_dotenv(_ENV_FILE, override=False)
except Exception:  # python-dotenv missing or .env absent — fall back to process env
    pass

logger = logging.getLogger(__name__)


class Neo4jConnectionError(Exception):
    """Raised when Neo4j cannot be connected to, or a query fails."""


class Neo4jConnection:
    """
    Async Neo4j connection manager + fact-driven inference queries.

    Usage
    -----
        conn = Neo4jConnection()
        await conn.connect()
        rows = await conn.query("MATCH (b:Biomarker) RETURN b.name AS name LIMIT $k", {"k": 5})
        await conn.disconnect()
    """

    def __init__(self) -> None:
        self.uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        # NEO4J_USERNAME is the documented name; NEO4J_USER kept as a fallback.
        self.user: str = os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER", "neo4j")
        self.password: Optional[str] = os.getenv("NEO4J_PASSWORD")
        self.logger = logging.getLogger(__name__)
        self._driver: Any = None  # neo4j.AsyncDriver once connected

    def _candidate_uris(self) -> List[str]:
        """
        The configured URI, plus a ``bolt://`` fallback for the ``neo4j://``
        routing scheme (a single, non-cluster instance can't answer routing
        requests → "Unable to retrieve routing information"; the direct ``bolt``
        scheme works). ``neo4j+s://`` → ``bolt+s://``, etc.
        """
        uris = [self.uri]
        if self.uri.startswith("neo4j"):
            fallback = "bolt" + self.uri[len("neo4j"):]  # neo4j[+s]://… → bolt[+s]://…
            if fallback not in uris:
                uris.append(fallback)
        return uris

    # ── Lifecycle ────────────────────────────────────────────────────────────
    async def connect(self) -> None:
        """
        Create the async driver and verify connectivity.

        Tries the configured ``NEO4J_URI`` first, then a ``bolt://`` fallback if
        a ``neo4j://`` routing URI fails (single-instance servers).

        Raises
        ------
        Neo4jConnectionError
            If the driver is unavailable or no candidate URI can be reached.
        """
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError as exc:
            raise Neo4jConnectionError(
                "The 'neo4j' driver is not installed. Run: pip install neo4j"
            ) from exc

        errors: List[str] = []
        for uri in self._candidate_uris():
            driver = None
            try:
                driver = AsyncGraphDatabase.driver(uri, auth=(self.user, self.password))
                await driver.verify_connectivity()
                self._driver = driver
                self.uri = uri  # remember the URI that actually worked
                self.logger.info("Connected to Neo4j at %s", uri)
                return
            except Exception as exc:
                if driver is not None:
                    await driver.close()
                errors.append(f"{uri}: {exc}")
                self.logger.warning("Neo4j connect attempt failed for %s: %s", uri, exc)

        self._driver = None
        raise Neo4jConnectionError(
            "Could not connect to Neo4j. Check NEO4J_URI/credentials in .env and that "
            "the server is reachable from this host. Tried — " + " | ".join(errors)
        )

    async def disconnect(self) -> None:
        """Close the driver if it is open."""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
            self.logger.info("Disconnected from Neo4j")

    # ── Generic query ────────────────────────────────────────────────────────
    async def query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Execute a Cypher query and return the results as a list of dicts.

        Parameters
        ----------
        cypher : str
            The Cypher statement.
        params : Optional[Dict[str, Any]]
            Query parameters (``$name`` placeholders).

        Returns
        -------
        List[Dict[str, Any]]
            One dict per record (via ``record.data()``).

        Raises
        ------
        Neo4jConnectionError
            If not connected, or the query fails.
        """
        if self._driver is None:
            raise Neo4jConnectionError("Not connected — call connect() first.")
        try:
            async with self._driver.session() as session:
                result = await session.run(cypher, params or {})
                return [record.data() async for record in result]
        except Exception as exc:
            self.logger.error("Cypher query failed: %s", exc)
            raise Neo4jConnectionError(f"Query failed: {exc}") from exc
