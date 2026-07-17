from datetime import UTC, datetime, timedelta

from afc.diagnosis.evidence import EvidenceCatalog
from afc.diagnosis.models import (
    ClaimStage,
    DiagnoserKind,
    DiagnosisClaim,
    DiagnosisProvenance,
    DiagnosisReport,
    DiagnosisStatus,
    EvidenceSelector,
)
from afc.diagnosis.trace_view import DiagnosticSpan, DiagnosticTraceView
from afc.failure_types import FailureType
from afc.review.models import (
    CorrectionClaim,
    DiagnosisCorrectionDraft,
    DiagnosisReviewCase,
    DiagnosisRevision,
    FindingCode,
    FindingSeverity,
    OperationalErrorMetadata,
    ReviewInputSnapshot,
    ReviewStatus,
    RevisionOrigin,
    VerificationFinding,
    VerificationMode,
    VerifierKind,
    VerifierProvenance,
    VerifierReport,
    VerifierVerdict,
    canonical_json,
    canonical_sha256,
)
from afc.trace_ir.models import SpanKind, SpanStatus

NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def make_trace_view() -> DiagnosticTraceView:
    return DiagnosticTraceView(
        spans=(
            DiagnosticSpan(
                span_id="span-root",
                name="support-agent",
                kind=SpanKind.AGENT,
                status=SpanStatus.ERROR,
                started_at=NOW,
                ended_at=NOW + timedelta(seconds=2),
                attributes={"run.outcome": "failed"},
            ),
            DiagnosticSpan(
                span_id="span-tool",
                parent_span_id="span-root",
                name="refund_order",
                kind=SpanKind.TOOL,
                status=SpanStatus.ERROR,
                started_at=NOW + timedelta(seconds=1),
                ended_at=NOW + timedelta(seconds=2),
                attributes={
                    "tool.name": "refund_order",
                    "tool.error.type": "RefundRejected",
                },
            ),
        )
    )


def make_diagnosis_report() -> DiagnosisReport:
    catalog = EvidenceCatalog.from_view(make_trace_view())
    evidence = catalog.resolve(
        EvidenceSelector(
            span_id="span-tool",
            field_path="attributes.tool.error.type",
        ),
        description="The refund tool rejected the request.",
    )
    return DiagnosisReport(
        trace_id="trace-review-1",
        run_id="run-review-1",
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.DIAGNOSED,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-tool",),
        causal_chain=(
            DiagnosisClaim(
                stage=ClaimStage.CAUSE,
                statement="The refund tool rejected the request.",
                evidence_ids=(evidence.evidence_id,),
            ),
        ),
        evidence=(evidence,),
        confidence=1.0,
        provenance=DiagnosisProvenance(
            taxonomy_version="1.0",
            diagnoser_version="review-test-rules-v1",
            ruleset_version="review-test-rules-v1",
        ),
    )


def make_review_snapshot() -> ReviewInputSnapshot:
    view_json = canonical_json(make_trace_view())
    return ReviewInputSnapshot(
        trace_id="trace-review-1",
        run_id="run-review-1",
        view_json=view_json,
        input_sha256=canonical_sha256(view_json),
        catalog_version="evidence-catalog-v1",
        created_at=NOW,
    )


def make_verifier_report(
    *,
    kind: VerifierKind = VerifierKind.DETERMINISTIC,
    verdict: VerifierVerdict = VerifierVerdict.VERIFIED,
    findings: tuple[VerificationFinding, ...] = (),
    operational_error: OperationalErrorMetadata | None = None,
) -> VerifierReport:
    return VerifierReport(
        verifier_run_id=f"verifier-{kind.value}-1",
        revision_number=0,
        verifier_kind=kind,
        verdict=verdict,
        findings=findings,
        operational_error=operational_error,
        provenance=VerifierProvenance(
            verifier_kind=kind,
            verifier_version=f"{kind.value}-verifier-v1",
            policy_version="review-policy-v1",
        ),
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=5),
    )


def make_revision(
    *,
    revision_number: int = 0,
    origin: RevisionOrigin | None = None,
    previous_report_sha256: str | None = None,
    triggering_gap_ids: tuple[str, ...] = (),
) -> DiagnosisRevision:
    report = make_diagnosis_report()
    return DiagnosisRevision(
        revision_id=f"revision-{revision_number}",
        case_id="case-review-1",
        revision_number=revision_number,
        origin=origin
        or (
            RevisionOrigin.INITIAL_DIAGNOSIS
            if revision_number == 0
            else RevisionOrigin.EVIDENCE_REVISION
        ),
        previous_report_sha256=previous_report_sha256,
        report=report,
        report_sha256=canonical_sha256(report),
        triggering_gap_ids=triggering_gap_ids,
        provenance=report.provenance,
        created_at=NOW,
    )


def make_correction_draft() -> DiagnosisCorrectionDraft:
    return DiagnosisCorrectionDraft(
        status=DiagnosisStatus.DIAGNOSED,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-tool",),
        causal_chain=(
            CorrectionClaim(
                stage=ClaimStage.CAUSE,
                statement="The refund tool rejected the request.",
                selectors=(
                    EvidenceSelector(
                        span_id="span-tool",
                        field_path="attributes.tool.error.type",
                    ),
                ),
            ),
        ),
        confidence=1.0,
    )


def make_awaiting_human_case() -> DiagnosisReviewCase:
    return DiagnosisReviewCase(
        case_id="case-review-1",
        trace_id="trace-review-1",
        run_id="run-review-1",
        status=ReviewStatus.AWAITING_HUMAN_REVIEW,
        version=2,
        verification_mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.RULES,
        current_revision_number=0,
        evidence_revision_count=0,
        deterministic_run_id="verifier-deterministic-1",
        composite_verdict=VerifierVerdict.VERIFIED,
        created_at=NOW,
        updated_at=NOW,
    )


def make_finding(
    *,
    finding_id: str = "finding-1",
    code: FindingCode = FindingCode.CLAIM_NOT_GROUNDED,
    severity: FindingSeverity = FindingSeverity.HARD,
    revisable: bool = True,
) -> VerificationFinding:
    return VerificationFinding(
        finding_id=finding_id,
        code=code,
        severity=severity,
        message="The claim requires grounded evidence.",
        revisable=revisable,
        related_selectors=("span-tool::attributes.tool.error.type",),
        related_span_ids=("span-tool",),
    )
