from __future__ import annotations

from collections.abc import Callable, Hashable
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from afc.diagnosis.errors import (
    ProviderConfigurationError,
    ProviderError,
    ProviderProtocolError,
    ProviderRequestError,
)
from afc.review.commands import (
    AppendDiagnosisRevision,
    AppendVerifierRun,
    ClaimReviewWork,
    RouteRevisionFailureToHuman,
    RouteToHumanReview,
    WorkflowEventType,
)
from afc.review.errors import ReviewConflictError, ReviewError
from afc.review.models import (
    DiagnosisReviewDetail,
    DiagnosisRevision,
    FindingCode,
    FindingSeverity,
    OperationalErrorMetadata,
    ReviewRuntimeBundle,
    ReviewStatus,
    RevisionOrigin,
    VerificationFinding,
    VerificationInput,
    VerificationMode,
    VerifierKind,
    VerifierProvenance,
    VerifierReport,
    VerifierVerdict,
    canonical_json,
    canonical_sha256,
)
from afc.review.protocols import ReviewRepository, ReviewReviser, Verifier
from afc.review.verdicts import MergedVerifierReports, merge_verifier_reports


class ReviewWorkflowState(TypedDict):
    case_id: str
    verification_round: int
    composite_verdict: str | None
    route: NotRequired[str]
    lease_claimed: NotRequired[bool]


class ReviewWorkflowProviderError(ReviewError):
    """Sanitized provider failure raised only after durable human routing."""

    def __init__(self, case_id: str, code: str, *, retryable: bool) -> None:
        super().__init__(f"review provider failed: {code}")
        self.case_id = case_id
        self.code = code
        self.retryable = retryable


def _require_aware_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("clock must return an aware UTC timestamp")
    return value


def _provider_failure(error: ProviderError) -> tuple[str, bool]:
    if isinstance(error, ProviderRequestError):
        code = (
            error.code
            if error.code
            in {"transport_error", "upstream_http_error", "missing_response"}
            else "provider_request_error"
        )
        return code, error.retryable
    if isinstance(error, ProviderConfigurationError):
        return "provider_not_configured", False
    if isinstance(error, ProviderProtocolError):
        return "provider_protocol_error", False
    return "provider_error", False


