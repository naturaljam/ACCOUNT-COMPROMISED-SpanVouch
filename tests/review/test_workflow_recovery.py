import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from afc.diagnosis.errors import (
    ProviderConfigurationError,
    ProviderProtocolError,
    ProviderRequestError,
)
from afc.diagnosis.models import DiagnoserKind
from afc.diagnosis.rule_diagnoser import RuleDiagnoser
from afc.diagnosis.service import DiagnosisService
from afc.invariants.engine import InvariantEngine
from afc.review.errors import ReviewConflictError
from afc.review.models import (
    ReviewStatus,
    VerificationMode,
    VerifierKind,
    VerifierVerdict,
)
from afc.review.service import ReviewService
from afc.review.sqlite_repository import SQLiteReviewRepository
from afc.review.workflow import ReviewWorkflow, ReviewWorkflowProviderError
from tests.review.test_workflow import (
    FakeReviser,
    FakeVerifier,
    MutableClock,
    SequenceIds,
    _create_case,
    _deepseek_report,
    _events,
    _report,
    _workflow,
)


class BlockingSemanticVerifier:
    kind = VerifierKind.SEMANTIC
    version_fingerprint = "blocking-semantic-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def verify(self, input_):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return _report(
            VerifierKind.SEMANTIC,
            VerifierVerdict.VERIFIED,
            revision_number=input_.revision_number,
            suffix=f"blocked-semantic-{self.calls}",
        ).model_copy(update={"report_sha256": input_.report_sha256})


class BlockingReviser(FakeReviser):
    def __init__(self) -> None:
        super().__init__(supported=(DiagnoserKind.DEEPSEEK,))
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def revise(self, runtime_bundle, evidence_gaps):  # type: ignore[no-untyped-def]
        self.calls.append((runtime_bundle, evidence_gaps))
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return _deepseek_report()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _resume_service(
    repository: SQLiteReviewRepository,
    workflow: ReviewWorkflow,
    deterministic: FakeVerifier,
    ids: SequenceIds,
) -> ReviewService:
    engine = InvariantEngine(())
    diagnosis_service = DiagnosisService(
        {DiagnoserKind.RULES: RuleDiagnoser(engine)}
    )
    return ReviewService(
        diagnosis_service=diagnosis_service,
        repository=repository,
        workflow=workflow,
        deterministic_verifier=deterministic,
        id_factory=ids,
        clock=_utc_now,
    )


class CrashAfterAppendRepository(SQLiteReviewRepository):
    def __init__(
        self,
        database: Path,
        *,
        crash_verifier_once: bool = False,
        crash_revision_once: bool = False,
    ) -> None:
        super().__init__(database)
        self.crash_verifier_once = crash_verifier_once
        self.crash_revision_once = crash_revision_once

    async def append_verifier_run(self, command):  # type: ignore[no-untyped-def]
        result = await super().append_verifier_run(command)
        if self.crash_verifier_once:
            self.crash_verifier_once = False
            raise RuntimeError("crash after verifier commit")
        return result

    async def append_revision(self, command):  # type: ignore[no-untyped-def]
        result = await super().append_revision(command)
        if self.crash_revision_once:
            self.crash_revision_once = False
            raise RuntimeError("crash after revision commit")
        return result


async def test_crash_after_verifying_commit_requires_expired_lease_before_resume(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verify-crash.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.RULES,
    )
    clock = MutableClock()
    verifier = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            RuntimeError("process crashed during provider call"),
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix="recovered",
            ),
        ],
    )
    workflow = _workflow(repository, verifier, clock=clock)

    with pytest.raises(RuntimeError, match="process crashed"):
        await workflow.run("case-review-1")
    crashed = await repository.get_detail("case-review-1")
    assert crashed.case.status is ReviewStatus.VERIFYING
    assert not crashed.verifier_reports

    clock.now += timedelta(seconds=10)
    with pytest.raises(ReviewConflictError, match="lease is still active"):
        await workflow.resume("case-review-1")
    assert len(verifier.inputs) == 1

    clock.now += timedelta(seconds=21)
    recovered = await workflow.resume("case-review-1")
    assert recovered.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert len(verifier.inputs) == 2


