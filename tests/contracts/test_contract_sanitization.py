from pathlib import Path

from spanvouch.contracts.sanitization import (
    SECRET_REDACTION,
    sanitize_diagnostic_trace_view,
    sanitize_diagnostic_value,
)
from spanvouch.contracts.trace import TraceIR
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.diagnostic_view import sanitize_diagnostic_value as trace_sanitize


def _clean_view():
    traces = Path("evals/datasets/supportlab-v1/traces.jsonl").read_text(encoding="utf-8")
    trace = next(
        TraceIR.model_validate_json(line)
        for line in traces.splitlines()
        if '"run_id":"clean-01"' in line
    )
    return TraceProjector().project(trace).view


def test_contract_sanitizer_preserves_frozen_view_hash_and_redacts_secrets() -> None:
    view = _clean_view()

    assert canonical_sha256(sanitize_diagnostic_trace_view(view)) == (
        "14d811439f3d80a79a747cc079b0e1c954dcfa864bdb9584d13ace4a0300862b"
    )
    assert sanitize_diagnostic_value(
        "Authorization: Bearer value-level-sentinel-credential; safe tail"
    ) == f"Authorization: {SECRET_REDACTION}"
    assert trace_sanitize is sanitize_diagnostic_value
