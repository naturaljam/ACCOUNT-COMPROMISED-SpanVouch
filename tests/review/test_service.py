from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from afc.diagnosis.models import (
    ClaimStage,
    DiagnoserKind,
    DiagnosisClaim,
    DiagnosisDecision,
    DiagnosisExecution,
    DiagnosisProvenance,
    DiagnosisStatus,
    EvidenceSelector,
)
from afc.diagnosis.service import DiagnosisService
from afc.failure_types import FailureType
from afc.review.commands import ApplyHumanDecision, CreateReviewCase
from afc.review.errors import ReviewConflictError
from afc.review.models import (
    CorrectionClaim,
    DecisionAction,
    DiagnosisCorrectionDraft,
    DiagnosisReviewCase,
    DiagnosisReviewDetail,
    HumanDecisionDraft,
    ReviewRuntimeBundle,
    ReviewStatus,
    VerificationMode,
    VerifierKind,
    VerifierVerdict,
    canonical_json,
    canonical_sha256,
)
from afc.review.service import ReviewService
from afc.review.sqlite_repository import SQLiteReviewRepository
from tests.diagnosis.test_trace_view import load_trace
from tests.review.factories import NOW, make_verifier_report
from tests.review.test_sqlite_repository import (
    _claim_command,
    _route_command,
    _verifier_command,
)


class SelectorDiagnoser:
    version_fingerprint = "selector-diagnoser-v1"

    def __init__(self, selector: EvidenceSelector) -> None:
        self.selector = selector
        self.calls = 0

    async def diagnose(self, view: Any, evidence: Any) -> DiagnosisExecution:
        self.calls += 1
        resolved = evidence.resolve(self.selector, description="Server-owned evidence")
        return DiagnosisExecution(
            decision=DiagnosisDecision(
                status=DiagnosisStatus.DIAGNOSED,
                failure_type=FailureType.POLICY_VIOLATION,
                critical_span_ids=(self.selector.span_id,),
                causal_chain=(
                    DiagnosisClaim(
                        stage=ClaimStage.CAUSE,
                        statement="The refund was rejected.",
                        evidence_ids=(resolved.evidence_id,),
                    ),
                ),
                evidence=(resolved,),
                confidence=0.9,
            ),
            provenance=DiagnosisProvenance(
                taxonomy_version="1.0",
                diagnoser_version=self.version_fingerprint,
                ruleset_version=self.version_fingerprint,
            ),
        )


class FailingDiagnoser:
    version_fingerprint = "must-not-run"

    async def diagnose(self, view: Any, evidence: Any) -> DiagnosisExecution:
        del view, evidence
        raise AssertionError("diagnoser must not run during durable preflight")