class ReviewWorkflow:
    """Coordinate one bounded review invocation over SQLite-authoritative state.

    A provider call is made only after a durable lease claim. Crash recovery may
    therefore invoke a model at least once (and may bill it more than once), while
    repository CAS and immutable IDs provide exactly-once persisted domain effects.
    LangGraph state contains routing hints only and is never a recovery record.
    """

    def __init__(
        self,
        *,
        repository: ReviewRepository,
        deterministic_verifier: Verifier,
        semantic_verifier: Verifier | None,
        reviser: ReviewReviser,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
        lease_owner: str,
        lease_duration: timedelta,
    ) -> None:
        if deterministic_verifier.kind is not VerifierKind.DETERMINISTIC:
            raise ValueError("deterministic_verifier must be deterministic")
        if semantic_verifier is not None and semantic_verifier.kind is not VerifierKind.SEMANTIC:
            raise ValueError("semantic_verifier must be semantic")
        if not lease_owner:
            raise ValueError("lease_owner must not be empty")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        _require_aware_utc(clock())
        self._repository = repository
        self._deterministic = deterministic_verifier
        self._semantic = semantic_verifier
        self._reviser = reviser
        self._id_factory = id_factory
        self._clock = clock
        self._lease_owner = lease_owner
        self._lease_duration = lease_duration
        self.graph = self._compile_graph()

    def _compile_graph(self) -> Any:
        graph = StateGraph(ReviewWorkflowState)
        graph.add_node("dispatch", self._dispatch)
        graph.add_node("verify_initial", self._verify_initial)
        graph.add_node("request_revision", self._request_revision)
        graph.add_node("revise_once", self._revise_once)
        graph.add_node("verify_final", self._verify_final)
        graph.add_node("route_to_human", self._route_to_human)
        graph.add_edge(START, "dispatch")
        routes: dict[Hashable, str] = {
            "verify_initial": "verify_initial",
            "request_revision": "request_revision",
            "revise_once": "revise_once",
            "verify_final": "verify_final",
            "route_to_human": "route_to_human",
            "end": END,
        }
        graph.add_conditional_edges("dispatch", self._route, routes)
        graph.add_conditional_edges("verify_initial", self._route, routes)
        graph.add_conditional_edges("request_revision", self._route, routes)
        graph.add_conditional_edges("revise_once", self._route, routes)
        graph.add_conditional_edges("verify_final", self._route, routes)
        graph.add_edge("route_to_human", END)
        return graph.compile()

    @staticmethod
    def _route(state: ReviewWorkflowState) -> str:
        return state["route"]

    def _now(self) -> datetime:
        return _require_aware_utc(self._clock())

    async def run(self, case_id: str) -> DiagnosisReviewDetail:
        return await self._execute(case_id)

    async def resume(self, case_id: str) -> DiagnosisReviewDetail:
        return await self._execute(case_id)

    async def _execute(self, case_id: str) -> DiagnosisReviewDetail:
        runtime = await self._repository.load_runtime(case_id)
        if runtime.case.status in {
            ReviewStatus.AWAITING_HUMAN_REVIEW,
            ReviewStatus.CONFIRMED,
            ReviewStatus.CORRECTED,
            ReviewStatus.REJECTED,
        }:
            raise ReviewConflictError("review case cannot be resumed from its current status")
        state: ReviewWorkflowState = {
            "case_id": case_id,
            "verification_round": runtime.case.current_revision_number,
            "composite_verdict": (
                runtime.case.composite_verdict.value
                if runtime.case.composite_verdict is not None
                else None
            ),
        }
        await self.graph.ainvoke(state)
        return await self._repository.get_detail(case_id)

    async def _dispatch(self, state: ReviewWorkflowState) -> ReviewWorkflowState:
        runtime = await self._repository.load_runtime(state["case_id"])
        status = runtime.case.status
        if status is ReviewStatus.PENDING_VERIFICATION:
            route = "verify_initial"
        elif status is ReviewStatus.VERIFYING:
            route = (
                "verify_initial" if runtime.case.current_revision_number == 0 else "verify_final"
            )
        elif status is ReviewStatus.REVISION_REQUESTED:
            route = "request_revision"
        elif status is ReviewStatus.REVISING:
            route = "revise_once"
        else:
            raise ReviewConflictError("review case cannot be resumed from its current status")
        return {
            **state,
            "verification_round": runtime.case.current_revision_number,
            "composite_verdict": (
                runtime.case.composite_verdict.value
                if runtime.case.composite_verdict is not None
                else None
            ),
            "route": route,
            "lease_claimed": False,
        }

    async def _verify_initial(self, state: ReviewWorkflowState) -> ReviewWorkflowState:
        return await self._verify_round(state, expected_round=0)

    async def _verify_final(self, state: ReviewWorkflowState) -> ReviewWorkflowState:
        return await self._verify_round(state, expected_round=1)

    async def _claim(
        self,
        runtime: ReviewRuntimeBundle,
        *,
        target: ReviewStatus,
        event_type: WorkflowEventType,
    ) -> ReviewRuntimeBundle:
        now = self._now()
        await self._repository.claim_work(
            ClaimReviewWork(
                case_id=runtime.case.case_id,
                expected_version=runtime.case.version,
                prior_status=runtime.case.status,
                target_status=target,
                lease_owner=self._lease_owner,
                lease_expires_at=now + self._lease_duration,
                now=now,
                event_id=self._id_factory(),
                event_type=event_type,
                event_metadata_json=canonical_json({"lease_owner": self._lease_owner}),
                occurred_at=now,
            )
        )
        return await self._repository.load_runtime(runtime.case.case_id)

    @staticmethod
    def _reports_for_revision(
        runtime: ReviewRuntimeBundle,
    ) -> tuple[VerifierReport | None, VerifierReport | None]:
        deterministic: VerifierReport | None = None
        semantic: VerifierReport | None = None
        for report in runtime.verifier_reports:
            if report.revision_number != runtime.case.current_revision_number:
                continue
            if report.verifier_kind is VerifierKind.DETERMINISTIC:
                deterministic = report
            else:
                semantic = report
        return deterministic, semantic

    def _should_request_revision(
        self, runtime: ReviewRuntimeBundle, verdict: VerifierVerdict
    ) -> bool:
        return (
            verdict is VerifierVerdict.NEEDS_EVIDENCE
            and runtime.case.current_revision_number == 0
            and runtime.case.evidence_revision_count == 0
            and self._reviser.supports(runtime.case.diagnoser)
        )

    async def _verify_round(
        self, state: ReviewWorkflowState, *, expected_round: int
    ) -> ReviewWorkflowState:
        case_id = state["case_id"]
        runtime = await self._repository.load_runtime(case_id)
        if runtime.case.current_revision_number != expected_round:
            raise ReviewConflictError("verification round conflicts with durable state")
        deterministic, semantic = self._reports_for_revision(runtime)

        if deterministic is None:
            if runtime.case.status not in {
                ReviewStatus.PENDING_VERIFICATION,
                ReviewStatus.VERIFYING,
            }:
                raise ReviewConflictError("deterministic verification is not claimable")
            runtime = await self._claim(
                runtime,
                target=ReviewStatus.VERIFYING,
                event_type=WorkflowEventType.VERIFICATION_STARTED,
            )
            input_ = self._verification_input(runtime)
            report = await self._deterministic.verify(input_)
            deterministic = self._normalize_report(
                report, VerifierKind.DETERMINISTIC, expected_round
            )
            merged = merge_verifier_reports(deterministic, None)
            request_revision = self._should_request_revision(runtime, merged.verdict)
            runtime = await self._append_verifier(
                runtime,
                deterministic,
                merged,
                request_revision=request_revision,
            )
            if request_revision:
                return self._state_after(runtime, "request_revision")

        if deterministic.verdict is not VerifierVerdict.VERIFIED:
            runtime = await self._repository.load_runtime(case_id)
            return self._state_after(runtime, "route_to_human")

        runtime = await self._repository.load_runtime(case_id)
        if runtime.case.verification_mode is VerificationMode.HYBRID:
            deterministic, semantic = self._reports_for_revision(runtime)
            if deterministic is None:
                raise ReviewConflictError("deterministic verification is missing")
            if semantic is None:
                runtime = await self._claim(
                    runtime,
                    target=ReviewStatus.VERIFYING,
                    event_type=WorkflowEventType.VERIFICATION_STARTED,
                )
                started_at = self._now()
                if self._semantic is None:
                    error = ProviderConfigurationError(
                        "semantic verifier is not configured"
                    )
                    await self._persist_semantic_failure(
                        runtime, deterministic, error, started_at=started_at
                    )
                    raise ReviewWorkflowProviderError(
                        case_id, "provider_not_configured", retryable=False
                    ) from None
                try:
                    report = await self._semantic.verify(self._verification_input(runtime))
                except ProviderError as error:
                    await self._persist_semantic_failure(
                        runtime, deterministic, error, started_at=started_at
                    )
                    code, retryable = _provider_failure(error)
                    raise ReviewWorkflowProviderError(case_id, code, retryable=retryable) from None
                semantic = self._normalize_report(report, VerifierKind.SEMANTIC, expected_round)
                merged = merge_verifier_reports(deterministic, semantic)
                request_revision = self._should_request_revision(runtime, merged.verdict)
                runtime = await self._append_verifier(
                    runtime,
                    semantic,
                    merged,
                    request_revision=request_revision,
                )
                if request_revision:
                    return self._state_after(runtime, "request_revision")

        runtime = await self._repository.load_runtime(case_id)
        return self._state_after(runtime, "route_to_human")

    @staticmethod
    def _verification_input(runtime: ReviewRuntimeBundle) -> VerificationInput:
        revision = runtime.revisions[-1]
        return VerificationInput(
            snapshot=runtime.snapshot,
            report=revision.report,
            report_sha256=revision.report_sha256,
        )

    @staticmethod
    def _normalize_report(
        report: VerifierReport, kind: VerifierKind, revision_number: int
    ) -> VerifierReport:
        if report.verifier_kind is not kind or report.provenance.verifier_kind is not kind:
            raise ReviewConflictError("verifier returned the wrong verifier kind")
        return VerifierReport.model_validate(
            {**report.model_dump(), "revision_number": revision_number}
        )

    async def _append_verifier(
        self,
        runtime: ReviewRuntimeBundle,
        report: VerifierReport,
        merged: MergedVerifierReports,
        *,
        request_revision: bool,
        event_type: WorkflowEventType | None = None,
    ) -> ReviewRuntimeBundle:
        now = self._now()
        target = ReviewStatus.REVISION_REQUESTED if request_revision else ReviewStatus.VERIFYING
        if event_type is None:
            event_type = (
                WorkflowEventType.REVISION_REQUESTED
                if request_revision
                else WorkflowEventType.VERIFICATION_COMPLETED
            )
        await self._repository.append_verifier_run(
            AppendVerifierRun(
                case_id=runtime.case.case_id,
                expected_version=runtime.case.version,
                prior_status=ReviewStatus.VERIFYING,
                target_status=target,
                report=report,
                composite_verdict=merged.verdict,
                event_id=self._id_factory(),
                event_type=event_type,
                event_metadata_json=canonical_json(
                    {
                        "revision_number": report.revision_number,
                        "verdict": merged.verdict.value,
                        "verifier_kind": report.verifier_kind.value,
                    }
                ),
                occurred_at=now,
            )
        )
        return await self._repository.load_runtime(runtime.case.case_id)

    async def _request_revision(self, state: ReviewWorkflowState) -> ReviewWorkflowState:
        runtime = await self._repository.load_runtime(state["case_id"])
        if runtime.case.status is not ReviewStatus.REVISION_REQUESTED:
            raise ReviewConflictError("revision is not requested")
        if runtime.case.evidence_revision_count != 0 or runtime.case.current_revision_number != 0:
            raise ReviewConflictError("evidence revision limit reached")
        runtime = await self._claim(
            runtime,
            target=ReviewStatus.REVISING,
            event_type=WorkflowEventType.REVISION_STARTED,
        )
        return {
            **self._state_after(runtime, "revise_once"),
            "lease_claimed": True,
        }

    async def _revise_once(self, state: ReviewWorkflowState) -> ReviewWorkflowState:
        runtime = await self._repository.load_runtime(state["case_id"])
        if runtime.case.status is not ReviewStatus.REVISING:
            raise ReviewConflictError("review case is not revising")
        if runtime.case.evidence_revision_count != 0 or runtime.case.current_revision_number != 0:
            raise ReviewConflictError("evidence revision limit reached")
        if not state.get("lease_claimed", False):
            runtime = await self._claim(
                runtime,
                target=ReviewStatus.REVISING,
                event_type=WorkflowEventType.REVISION_STARTED,
            )
        deterministic, semantic = self._reports_for_revision(runtime)
        if deterministic is None:
            raise ReviewConflictError("revision requires a verifier report")
        merged = merge_verifier_reports(deterministic, semantic)
        gaps = tuple(sorted(merged.evidence_gaps, key=lambda gap: gap.gap_id))
        if not gaps or not self._reviser.supports(runtime.case.diagnoser):
            raise ReviewConflictError("evidence revision is unsupported")
        try:
            revised_report = await self._reviser.revise(runtime, gaps)
        except ProviderError as error:
            _, retryable = _provider_failure(error)
            await self._persist_revision_failure(runtime, retryable=retryable)
            raise ReviewWorkflowProviderError(
                runtime.case.case_id,
                "revision_provider_failed",
                retryable=retryable,
            ) from None
        previous = runtime.revisions[-1]
        if (
            revised_report.trace_id != runtime.snapshot.trace_id
            or revised_report.run_id != runtime.snapshot.run_id
            or revised_report.diagnoser is not runtime.case.diagnoser
        ):
            raise ReviewConflictError("revised diagnosis is not bound to the review input")
        now = self._now()
        revision = DiagnosisRevision(
            revision_id=self._id_factory(),
            case_id=runtime.case.case_id,
            revision_number=1,
            origin=RevisionOrigin.EVIDENCE_REVISION,
            previous_report_sha256=previous.report_sha256,
            report=revised_report,
            report_sha256=canonical_sha256(revised_report),
            triggering_gap_ids=tuple(gap.gap_id for gap in gaps),
            provenance=revised_report.provenance,
            created_at=now,
        )
        await self._repository.append_revision(
            AppendDiagnosisRevision(
                case_id=runtime.case.case_id,
                expected_version=runtime.case.version,
                prior_status=ReviewStatus.REVISING,
                target_status=ReviewStatus.VERIFYING,
                revision=revision,
                event_id=self._id_factory(),
                event_type=WorkflowEventType.REVISION_COMPLETED,
                event_metadata_json=canonical_json(
                    {
                        "revision_number": 1,
                        "triggering_gap_ids": list(revision.triggering_gap_ids),
                    }
                ),
                occurred_at=now,
            )
        )
        runtime = await self._repository.load_runtime(runtime.case.case_id)
        return self._state_after(runtime, "verify_final")

    async def _route_to_human(self, state: ReviewWorkflowState) -> ReviewWorkflowState:
        runtime = await self._repository.load_runtime(state["case_id"])
        if runtime.case.status is not ReviewStatus.VERIFYING:
            raise ReviewConflictError("review case is not ready for human review")
        if runtime.case.composite_verdict is None:
            raise ReviewConflictError("review case has no composite verdict")
        now = self._now()
        await self._repository.route_to_human(
            RouteToHumanReview(
                case_id=runtime.case.case_id,
                expected_version=runtime.case.version,
                prior_status=ReviewStatus.VERIFYING,
                target_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
                event_id=self._id_factory(),
                event_type=WorkflowEventType.AWAITING_HUMAN_REVIEW,
                event_metadata_json=canonical_json(
                    {"verdict": runtime.case.composite_verdict.value}
                ),
                occurred_at=now,
            )
        )
        runtime = await self._repository.load_runtime(runtime.case.case_id)
        return self._state_after(runtime, "end")

    async def _persist_semantic_failure(
        self,
        runtime: ReviewRuntimeBundle,
        deterministic: VerifierReport,
        error: ProviderError,
        *,
        started_at: datetime,
    ) -> None:
        code, retryable = _provider_failure(error)
        completed_at = self._now()
        source = f"{runtime.case.case_id}:{runtime.case.current_revision_number}:semantic:{code}"
        digest = sha256(source.encode("utf-8")).hexdigest()
        finding = VerificationFinding(
            finding_id=f"finding-{digest}",
            code=FindingCode.PROVIDER_OPERATIONAL_ERROR,
            severity=FindingSeverity.OPERATIONAL,
            message="Semantic verifier provider failed.",
            revisable=False,
        )
        report = VerifierReport(
            verifier_run_id=f"verifier-{digest}",
            revision_number=runtime.case.current_revision_number,
            verifier_kind=VerifierKind.SEMANTIC,
            verdict=VerifierVerdict.REVIEW_REQUIRED,
            findings=(finding,),
            provenance=VerifierProvenance(
                verifier_kind=VerifierKind.SEMANTIC,
                verifier_version=(
                    self._semantic.version_fingerprint
                    if self._semantic is not None
                    else "semantic-verifier-unconfigured-v1"
                ),
                policy_version="semantic-provider-failure-v1",
            ),
            operational_error=OperationalErrorMetadata(
                code=code,
                message="Semantic verifier provider failed.",
                retryable=retryable,
            ),
            started_at=started_at,
            completed_at=completed_at,
        )
        merged = merge_verifier_reports(deterministic, report)
        runtime = await self._append_verifier(
            runtime,
            report,
            merged,
            request_revision=False,
            event_type=WorkflowEventType.PROVIDER_FAILED,
        )
        await self._route_to_human(self._state_after(runtime, "route_to_human"))

    async def _persist_revision_failure(
        self, runtime: ReviewRuntimeBundle, *, retryable: bool
    ) -> None:
        now = self._now()
        await self._repository.route_revision_failure(
            RouteRevisionFailureToHuman(
                case_id=runtime.case.case_id,
                expected_version=runtime.case.version,
                prior_status=ReviewStatus.REVISING,
                target_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
                composite_verdict=VerifierVerdict.REVIEW_REQUIRED,
                event_id=self._id_factory(),
                event_type=WorkflowEventType.REVISION_PROVIDER_FAILED,
                event_metadata_json=canonical_json(
                    {"code": "revision_provider_failed", "retryable": retryable}
                ),
                occurred_at=now,
            )
        )

    @staticmethod
    def _state_after(runtime: ReviewRuntimeBundle, route: str) -> ReviewWorkflowState:
        return {
            "case_id": runtime.case.case_id,
            "verification_round": runtime.case.current_revision_number,
            "composite_verdict": (
                runtime.case.composite_verdict.value
                if runtime.case.composite_verdict is not None
                else None
            ),
            "route": route,
            "lease_claimed": False,
        }
