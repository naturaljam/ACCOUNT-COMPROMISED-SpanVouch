from pathlib import Path

from afc.diagnosis.trace_view import (
    ALLOWED_ATTRIBUTES,
    SECRET_REDACTION,
    DiagnosticTraceView,
)
from afc.review.models import canonical_json
from afc.trace_ir.models import TraceIR

DATASET = Path("evals/datasets/supportlab-v1/traces.jsonl")
FORBIDDEN_PARTS = (
    "scenario",
    "idempotency_key",
    "ignore_error",
    "calculated_amount",
)
VALUE_SECRET = "value-level-sentinel-credential"


def load_trace(run_id: str) -> TraceIR:
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        trace = TraceIR.model_validate_json(line)
        if trace.run_id == run_id:
            return trace
    raise AssertionError(f"missing fixture: {run_id}")


def test_trace_view_removes_identity_and_fault_injection_fields() -> None:
    trace = load_trace("invalid_argument-01")
    original = trace.model_dump()

    view = DiagnosticTraceView.from_trace(trace)

    assert not hasattr(view, "trace_id")
    assert not hasattr(view, "run_id")
    assert trace.model_dump() == original
    assert tuple(span.span_id for span in view.spans) == tuple(
        span.span_id
        for span in sorted(
            trace.spans,
            key=lambda span: (span.started_at, span.ended_at, span.span_id),
        )
    )
    for span in view.spans:
        assert set(span.attributes) <= ALLOWED_ATTRIBUTES
        assert not any(part in key for key in span.attributes for part in FORBIDDEN_PARTS)


def test_trace_view_keeps_only_diagnostic_business_attributes() -> None:
    view = DiagnosticTraceView.from_trace(load_trace("policy_violation-01"))
    submit = next(span for span in view.spans if span.name == "submit_refund")

    assert submit.attributes["tool.arguments.approval"] == "none"
    assert submit.attributes["tool.error.type"] == "RefundRejected"
    assert submit.attributes["tool.error.message"] == "missing_approval"


def test_trace_view_recursively_redacts_credentials_inside_allowed_values() -> None:
    trace = load_trace("clean-01")
    root = trace.spans[0]
    root = root.model_copy(
        update={
            "attributes": {
                **root.attributes,
                "tool.result": {
                    "api_key": VALUE_SECRET,
                    "nested": [
                        f"DEEPSEEK_API_KEY={VALUE_SECRET}",
                        {"password": VALUE_SECRET, "safe": "useful context"},
                    ],
                },
                "tool.error.message": (
                    f"request rejected; Authorization: Bearer {VALUE_SECRET}; retry later"
                ),
                "run.final_message": (
                    f"upstream https://agent:{VALUE_SECRET}@example.test/path failed"
                ),
            }
        }
    )
    trace = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(trace)
    serialized = canonical_json(view)

    assert VALUE_SECRET not in serialized
    assert "useful context" in serialized
    assert "request rejected" in serialized
    assert "retry later" in serialized
    assert "example.test/path failed" in serialized
    assert f"{SECRET_REDACTION}]" not in serialized
    assert DiagnosticTraceView.model_validate(view.model_dump()) == view
