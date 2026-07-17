from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from afc.trace_ir.models import SpanKind, SpanStatus, TraceIR

ALLOWED_ATTRIBUTES = frozenset(
    {
        "run.outcome",
        "run.final_message",
        "tool.name",
        "tool.arguments.customer_id",
        "tool.arguments.order_id",
        "tool.arguments.item_skus",
        "tool.arguments.amount",
        "tool.arguments.approval",
        "tool.arguments.reason",
        "tool.result",
        "tool.error.type",
        "tool.error.message",
    }
)


class DiagnosticSpan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    name: str = Field(min_length=1)
    kind: SpanKind
    status: SpanStatus
    started_at: datetime
    ended_at: datetime
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class DiagnosticTraceView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    spans: tuple[DiagnosticSpan, ...] = Field(min_length=1)

    @classmethod
    def from_trace(cls, trace: TraceIR) -> "DiagnosticTraceView":
        spans = tuple(
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
        return cls(spans=spans)