class RecordingRepository:
    def __init__(self) -> None:
        self.detail: DiagnosisReviewDetail | None = None
        self.snapshot = None
        self.create_commands: list[CreateReviewCase] = []
        self.decision_commands: list[ApplyHumanDecision] = []
        self._create_keys: dict[tuple[str, str], tuple[str, DiagnosisReviewDetail]] = {}
        self._decision_keys: dict[tuple[str, str], tuple[str, DiagnosisReviewDetail]] = {}
        self.fail_decision = False
        self.preflight_calls: list[tuple[str, str, str, str]] = []

    async def replay_detail(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        *,
        result_type: str,
    ) -> DiagnosisReviewDetail | None:
        self.preflight_calls.append(
            (scope, idempotency_key, request_sha256, result_type)
        )
        keys = self._create_keys if result_type == "review_case" else self._decision_keys
        replay = keys.get((scope, idempotency_key))
        if replay is None:
            return None
        fingerprint, detail = replay
        if fingerprint != request_sha256:
            raise ReviewConflictError("idempotency key conflict")
        return detail

    async def create_case(self, command: CreateReviewCase) -> DiagnosisReviewDetail:
        self.create_commands.append(command)
        key = (command.idempotency_scope, command.idempotency_key)
        replay = self._create_keys.get(key)
        if replay is not None:
            fingerprint, detail = replay
            if fingerprint != command.request_sha256:
                raise ReviewConflictError("idempotency key conflict")
            return detail
        case = DiagnosisReviewCase(
            case_id=command.case_id,
            trace_id=command.snapshot.trace_id,
            run_id=command.snapshot.run_id,
            status=ReviewStatus.PENDING_VERIFICATION,
            version=0,
            verification_mode=command.verification_mode,
            diagnoser=command.diagnoser,
            current_revision_number=0,
            evidence_revision_count=0,
            created_at=command.created_at,
            updated_at=command.created_at,
        )
        self.snapshot = command.snapshot
        self.detail = DiagnosisReviewDetail(case=case, revisions=(command.initial_revision,))
        self._create_keys[key] = (command.request_sha256, self.detail)
        return self.detail

    async def get_detail(self, case_id: str) -> DiagnosisReviewDetail:
        assert self.detail is not None and self.detail.case.case_id == case_id
        return self.detail

    async def load_runtime(self, case_id: str) -> ReviewRuntimeBundle:
        assert self.detail is not None and self.snapshot is not None
        assert self.detail.case.case_id == case_id
        return ReviewRuntimeBundle(
            case=self.detail.case,
            snapshot=self.snapshot,
            revisions=self.detail.revisions,
            verifier_reports=self.detail.verifier_reports,
        )

    async def apply_human_decision(
        self, command: ApplyHumanDecision
    ) -> DiagnosisReviewDetail:
        self.decision_commands.append(command)
        key = (command.idempotency_scope, command.idempotency_key)
        replay = self._decision_keys.get(key)
        if replay is not None:
            fingerprint, detail = replay
            if fingerprint != command.request_sha256:
                raise ReviewConflictError("idempotency key conflict")
            return detail
        if self.fail_decision:
            raise ReviewConflictError("injected atomic failure")
        assert self.detail is not None
        if self.detail.case.status in {
            ReviewStatus.CONFIRMED,
            ReviewStatus.CORRECTED,
            ReviewStatus.REJECTED,
        }:
            raise ReviewConflictError("terminal review case conflict")
        if self.detail.case.version != command.expected_version:
            raise ReviewConflictError("review version conflict")
        if self.detail.case.status is not command.prior_status:
            raise ReviewConflictError("review status conflict")
        terminal_case = self.detail.case.model_copy(
            update={
                "status": command.target_status,
                "version": command.expected_version + 1,
                "current_revision_number": (
                    command.correction_revision.revision_number
                    if command.correction_revision is not None
                    else self.detail.case.current_revision_number
                ),
                "terminal_decision_id": command.decision.decision_id,
                "updated_at": command.occurred_at,
            }
        )
        revisions = self.detail.revisions + (
            (command.correction_revision,) if command.correction_revision is not None else ()
        )
        self.detail = DiagnosisReviewDetail(
            case=terminal_case,
            revisions=revisions,
            verifier_reports=self.detail.verifier_reports,
            decision=command.decision,
        )
        self._decision_keys[key] = (command.request_sha256, self.detail)
        return self.detail


class RecordingWorkflow:
    def __init__(self, repository: RecordingRepository) -> None:
        self.repository = repository
        self.runs: list[str] = []
        self.resumes: list[str] = []

    async def run(self, case_id: str) -> None:
        assert self.repository.detail is not None
        self.runs.append(case_id)

    async def resume(self, case_id: str) -> None:
        self.resumes.append(case_id)


class RecordingVerifier:
    kind = VerifierKind.DETERMINISTIC
    version_fingerprint = "recording-v1"

    def __init__(self, verdict: VerifierVerdict = VerifierVerdict.VERIFIED) -> None:
        self.verdict = verdict
        self.inputs: list[Any] = []

    async def verify(self, input_: Any) -> Any:
        self.inputs.append(input_)
        return make_verifier_report(verdict=self.verdict)


class NoopWorkflow:
    async def run(self, case_id: str) -> None:
        del case_id

    async def resume(self, case_id: str) -> None:
        del case_id


def id_factory() -> Callable[[], str]:
    values = iter(f"id-{number}" for number in range(100))
    return lambda: next(values)


