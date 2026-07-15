from opentelemetry.trace import Status, StatusCode

from afc.observability.tracing import build_test_tracer
from afc.trace_ir.mapper import map_spans
from afc.trace_ir.models import SpanKind, SpanStatus


def test_otel_spans_map_to_connected_trace_ir() -> None:
    tracer, exporter = build_test_tracer()
    with tracer.start_as_current_span(
        "supportlab.run",
        attributes={"openinference.span.kind": "AGENT"},
    ), tracer.start_as_current_span(
        "get_order",
        attributes={
            "openinference.span.kind": "TOOL",
            "tool.name": "get_order",
            "tool.arguments.order_id": "order-001",
        },
    ) as tool_span:
        tool_span.set_status(Status(StatusCode.OK))

    trace = map_spans("run-001", exporter.get_finished_spans())

    assert len(trace.spans) == 2
    tool = next(span for span in trace.spans if span.kind is SpanKind.TOOL)
    assert tool.status is SpanStatus.OK
    assert tool.parent_span_id is not None
    assert tool.attributes["tool.name"] == "get_order"
