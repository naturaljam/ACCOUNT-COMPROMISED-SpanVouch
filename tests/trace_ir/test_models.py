from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from afc.trace_ir.models import SpanKind, SpanStatus, TraceIR, TraceSpan

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def make_span(span_id: str, parent_span_id: str | None = None) -> TraceSpan:
    return TraceSpan(
        trace_id="trace-1",
        span_id=span_id,
        parent_span_id=parent_span_id,
        name="supportlab.step",
        kind=SpanKind.AGENT,
        status=SpanStatus.OK,
        started_at=NOW,
        ended_at=NOW + timedelta(milliseconds=10),
        attributes={"agent.name": "supportlab"},
    )


def test_trace_accepts_a_connected_span_tree() -> None:
    trace = TraceIR(
        trace_id="trace-1",
        run_id="run-1",
        spans=[make_span("root"), make_span("child", "root")],
    )

    assert trace.span_by_id("child").parent_span_id == "root"


def test_trace_rejects_orphan_parent() -> None:
    with pytest.raises(ValidationError, match="missing parent span"):
        TraceIR(
            trace_id="trace-1",
            run_id="run-1",
            spans=[make_span("child", "missing")],
        )


def test_trace_rejects_duplicate_span_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate span_id"):
        TraceIR(
            trace_id="trace-1",
            run_id="run-1",
            spans=[make_span("same"), make_span("same")],
        )


def test_span_rejects_naive_or_reverse_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TraceSpan(
            trace_id="trace-1",
            span_id="bad-timezone",
            name="bad",
            kind=SpanKind.TOOL,
            status=SpanStatus.ERROR,
            started_at=datetime(2026, 7, 15),
            ended_at=datetime(2026, 7, 15),
        )

    with pytest.raises(ValidationError, match="ended_at"):
        TraceSpan(
            trace_id="trace-1",
            span_id="reverse",
            name="bad",
            kind=SpanKind.TOOL,
            status=SpanStatus.ERROR,
            started_at=NOW,
            ended_at=NOW - timedelta(seconds=1),
        )