async def test_crash_after_revising_commit_recovers_once_after_expiry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "revision-crash.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.DEEPSEEK,
    )
    clock = MutableClock()
    verifier = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.NEEDS_EVIDENCE,
                revision_number=0,
                suffix="needs-revision",
            ),
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=1,
                suffix="revised",
            ),
        ],
    )
    reviser = FakeReviser(
        supported=(DiagnoserKind.DEEPSEEK,),
        outcomes=[RuntimeError("process crashed during revision"), _deepseek_report()],
    )
    workflow = _workflow(repository, verifier, reviser=reviser, clock=clock)

    with pytest.raises(RuntimeError, match="process crashed"):
        await workflow.run("case-review-1")
    assert (await repository.get_detail("case-review-1")).case.status is ReviewStatus.REVISING

    clock.now += timedelta(seconds=31)
    recovered = await workflow.resume("case-review-1")
    assert recovered.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert recovered.case.current_revision_number == 1
    assert len(reviser.calls) == 2


async def test_pending_resume_succeeds_but_human_and_terminal_resume_do_not_call_provider(
    tmp_path: Path,
) -> None:
    repository = SQLiteReviewRepository(tmp_path / "resume.sqlite3")
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.RULES,
    )
    verifier = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix="pending-resume",
            )
        ],
    )
    workflow = _workflow(repository, verifier)

    detail = await workflow.resume("case-review-1")
    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    with pytest.raises(ReviewConflictError, match="cannot be resumed"):
        await workflow.resume("case-review-1")
    assert len(verifier.inputs) == 1

    with sqlite3.connect(tmp_path / "resume.sqlite3") as connection:
        connection.execute(
            "UPDATE review_cases SET status = 'confirmed', terminal_decision_id = 'manual' "
            "WHERE case_id = 'case-review-1'"
        )
        connection.execute(
            "INSERT INTO human_decisions(decision_id, case_id, action, reviewer_label, "
            "expected_version, created_at) VALUES "
            "('manual', 'case-review-1', 'confirm', 'test', 3, ?)",
            (detail.case.updated_at.isoformat(),),
        )
        connection.commit()
    with pytest.raises(ReviewConflictError, match="cannot be resumed"):
        await workflow.resume("case-review-1")
    assert len(verifier.inputs) == 1


async def test_restart_after_committed_verifier_effect_does_not_repeat_provider(
    tmp_path: Path,
) -> None:
    database = tmp_path / "restart-verifier.sqlite3"
    crashing_repository = CrashAfterAppendRepository(database, crash_verifier_once=True)
    await crashing_repository.initialize()
    await _create_case(
        crashing_repository,
        mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.RULES,
    )
    verifier = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix="committed",
            )
        ],
    )
    ids = SequenceIds()

    with pytest.raises(RuntimeError, match="after verifier commit"):
        await _workflow(crashing_repository, verifier, id_factory=ids).run("case-review-1")
    restarted_repository = SQLiteReviewRepository(database)
    restarted = await _workflow(restarted_repository, verifier, id_factory=ids).resume(
        "case-review-1"
    )

    assert restarted.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert len(verifier.inputs) == 1
    assert len(restarted.verifier_reports) == 1
    assert [event for event, _ in _events(database)].count("verification_completed") == 1


async def test_restart_after_committed_revision_does_not_repeat_reviser(
    tmp_path: Path,
) -> None:
    database = tmp_path / "restart-revision.sqlite3"
    repository = CrashAfterAppendRepository(database, crash_revision_once=True)
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.DEEPSEEK,
    )
    verifier = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.NEEDS_EVIDENCE,
                revision_number=0,
                suffix="revision-needed",
            ),
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=1,
                suffix="revision-recovered",
            ),
        ],
    )
    reviser = FakeReviser(supported=(DiagnoserKind.DEEPSEEK,), outcomes=[_deepseek_report()])
    ids = SequenceIds()

    with pytest.raises(RuntimeError, match="after revision commit"):
        await _workflow(repository, verifier, reviser=reviser, id_factory=ids).run("case-review-1")
    restarted_repository = SQLiteReviewRepository(database)
    detail = await _workflow(
        restarted_repository, verifier, reviser=reviser, id_factory=ids
    ).resume("case-review-1")

    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert len(reviser.calls) == 1
    assert len(detail.revisions) == 2
    assert [event for event, _ in _events(database)].count("revision_completed") == 1