def make_service(
    repository: RecordingRepository,
    *,
    verifier: RecordingVerifier | None = None,
) -> tuple[ReviewService, SelectorDiagnoser, RecordingWorkflow, RecordingVerifier]:
    diagnoser = SelectorDiagnoser(
        EvidenceSelector(
            span_id="span-005",
            field_path="attributes.tool.error.type",
        )
    )
    diagnosis_service = DiagnosisService(
        {DiagnoserKind.RULES: diagnoser, DiagnoserKind.DEEPSEEK: diagnoser}
    )
    workflow = RecordingWorkflow(repository)
    deterministic = verifier or RecordingVerifier()
    service = ReviewService(
        diagnosis_service=diagnosis_service,
        repository=repository,
        workflow=workflow,
        deterministic_verifier=deterministic,
        id_factory=id_factory(),
        clock=lambda: NOW,
    )
    return service, diagnoser, workflow, deterministic


async def create_case(
    service: ReviewService,
    repository: RecordingRepository,
    *,
    key: str = "create-key",
) -> DiagnosisReviewDetail:
    detail = await service.create(
        load_trace("policy_violation-01"),
        diagnoser=DiagnoserKind.RULES,
        verification_mode=VerificationMode.DETERMINISTIC,
        idempotency_key=key,
    )
    assert repository.detail is not None
    return detail


def route_fake_to_human(
    repository: RecordingRepository,
    *,
    verdict: VerifierVerdict = VerifierVerdict.VERIFIED,
) -> None:
    assert repository.detail is not None
    report = make_verifier_report(verdict=verdict)
    repository.detail = repository.detail.model_copy(
        update={
            "case": repository.detail.case.model_copy(
                update={
                    "status": ReviewStatus.AWAITING_HUMAN_REVIEW,
                    "version": 3,
                    "deterministic_run_id": report.verifier_run_id,
                    "composite_verdict": verdict,
                }
            ),
            "verifier_reports": (report,),
        }
    )


async def test_create_persists_canonical_snapshot_before_requested_workflow() -> None:
    repository = RecordingRepository()
    service, diagnoser, workflow, _ = make_service(repository)

    detail = await create_case(service, repository)

    command = repository.create_commands[0]
    assert diagnoser.calls == 1
    assert command.diagnoser is DiagnoserKind.RULES
    assert command.verification_mode is VerificationMode.DETERMINISTIC
    assert command.snapshot.view_json == canonical_json(command.snapshot.trace_view())
    assert command.snapshot.input_sha256 == canonical_sha256(command.snapshot.trace_view())
    assert command.snapshot.catalog_version == "evidence-catalog-v1"
    assert command.initial_revision.revision_number == 0
    assert command.initial_revision.report_sha256 == canonical_sha256(
        command.initial_revision.report
    )
    assert workflow.runs == [detail.case.case_id]


async def test_create_replays_identical_request_without_running_workflow_twice() -> None:
    repository = RecordingRepository()
    service, diagnoser, workflow, _ = make_service(repository)
    trace = load_trace("policy_violation-01")

    first = await service.create(
        trace,
        diagnoser=DiagnoserKind.RULES,
        verification_mode=VerificationMode.DETERMINISTIC,
        idempotency_key="same",
    )
    replay = await service.create(
        trace,
        diagnoser=DiagnoserKind.RULES,
        verification_mode=VerificationMode.DETERMINISTIC,
        idempotency_key="same",
    )

    assert replay == first
    assert diagnoser.calls == 1
    assert workflow.runs == [first.case.case_id]


async def test_create_restart_preflight_replays_without_diagnosis_or_id_allocation() -> None:
    repository = RecordingRepository()
    service, _, _, _ = make_service(repository)
    first = await create_case(service, repository, key="restart")

    def forbidden_id() -> str:
        raise AssertionError("id factory must not run during durable preflight")

    restarted = ReviewService(
        diagnosis_service=DiagnosisService(
            {
                DiagnoserKind.RULES: FailingDiagnoser(),
                DiagnoserKind.DEEPSEEK: FailingDiagnoser(),
            }
        ),
        repository=repository,
        workflow=RecordingWorkflow(repository),
        deterministic_verifier=RecordingVerifier(),
        id_factory=forbidden_id,
        clock=lambda: NOW,
    )

    replay = await restarted.create(
        load_trace("policy_violation-01"),
        diagnoser=DiagnoserKind.RULES,
        verification_mode=VerificationMode.DETERMINISTIC,
        idempotency_key="restart",
    )
    assert replay == first


