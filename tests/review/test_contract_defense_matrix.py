from datetime import timedelta

import pytest
from pydantic import ValidationError

from spanvouch.contracts.diagnosis import (
    AbstainReason,
    DiagnosisReport,
    DiagnosisStatus,
)
from spanvouch.contracts.review import (
    CorrectionClaim,
    DecisionAction,
    DiagnosisCorrectionDraft,
    DiagnosisReviewCase,
    DiagnosisRevision,
    HumanDecisionDraft,
    HumanReviewDecision,
    ReviewStatus,
    RevisionOrigin,
    resume_requires_live_api,
)
from spanvouch.contracts.verification import (
    EvidenceGap,
    FindingCode,
    FindingSeverity,
    VerificationFinding,
    VerificationInput,
    VerifierProvenance,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.failure_types import FailureType
from tests.review.factories import (
    NOW,
    make_awaiting_human_case,
    make_correction_draft,
    make_diagnosis_report,
    make_finding,
    make_pending_case,
    make_review_snapshot,
    make_revision,
    make_verifier_report,
)


def _invalid(model: type[object], payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        model.model_validate(payload)  # type: ignore[attr-defined]


def test_diagnosis_report_rejects_each_internally_inconsistent_state() -> None:
    report = make_diagnosis_report()
    payload = report.model_dump(mode="python")
    evidence = report.evidence[0]
    claim = report.causal_chain[0]

    _invalid(
        DiagnosisReport,
        {**payload, "critical_span_ids": ("span-tool", "span-tool")},
        "critical_span_ids must be unique",
    )
    _invalid(
        DiagnosisReport,
        {**payload, "evidence": (evidence, evidence)},
        "evidence_id must be unique",
    )
    _invalid(DiagnosisReport, {**payload, "failure_type": None}, "requires a failure type")
    _invalid(
        DiagnosisReport,
        {**payload, "abstain_reason": AbstainReason.INVALID_MODEL_OUTPUT},
        "forbids abstain_reason",
    )
    _invalid(
        DiagnosisReport,
        {
            **payload,
            "status": DiagnosisStatus.NO_FAILURE,
            "failure_type": FailureType.POLICY_VIOLATION,
            "critical_span_ids": (),
            "causal_chain": (),
            "evidence": (),
        },
        "requires no_failure type",
    )
    _invalid(
        DiagnosisReport,
        {
            **payload,
            "status": DiagnosisStatus.ABSTAINED,
            "failure_type": None,
            "critical_span_ids": (),
            "causal_chain": (),
            "evidence": (),
            "abstain_reason": None,
        },
        "requires abstain_reason",
    )
    _invalid(
        DiagnosisReport,
        {
            **payload,
            "status": DiagnosisStatus.ABSTAINED,
            "failure_type": None,
            "critical_span_ids": ("span-tool",),
            "causal_chain": (claim,),
            "evidence": (evidence,),
            "abstain_reason": AbstainReason.INSUFFICIENT_EVIDENCE,
        },
        "forbids failure details",
    )


def test_review_revision_and_correction_models_reject_forged_chains() -> None:
    revision = make_revision()
    payload = revision.model_dump(mode="python")
    other_provenance = revision.provenance.model_copy(
        update={"diagnoser_version": "other-version"}
    )

    _invalid(
        DiagnosisRevision,
        {**payload, "triggering_gap_ids": ("gap-z", "gap-a")},
        "sorted and unique",
    )
    _invalid(DiagnosisRevision, {**payload, "report_sha256": "0" * 64}, "does not match")
    _invalid(
        DiagnosisRevision,
        {**payload, "provenance": other_provenance},
        "provenance must match",
    )
    _invalid(
        DiagnosisRevision,
        {**payload, "origin": RevisionOrigin.HUMAN_CORRECTION},
        "revision zero must be",
    )
    _invalid(
        DiagnosisRevision,
        {**payload, "previous_report_sha256": "1" * 64},
        "revision zero has no previous",
    )
    evidence_revision = make_revision(
        revision_number=1,
        previous_report_sha256="1" * 64,
        triggering_gap_ids=("gap-1",),
    )
    _invalid(
        DiagnosisRevision,
        {**evidence_revision.model_dump(mode="python"), "triggering_gap_ids": ()},
        "require triggering_gap_ids",
    )

    correction = make_correction_draft()
    claim = correction.causal_chain[0]
    _invalid(
        CorrectionClaim,
        {
            **claim.model_dump(mode="python"),
            "selectors": (claim.selectors[0], claim.selectors[0]),
        },
        "selectors must be sorted and unique",
    )


def test_correction_draft_rejects_every_cross_status_payload_leak() -> None:
    correction = make_correction_draft()
    payload = correction.model_dump(mode="python")
    claim = correction.causal_chain[0]

    cases = (
        ({**payload, "critical_span_ids": ("span-tool", "span-tool")}, "must be unique"),
        ({**payload, "failure_type": FailureType.NO_FAILURE}, "supported failure type"),
        ({**payload, "critical_span_ids": ()}, "requires critical spans"),
        (
            {**payload, "abstain_reason": AbstainReason.INVALID_MODEL_OUTPUT},
            "forbids abstain_reason",
        ),
        (
            {
                **payload,
                "status": DiagnosisStatus.NO_FAILURE,
                "failure_type": FailureType.POLICY_VIOLATION,
                "critical_span_ids": (),
                "causal_chain": (),
            },
            "requires no_failure type",
        ),
        (
            {
                **payload,
                "status": DiagnosisStatus.NO_FAILURE,
                "failure_type": FailureType.NO_FAILURE,
            },
            "forbids failure details",
        ),
        (
            {
                **payload,
                "status": DiagnosisStatus.ABSTAINED,
                    "abstain_reason": AbstainReason.INSUFFICIENT_EVIDENCE,
            },
            "forbids failure_type",
        ),
        (
            {
                **payload,
                "status": DiagnosisStatus.ABSTAINED,
                "failure_type": None,
                "critical_span_ids": (),
                "causal_chain": (),
                "abstain_reason": None,
            },
            "requires abstain_reason",
        ),
        (
            {
                **payload,
                "status": DiagnosisStatus.ABSTAINED,
                "failure_type": None,
                "abstain_reason": AbstainReason.INSUFFICIENT_EVIDENCE,
                "causal_chain": (claim,),
            },
            "forbids failure details",
        ),
    )
    for invalid_payload, message in cases:
        _invalid(DiagnosisCorrectionDraft, invalid_payload, message)


def test_human_decision_and_case_models_reject_impossible_workflow_states() -> None:
    correction = make_correction_draft()
    _invalid(
        HumanDecisionDraft,
        {
            "action": DecisionAction.CORRECT,
            "expected_version": 0,
            "reviewer_label": "reviewer",
            "correction": None,
        },
        "requires correction",
    )
    _invalid(
        HumanDecisionDraft,
        {
            "action": DecisionAction.REJECT,
            "expected_version": 0,
            "reviewer_label": "reviewer",
            "reason": None,
        },
        "requires reason",
    )
    correct_decision = {
        "action": DecisionAction.CORRECT,
        "expected_version": 0,
        "reviewer_label": "reviewer",
        "correction": correction,
        "decision_id": "decision-1",
        "case_id": "case-1",
        "created_at": NOW,
        "resulting_revision_id": None,
    }
    _invalid(HumanReviewDecision, correct_decision, "requires a resulting revision")
    _invalid(
        HumanReviewDecision,
        {
            **correct_decision,
            "action": DecisionAction.CONFIRM,
            "correction": None,
            "resulting_revision_id": "revision-1",
        },
        "only a correct decision",
    )

    case = make_awaiting_human_case()
    payload = case.model_dump(mode="python")
    _invalid(
        DiagnosisReviewCase,
        {**payload, "updated_at": case.created_at - timedelta(seconds=1)},
        "must not precede",
    )
    _invalid(
        DiagnosisReviewCase,
        {**payload, "status": ReviewStatus.CONFIRMED},
        "terminal status",
    )
    _invalid(
        DiagnosisReviewCase,
        {**payload, "semantic_run_id": "semantic-1"},
        "forbids a semantic",
    )

    assert not resume_requires_live_api(
        make_pending_case(), (make_verifier_report(),)
    )


def test_verification_contracts_reject_unbound_or_contradictory_evidence() -> None:
    finding = make_finding()
    _invalid(
        VerificationFinding,
        {
            **finding.model_dump(mode="python"),
            "related_span_ids": ("span-z", "span-a"),
        },
        "sorted and unique",
    )
    provenance = make_verifier_report().provenance
    _invalid(
        VerifierProvenance,
        {**provenance.model_dump(mode="python"), "prompt_version": "prompt-v1"},
        "must be provided together",
    )

    snapshot = make_review_snapshot()
    report = make_diagnosis_report()
    binding = {
        "snapshot": snapshot,
        "report": report,
        "report_sha256": canonical_sha256(report),
    }
    wrong_trace = report.model_copy(update={"trace_id": "other-trace"})
    _invalid(
        VerificationInput,
        {
            **binding,
            "report": wrong_trace,
            "report_sha256": canonical_sha256(wrong_trace),
        },
        "trace_id must match",
    )
    wrong_run = report.model_copy(update={"run_id": "other-run"})
    _invalid(
        VerificationInput,
        {
            **binding,
            "report": wrong_run,
            "report_sha256": canonical_sha256(wrong_run),
        },
        "run_id must match",
    )

    verifier = make_verifier_report()
    verifier_payload = verifier.model_dump(mode="python")
    _invalid(
        VerifierReport,
        {
            **verifier_payload,
            "provenance": verifier.provenance.model_copy(
                update={"verifier_kind": "semantic"}
            ),
        },
        "verifier_kind must match",
    )
    _invalid(
        VerifierReport,
        {**verifier_payload, "completed_at": verifier.started_at - timedelta(seconds=1)},
        "must not precede",
    )
    _invalid(
        VerifierReport,
        {**verifier_payload, "findings": (finding, finding)},
        "finding_id must be unique",
    )
    gap = EvidenceGap(
        gap_id="gap-1",
        finding_code=FindingCode.CLAIM_NOT_GROUNDED,
        required_evidence_kind="tool evidence",
        instruction="Ground the claim.",
    )
    _invalid(
        VerifierReport,
        {
            **verifier_payload,
            "verdict": VerifierVerdict.REVIEW_REQUIRED,
            "evidence_gaps": (gap, gap),
        },
        "gap_id must be unique",
    )
    _invalid(
        VerifierReport,
        {
            **verifier_payload,
            "findings": (
                make_finding(severity=FindingSeverity.HARD),
            ),
        },
        "verified verdict forbids hard findings",
    )