@pytest.mark.parametrize(
    ("error", "expected_code", "retryable"),
    [
        (ProviderConfigurationError("TOP-SECRET"), "provider_not_configured", False),
        (ProviderProtocolError("TOP-SECRET"), "provider_protocol_error", False),
        (ProviderRequestError("transport_error", retryable=True), "transport_error", True),
        (ProviderRequestError("TOP-SECRET", retryable=False), "provider_request_error", False),
    ],
)
async def test_semantic_provider_failure_is_persisted_before_typed_error(
    tmp_path: Path,
    error: Exception,
    expected_code: str,
    retryable: bool,
) -> None:
    database = tmp_path / f"semantic-{expected_code}.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(repository, mode=VerificationMode.HYBRID, diagnoser=DiagnoserKind.DEEPSEEK)
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix=f"det-{expected_code}",
            )
        ],
    )
    semantic = FakeVerifier(VerifierKind.SEMANTIC, [error])

    with pytest.raises(ReviewWorkflowProviderError) as raised:
        await _workflow(repository, deterministic, semantic=semantic).run("case-review-1")

    assert raised.value.case_id == "case-review-1"
    assert raised.value.code == expected_code
    assert raised.value.retryable is retryable
    detail = await repository.get_detail("case-review-1")
    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    semantic_report = detail.verifier_reports[-1]
    assert semantic_report.operational_error is not None
    assert semantic_report.operational_error.code == expected_code
    assert "TOP-SECRET" not in semantic_report.model_dump_json()
    assert [event for event, _ in _events(database)][-2:] == [
        "provider_failed",
        "awaiting_human_review",
    ]


async def test_missing_semantic_verifier_persists_configuration_failure(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic-missing.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository, mode=VerificationMode.HYBRID, diagnoser=DiagnoserKind.DEEPSEEK
    )
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix="det-missing-semantic",
            )
        ],
    )

    with pytest.raises(ReviewWorkflowProviderError) as raised:
        await _workflow(repository, deterministic, semantic=None).run("case-review-1")

    assert raised.value.case_id == "case-review-1"
    assert raised.value.code == "provider_not_configured"
    assert raised.value.retryable is False
    detail = await repository.get_detail("case-review-1")
    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert len(detail.verifier_reports) == 2
    semantic_report = detail.verifier_reports[-1]
    assert semantic_report.verifier_kind is VerifierKind.SEMANTIC
    assert semantic_report.operational_error is not None
    assert semantic_report.operational_error.code == "provider_not_configured"
    assert [event for event, _ in _events(database)][-2:] == [
        "provider_failed",
        "awaiting_human_review",
    ]


@pytest.mark.parametrize(
    ("provider_error", "expected_code", "expected_retryable"),
    (
        (
            ProviderRequestError("transport_error", retryable=True),
            "transport_error",
            True,
        ),
        (
            ProviderProtocolError("TOP-SECRET"),
            "provider_protocol_error",
            False,
        ),
    ),
)
async def test_revision_provider_failure_preserves_classification_without_fabricated_effects(
    tmp_path: Path,
    provider_error: Exception,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    database = tmp_path / f"revision-provider-{expected_code}.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.DEEPSEEK,
    )
    verifier = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.NEEDS_EVIDENCE,
                revision_number=0,
                suffix="revision-provider",
            )
        ],
    )
    reviser = FakeReviser(
        supported=(DiagnoserKind.DEEPSEEK,),
        outcomes=[provider_error],
    )

    with pytest.raises(ReviewWorkflowProviderError) as raised:
        await _workflow(repository, verifier, reviser=reviser).run("case-review-1")

    assert raised.value.code == expected_code
    assert raised.value.retryable is expected_retryable
    detail = await repository.get_detail("case-review-1")
    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert detail.case.composite_verdict is VerifierVerdict.REVIEW_REQUIRED
    assert len(detail.revisions) == 1
    assert len(detail.verifier_reports) == 1
    with sqlite3.connect(database) as connection:
        event = connection.execute(
            "SELECT event_type, metadata_json FROM workflow_events "
            "ORDER BY event_sequence DESC LIMIT 1"
        ).fetchone()
    assert event is not None
    assert event[0] == "revision_provider_failed"
    assert f'"code":"{expected_code}"' in event[1]
    assert f'"retryable":{str(expected_retryable).lower()}' in event[1]
    assert "TOP-SECRET" not in event[1]


async def test_semantic_heartbeat_blocks_concurrent_consented_resume_past_original_expiry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic-heartbeat.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.HYBRID,
        diagnoser=DiagnoserKind.DEEPSEEK,
    )
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix="heartbeat-semantic",
            )
        ],
    )
    semantic = BlockingSemanticVerifier()
    ids = SequenceIds()
    duration = timedelta(milliseconds=150)
    first_workflow = _workflow(
        repository,
        deterministic,
        semantic=semantic,
        clock=_utc_now,
        id_factory=ids,
        lease_owner="semantic-worker-a",
        lease_duration=duration,
    )
    second_workflow = _workflow(
        repository,
        deterministic,
        semantic=semantic,
        clock=_utc_now,
        id_factory=ids,
        lease_owner="semantic-worker-b",
        lease_duration=duration,
    )
    service = _resume_service(repository, second_workflow, deterministic, ids)
    first = asyncio.create_task(first_workflow.run("case-review-1"))
    await asyncio.wait_for(semantic.entered.wait(), timeout=1.0)
    await asyncio.sleep(0.22)

    try:
        with pytest.raises(ReviewConflictError, match="lease is still active"):
            await asyncio.wait_for(
                service.resume("case-review-1", allow_live_api=True),
                timeout=0.5,
            )
        assert semantic.calls == 1
    finally:
        semantic.release.set()
    completed = await asyncio.wait_for(first, timeout=1.0)
    assert completed.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert semantic.calls == 1


