from datetime import UTC, datetime

import pytest

from spanvouch.trace_ir.models import TraceIR, TraceSpan
from spanvouch.trace_ir.repository import InMemoryTraceRepository, TraceConflictError


def make_trace(*, trace_id: str = "trace-repository-1", run_id: str = "run-1") -> TraceIR:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    return TraceIR(
        trace_id=trace_id,
        run_id=run_id,
        spans=[
            TraceSpan(
                trace_id=trace_id,
                span_id="root",
                name="supportlab.run",
                kind="agent",
                status="ok",
                started_at=now,
                ended_at=now,
            )
        ],
    )


async def test_save_allows_idempotent_retry() -> None:
    repository = InMemoryTraceRepository()
    trace = make_trace()

    first_saved = await repository.save(trace)
    retry_saved = await repository.save(trace)

    assert first_saved == trace
    assert retry_saved == trace
    assert await repository.get(trace.trace_id) == trace


async def test_save_raises_trace_conflict_for_different_content_with_same_id() -> None:
    repository = InMemoryTraceRepository()
    await repository.save(make_trace())

    with pytest.raises(TraceConflictError, match="trace_id conflict: trace-repository-1"):
        await repository.save(make_trace(run_id="run-2"))
