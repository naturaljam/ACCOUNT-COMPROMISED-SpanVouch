import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from spanvouch.contracts.trace import DiagnosticContext, TraceIR, TraceSpan
from spanvouch.contracts.versioning import canonical_bytes, canonical_json
from spanvouch.trace.diagnostic_view import TraceProjector

ROOT = Path(__file__).resolve().parents[2]


def _trace() -> TraceIR:
    now = datetime(2026, 7, 18, tzinfo=UTC)
    return TraceIR(
        trace_id="trace-1",
        run_id="run-1",
        spans=[
            TraceSpan(
                trace_id="trace-1",
                span_id="root",
                parent_span_id=None,
                name="agent",
                kind="agent",
                status="error",
                started_at=now,
                ended_at=now,
                attributes={"error.type": "tool_error", "secret": "must-not-pass"},
            )
        ],
    )


def _context() -> DiagnosticContext:
    return TraceProjector().project(_trace())


def _schema_bytes(schema: dict[str, object]) -> bytes:
    return (
        json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def test_trace_contract_has_stable_identity() -> None:
    trace = _trace()
    assert trace.schema_name == "spanvouch.trace"
    assert trace.schema_version == "1.0"
    assert '"schema_name":"spanvouch.trace"' in canonical_json(trace)


def test_projector_returns_bound_sanitized_context() -> None:
    context = _context()
    assert isinstance(context, DiagnosticContext)
    assert context.schema_name == "spanvouch.diagnostic-context"
    assert context.trace_id == "trace-1"
    assert context.run_id == "run-1"
    assert context.view.spans[0].attributes == {"error.type": "tool_error"}


def test_checked_in_schemas_match_contract_models_byte_for_byte() -> None:
    assert (ROOT / "schemas/v1/spanvouch.trace-1.0.schema.json").read_bytes() == (
        _schema_bytes(TraceIR.model_json_schema())
    )
    assert (
        ROOT / "schemas/v1/spanvouch.diagnostic-context-1.0.schema.json"
    ).read_bytes() == _schema_bytes(DiagnosticContext.model_json_schema())


def test_checked_in_fixtures_match_canonical_contract_bytes() -> None:
    assert (ROOT / "tests/contracts/fixtures/v1/trace.valid.json").read_bytes() == (
        canonical_bytes(_trace()) + b"\n"
    )
    assert (
        ROOT / "tests/contracts/fixtures/v1/diagnostic-context.valid.json"
    ).read_bytes() == canonical_bytes(_context()) + b"\n"


def test_trace_contract_does_not_import_runtime_modules() -> None:
    module = ROOT / "src/spanvouch/contracts/trace.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(
        name.startswith(
            (
                "spanvouch.trace",
                "spanvouch.diagnosis",
                "spanvouch.verification",
                "spanvouch.review",
                "spanvouch.adapters",
            )
        )
        for name in imported
    )


def test_trace_contract_source_contains_no_runtime_sanitizer_policy() -> None:
    contract_source = (ROOT / "src/spanvouch/contracts/trace.py").read_text(
        encoding="utf-8"
    )
    runtime_source = (ROOT / "src/spanvouch/contracts/sanitization.py").read_text(
        encoding="utf-8"
    )
    runtime_symbols = (
        "ALLOWED_ATTRIBUTES",
        "SECRET_REDACTION",
        "_SanitizationBudget",
        "sanitize_diagnostic_value",
        "sanitize_diagnostic_trace_view",
    )

    assert all(symbol not in contract_source for symbol in runtime_symbols)
    assert all(symbol in runtime_source for symbol in runtime_symbols)


def test_trace_contract_rejects_cross_trace_and_self_parent_spans() -> None:
    root = _trace().spans[0]

    with pytest.raises(ValidationError, match="span trace_id does not match trace"):
        TraceIR(
            trace_id="trace-1",
            run_id="run-1",
            spans=[root.model_copy(update={"trace_id": "trace-other"})],
        )

    with pytest.raises(ValidationError, match="span cannot parent itself"):
        TraceIR(
            trace_id="trace-1",
            run_id="run-1",
            spans=[root.model_copy(update={"parent_span_id": "root"})],
        )


def test_trace_span_lookup_raises_key_error_for_unknown_id() -> None:
    with pytest.raises(KeyError, match="missing"):
        _trace().span_by_id("missing")
