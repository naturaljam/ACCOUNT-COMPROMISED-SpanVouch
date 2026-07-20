from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from spanvouch.contracts.trace import SpanKind, SpanStatus, TraceIR, TraceSpan
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.corpus import CorpusEntry, CorpusManifestMetadata
from spanvouch.labs.runtime import (
    ExecutionProvenance,
    ExecutionRecord,
    ExecutionStatus,
    FrameworkId,
    ParityResult,
    RuntimeConfig,
    RuntimeState,
    ToolObservation,
)


def make_record(
    *,
    scenario_id: str = "scenario-1",
    framework_id: FrameworkId = FrameworkId.LANGGRAPH,
    repetition: int = 1,
    seed: int = 20260719,
) -> ExecutionRecord:
    started_at = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    trace_id = f"trace-{framework_id.value}-{repetition}"
    trace = TraceIR(
        trace_id=trace_id,
        run_id=f"run-{framework_id.value}-{repetition}",
        spans=[
            TraceSpan(
                trace_id=trace_id,
                span_id="span-root",
                name="supportlab.run",
                kind=SpanKind.AGENT,
                status=SpanStatus.OK,
                started_at=started_at,
                ended_at=started_at + timedelta(seconds=2),
                attributes={"run.outcome": "succeeded"},
            )
        ],
    )
    config = RuntimeConfig(
        seed=seed,
        repetition=repetition,
        max_steps=8,
        timeout_seconds=5.0,
        max_retries=0,
        max_tool_calls=8,
    )
    state = RuntimeState.initial().with_observation(
        ToolObservation(
            tool_name="get_order",
            result={"order_id": "order-1"},
            status="ok",
            retryable=False,
        )
    ).with_final("Refund created.")
    from spanvouch.labs.runtime import LabScenario

    scenario = LabScenario(
        scenario_id=scenario_id,
        template_id="template-1",
        domain="supportlab",
        failure_family="clean",
        user_request="Refund the damaged item.",
        parameters={"order_id": "order-1"},
        injection={"enabled": False},
        tool_contract_sha256="a" * 64,
        terminal_predicate_id="refund-created",
        allowed_evidence_selectors=("tool.name", "tool.status"),
    )
    return ExecutionRecord.from_run(
        scenario=scenario,
        run_config=config,
        framework_id=framework_id,
        framework_version="0.6.7",
        trace=trace,
        state=state,
        status=ExecutionStatus.SUCCEEDED,
        failure=None,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        provenance=ExecutionProvenance(
            git_commit="b" * 40,
            package_version="0.2.0",
            dependency_lock_sha256="c" * 64,
            dataset_manifest_sha256="d" * 64,
            environment_sha256="e" * 64,
            tool_versions={"supportlab": "1.0"},
            runtime_versions={"python": "3.12.10"},
            dirty_worktree=False,
        ),
    )


@pytest.fixture
def record() -> ExecutionRecord:
    return make_record()


@pytest.fixture
def second_record() -> ExecutionRecord:
    return make_record(
        scenario_id="scenario-2",
        framework_id=FrameworkId.AUTOGEN,
        repetition=2,
        seed=20260720,
    )


@pytest.fixture
def parity_results() -> tuple[ParityResult, ...]:
    return (ParityResult(status="matched"),)


@pytest.fixture
def manifest_metadata(parity_results: tuple[ParityResult, ...]) -> CorpusManifestMetadata:
    return CorpusManifestMetadata(
        corpus_id="supportlab-pilot-20260719",
        mode="pilot",
        experiment_config_sha256="1" * 64,
        git_commit="2" * 40,
        dependency_lock_sha256="3" * 64,
        dataset_manifest_sha256="4" * 64,
        created_at_utc=datetime(2026, 7, 19, 9, 0, tzinfo=UTC),
        parity_results_sha256=canonical_sha256(list(parity_results)),
    )


@pytest.fixture
def entry(record: ExecutionRecord) -> CorpusEntry:
    return CorpusEntry.from_record(record)
