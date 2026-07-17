import pytest
from pydantic import ValidationError

from afc.diagnosis.models import (
    AbstainReason,
    ClaimStage,
    DiagnoserKind,
    DiagnosisClaim,
    DiagnosisDecision,
    DiagnosisProvenance,
    DiagnosisReport,
    DiagnosisStatus,
    EvidenceRef,
)
from afc.failure_types import FailureType

EVIDENCE = EvidenceRef(
    evidence_id="ev-1",
    span_id="span-005",
    field_path="attributes.tool.error.type",
    observed_value="RefundRejected",
    value_sha256="a" * 64,
    description="tool rejected",
)
CLAIM = DiagnosisClaim(
    stage=ClaimStage.CAUSE,
    statement="The refund tool rejected the call.",
    evidence_ids=("ev-1",),
)


def test_diagnosed_decision_requires_failure_evidence_and_claim() -> None:
    with pytest.raises(ValidationError):
        DiagnosisDecision(
            status=DiagnosisStatus.DIAGNOSED,
            failure_type=FailureType.POLICY_VIOLATION,
            critical_span_ids=("span-005",),
            confidence=1.0,
        )


def test_no_failure_decision_rejects_critical_spans() -> None:
    with pytest.raises(ValidationError):
        DiagnosisDecision(
            status=DiagnosisStatus.NO_FAILURE,
            failure_type=FailureType.NO_FAILURE,
            critical_span_ids=("span-005",),
            confidence=1.0,
        )


def test_abstained_decision_requires_reason_and_forbids_failure_type() -> None:
    with pytest.raises(ValidationError):
        DiagnosisDecision(
            status=DiagnosisStatus.ABSTAINED,
            failure_type=FailureType.WRONG_TOOL,
            confidence=0.0,
        )

    valid = DiagnosisDecision(
        status=DiagnosisStatus.ABSTAINED,
        confidence=0.0,
        abstain_reason=AbstainReason.INSUFFICIENT_EVIDENCE,
    )
    assert valid.failure_type is None


def test_claim_references_must_exist_in_evidence() -> None:
    with pytest.raises(ValidationError):
        DiagnosisDecision(
            status=DiagnosisStatus.DIAGNOSED,
            failure_type=FailureType.POLICY_VIOLATION,
            critical_span_ids=("span-005",),
            causal_chain=(CLAIM,),
            evidence=(),
            confidence=1.0,
        )


def test_report_round_trips_through_json() -> None:
    report = DiagnosisReport(
        trace_id="trace-1",
        run_id="run-1",
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.DIAGNOSED,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-005",),
        causal_chain=(CLAIM,),
        evidence=(EVIDENCE,),
        confidence=1.0,
        provenance=DiagnosisProvenance(
            taxonomy_version="1.0",
            diagnoser_version="evidence-rules-v1",
            ruleset_version="rules-sha",
        ),
    )
    assert DiagnosisReport.model_validate_json(report.model_dump_json()) == report