@pytest.mark.parametrize(
    ("run_id", "diagnoser", "mode"),
    [
        ("policy_violation-02", DiagnoserKind.RULES, VerificationMode.DETERMINISTIC),
        ("policy_violation-01", DiagnoserKind.DEEPSEEK, VerificationMode.DETERMINISTIC),
        ("policy_violation-01", DiagnoserKind.RULES, VerificationMode.HYBRID),
    ],
)
async def test_create_changed_fingerprint_conflicts_before_any_diagnoser(
    run_id: str, diagnoser: DiagnoserKind, mode: VerificationMode
) -> None:
    repository = RecordingRepository()
    service, _, _, _ = make_service(repository)
    await create_case(service, repository, key="preflight-conflict")
    restarted = ReviewService(
        diagnosis_service=DiagnosisService(
            {
                DiagnoserKind.RULES: FailingDiagnoser(),
                DiagnoserKind.DEEPSEEK: FailingDiagnoser(),
            }
        ),
        repository=repository,
        workflow=RecordingWorkflow(repository),
        deterministic_verifier=RecordingVerifier(),
        id_factory=id_factory(),
        clock=lambda: NOW,
    )

    with pytest.raises(ReviewConflictError, match="idempotency"):
        await restarted.create(
            load_trace(run_id),
            diagnoser=diagnoser,
            verification_mode=mode,
            idempotency_key="preflight-conflict",
        )


async def test_create_rejects_same_key_with_changed_trace_or_mode() -> None:
    repository = RecordingRepository()
    service, _, _, _ = make_service(repository)
    await create_case(service, repository, key="collision")

    with pytest.raises(ReviewConflictError, match="idempotency"):
        await service.create(
            load_trace("policy_violation-02"),
            diagnoser=DiagnoserKind.RULES,
            verification_mode=VerificationMode.DETERMINISTIC,
            idempotency_key="collision",
        )
    with pytest.raises(ReviewConflictError, match="idempotency"):
        await service.create(
            load_trace("policy_violation-01"),
            diagnoser=DiagnoserKind.RULES,
            verification_mode=VerificationMode.HYBRID,
            idempotency_key="collision",
        )


def test_create_has_no_service_owned_diagnoser_or_mode_defaults() -> None:
    repository = RecordingRepository()
    service, _, _, _ = make_service(repository)

    with pytest.raises(TypeError):
        service.create(load_trace("policy_violation-01"), idempotency_key="missing")  # type: ignore[call-arg]


async def test_get_and_resume_delegate_to_repository_and_workflow() -> None:
    repository = RecordingRepository()
    service, _, workflow, _ = make_service(repository)
    created = await create_case(service, repository)

    assert await service.get(created.case.case_id) == created
    assert await service.resume(created.case.case_id) == created
    assert workflow.resumes == [created.case.case_id]


@pytest.mark.parametrize(
    "verdict", (VerifierVerdict.NEEDS_EVIDENCE, VerifierVerdict.REVIEW_REQUIRED)
)
async def test_confirm_nonverified_requires_nonempty_override_reason(
    verdict: VerifierVerdict,
) -> None:
    repository = RecordingRepository()
    service, _, _, _ = make_service(repository)
    await create_case(service, repository)
    route_fake_to_human(repository, verdict=verdict)

    draft = HumanDecisionDraft(
        action=DecisionAction.CONFIRM,
        expected_version=3,
        reviewer_label="reviewer-a",
        reason="   ",
    )
    with pytest.raises(ReviewConflictError, match="override reason"):
        await service.decide("id-0", draft, idempotency_key="decision")
    assert repository.decision_commands == []


async def test_confirm_verified_and_reject_create_terminal_decisions() -> None:
    repository = RecordingRepository()
    service, _, _, _ = make_service(repository)
    created = await create_case(service, repository)
    route_fake_to_human(repository)

    confirmed = await service.decide(
        created.case.case_id,
        HumanDecisionDraft(
            action=DecisionAction.CONFIRM,
            expected_version=3,
            reviewer_label="reviewer-a",
        ),
        idempotency_key="confirm-key",
    )
    assert confirmed.case.status is ReviewStatus.CONFIRMED
    assert repository.decision_commands[-1].correction_revision is None

    second_repository = RecordingRepository()
    second_service, _, _, _ = make_service(second_repository)
    second = await create_case(second_service, second_repository)
    route_fake_to_human(second_repository)
    rejected = await second_service.decide(
        second.case.case_id,
        HumanDecisionDraft(
            action=DecisionAction.REJECT,
            expected_version=3,
            reviewer_label="reviewer-b",
            reason="Unsafe diagnosis",
        ),
        idempotency_key="reject-key",
    )
    assert rejected.case.status is ReviewStatus.REJECTED
    assert rejected.decision is not None and rejected.decision.reason == "Unsafe diagnosis"


