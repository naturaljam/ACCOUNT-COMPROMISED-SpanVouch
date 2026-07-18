from pathlib import Path

from afc.diagnosis.trace_view import ALLOWED_ATTRIBUTES, DiagnosticTraceView
from afc.trace_ir.models import TraceIR

DATASET = Path("evals/datasets/supportlab-v1/traces.jsonl")
FORBIDDEN_PARTS = (
    "scenario",
    "idempotency_key",
    "ignore_error",
    "calculated_amount",
)


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
