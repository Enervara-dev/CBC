"""
FastAPI application for the ENERVERA CBC pipeline.

Run from ``backend/src``:

    uvicorn api.main:app --reload

Startup wires the stateless Layer 3–5 services into a single :class:`CBCOrchestrator`
and registers it, plus a per-request DB session factory and a Layer-2 normalizer
factory (see ``api.routes``). Layer 2 is built fresh per request from a dedicated
``AsyncSession`` so concurrent requests never share a session. Shutdown disposes
the DB engine and the Neo4j driver.

Notes
-----
- Uses the lifespan context manager (the current FastAPI idiom) rather than the
  deprecated ``@app.on_event("startup")``.
- DB and Neo4j init are both best-effort: if the engine can't be created or Neo4j
  is down, startup still succeeds and the affected requests degrade (503 for DB,
  Layer-4 ``partial`` for Neo4j) instead of taking the whole service down.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI

# Make ``backend/src`` importable regardless of the launch CWD, so the service
# modules' bare imports (``from models...``, ``from services...``) resolve.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from api.auth import api_key_middleware  # noqa: E402
from api.routes import (  # noqa: E402
    init_db,
    init_normalizer_factory,
    init_orchestrator,
    init_report_generator,
    router,
)
from db.config import get_settings  # noqa: E402
from db.session import get_async_engine  # noqa: E402
from orchestration.cbc_orchestrator import CBCOrchestrator  # noqa: E402
from services.presentation.llm_client import GeminiLLMClient  # noqa: E402
from services.presentation.report_generator import ReportGenerator  # noqa: E402
from services.confidence_validation.confidence_calibrator import ConfidenceCalibrator  # noqa: E402
from services.confidence_validation.validation_engine import ConfidenceValidationEngine  # noqa: E402
from services.confidence_validation.validation_rules import load_validation_rules  # noqa: E402
from services.feature_generation.feature_definitions import FEATURE_REGISTRY  # noqa: E402
from services.feature_generation.feature_generator import FeatureGenerator  # noqa: E402
from services.graph_reasoning.graph_contract import load_contract  # noqa: E402
from services.graph_reasoning.neo4j_connection import Neo4jConnection  # noqa: E402
from services.graph_reasoning.reasoning_engine import GraphReasoningEngine  # noqa: E402
from services.normalization.normalizer import DataNormalizer  # noqa: E402
from services.normalization.reference_lookup import ReferenceRangeLookup  # noqa: E402
from services.normalization.unit_converter import UnitConverter  # noqa: E402

logger = logging.getLogger(__name__)


def _make_normalizer(session: AsyncSession) -> DataNormalizer:
    """Build a Layer-2 normalizer bound to one request's DB session."""
    return DataNormalizer(
        db_session=session,
        unit_converter=UnitConverter,
        reference_lookup=ReferenceRangeLookup(session),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the pipeline on startup; tear down resources on shutdown."""
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    engine: Any = None
    neo4j: Neo4jConnection | None = None
    try:
        # ── Layer 2 — per-request DB sessions (engine created once, lazily connects)
        try:
            engine = get_async_engine()
            sessionmaker = async_sessionmaker(
                engine, expire_on_commit=False, class_=AsyncSession
            )
            init_db(sessionmaker)
            init_normalizer_factory(_make_normalizer)
            app.state.engine = engine
            logger.info("Database engine + session factory ready.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Database unavailable at startup (%s); /api/analyze will 503.", exc)

        # ── Layer 3 — feature generation (stateless singleton)
        layer3_feature_gen = FeatureGenerator(FEATURE_REGISTRY)

        # ── Layer 4 — graph reasoning (contract-driven; Neo4j connect best-effort)
        neo4j = Neo4jConnection()
        try:
            await neo4j.connect()
            logger.info("Neo4j connected.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j unavailable at startup (%s); Layer 4 will degrade.", exc)
        # Load the shared Graph Contract once at startup. An incompatible contract
        # raises here (fail fast) rather than silently returning zero findings.
        graph_contract = load_contract()
        logger.info("Graph Contract loaded: v%s (%s)",
                    graph_contract.version, graph_contract.source_path)
        layer4_graph_reasoner = GraphReasoningEngine(
            neo4j_connection=neo4j, logger=logger, contract=graph_contract,
        )

        # ── Layer 5 — confidence validation (stateless singleton)
        rules = load_validation_rules()
        layer5_validator = ConfidenceValidationEngine(
            confidence_calibrator=ConfidenceCalibrator(rules),
            validation_rules=rules,
            logger=logger,
        )

        # Orchestrator holds the shared L3–L5 layers; Layer 2 is injected per request.
        orchestrator = CBCOrchestrator(
            layer2_normalizer=None,
            layer3_feature_generator=layer3_feature_gen,
            layer4_graph_reasoner=layer4_graph_reasoner,
            layer5_validator=layer5_validator,
            logger=logger,
        )
        init_orchestrator(orchestrator)
        app.state.neo4j = neo4j
        logger.info("CBC orchestrator initialized.")

        # ── Layer 6 — LLM presentation (best-effort; needs GEMINI_API_KEY)
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            init_report_generator(ReportGenerator(GeminiLLMClient(), logger=logger))
            logger.info("Layer 6 report generator ready (GEMINI_API_KEY set).")
        else:
            init_report_generator(None)
            logger.info("Layer 6 report generator disabled (no GEMINI_API_KEY); "
                        "/api/analyze reports=unavailable.")
    except Exception:
        logger.exception("Failed to initialize the CBC pipeline.")
        if neo4j is not None:
            await neo4j.disconnect()
        if engine is not None:
            await engine.dispose()
        raise

    try:
        yield
    finally:
        if getattr(app.state, "neo4j", None) is not None:
            await app.state.neo4j.disconnect()
        if getattr(app.state, "engine", None) is not None:
            await app.state.engine.dispose()
        logger.info("CBC pipeline resources released.")


app = FastAPI(
    title="ENERVERA CBC Analysis API",
    version="1.0.0",
    description="Knowledge-graph-first CBC interpretation pipeline (Layers 2→5).",
    lifespan=lifespan,
)
app.include_router(router)

# Enforced for every path except api.auth.PUBLIC_PATHS. Registered after the
# router so it wraps the real routes; `/api/health` stays open because the
# ALB health check cannot send a custom header.
app.middleware("http")(api_key_middleware)


@app.get("/", tags=["health"])
async def root() -> dict[str, str]:
    """Service banner."""
    return {"service": "ENERVERA CBC Analysis API", "docs": "/docs", "health": "/api/health"}
