from hashlib import sha256

import pytest

from spanvouch.diagnosis.models import EvidenceSelector
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog, canonical_json
from tests.trace.test_diagnostic_view import load_trace


def test_catalog_resolves_real_span_fields_deterministically() -> None:
    context = TraceProjector().project(load_trace("invalid_argument-01"))
    first = EvidenceCatalog.from_context(context)
    second = EvidenceCatalog.from_context(context)
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
    catalog = EvidenceCatalog.from_context(
        TraceProjector().project(load_trace("ignored_tool_error-01"))
    )

    assert "span-005::name" in catalog.selectors
    assert "span-000::status" in catalog.selectors
    assert not any("scenario" in selector for selector in catalog.selectors)
    assert not any("idempotency_key" in selector for selector in catalog.selectors)
    assert not any("ignore_error" in selector for selector in catalog.selectors)


def test_catalog_rejects_unknown_selector() -> None:
    catalog = EvidenceCatalog.from_context(
        TraceProjector().project(load_trace("clean-01"))
    )

    with pytest.raises(KeyError, match="span-999"):
        catalog.resolve(
            EvidenceSelector(span_id="span-999", field_path="name"),
            description="missing",
        )
