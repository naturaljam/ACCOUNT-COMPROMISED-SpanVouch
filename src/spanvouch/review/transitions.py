from enum import StrEnum

from spanvouch.contracts.review import (
    DecisionAction,
    DiagnosisReviewCase,
    ReviewStatus,
    WorkflowEventType,
)
from spanvouch.contracts.verification import VerifierVerdict


class ReviewRoute(StrEnum):
    VERIFY_INITIAL = "verify_initial"
    REQUEST_REVISION = "request_revision"
    REVISE_ONCE = "revise_once"
    VERIFY_FINAL = "verify_final"
    ROUTE_TO_HUMAN = "route_to_human"
    END = "end"


def next_route(case: DiagnosisReviewCase) -> ReviewRoute:
    if case.status in {
        ReviewStatus.AWAITING_HUMAN_REVIEW,
        ReviewStatus.CONFIRMED,
        ReviewStatus.CORRECTED,
        ReviewStatus.REJECTED,
    }:
        return ReviewRoute.END
    if case.status in {
        ReviewStatus.PENDING_VERIFICATION,
        ReviewStatus.VERIFYING,
    }:
        if case.current_revision_number == 0:
            return ReviewRoute.VERIFY_INITIAL
        return ReviewRoute.VERIFY_FINAL
    revision_limit_reached = (
        case.current_revision_number >= 1 or case.evidence_revision_count >= 1
    )
    if case.status is ReviewStatus.REVISION_REQUESTED:
        if revision_limit_reached:
            return ReviewRoute.ROUTE_TO_HUMAN
        return ReviewRoute.REQUEST_REVISION
    if case.status is ReviewStatus.REVISING:
        if revision_limit_reached:
            return ReviewRoute.ROUTE_TO_HUMAN
        return ReviewRoute.REVISE_ONCE
    return ReviewRoute.ROUTE_TO_HUMAN


def should_request_revision(
    case: DiagnosisReviewCase,
    verdict: VerifierVerdict,
    *,
    reviser_supported: bool,
) -> bool:
    return (
        verdict is VerifierVerdict.NEEDS_EVIDENCE
        and case.current_revision_number == 0
        and case.evidence_revision_count == 0
        and reviser_supported
    )


def human_decision_transition(
    action: DecisionAction,
) -> tuple[ReviewStatus, WorkflowEventType]:
    return {
        DecisionAction.CONFIRM: (
            ReviewStatus.CONFIRMED,
            WorkflowEventType.HUMAN_CONFIRMED,
        ),
        DecisionAction.CORRECT: (
            ReviewStatus.CORRECTED,
            WorkflowEventType.HUMAN_CORRECTED,
        ),
        DecisionAction.REJECT: (
            ReviewStatus.REJECTED,
            WorkflowEventType.HUMAN_REJECTED,
        ),
    }[action]
