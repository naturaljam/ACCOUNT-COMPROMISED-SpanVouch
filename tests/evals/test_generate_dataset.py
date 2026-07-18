import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from spanvouch.evals.generate_dataset import _normalize_trace, generate_dataset
from spanvouch.trace_ir.models import SpanKind, SpanStatus, TraceIR, TraceSpan

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def make_span(span_id: str, parent_span_id: str | None = None) -> TraceSpan:
    return TraceSpan(
        trace_id="source-trace",
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=span_id,
        kind=SpanKind.AGENT,
        status=SpanStatus.OK,
        started_at=NOW,
        ended_at=NOW + timedelta(milliseconds=1),
    )


def make_trace(*spans: TraceSpan) -> TraceIR:
    return TraceIR(trace_id="source-trace", run_id="run-1", spans=list(spans))


def assert_parent_envelopes_children(trace: TraceIR) -> None:
    spans_by_id = {span.span_id: span for span in trace.spans}
    for child in trace.spans:
        if child.parent_span_id is None:
            continue
        parent = spans_by_id[child.parent_span_id]
        assert parent.started_at <= child.started_at <= child.ended_at <= parent.ended_at


def test_normalization_keeps_each_child_inside_its_parent_time_envelope() -> None:
    normalized = _normalize_trace(
        make_trace(make_span("root"), make_span("child", "root")),
        sequence=1,
    )

    assert_parent_envelopes_children(normalized)


def test_normalization_orders_a_multilevel_tree_parent_first_with_stable_siblings() -> None:
    trace = make_trace(
        make_span("grandchild", "first-child"),
        make_span("first-child", "root"),
        make_span("second-child", "root"),
        make_span("root"),
    )

    normalized = _normalize_trace(trace, sequence=1)

    assert [span.name for span in normalized.spans] == [
        "root",
        "first-child",
        "grandchild",
        "second-child",
    ]
    assert_parent_envelopes_children(normalized)


def test_normalization_rejects_a_parent_cycle() -> None:
    trace = TraceIR.model_construct(
        trace_id="source-trace",
        run_id="run-1",
        spans=[make_span("first", "second"), make_span("second", "first")],
    )

    with pytest.raises(ValueError, match="cycle detected"):
        _normalize_trace(trace, sequence=1)


def test_normalization_rejects_a_missing_parent() -> None:
    trace = TraceIR.model_construct(
        trace_id="source-trace",
        run_id="run-1",
        spans=[make_span("orphan", "missing")],
    )

    with pytest.raises(ValueError, match="missing parent span: missing"):
        _normalize_trace(trace, sequence=1)


def test_normalization_rejects_multiple_roots() -> None:
    trace = TraceIR.model_construct(
        trace_id="source-trace",
        run_id="run-1",
        spans=[make_span("first-root"), make_span("second-root")],
    )

    with pytest.raises(ValueError, match="exactly one root"):
        _normalize_trace(trace, sequence=1)


@pytest.mark.asyncio
async def test_dataset_generation_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_manifest = await generate_dataset(first, seed=7)
    second_manifest = await generate_dataset(second, seed=7)

    assert first_manifest == second_manifest
    assert (first / "traces.jsonl").read_bytes() == (second / "traces.jsonl").read_bytes()
    assert (first / "labels.jsonl").read_bytes() == (second / "labels.jsonl").read_bytes()
    labels = [json.loads(line) for line in (first / "labels.jsonl").read_text().splitlines()]
    assert len(labels) == 20


@pytest.mark.asyncio
async def test_committed_dataset_matches_seeded_generation(tmp_path: Path) -> None:
    generated = tmp_path / "supportlab-v1"
    committed = Path(__file__).parents[2] / "evals" / "datasets" / "supportlab-v1"

    await generate_dataset(generated, seed=20260715)

    for filename in ("traces.jsonl", "labels.jsonl", "manifest.json"):
        assert (generated / filename).read_bytes() == (committed / filename).read_bytes()
