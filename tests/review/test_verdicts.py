import pytest

from afc.review.models import (
    FindingCode,
    FindingSeverity,
    OperationalErrorMetadata,
    ReviewStatus,
    VerifierKind,
    VerifierVerdict,
)
from afc.review.verdicts import (
    assert_revision_request_allowed,
    assert_transition,
    merge_verifier_reports,
)
from tests.review.factories import make_finding, make_verifier_report


@pytest.mark.parametrize(
    ("current", "target"),
    (
        (ReviewStatus.PENDING_VERIFICATION, ReviewStatus.VERIFYING),
        (ReviewStatus.VERIFYING, ReviewStatus.REVISION_REQUESTED),
        (ReviewStatus.VERIFYING, ReviewStatus.AWAITING_HUMAN_REVIEW),
        (ReviewStatus.REVISION_REQUESTED, ReviewStatus.REVISING),
        (ReviewStatus.REVISING, ReviewStatus.VERIFYING),
        (ReviewStatus.AWAITING_HUMAN_REVIEW, ReviewStatus.CONFIRMED),
        (ReviewStatus.AWAITING_HUMAN_REVIEW, ReviewStatus.CORRECTED),
        (ReviewStatus.AWAITING_HUMAN_REVIEW, ReviewStatus.REJECTED),
    ),
)
def test_transition_accepts_every_allowed_edge(
    current: ReviewStatus,
    target: ReviewStatus,
) -> None:
    assert_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    (
        (ReviewStatus.CONFIRMED, ReviewStatus.PENDING_VERIFICATION),
        (ReviewStatus.CORRECTED, ReviewStatus.AWAITING_HUMAN_REVIEW),
        (ReviewStatus.REJECTED, ReviewStatus.VERIFYING),
        (ReviewStatus.VERIFYING, ReviewStatus.PENDING_VERIFICATION),
        (ReviewStatus.REVISION_REQUESTED, ReviewStatus.VERIFYING),
        (ReviewStatus.REVISING, ReviewStatus.REVISION_REQUESTED),
        (ReviewStatus.AWAITING_HUMAN_REVIEW, ReviewStatus.VERIFYING),
        (ReviewStatus.REVISION_REQUESTED, ReviewStatus.REVISION_REQUESTED),
    ),
)
def test_transition_rejects_terminal_backward_and_second_revision_edges(
    current: ReviewStatus,
    target: ReviewStatus,
) -> None:
    with pytest.raises(ValueError, match="transition"):
        assert_transition(current, target)


def test_transition_retains_the_documented_two_argument_interface() -> None:
    assert_transition(ReviewStatus.PENDING_VERIFICATION, ReviewStatus.VERIFYING)


def test_transition_rejects_a_second_revision_request_after_one_completed_cycle() -> None:
    assert_revision_request_allowed(0)
    assert_transition(ReviewStatus.VERIFYING, ReviewStatus.REVISION_REQUESTED)
    assert_transition(ReviewStatus.REVISION_REQUESTED, ReviewStatus.REVISING)
    assert_transition(ReviewStatus.REVISING, ReviewStatus.VERIFYING)

    with pytest.raises(ValueError, match="revision"):
        assert_revision_request_allowed(1)


@pytest.mark.parametrize("evidence_revision_count", (1, 2))
def test_revision_history_policy_rejects_any_used_revision_allowance(
    evidence_revision_count: int,
) -> None:
    with pytest.raises(ValueError, match="revision"):
        assert_revision_request_allowed(evidence_revision_count)


def test_revisable_deterministic_hard_finding_needs_evidence() -> None:
    deterministic = make_verifier_report(
        verdict=VerifierVerdict.REVIEW_REQUIRED,
        findings=(make_finding(revisable=True),),
    )

    merged = merge_verifier_reports(deterministic, None)

    assert merged.verdict is VerifierVerdict.NEEDS_EVIDENCE


def test_non_revisable_deterministic_hard_finding_requires_review() -> None:
    deterministic = make_verifier_report(
        verdict=VerifierVerdict.NEEDS_EVIDENCE,
        findings=(make_finding(revisable=False),),
    )

    merged = merge_verifier_reports(deterministic, None)

    assert merged.verdict is VerifierVerdict.REVIEW_REQUIRED