def correction_draft() -> DiagnosisCorrectionDraft:
    return DiagnosisCorrectionDraft(
        status=DiagnosisStatus.DIAGNOSED,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-005",),
        causal_chain=(
            CorrectionClaim(
                stage=ClaimStage.CAUSE,
                statement="Approval was missing.",
                selectors=(
                    EvidenceSelector(
                        span_id="span-005",
                        field_path="attributes.tool.error.type",
                    ),
                ),
            ),
        ),
        confidence=0.95,
    )


async def test_correct_rebuilds_server_owned_evidence_and_verifies_deterministically() -> None:
    repository = RecordingRepository()
    service, _, _, verifier = make_service(repository)
    created = await create_case(service, repository)
    route_fake_to_human(repository)

    corrected = await service.decide(
        created.case.case_id,
        HumanDecisionDraft(
            action=DecisionAction.CORRECT,
            expected_version=3,
            reviewer_label="reviewer-a",
            reason="Use the policy evidence",
            correction=correction_draft(),
        ),
        idempotency_key="correct-key",
    )

    command = repository.decision_commands[-1]
    revision = command.correction_revision
    assert revision is not None
    assert revision.report.trace_id == created.case.trace_id
    assert revision.report.run_id == created.case.run_id
    assert revision.report.diagnoser is DiagnoserKind.RULES
    assert revision.provenance.diagnoser_version == "human-correction-v1"
    assert revision.origin.value == "human_correction"
    resolved = revision.report.evidence[0]
    assert resolved.observed_value == "RefundRejected"
    assert resolved.value_sha256 == canonical_sha256("RefundRejected")
    assert verifier.inputs[0].report == revision.report
    assert corrected.case.status is ReviewStatus.CORRECTED


async def test_correction_and_idempotency_are_atomic_with_sqlite(tmp_path: Path) -> None:
    repository = SQLiteReviewRepository(tmp_path / "reviews.sqlite3")
    await repository.initialize()
    diagnoser = SelectorDiagnoser(
        EvidenceSelector(span_id="span-005", field_path="attributes.tool.error.type")
    )
    identifiers = iter(
        (
            "case-review-1",
            "revision-service-0",
            "event-service-create",
            "decision-service-1",
            "revision-service-1",
            "event-service-correct",
            "decision-replay",
            "revision-replay",
            "event-replay",
            "decision-changed",
            "revision-changed",
            "event-changed",
        )
    )
    verifier = RecordingVerifier()
    service = ReviewService(
        diagnosis_service=DiagnosisService({DiagnoserKind.RULES: diagnoser}),
        repository=repository,
        workflow=NoopWorkflow(),
        deterministic_verifier=verifier,
        id_factory=lambda: next(identifiers),
        clock=lambda: NOW,
    )
    created = await service.create(
        load_trace("policy_violation-01"),
        diagnoser=DiagnoserKind.RULES,
        verification_mode=VerificationMode.DETERMINISTIC,
        idempotency_key="create-sqlite",
    )
    assert created.case.case_id == "case-review-1"

    def forbidden_id() -> str:
        raise AssertionError("durable replay must not allocate an ID")

    reopened = SQLiteReviewRepository(tmp_path / "reviews.sqlite3")
    await reopened.initialize()
    restarted = ReviewService(
        diagnosis_service=DiagnosisService(
            {
                DiagnoserKind.RULES: FailingDiagnoser(),
                DiagnoserKind.DEEPSEEK: FailingDiagnoser(),
            }
        ),
        repository=reopened,
        workflow=NoopWorkflow(),
        deterministic_verifier=RecordingVerifier(),
        id_factory=forbidden_id,
        clock=lambda: NOW,
    )
    durable_replay = await restarted.create(
        load_trace("policy_violation-01"),
        diagnoser=DiagnoserKind.RULES,
        verification_mode=VerificationMode.DETERMINISTIC,
        idempotency_key="create-sqlite",
    )
    assert durable_replay == created
    with pytest.raises(ReviewConflictError, match="idempotency"):
        await restarted.create(
            load_trace("policy_violation-01"),
            diagnoser=DiagnoserKind.DEEPSEEK,
            verification_mode=VerificationMode.DETERMINISTIC,
            idempotency_key="create-sqlite",
        )

    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(_verifier_command())
    await repository.route_to_human(_route_command())
    draft = HumanDecisionDraft(
        action=DecisionAction.CORRECT,
        expected_version=3,
        reviewer_label="reviewer-a",
        correction=correction_draft(),
    )

    corrected = await service.decide(
        created.case.case_id, draft, idempotency_key="decision-sqlite"
    )
    replay = await service.decide(
        created.case.case_id, draft, idempotency_key="decision-sqlite"
    )
    assert replay == corrected
    assert len(verifier.inputs) == 1
    assert corrected.case.status is ReviewStatus.CORRECTED
    assert len(corrected.revisions) == 2

    changed = draft.model_copy(update={"reviewer_label": "reviewer-b"})
    with pytest.raises(ReviewConflictError, match="idempotency"):
        await service.decide(
            created.case.case_id, changed, idempotency_key="decision-sqlite"
        )
    assert len(verifier.inputs) == 1
    assert await repository.get_detail(created.case.case_id) == corrected


