from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256

from spanvouch.contracts.diagnosis import (
    DiagnoserKind,
    DiagnosisClaim,
    DiagnosisProvenance,
    DiagnosisReport,
    EvidenceRef,
)
from spanvouch.contracts.trace import DiagnosticContext, TraceIR
from spanvouch.contracts.verification import (
    ReviewInputSnapshot,
    VerificationInput,
    VerificationMode,
    VerifierKind,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.diagnosis.engine import DiagnosisEngine
from spanvouch.review.commands import (
    ApplyHumanDecision,
    CreateReviewCase,
    WorkflowEventType,
    human_decision_transition,
)
from spanvouch.review.errors import ReviewConflictError, ReviewValidationError
from spanvouch.review.models import (
    DecisionAction,
    DiagnosisCorrectionDraft,
    DiagnosisReviewDetail,
    DiagnosisRevision,
    HumanDecisionDraft,
    HumanReviewDecision,
    ReviewStatus,
    RevisionOrigin,
    canonical_json,
    canonical_sha256,
    resume_requires_live_api,
)
from spanvouch.review.protocols import ReviewRepository, ReviewWorkflowRunner
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.protocols import Verifier

CATALOG_VERSION = "evidence-catalog-v1"
HUMAN_CORRECTION_VERSION = "human-correction-v1"
DEFAULT_CREATE_RESERVATION_LEASE = timedelta(seconds=30)
DEFAULT_CREATE_RESERVATION_HEARTBEAT = timedelta(seconds=10)


def _require_aware_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("clock must return an aware UTC timestamp")
    return value


def _build_corrected_report(
    snapshot: ReviewInputSnapshot,
    current_report: DiagnosisReport,
    draft: DiagnosisCorrectionDraft,
) -> DiagnosisReport:
    view = snapshot.trace_view()
    span_ids = {span.span_id for span in view.spans}
    if any(span_id not in span_ids for span_id in draft.critical_span_ids):
        raise ValueError("correction contains an unknown critical span")

    context = DiagnosticContext(
        trace_id=snapshot.trace_id,
        run_id=snapshot.run_id,
        view=view,
    )
    catalog = EvidenceCatalog.from_context(context)
    evidence_by_selector: dict[str, EvidenceRef] = {}
    claims: list[DiagnosisClaim] = []
    for claim in draft.causal_chain:
        claim_evidence_ids: list[str] = []
        for selector in claim.selectors:
            resolved = catalog.resolve(
                selector,
                description="Human correction evidence resolved from the stored snapshot.",
            )
            evidence_by_selector.setdefault(selector.canonical, resolved)
            claim_evidence_ids.append(resolved.evidence_id)
        claims.append(
            DiagnosisClaim(
                stage=claim.stage,
                statement=claim.statement,
                evidence_ids=tuple(claim_evidence_ids),
            )
        )

    if current_report.diagnoser == DiagnoserKind.RULES:
        ruleset_version = HUMAN_CORRECTION_VERSION
        prompt_version = None
        prompt_sha256 = None
    else:
        ruleset_version = None
        prompt_version = HUMAN_CORRECTION_VERSION
        prompt_sha256 = sha256(HUMAN_CORRECTION_VERSION.encode("utf-8")).hexdigest()
    provenance = DiagnosisProvenance(
        taxonomy=current_report.provenance.taxonomy,
        diagnoser_version=HUMAN_CORRECTION_VERSION,
        ruleset_version=ruleset_version,
        prompt_version=prompt_version,
        prompt_sha256=prompt_sha256,
    )
    return DiagnosisReport(
        trace_id=snapshot.trace_id,
        run_id=snapshot.run_id,
        diagnoser=current_report.diagnoser,
        status=draft.status,
        failure_type=draft.failure_type,
        critical_span_ids=draft.critical_span_ids,
        causal_chain=tuple(claims),
        evidence=tuple(
            evidence_by_selector[selector]
            for selector in sorted(evidence_by_selector)
        ),
        confidence=draft.confidence,
        abstain_reason=draft.abstain_reason,
        provenance=provenance,
    )


class ReviewService:
    def __init__(
        self,
        *,
        diagnosis_service: DiagnosisEngine,
        repository: ReviewRepository,
        workflow: ReviewWorkflowRunner,
        deterministic_verifier: Verifier,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
        idempotency_lease_duration: timedelta = DEFAULT_CREATE_RESERVATION_LEASE,
        idempotency_heartbeat_interval: timedelta = DEFAULT_CREATE_RESERVATION_HEARTBEAT,
    ) -> None:
        self._diagnosis_service = diagnosis_service
        self._repository = repository
        self._workflow = workflow
        if deterministic_verifier.kind is not VerifierKind.DETERMINISTIC:
            raise ValueError("human correction requires the deterministic verifier")
        self._deterministic_verifier = deterministic_verifier
        self._id_factory = id_factory
        self._clock = clock
        if idempotency_lease_duration <= timedelta(0):
            raise ValueError("idempotency lease duration must be positive")
        if not timedelta(0) < idempotency_heartbeat_interval < idempotency_lease_duration:
            raise ValueError(
                "idempotency heartbeat interval must be positive and shorter than the lease"
            )
        self._idempotency_lease_duration = idempotency_lease_duration
        self._idempotency_heartbeat_interval = idempotency_heartbeat_interval
        _require_aware_utc(clock())

    def _now(self) -> datetime:
        return _require_aware_utc(self._clock())

    async def _renew_create_reservation_until_stopped(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        reservation_id: str,
        stopped: asyncio.Event,
    ) -> None:
        heartbeat_seconds = self._idempotency_heartbeat_interval.total_seconds()
        while True:
            try:
                await asyncio.wait_for(stopped.wait(), timeout=heartbeat_seconds)
                return
            except TimeoutError:
                now = self._now()
                await self._repository.renew_create_reservation(
                    scope,
                    idempotency_key,
                    request_sha256,
                    reservation_id=reservation_id,
                    now=now,
                    lease_expires_at=now + self._idempotency_lease_duration,
                )

    async def _complete_reserved_create(
        self,
        trace: TraceIR,
        diagnoser: DiagnoserKind,
        *,
        verification_mode: VerificationMode,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        reservation_id: str,
        view_json: str,
        input_sha256: str,
        created_at: datetime,
    ) -> tuple[DiagnosisReviewDetail, str]:
        stopped = asyncio.Event()

        async def persist_reserved_case() -> tuple[DiagnosisReviewDetail, str]:
            try:
                snapshot = ReviewInputSnapshot(
                    trace_id=trace.trace_id,
                    run_id=trace.run_id,
                    view_json=view_json,
                    input_sha256=input_sha256,
                    catalog_version=CATALOG_VERSION,
                    created_at=created_at,
                )
                report = await self._diagnosis_service.diagnose(trace, diagnoser)
                case_id = self._id_factory()
                revision = DiagnosisRevision(
                    revision_id=self._id_factory(),
                    case_id=case_id,
                    revision_number=0,
                    origin=RevisionOrigin.INITIAL_DIAGNOSIS,
                    report=report,
                    report_sha256=canonical_sha256(report),
                    provenance=report.provenance,
                    created_at=created_at,
                )
                command = CreateReviewCase(
                    case_id=case_id,
                    snapshot=snapshot,
                    initial_revision=revision,
                    target_status=ReviewStatus.PENDING_VERIFICATION,
                    verification_mode=verification_mode,
                    diagnoser=diagnoser,
                    idempotency_scope=scope,
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha256,
                    idempotency_reservation_id=reservation_id,
                    event_id=self._id_factory(),
                    event_type=WorkflowEventType.CASE_CREATED,
                    event_metadata_json=canonical_json(
                        {
                            "diagnoser": diagnoser.value,
                            "verification_mode": verification_mode.value,
                        }
                    ),
                    created_at=created_at,
                )
                persisted = await self._repository.create_case(command)
                return persisted, case_id
            finally:
                stopped.set()

        try:
            async with asyncio.TaskGroup() as task_group:
                operation_task = task_group.create_task(persist_reserved_case())
                task_group.create_task(
                    self._renew_create_reservation_until_stopped(
                        scope=scope,
                        idempotency_key=idempotency_key,
                        request_sha256=request_sha256,
                        reservation_id=reservation_id,
                        stopped=stopped,
                    )
                )
        except ExceptionGroup as error:
            if len(error.exceptions) == 1:
                raise error.exceptions[0] from None
            raise
        return operation_task.result()

    async def create(
        self,
        trace: TraceIR,
        *,
        diagnoser: DiagnoserKind,
        verification_mode: VerificationMode,
        idempotency_key: str,
    ) -> DiagnosisReviewDetail:
        context = TraceProjector().project(trace)
        view_json = canonical_json(context.view)
        input_sha256 = canonical_sha256(context.view)
        request_sha256 = canonical_sha256(
            {
                "trace_id": trace.trace_id,
                "run_id": trace.run_id,
                "input_sha256": input_sha256,
                "diagnoser": diagnoser.value,
                "verification_mode": verification_mode.value,
            }
        )
        idempotency_scope = f"review.create:{trace.trace_id}"
        now = self._now()
        reservation_source = canonical_json(
            {
                "scope": idempotency_scope,
                "idempotency_key": idempotency_key,
                "request_sha256": request_sha256,
                "reserved_at": now.isoformat(),
            }
        )
        reservation_id = sha256(reservation_source.encode("utf-8")).hexdigest()
        replay = await self._repository.reserve_create(
            idempotency_scope,
            idempotency_key,
            request_sha256,
            reservation_id=reservation_id,
            now=now,
            lease_expires_at=now + self._idempotency_lease_duration,
        )
        if replay is not None:
            return replay
        persisted, case_id = await self._complete_reserved_create(
            trace,
            diagnoser,
            verification_mode=verification_mode,
            scope=idempotency_scope,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            reservation_id=reservation_id,
            view_json=view_json,
            input_sha256=input_sha256,
            created_at=now,
        )
        if persisted.case.case_id != case_id:
            return persisted
        await self._workflow.run(case_id)
        return await self._repository.get_detail(case_id)

    async def get(self, case_id: str) -> DiagnosisReviewDetail:
        return await self._repository.get_detail(case_id)

    async def resume(
        self, case_id: str, *, allow_live_api: bool = False
    ) -> DiagnosisReviewDetail:
        runtime = await self._repository.load_runtime(case_id)
        if (
            resume_requires_live_api(runtime.case, runtime.verifier_reports)
            and not allow_live_api
        ):
            raise ReviewConflictError("live API resume requires explicit consent")
        await self._workflow.resume(case_id)
        return await self._repository.get_detail(case_id)

    async def decide(
        self,
        case_id: str,
        decision: HumanDecisionDraft,
        *,
        idempotency_key: str,
    ) -> DiagnosisReviewDetail:
        request_sha256 = canonical_sha256(
            {"case_id": case_id, "decision": decision.model_dump(mode="json")}
        )
        replay = await self._repository.replay_detail(
            f"review.decision:{case_id}",
            idempotency_key,
            request_sha256,
            result_type="review_detail",
        )
        if replay is not None:
            return replay
        runtime = await self._repository.load_runtime(case_id)
        if decision.action is DecisionAction.REJECT and not (decision.reason or "").strip():
            raise ReviewConflictError("reject requires a non-empty reason")
        if (
            decision.action is DecisionAction.CONFIRM
            and runtime.case.composite_verdict is not VerifierVerdict.VERIFIED
            and not (decision.reason or "").strip()
        ):
            raise ReviewConflictError("confirm requires a non-empty override reason")

        now = self._now()
        correction_revision: DiagnosisRevision | None = None
        correction_verifier_report: VerifierReport | None = None
        if decision.action is DecisionAction.CORRECT:
            if decision.correction is None:
                raise ReviewValidationError("correct requires a complete correction")
            try:
                current_revision = runtime.revisions[-1]
                report = _build_corrected_report(
                    runtime.snapshot, current_revision.report, decision.correction
                )
            except (IndexError, KeyError, TypeError, ValueError):
                raise ReviewValidationError("human correction is invalid") from None
            report_sha256 = canonical_sha256(report)
            try:
                verification = await self._deterministic_verifier.verify(
                    VerificationInput(
                        snapshot=runtime.snapshot,
                        report=report,
                        report_sha256=report_sha256,
                        revision_number=runtime.case.current_revision_number + 1,
                    )
                )
            except Exception:
                raise ReviewValidationError(
                    "human correction verification failed"
                ) from None
            if verification.verdict is not VerifierVerdict.VERIFIED:
                raise ReviewValidationError(
                    "human correction failed deterministic verification"
                )
            correction_revision = DiagnosisRevision(
                revision_id=self._id_factory(),
                case_id=case_id,
                revision_number=runtime.case.current_revision_number + 1,
                origin=RevisionOrigin.HUMAN_CORRECTION,
                previous_report_sha256=current_revision.report_sha256,
                report=report,
                report_sha256=report_sha256,
                provenance=report.provenance,
                created_at=now,
            )
            if verification.report_sha256 != correction_revision.report_sha256:
                raise ReviewValidationError("human correction verification binding failed")
            correction_verifier_report = VerifierReport.model_validate(
                {
                    **verification.model_dump(),
                    "revision_number": correction_revision.revision_number,
                }
            )

        decision_id = self._id_factory()
        human_decision = HumanReviewDecision(
            **decision.model_dump(),
            decision_id=decision_id,
            case_id=case_id,
            resulting_revision_id=(
                correction_revision.revision_id if correction_revision is not None else None
            ),
            created_at=now,
        )
        target_status, event_type = human_decision_transition(decision.action)
        command = ApplyHumanDecision(
            case_id=case_id,
            expected_version=decision.expected_version,
            prior_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
            target_status=target_status,
            decision=human_decision,
            correction_revision=correction_revision,
            correction_verifier_report=correction_verifier_report,
            idempotency_scope=f"review.decision:{case_id}",
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            event_id=self._id_factory(),
            event_type=event_type,
            event_metadata_json=canonical_json(
                {"action": decision.action.value, "reviewer_label": decision.reviewer_label}
            ),
            occurred_at=now,
        )
        return await self._repository.apply_human_decision(command)