def test_verified_deterministic_without_semantic_is_verified() -> None:
    deterministic = make_verifier_report()

    merged = merge_verifier_reports(deterministic, None)

    assert merged.verdict is VerifierVerdict.VERIFIED


@pytest.mark.parametrize(
    "semantic_verdict",
    (
        VerifierVerdict.VERIFIED,
        VerifierVerdict.NEEDS_EVIDENCE,
        VerifierVerdict.REVIEW_REQUIRED,
    ),
)
def test_verified_deterministic_uses_semantic_verdict(
    semantic_verdict: VerifierVerdict,
) -> None:
    deterministic = make_verifier_report()
    semantic = make_verifier_report(
        kind=VerifierKind.SEMANTIC,
        verdict=semantic_verdict,
    )

    merged = merge_verifier_reports(deterministic, semantic)

    assert merged.verdict is semantic_verdict


def test_deterministic_semantic_disagreement_requires_review() -> None:
    deterministic = make_verifier_report(
        verdict=VerifierVerdict.NEEDS_EVIDENCE,
        findings=(make_finding(revisable=True),),
    )
    semantic = make_verifier_report(
        kind=VerifierKind.SEMANTIC,
        verdict=VerifierVerdict.VERIFIED,
    )

    merged = merge_verifier_reports(deterministic, semantic)

    assert merged.verdict is VerifierVerdict.REVIEW_REQUIRED


def test_semantic_provider_operational_error_requires_review() -> None:
    deterministic = make_verifier_report()
    semantic = make_verifier_report(
        kind=VerifierKind.SEMANTIC,
        verdict=VerifierVerdict.NEEDS_EVIDENCE,
        findings=(
            make_finding(
                code=FindingCode.PROVIDER_OPERATIONAL_ERROR,
                severity=FindingSeverity.OPERATIONAL,
                revisable=False,
            ),
        ),
        operational_error=OperationalErrorMetadata(
            code="provider_unavailable",
            message="The provider is unavailable.",
            retryable=True,
        ),
    )

    merged = merge_verifier_reports(deterministic, semantic)

    assert merged.verdict is VerifierVerdict.REVIEW_REQUIRED


def test_merge_preserves_reports_and_orders_findings_stably() -> None:
    deterministic = make_verifier_report(
        verdict=VerifierVerdict.REVIEW_REQUIRED,
        findings=(
            make_finding(
                finding_id="det-advisory",
                code=FindingCode.ALTERNATIVE_HYPOTHESIS,
                severity=FindingSeverity.ADVISORY,
                revisable=False,
            ),
            make_finding(
                finding_id="det-hard-z",
                code=FindingCode.CLAIM_NOT_GROUNDED,
                severity=FindingSeverity.HARD,
                revisable=False,
            ),
            make_finding(
                finding_id="det-invalid-selector",
                code=FindingCode.INVALID_SELECTOR,
                severity=FindingSeverity.HARD,
                revisable=False,
            ),
            make_finding(
                finding_id="det-hard-a",
                code=FindingCode.CLAIM_NOT_GROUNDED,
                severity=FindingSeverity.HARD,
                revisable=False,
            ),
        ),
    )
    semantic = make_verifier_report(
        kind=VerifierKind.SEMANTIC,
        verdict=VerifierVerdict.REVIEW_REQUIRED,
        findings=(
            make_finding(
                finding_id="sem-operational",
                code=FindingCode.PROVIDER_OPERATIONAL_ERROR,
                severity=FindingSeverity.OPERATIONAL,
                revisable=False,
            ),
            make_finding(
                finding_id="sem-hard",
                code=FindingCode.SEMANTIC_SUPPORT_MISSING,
                severity=FindingSeverity.HARD,
                revisable=False,
            ),
        ),
    )
    deterministic_dump = deterministic.model_dump(mode="json")
    semantic_dump = semantic.model_dump(mode="json")

    merged = merge_verifier_reports(deterministic, semantic)

    assert merged.deterministic is deterministic
    assert merged.semantic is semantic
    assert deterministic.model_dump(mode="json") == deterministic_dump
    assert semantic.model_dump(mode="json") == semantic_dump
    assert tuple(finding.finding_id for finding in merged.findings) == (
        "det-hard-a",
        "det-hard-z",
        "det-invalid-selector",
        "det-advisory",
        "sem-hard",
        "sem-operational",
    )
