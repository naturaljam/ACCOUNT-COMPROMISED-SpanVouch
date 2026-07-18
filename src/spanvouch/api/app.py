from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI

from spanvouch.adapters.frameworks.langgraph_review import LangGraphReviewWorkflow
from spanvouch.adapters.models.deepseek import DeepSeekConfig, DeepSeekProvider
from spanvouch.adapters.storage.sqlite import SQLiteReviewRepository
from spanvouch.api.routes.diagnoses import build_diagnosis_router
from spanvouch.api.routes.diagnosis_reviews import build_diagnosis_review_router
from spanvouch.api.routes.health import router as health_router
from spanvouch.api.routes.traces import build_trace_router
from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.diagnosis.engine import DiagnosisEngine
from spanvouch.diagnosis.errors import ProviderConfigurationError
from spanvouch.diagnosis.llm_diagnoser import LlmDiagnoser
from spanvouch.diagnosis.protocols import Diagnoser
from spanvouch.diagnosis.rule_diagnoser import RuleDiagnoser
from spanvouch.invariants.supportlab import supportlab_rules
from spanvouch.review.application import ReviewApplication
from spanvouch.review.policy import DEFAULT_REVIEW_POLICY_VERSION
from spanvouch.review.protocols import ReviewRepository
from spanvouch.review.reviser import DiagnosisReviser
from spanvouch.trace.repository import InMemoryTraceRepository, TraceRepository
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine
from spanvouch.verification.protocols import Verifier
from spanvouch.verification.semantic import SemanticVerifier

DEFAULT_REVIEW_DATABASE = Path(".data/spanvouch.db")


def _default_runtime() -> tuple[dict[str, Diagnoser], DeterministicVerifier, Verifier | None]:
    diagnosers, deterministic_verifier = _deterministic_runtime()
    semantic_verifier: Verifier | None = None
    try:
        deepseek_config = DeepSeekConfig.from_env()
    except ProviderConfigurationError:
        pass
    else:
        provider = DeepSeekProvider(deepseek_config)
        diagnosers[DiagnoserKind.DEEPSEEK.value] = LlmDiagnoser(provider)
        semantic_verifier = SemanticVerifier(
            provider,
            provider_id="deepseek",
            model="deepseek-v4-flash",
        )
    return diagnosers, deterministic_verifier, semantic_verifier


def _deterministic_runtime() -> tuple[dict[str, Diagnoser], DeterministicVerifier]:
    engine = InvariantEngine(supportlab_rules())
    diagnosers: dict[str, Diagnoser] = {
        DiagnoserKind.RULES.value: RuleDiagnoser(engine)
    }
    deterministic_verifier = DeterministicVerifier(
        engine,
        policy_version=DEFAULT_REVIEW_POLICY_VERSION,
    )
    return diagnosers, deterministic_verifier


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_database_parent(database: str | Path) -> None:
    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def build_default_diagnosis_service() -> DiagnosisEngine:
    diagnosers, _, _ = _default_runtime()
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
    trace_store = trace_repository or InMemoryTraceRepository()
    database = review_database or os.environ.get("SPANVOUCH_DB_PATH") or DEFAULT_REVIEW_DATABASE
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
        diagnosis = DiagnosisEngine(diagnosers)
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
