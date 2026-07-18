from hashlib import sha256

import pytest

from spanvouch.diagnosis.evidence import EvidenceCatalog, canonical_json
from spanvouch.diagnosis.models import EvidenceSelector
from spanvouch.diagnosis.trace_view import DiagnosticTraceView
from tests.diagnosis.test_trace_view import load_trace


def test_catalog_resolves_real_span_fields_deterministically() -> None:
    view = DiagnosticTraceView.from_trace(load_trace("invalid_argument-01"))
    first = EvidenceCatalog.from_view(view)
    second = EvidenceCatalog.from_view(view)
    selector = EvidenceSelector(
        span_id="span-005",
        field_path="attributes.tool.error.type",
    )

    evidence = first.resolve(selector, description="tool error type")

    assert first.selectors == second.selectors
    assert evidence.observed_value == "RefundRejected"
    assert evidence.value_sha256 == sha256(
        canonical_json("RefundRejected").encode("utf-8")
    ).hexdigest()
    assert evidence.evidence_id.startswith("ev-")


def test_catalog_contains_basic_span_fields_but_no_forbidden_attributes() -> None:
    catalog = EvidenceCatalog.from_view(
        DiagnosticTraceView.from_trace(load_trace("ignored_tool_error-01"))
    )

    assert "span-005::name" in catalog.selectors
    assert "span-000::status" in catalog.selectors
    assert not any("scenario" in selector for selector in catalog.selectors)
    assert not any("idempotency_key" in selector for selector in catalog.selectors)
    assert not any("ignore_error" in selector for selector in catalog.selectors)


def test_catalog_rejects_unknown_selector() -> None:
    catalog = EvidenceCatalog.from_view(
        DiagnosticTraceView.from_trace(load_trace("clean-01"))
    )

    with pytest.raises(KeyError, match="span-999"):
        catalog.resolve(
            EvidenceSelector(span_id="span-999", field_path="name"),
            description="missing",
        )
