import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanContext, Status, StatusCode

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


def test_otel_attribute_sequences_are_normalized_to_json_lists() -> None:
    tracer, exporter = build_test_tracer()
    with tracer.start_as_current_span(
        "attribute-types",
        attributes={
            "string": "value",
            "boolean": True,
            "integer": 42,
            "float": 3.5,
            "strings": ["a", "b"],
            "booleans": [True, False],
            "integers": [1, 2],
            "floats": [1.5, 2.5],
        },
    ):
        pass

    trace = map_spans("run-attribute-types", exporter.get_finished_spans())

    attributes = trace.spans[0].attributes
    assert attributes == {
        "string": "value",
        "boolean": True,
        "integer": 42,
        "float": 3.5,
        "strings": ["a", "b"],
        "booleans": [True, False],
        "integers": [1, 2],
        "floats": [1.5, 2.5],
    }


def test_mixed_trace_ids_are_rejected() -> None:
    tracer, exporter = build_test_tracer()
    with tracer.start_as_current_span("first-trace"):
        pass
    with tracer.start_as_current_span("second-trace"):
        pass

    with pytest.raises(
        ValueError,
        match=r"span 'second-trace' has trace_id [0-9a-f]+; expected [0-9a-f]+",
    ):
        map_spans("run-mixed-traces", exporter.get_finished_spans())


@pytest.mark.parametrize(
    ("span", "missing_field"),
    [
        (ReadableSpan("missing-context", start_time=1, end_time=2), "context"),
        (
            ReadableSpan(
                "missing-start-time",
                context=SpanContext(trace_id=1, span_id=1, is_remote=False),
                end_time=2,
            ),
            "start_time",
        ),
        (
            ReadableSpan(
                "missing-end-time",
                context=SpanContext(trace_id=1, span_id=1, is_remote=False),
                start_time=1,
            ),
            "end_time",
        ),
    ],
)
def test_missing_required_span_fields_are_rejected(
    span: ReadableSpan,
    missing_field: str,
) -> None:
    with pytest.raises(ValueError, match=rf"span {span.name!r} is missing {missing_field}"):
        map_spans("run-missing-field", [span])
