import json
from pathlib import Path

from afc.diagnosis.trace_view import (
    ALLOWED_ATTRIBUTES,
    SECRET_REDACTION,
    DiagnosticTraceView,
    sanitize_diagnostic_trace_view,
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


def test_trace_view_redacts_every_authorization_scheme_inside_allowed_strings() -> None:
    trace = load_trace("clean-01")
    schemes = (
        f"Token {VALUE_SECRET}",
        f'Digest username="agent", response="{VALUE_SECRET}"',
        (
            "AWS4-HMAC-SHA256 "
            f"Credential={VALUE_SECRET}/20260718/cn-north-1/service/aws4_request, "
            f"SignedHeaders=host;x-date, Signature={VALUE_SECRET}"
        ),
        f"Custom-Scheme {VALUE_SECRET}",
    )

    for scheme in schemes:
        root = trace.spans[0].model_copy(
            update={
                "attributes": {
                    **trace.spans[0].attributes,
                    "tool.error.message": (
                        f"request failed; Authorization: {scheme}; safe retry context"
                    ),
                }
            }
        )
        candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

        serialized = canonical_json(DiagnosticTraceView.from_trace(candidate))

        assert VALUE_SECRET not in serialized
        assert "safe retry context" in serialized


def test_trace_view_sanitizes_escaped_and_double_encoded_json_idempotently() -> None:
    trace = load_trace("clean-01")
    encoded = json.dumps(
        {
            "Authorization": (
                "AWS4-HMAC-SHA256 "
                f"Credential={VALUE_SECRET}/20260718/region/service/aws4_request"
            ),
            "nested": {"token": VALUE_SECRET},
            "safe": "保留安全上下文",
        },
        ensure_ascii=False,
    )
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": [encoded, json.dumps(encoded, ensure_ascii=False)],
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)
    serialized = canonical_json(view)

    assert VALUE_SECRET not in serialized
    assert "保留安全上下文" in serialized
    assert sanitize_diagnostic_trace_view(view) == view
    assert sanitize_diagnostic_trace_view(sanitize_diagnostic_trace_view(view)) == view


def test_trace_view_preserves_safe_bearer_prose_and_redacts_token_shaped_bearer() -> None:
    trace = load_trace("clean-01")
    safe_message = "Bearer of good news crossed the bridge. 熊猫依旧安全。"
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "run.final_message": safe_message,
                "tool.error.message": f"Bearer {VALUE_SECRET}; safe tail",
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)

    assert view.spans[0].attributes["run.final_message"] == safe_message
    assert VALUE_SECRET not in canonical_json(view)
    assert "safe tail" in canonical_json(view)


def test_trace_view_redacts_opaque_bearer_tokens_only_at_value_boundaries() -> None:
    trace = load_trace("clean-01")
    opaque_letters = "qwertyuiopasdfghjklzxcvbnm"
    short_token = "zzq"
    safe_prose = "Bearer of good news remains ordinary prose. 熊猫仍然安全。"
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": [
                    f"opaque=Bearer {opaque_letters}",
                    f"short=Bearer {short_token},next=retry",
                    json.dumps(
                        {"credential": f"Bearer {short_token}"},
                        ensure_ascii=False,
                    ),
                ],
                "tool.error.message": (
                    f"Authorization: Custom {short_token}; auth context survives"
                ),
                "run.final_message": safe_prose,
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)
    serialized = canonical_json(view)

    assert f"Bearer {opaque_letters}" not in serialized
    assert f"Bearer {short_token}" not in serialized
    assert "next=retry" in serialized
    assert "auth context survives" in serialized
    assert view.spans[0].attributes["run.final_message"] == safe_prose
    assert sanitize_diagnostic_trace_view(view) == view
    assert sanitize_diagnostic_trace_view(sanitize_diagnostic_trace_view(view)) == view
