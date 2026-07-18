from dataclasses import dataclass

from spanvouch.contracts.verification import (
    EvidenceGap,
    FindingCode,
    FindingSeverity,
    VerificationFinding,
    VerifierKind,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.review.models import (
    ReviewStatus,
)

_ALLOWED_TRANSITIONS = frozenset(
    {
        (ReviewStatus.PENDING_VERIFICATION, ReviewStatus.VERIFYING),
        (ReviewStatus.VERIFYING, ReviewStatus.REVISION_REQUESTED),
        (ReviewStatus.VERIFYING, ReviewStatus.AWAITING_HUMAN_REVIEW),
        (ReviewStatus.REVISION_REQUESTED, ReviewStatus.REVISING),
        (ReviewStatus.REVISING, ReviewStatus.VERIFYING),
        (ReviewStatus.AWAITING_HUMAN_REVIEW, ReviewStatus.CONFIRMED),
        (ReviewStatus.AWAITING_HUMAN_REVIEW, ReviewStatus.CORRECTED),
        (ReviewStatus.AWAITING_HUMAN_REVIEW, ReviewStatus.REJECTED),
    }
)

_KIND_ORDER: dict[str, int] = {
    VerifierKind.DETERMINISTIC: 0,
    VerifierKind.SEMANTIC: 1,
}
_SEVERITY_ORDER = {
    FindingSeverity.HARD: 0,
    FindingSeverity.ADVISORY: 1,
    FindingSeverity.OPERATIONAL: 2,
}


@dataclass(frozen=True)
class MergedVerifierReports:
    deterministic: VerifierReport
    semantic: VerifierReport | None
    verdict: VerifierVerdict
    findings: tuple[VerificationFinding, ...]
    evidence_gaps: tuple[EvidenceGap, ...]


def assert_transition(current: ReviewStatus, target: ReviewStatus) -> None:
    """Raise when a review state transition is not part of the closed graph."""
    if (current, target) not in _ALLOWED_TRANSITIONS:
        raise ValueError(f"invalid review transition: {current.value} -> {target.value}")


def assert_revision_request_allowed(evidence_revision_count: int) -> None:
    """Raise unless the one evidence-revision allowance remains unused."""
    if evidence_revision_count < 0:
        raise ValueError("evidence revision count must be non-negative")
    if evidence_revision_count >= 1:
        raise ValueError("a second evidence revision transition is forbidden")


def _deterministic_verdict(report: VerifierReport) -> VerifierVerdict:
    hard_findings = tuple(
        finding for finding in report.findings if finding.severity is FindingSeverity.HARD
    )
    if not hard_findings:
        return report.verdict
    if all(finding.revisable for finding in hard_findings):
        return VerifierVerdict.NEEDS_EVIDENCE
    return VerifierVerdict.REVIEW_REQUIRED


def _has_provider_operational_error(report: VerifierReport) -> bool:
    return report.operational_error is not None or any(
        finding.code is FindingCode.PROVIDER_OPERATIONAL_ERROR for finding in report.findings
    )


def _composite_verdict(
    deterministic: VerifierReport,
    semantic: VerifierReport | None,
) -> VerifierVerdict:
    deterministic_verdict = _deterministic_verdict(deterministic)
    if semantic is None:
        return deterministic_verdict
    if _has_provider_operational_error(semantic):
        return VerifierVerdict.REVIEW_REQUIRED
    if deterministic_verdict is VerifierVerdict.VERIFIED:
        return semantic.verdict
    if semantic.verdict is deterministic_verdict:
        return deterministic_verdict
    return VerifierVerdict.REVIEW_REQUIRED


def merge_verifier_reports(
    deterministic: VerifierReport,
    semantic: VerifierReport | None,
) -> MergedVerifierReports:
    """Combine verifier decisions without rewriting either source report."""
    if deterministic.verifier_kind != VerifierKind.DETERMINISTIC:
        raise ValueError("deterministic report must have deterministic verifier kind")
    if semantic is not None:
        if semantic.verifier_kind != VerifierKind.SEMANTIC:
            raise ValueError("semantic report must have semantic verifier kind")
        if semantic.revision_number != deterministic.revision_number:
            raise ValueError("verifier reports must target the same revision")

    sourced_findings: list[tuple[str, VerificationFinding]] = [
        (deterministic.verifier_kind, finding) for finding in deterministic.findings
    ]
    if semantic is not None:
        sourced_findings.extend(
            (semantic.verifier_kind, finding) for finding in semantic.findings
        )
    sourced_findings.sort(
        key=lambda item: (
            _KIND_ORDER[item[0]],
            _SEVERITY_ORDER[item[1].severity],
            item[1].code.value,
            item[1].finding_id,
        )
    )

    evidence_gaps = deterministic.evidence_gaps
    if semantic is not None:
        evidence_gaps += semantic.evidence_gaps
    return MergedVerifierReports(
        deterministic=deterministic,
        semantic=semantic,
        verdict=_composite_verdict(deterministic, semantic),
        findings=tuple(finding for _, finding in sourced_findings),
        evidence_gaps=evidence_gaps,
    )
