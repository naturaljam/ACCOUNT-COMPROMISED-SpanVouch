from typing import Protocol

from spanvouch.contracts.sanitization import (
    ALLOWED_ATTRIBUTES,
    SECRET_REDACTION,
    sanitize_diagnostic_trace_view,
    sanitize_diagnostic_value,
)
from spanvouch.contracts.trace import (
    DiagnosticContext,
    DiagnosticSpan,
    DiagnosticTraceView,
    TraceIR,
)

__all__ = [
    "ALLOWED_ATTRIBUTES",
    "SECRET_REDACTION",
    "TraceProjector",
    "TraceProjectorPort",
    "sanitize_diagnostic_trace_view",
    "sanitize_diagnostic_value",
]


class TraceProjectorPort(Protocol):
    def project(self, trace: TraceIR) -> DiagnosticContext:
        raise NotImplementedError


class TraceProjector:
    def project(self, trace: TraceIR) -> DiagnosticContext:
        raw_view = DiagnosticTraceView(
            spans=tuple(
                DiagnosticSpan(
                    span_id=span.span_id,
                    parent_span_id=span.parent_span_id,
                    name=span.name,
                    kind=span.kind,
                    status=span.status,
                    started_at=span.started_at,
                    ended_at=span.ended_at,
                    attributes={
                        key: value
                        for key, value in span.attributes.items()
                        if key in ALLOWED_ATTRIBUTES
                    },
                )
                for span in sorted(
                    trace.spans,
                    key=lambda item: (item.started_at, item.ended_at, item.span_id),
                )
            )
        )
        return DiagnosticContext(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            view=sanitize_diagnostic_trace_view(raw_view),
        )
