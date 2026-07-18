import ast
import json
from pathlib import Path

from spanvouch.contracts.diagnosis import (
    DiagnosisDecision,
    DiagnosisExecution,
    DiagnosisProvenance,
    DiagnosisReport,
    TaxonomyRef,
)
from spanvouch.contracts.versioning import canonical_bytes

ROOT = Path(__file__).resolve().parents[2]


def _future_report() -> DiagnosisReport:
    decision = DiagnosisDecision(
        status="diagnosed",
        failure_type="deadlock_cycle",
        critical_span_ids=("span-1",),
        causal_chain=(
            {
                "stage": "cause",
                "statement": "two workers wait on each other",
                "evidence_ids": ("ev-1",),
            },
        ),
        evidence=(
            {
                "evidence_id": "ev-1",
                "span_id": "span-1",
                "field_path": "attributes.waits_for",
                "observed_value": "worker-2",
                "value_sha256": "1" * 64,
                "description": "wait edge",
            },
        ),
        confidence=0.8,
    )
    execution = DiagnosisExecution(
        decision=decision,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="opslab", taxonomy_version="1.0"),
            diagnoser_version="rules-v2",
        ),
    )
    return DiagnosisReport.from_execution(
        trace_id="t1", run_id="r1", diagnoser="rules", execution=execution
    )


def test_diagnosis_contract_accepts_namespaced_future_taxonomy() -> None:
    report = _future_report()
    assert report.schema_name == "spanvouch.diagnosis"
    assert report.failure_type == "deadlock_cycle"
    assert report.provenance.taxonomy.taxonomy_id == "opslab"


def test_diagnosed_state_still_requires_grounding() -> None:
    try:
        DiagnosisDecision(
            status="diagnosed",
            failure_type="x",
            confidence=0.5,
        )
    except ValueError as error:
        assert "critical spans, claims, and evidence" in str(error)
    else:
        raise AssertionError("ungrounded diagnosis was accepted")


def test_checked_in_schema_and_fixture_match_contract() -> None:
    schema = json.dumps(
        DiagnosisReport.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"
    assert (
        ROOT / "schemas/v1/spanvouch.diagnosis-1.0.schema.json"
    ).read_bytes() == schema
    assert (
        ROOT / "tests/contracts/fixtures/v1/diagnosis.valid.json"
    ).read_bytes() == canonical_bytes(_future_report()) + b"\n"


def test_diagnosis_contract_does_not_import_runtime_or_supportlab_modules() -> None:
    module = ROOT / "src/spanvouch/contracts/diagnosis.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(
        name.startswith(
            (
                "spanvouch.diagnosis",
                "spanvouch.failure_types",
                "spanvouch.labs",
                "spanvouch.evaluation",
                "spanvouch.verification",
                "spanvouch.review",
                "spanvouch.adapters",
            )
        )
        for name in imported
    )
