from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI

from spanvouch.adapters.frameworks.langgraph_review import LangGraphReviewWorkflow
from spanvouch.adapters.storage.sqlite import SQLiteReviewRepository
from spanvouch.adapters.storage.sqlite_trace import SQLiteTraceRepository
from spanvouch.api.composition import default_runtime, deterministic_runtime
from spanvouch.api.routes.diagnoses import build_diagnosis_router
from spanvouch.api.routes.diagnosis_reviews import build_diagnosis_review_router
from spanvouch.api.routes.health import router as health_router
from spanvouch.api.routes.traces import build_trace_router
from spanvouch.diagnosis.engine import DiagnosisEngine
from spanvouch.diagnosis.protocols import Diagnoser
from spanvouch.review.application import ReviewApplication
from spanvouch.review.protocols import ReviewRepository
from spanvouch.review.reviser import DiagnosisReviser
from spanvouch.trace.repository import TraceRepository
from spanvouch.verification.protocols import Verifier

DEFAULT_REVIEW_DATABASE = Path(".data/spanvouch.db")


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_database_parent(database: str | Path) -> None:
    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def build_default_diagnosis_service() -> DiagnosisEngine:
    diagnosers, _, _ = default_runtime()
    return DiagnosisEngine(diagnosers)


def _build_review_service(
    *,
    diagnosis_service: DiagnosisEngine,
    repository: ReviewRepository,
    diagnosers: Mapping[str, Diagnoser],
    deterministic_verifier: Verifier,
    semantic_verifier: Verifier | None,
) -> ReviewApplication:
    reviser = DiagnosisReviser(diagnosers)
    workflow = LangGraphReviewWorkflow(
        repository=repository,
        deterministic_verifier=deterministic_verifier,
        semantic_verifier=semantic_verifier,
        reviser=reviser,
        id_factory=_new_id,
        clock=_utc_now,
        lease_owner="api",
        lease_duration=timedelta(seconds=30),
    )
    return ReviewApplication(
        diagnosis_service=diagnosis_service,
        repository=repository,
        workflow=workflow,
        deterministic_verifier=deterministic_verifier,
        id_factory=_new_id,
        clock=_utc_now,
    )


def create_app(
    trace_repository: TraceRepository | None = None,
    diagnosis_service: DiagnosisEngine | None = None,
    *,
    review_repository: ReviewRepository | None = None,
    review_service: ReviewApplication | None = None,
    review_database: str | Path | None = None,
) -> FastAPI:
    database = review_database or os.environ.get("SPANVOUCH_DB_PATH") or DEFAULT_REVIEW_DATABASE
    managed_trace_store: SQLiteTraceRepository | None = None
    if trace_repository is not None:
        trace_store = trace_repository
    else:
        managed_trace_store = SQLiteTraceRepository(database)
        trace_store = managed_trace_store
    review_store: ReviewRepository | None
    managed_database: str | Path | None = None
    if review_repository is not None:
        review_store = review_repository
    elif review_service is None:
        review_store = SQLiteReviewRepository(database)
        managed_database = database
    else:
        review_store = None

    if diagnosis_service is None:
        diagnosers, deterministic_verifier, semantic_verifier = default_runtime()
        diagnosis = DiagnosisEngine(diagnosers)
    else:
        diagnosers = {}
        _, deterministic_verifier = deterministic_runtime()
        semantic_verifier = None
        diagnosis = diagnosis_service

    if review_service is not None:
        review = review_service
    else:
        assert review_store is not None
        review = _build_review_service(
            diagnosis_service=diagnosis,
            repository=review_store,
            diagnosers=diagnosers,
            deterministic_verifier=deterministic_verifier,
            semantic_verifier=semantic_verifier,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if managed_database is not None or managed_trace_store is not None:
            _ensure_database_parent(database)
        if managed_trace_store is not None:
            await managed_trace_store.initialize()
        if review_store is not None:
            await review_store.initialize()
        yield

    application = FastAPI(
        title="SpanVouch",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(build_trace_router(trace_store))
    application.include_router(build_diagnosis_router(trace_store, diagnosis))
    application.include_router(build_diagnosis_review_router(trace_store, review))
    return application


app = create_app()