async def test_correction_verifier_error_is_sanitized_without_persistence() -> None:
    class ExplodingVerifier(RecordingVerifier):
        async def verify(self, input_: Any) -> Any:
            self.inputs.append(input_)
            raise RuntimeError("private verifier details")

    repository = RecordingRepository()
    service, _, _, verifier = make_service(repository, verifier=ExplodingVerifier())
    created = await create_case(service, repository)
    route_fake_to_human(repository)
    before = repository.detail

    with pytest.raises(ReviewConflictError) as captured:
        await service.decide(
            created.case.case_id,
            HumanDecisionDraft(
                action=DecisionAction.CORRECT,
                expected_version=3,
                reviewer_label="reviewer-a",
                correction=correction_draft(),
            ),
            idempotency_key="verifier-error",
        )
    assert str(captured.value) == "human correction verification failed"
    assert "private" not in str(captured.value)
    assert len(verifier.inputs) == 1
    assert repository.detail == before
    assert repository.decision_commands == []


@pytest.mark.parametrize(
    "forged_field",
    ("evidence_id", "observed_value", "value_sha256", "description", "provenance"),
)
def test_correction_rejects_client_supplied_evidence_fields(forged_field: str) -> None:
    payload = HumanDecisionDraft(
        action=DecisionAction.CORRECT,
        expected_version=3,
        reviewer_label="reviewer-a",
        correction=correction_draft(),
    ).model_dump(mode="json")
    selector = payload["correction"]["causal_chain"][0]["selectors"][0]  # type: ignore[index]
    selector[forged_field] = "forged"  # type: ignore[index]

    with pytest.raises(ValidationError, match=forged_field):
        HumanDecisionDraft.model_validate(payload)


@pytest.mark.parametrize(
    ("critical_span", "selector"),
    [
        ("missing-span", "span-005"),
        ("span-005", "missing-span"),
    ],
)
async def test_invalid_correction_is_typed_and_changes_no_history(
    critical_span: str, selector: str
) -> None:
    repository = RecordingRepository()
    service, _, _, verifier = make_service(repository)
    created = await create_case(service, repository)
    route_fake_to_human(repository)
    before = repository.detail
    draft = correction_draft().model_copy(
        update={
            "critical_span_ids": (critical_span,),
            "causal_chain": (
                CorrectionClaim(
                    stage=ClaimStage.CAUSE,
                    statement="Bad selector",
                    selectors=(
                        EvidenceSelector(
                            span_id=selector,
                            field_path="attributes.tool.error.type",
                        ),
                    ),
                ),
            ),
        }
    )

    with pytest.raises(ReviewConflictError, match="correction"):
        await service.decide(
            created.case.case_id,
            HumanDecisionDraft(
                action=DecisionAction.CORRECT,
                expected_version=3,
                reviewer_label="reviewer-a",
                correction=draft,
            ),
            idempotency_key="invalid",
        )

    assert repository.detail == before
    assert repository.decision_commands == []
    if selector == "missing-span":
        assert verifier.inputs == []


