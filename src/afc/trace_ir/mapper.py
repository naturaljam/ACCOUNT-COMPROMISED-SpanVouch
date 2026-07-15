from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode
from pydantic import JsonValue

from afc.trace_ir.models import SpanKind, SpanStatus, TraceIR, TraceSpan

_KIND_MAP = {
    "AGENT": SpanKind.AGENT,
    "LLM": SpanKind.LLM,
    "TOOL": SpanKind.TOOL,
    "RETRIEVER": SpanKind.RETRIEVAL,
    "CHAIN": SpanKind.WORKFLOW,
}


def _to_datetime(nanoseconds: int) -> datetime:
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, tz=UTC)


def _status(span: ReadableSpan) -> SpanStatus:
    if span.status.status_code is StatusCode.OK:
        return SpanStatus.OK
    if span.status.status_code is StatusCode.ERROR:
        return SpanStatus.ERROR
    return SpanStatus.UNSET


def map_spans(run_id: str, spans: Sequence[ReadableSpan]) -> TraceIR:
    if not spans:
        raise ValueError("cannot map an empty span sequence")
    trace_id = format(spans[0].context.trace_id, "032x") if spans[0].context else ""
    mapped: list[TraceSpan] = []
    for span in spans:
        assert span.context is not None
        assert span.start_time is not None
        assert span.end_time is not None
        attributes = {
            str(key): cast(JsonValue, value) for key, value in (span.attributes or {}).items()
        }
        kind_name = str(attributes.get("openinference.span.kind", "CHAIN")).upper()
        mapped.append(
            TraceSpan(
                trace_id=trace_id,
                span_id=format(span.context.span_id, "016x"),
                parent_span_id=(
                    format(span.parent.span_id, "016x") if span.parent is not None else None
                ),
                name=span.name,
                kind=_KIND_MAP.get(kind_name, SpanKind.WORKFLOW),
                status=_status(span),
                started_at=_to_datetime(span.start_time),
                ended_at=_to_datetime(span.end_time),
                attributes=attributes,
            )
        )
    return TraceIR(trace_id=trace_id, run_id=run_id, spans=mapped)
