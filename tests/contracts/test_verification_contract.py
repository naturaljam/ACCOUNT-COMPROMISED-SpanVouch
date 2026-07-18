import ast
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spanvouch.contracts.verification import VerifierReport
from spanvouch.contracts.versioning import canonical_bytes
from spanvouch.verification.protocols import Verifier

ROOT = Path(__file__).resolve().parents[2]


def _base() -> dict[str, object]:
    now = datetime(2026, 7, 18, tzinfo=UTC)
    return {
        "verifier_run_id": "vr-1",
        "revision_number": 0,
        "report_sha256": "1" * 64,
        "verifier_kind": "deterministic",
        "verdict": "verified",
        "provenance": {
            "verifier_kind": "deterministic",
            "verifier_version": "det-v1",
            "policy_version": "policy-v1",
        },
        "started_at": now,
        "completed_at": now,
    }


def _report() -> VerifierReport:
    return VerifierReport(**_base())


def test_verifier_report_is_a_versioned_root() -> None:
    report = _report()
    assert report.schema_name == "spanvouch.verification"
    assert report.schema_version == "1.0"


def test_verified_report_rejects_evidence_gaps() -> None:
    payload = _base()
    payload["evidence_gaps"] = (
        {
            "gap_id": "g1",
            "finding_code": "semantic_support_missing",
            "required_evidence_kind": "causal_support",
            "instruction": "supply causal evidence",
        },
    )
    with pytest.raises(ValueError, match="verified verdict forbids evidence gaps"):
        VerifierReport(**payload)


def test_checked_in_schema_and_fixture_match_contract() -> None:
    schema = json.dumps(
        VerifierReport.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"
    assert (
        ROOT / "schemas/v1/spanvouch.verification-1.0.schema.json"
    ).read_bytes() == schema
    assert (
        ROOT / "tests/contracts/fixtures/v1/verification.valid.json"
    ).read_bytes() == canonical_bytes(_report()) + b"\n"


def test_verifier_protocol_has_exact_public_signature() -> None:
    signature = inspect.signature(Verifier.verify)
    assert tuple(signature.parameters) == ("self", "request")
    assert signature.return_annotation == "VerifierReport"


def test_verification_interface_does_not_import_review_or_infrastructure() -> None:
    for module in (ROOT / "src/spanvouch/verification").glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not any(
            name.startswith(("spanvouch.review", "spanvouch.adapters"))
            for name in imported
        )