async def test_nonverified_correction_and_repository_failure_are_atomic() -> None:
    repository = RecordingRepository()
    verifier = RecordingVerifier(VerifierVerdict.NEEDS_EVIDENCE)
    service, _, _, _ = make_service(repository, verifier=verifier)
    created = await create_case(service, repository)
    route_fake_to_human(repository)
    before = repository.detail
    decision = HumanDecisionDraft(
        action=DecisionAction.CORRECT,
        expected_version=3,
        reviewer_label="reviewer-a",
        correction=correction_draft(),
    )

    with pytest.raises(ReviewConflictError, match="deterministic verification"):
        await service.decide(created.case.case_id, decision, idempotency_key="unverified")
    assert repository.detail == before
    assert repository.decision_commands == []

    verifier.verdict = VerifierVerdict.VERIFIED
    repository.fail_decision = True
    with pytest.raises(ReviewConflictError, match="atomic failure"):
        await service.decide(created.case.case_id, decision, idempotency_key="failed-write")
    assert repository.detail == before


async def test_decision_rejects_stale_and_terminal_then_replays_same_key() -> None:
    repository = RecordingRepository()
    service, _, _, _ = make_service(repository)
    created = await create_case(service, repository)
    route_fake_to_human(repository)
    stale = HumanDecisionDraft(
        action=DecisionAction.CONFIRM,
        expected_version=2,
        reviewer_label="reviewer-a",
    )
    with pytest.raises(ReviewConflictError, match="version"):
        await service.decide(created.case.case_id, stale, idempotency_key="stale")

    decision = stale.model_copy(update={"expected_version": 3})
    first = await service.decide(created.case.case_id, decision, idempotency_key="same")
    replay = await service.decide(created.case.case_id, decision, idempotency_key="same")
    assert replay == first

    changed = decision.model_copy(update={"reviewer_label": "reviewer-b"})
    with pytest.raises(ReviewConflictError, match="idempotency"):
        await service.decide(created.case.case_id, changed, idempotency_key="same")
    with pytest.raises(ReviewConflictError, match="terminal"):
        await service.decide(created.case.case_id, changed, idempotency_key="new")


def test_clock_must_return_aware_utc() -> None:
    repository = RecordingRepository()
    diagnoser = SelectorDiagnoser(
        EvidenceSelector(
            span_id="span-005",
            field_path="attributes.tool.error.type",
        )
    )
    with pytest.raises(ValueError, match="aware UTC"):
        ReviewService(
            diagnosis_service=DiagnosisService({DiagnoserKind.RULES: diagnoser}),
            repository=repository,
            workflow=RecordingWorkflow(repository),
            deterministic_verifier=RecordingVerifier(),
            id_factory=id_factory(),
            clock=lambda: datetime(2026, 7, 17),
        )
    with pytest.raises(ValueError, match="aware UTC"):
        ReviewService(
            diagnosis_service=DiagnosisService({DiagnoserKind.RULES: diagnoser}),
            repository=repository,
            workflow=RecordingWorkflow(repository),
            deterministic_verifier=RecordingVerifier(),
            id_factory=id_factory(),
            clock=lambda: datetime(
                2026, 7, 17, tzinfo=timezone(timedelta(hours=8))
            ),
        )


def test_human_correction_dependency_cannot_be_a_semantic_verifier() -> None:
    repository = RecordingRepository()
    diagnoser = SelectorDiagnoser(
        EvidenceSelector(span_id="span-005", field_path="attributes.tool.error.type")
    )
    verifier = RecordingVerifier()
    verifier.kind = VerifierKind.SEMANTIC

    with pytest.raises(ValueError, match="deterministic verifier"):
        ReviewService(
            diagnosis_service=DiagnosisService({DiagnoserKind.RULES: diagnoser}),
            repository=repository,
            workflow=RecordingWorkflow(repository),
            deterministic_verifier=verifier,
            id_factory=id_factory(),
            clock=lambda: NOW,
        )
