from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI

from afc.api.routes.diagnoses import build_diagnosis_router
from afc.api.routes.diagnosis_reviews import build_diagnosis_review_router
from afc.api.routes.health import router as health_router
from afc.api.routes.traces import build_trace_router
from afc.diagnosis.deepseek import DeepSeekConfig, DeepSeekProvider
from afc.diagnosis.errors import ProviderConfigurationError
from afc.diagnosis.llm_diagnoser import LlmDiagnoser
from afc.diagnosis.models import DiagnoserKind
from afc.diagnosis.protocols import Diagnoser
from afc.diagnosis.rule_diagnoser import RuleDiagnoser
from afc.diagnosis.service import DiagnosisService
from afc.invariants.engine import InvariantEngine
from afc.invariants.supportlab import supportlab_rules
from afc.review.evidence_verifier import EvidenceVerifier
from afc.review.policy import DEFAULT_REVIEW_POLICY_VERSION
from afc.review.protocols import ReviewRepository, Verifier
from afc.review.reviser import DiagnosisReviser
from afc.review.semantic_verifier import SemanticVerifier
from afc.review.service import ReviewService
from afc.review.sqlite_repository import SQLiteReviewRepository
from afc.review.workflow import ReviewWorkflow
from afc.trace_ir.repository import InMemoryTraceRepository, TraceRepository

DEFAULT_REVIEW_DATABASE = Path(".data/afc.db")


def _default_runtime() -> tuple[
    dict[DiagnoserKind, Diagnoser], EvidenceVerifier, Verifier | None
]:
    diagnosers, deterministic_verifier = _deterministic_runtime()
    semantic_verifier: Verifier | None = None
    try:
        deepseek_config = DeepSeekConfig.from_env()
    except ProviderConfigurationError:
        pass
    else:
        provider = DeepSeekProvider(deepseek_config)
        diagnosers[DiagnoserKind.DEEPSEEK] = LlmDiagnoser(provider)
        semantic_verifier = SemanticVerifier(provider)
    return diagnosers, deterministic_verifier, semantic_verifier


def _deterministic_runtime() -> tuple[dict[DiagnoserKind, Diagnoser], EvidenceVerifier]:
    engine = InvariantEngine(supportlab_rules())
    diagnosers: dict[DiagnoserKind, Diagnoser] = {
        DiagnoserKind.RULES: RuleDiagnoser(engine)
    }
    deterministic_verifier = EvidenceVerifier(
        engine,
        policy_version=DEFAULT_REVIEW_POLICY_VERSION,
    )
    return diagnosers, deterministic_verifier


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_database_parent(database: str | Path) -> None:
    value = os.fspath(database)
    if value == ":memory:" or value.startswith("file:"):
        return
    Path(value).expanduser().parent.mkdir(parents=True, exist_ok=True)


def build_default_diagnosis_service() -> DiagnosisService:
    diagnosers, _, _ = _default_runtime()
    return DiagnosisService(diagnosers)


def _build_review_service(
    *,
    diagnosis_service: DiagnosisService,
    repository: ReviewRepository,
    diagnosers: Mapping[DiagnoserKind, Diagnoser],
    deterministic_verifier: Verifier,
    semantic_verifier: Verifier | None,
) -> ReviewService:
    reviser = DiagnosisReviser(diagnosers)
    workflow = ReviewWorkflow(
        repository=repository,
        deterministic_verifier=deterministic_verifier,
        semantic_verifier=semantic_verifier,
        reviser=reviser,
        id_factory=_new_id,
        clock=_utc_now,
        lease_owner=f"api-{uuid4()}",
        lease_duration=timedelta(seconds=30),
    )
    return ReviewService(
        diagnosis_service=diagnosis_service,
        repository=repository,
        workflow=workflow,
        deterministic_verifier=deterministic_verifier,
        id_factory=_new_id,
        clock=_utc_now,
    )


def create_app(
    trace_repository: TraceRepository | None = None,
    diagnosis_service: DiagnosisService | None = None,
    *,
    review_repository: ReviewRepository | None = None,
    review_service: ReviewService | None = None,
    review_database: str | Path | None = None,
) -> FastAPI:
    trace_store = trace_repository or InMemoryTraceRepository()
    database = review_database or os.environ.get("AFC_DB_PATH") or DEFAULT_REVIEW_DATABASE
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
        diagnosers, deterministic_verifier, semantic_verifier = _default_runtime()
        diagnosis = DiagnosisService(diagnosers)
    else:
        diagnosers = {}
        _, deterministic_verifier = _deterministic_runtime()
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
        if review_store is not None:
            if managed_database is not None:
                _ensure_database_parent(managed_database)
            await review_store.initialize()
        yield

    application = FastAPI(
        title="Agent Failure Clinic",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(build_trace_router(trace_store))
    application.include_router(build_diagnosis_router(trace_store, diagnosis))
    application.include_router(build_diagnosis_review_router(trace_store, review))
    return application


app = create_app()