async def test_revision_heartbeat_blocks_concurrent_consented_resume_past_original_expiry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "revision-heartbeat.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.DEEPSEEK,
    )
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.NEEDS_EVIDENCE,
                revision_number=0,
                suffix="heartbeat-revision",
            ),
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=1,
                suffix="heartbeat-revision-final",
            ),
        ],
    )
    reviser = BlockingReviser()
    ids = SequenceIds()
    duration = timedelta(milliseconds=150)
    first_workflow = _workflow(
        repository,
        deterministic,
        reviser=reviser,
        clock=_utc_now,
        id_factory=ids,
        lease_owner="revision-worker-a",
        lease_duration=duration,
    )
    second_workflow = _workflow(
        repository,
        deterministic,
        reviser=reviser,
        clock=_utc_now,
        id_factory=ids,
        lease_owner="revision-worker-b",
        lease_duration=duration,
    )
    service = _resume_service(repository, second_workflow, deterministic, ids)
    first = asyncio.create_task(first_workflow.run("case-review-1"))
    await asyncio.wait_for(reviser.entered.wait(), timeout=1.0)
    await asyncio.sleep(0.22)

    try:
        with pytest.raises(ReviewConflictError, match="lease is still active"):
            await asyncio.wait_for(
                service.resume("case-review-1", allow_live_api=True),
                timeout=0.5,
            )
        assert len(reviser.calls) == 1
    finally:
        reviser.release.set()
    completed = await asyncio.wait_for(first, timeout=1.0)
    assert completed.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert completed.case.current_revision_number == 1
    assert len(reviser.calls) == 1


async def test_lease_ownership_loss_cancels_provider_then_allows_stale_recovery(
    tmp_path: Path,
) -> None:
    database = tmp_path / "heartbeat-owner-loss.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.HYBRID,
        diagnoser=DiagnoserKind.DEEPSEEK,
    )
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix="owner-loss",
            )
        ],
    )
    blocked = BlockingSemanticVerifier()
    ids = SequenceIds()
    duration = timedelta(milliseconds=150)
    first_workflow = _workflow(
        repository,
        deterministic,
        semantic=blocked,
        clock=_utc_now,
        id_factory=ids,
        lease_owner="owner-loss-worker",
        lease_duration=duration,
    )
    first = asyncio.create_task(first_workflow.run("case-review-1"))
    await asyncio.wait_for(blocked.entered.wait(), timeout=1.0)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE review_cases SET lease_owner = ? WHERE case_id = ?",
            ("replacement-owner", "case-review-1"),
        )
        connection.commit()

    with pytest.raises(ReviewConflictError, match="lease"):
        await asyncio.wait_for(first, timeout=0.5)
    await asyncio.wait_for(blocked.cancelled.wait(), timeout=0.5)
    detail = await repository.get_detail("case-review-1")
    assert detail.case.status is ReviewStatus.VERIFYING
    assert len(detail.verifier_reports) == 1

    with sqlite3.connect(database) as connection:
        stored_expiry = connection.execute(
            "SELECT lease_expires_at FROM review_cases WHERE case_id = ?",
            ("case-review-1",),
        ).fetchone()
    assert stored_expiry is not None and stored_expiry[0] is not None
    expires_at = datetime.fromisoformat(stored_expiry[0])
    await asyncio.sleep(max(0.0, (expires_at - _utc_now()).total_seconds()) + 0.05)

    recovered_semantic = FakeVerifier(
        VerifierKind.SEMANTIC,
        [
            _report(
                VerifierKind.SEMANTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix="owner-loss-recovered",
            )
        ],
    )
    recovery_workflow = _workflow(
        repository,
        deterministic,
        semantic=recovered_semantic,
        clock=_utc_now,
        id_factory=ids,
        lease_owner="recovery-worker",
        lease_duration=duration,
    )
    recovered = await _resume_service(
        repository, recovery_workflow, deterministic, ids
    ).resume("case-review-1", allow_live_api=True)
    assert recovered.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert len(recovered_semantic.inputs) == 1
