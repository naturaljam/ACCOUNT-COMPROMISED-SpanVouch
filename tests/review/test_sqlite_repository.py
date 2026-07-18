import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.contracts.verification import (
    VerificationMode,
    VerifierKind,
    VerifierVerdict,
)
from spanvouch.review import commands as review_commands
from spanvouch.review.commands import (
    AppendDiagnosisRevision,
    AppendVerifierRun,
    ApplyHumanDecision,
    ClaimReviewWork,
    CreateReviewCase,
    RouteRevisionFailureToHuman,
    RouteToHumanReview,
    WorkflowEventType,
)
from spanvouch.review.errors import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewPersistenceError,
)
from spanvouch.review.models import (
    DecisionAction,
    HumanReviewDecision,
    ReviewStatus,
    RevisionOrigin,
    canonical_json,
    canonical_sha256,
)
from spanvouch.review.schema import connect_database
from spanvouch.review.sqlite_repository import SQLiteReviewRepository
from tests.review.factories import (
    NOW,
    make_correction_draft,
    make_review_snapshot,
    make_revision,
    make_verifier_report,
)


@pytest.mark.parametrize(
    "database",
    (":memory:", "file:review-memory?mode=memory&cache=shared", "file:review.sqlite3"),
)
def test_repository_rejects_unsupported_sqlite_memory_and_uri_paths(
    database: str,
) -> None:
    with pytest.raises(ValueError, match="filesystem path"):
        SQLiteReviewRepository(database)


def _create_command(
    *, verification_mode: VerificationMode = VerificationMode.DETERMINISTIC
) -> CreateReviewCase:
    return CreateReviewCase(
        case_id="case-review-1",
        snapshot=make_review_snapshot(),
        initial_revision=make_revision(),
        target_status=ReviewStatus.PENDING_VERIFICATION,
        verification_mode=verification_mode,
        diagnoser=DiagnoserKind.RULES,
        idempotency_scope="review.create",
        idempotency_key="create-key-1",
        request_sha256="a" * 64,
        event_id="event-created-1",
        event_type=WorkflowEventType.CASE_CREATED,
        event_metadata_json=canonical_json({"source": "test"}),
        created_at=NOW,
    )


def _claim_command(
    *,
    expected_version: int = 0,
    prior_status: ReviewStatus = ReviewStatus.PENDING_VERIFICATION,
    target_status: ReviewStatus = ReviewStatus.VERIFYING,
    owner: str = "worker-1",
    event_id: str = "event-verification-started-1",
    event_type: WorkflowEventType = WorkflowEventType.VERIFICATION_STARTED,
    now=NOW,
) -> ClaimReviewWork:
    return ClaimReviewWork(
        case_id="case-review-1",
        expected_version=expected_version,
        prior_status=prior_status,
        target_status=target_status,
        lease_owner=owner,
        lease_expires_at=now + timedelta(seconds=30),
        now=now,
        event_id=event_id,
        event_type=event_type,
        event_metadata_json=canonical_json({"worker": owner}),
        occurred_at=now,
    )


def _verifier_command(
    *,
    verdict: VerifierVerdict = VerifierVerdict.VERIFIED,
    target_status: ReviewStatus = ReviewStatus.VERIFYING,
    event_id: str = "event-verification-completed-1",
) -> AppendVerifierRun:
    report = make_verifier_report(verdict=verdict)
    event_type = (
        WorkflowEventType.REVISION_REQUESTED
        if target_status is ReviewStatus.REVISION_REQUESTED
        else WorkflowEventType.VERIFICATION_COMPLETED
    )
    return AppendVerifierRun(
        case_id="case-review-1",
        expected_version=1,
        prior_status=ReviewStatus.VERIFYING,
        target_status=target_status,
        report=report,
        composite_verdict=verdict,
        event_id=event_id,
        event_type=event_type,
        event_metadata_json=canonical_json({"verdict": verdict.value}),
        occurred_at=report.completed_at,
    )


def _route_command(*, expected_version: int = 2) -> RouteToHumanReview:
    return RouteToHumanReview(
        case_id="case-review-1",
        expected_version=expected_version,
        prior_status=ReviewStatus.VERIFYING,
        target_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
        event_id="event-awaiting-human-1",
        event_type=WorkflowEventType.AWAITING_HUMAN_REVIEW,
        event_metadata_json=canonical_json({"verdict": "verified"}),
        occurred_at=NOW + timedelta(seconds=2),
    )


def _revision_command(
    *, lease_owner: str | None = "worker-1", occurred_at: datetime | None = None
) -> AppendDiagnosisRevision:
    original = make_revision()
    return AppendDiagnosisRevision(
        case_id="case-review-1",
        expected_version=3,
        prior_status=ReviewStatus.REVISING,
        target_status=ReviewStatus.VERIFYING,
        revision=make_revision(
            revision_number=1,
            previous_report_sha256=original.report_sha256,
            triggering_gap_ids=("gap-1",),
        ),
        lease_owner=lease_owner,
        event_id="event-revision-completed-1",
        event_type=WorkflowEventType.REVISION_COMPLETED,
        event_metadata_json=canonical_json({"revision": 1}),
        occurred_at=occurred_at or NOW + timedelta(seconds=3),
    )


def _revision_failure_command(
    *,
    expected_version: int = 3,
    event_id: str = "event-revision-provider-failed-1",
    lease_owner: str | None = "worker-1",
    occurred_at: datetime | None = None,
) -> RouteRevisionFailureToHuman:
    return RouteRevisionFailureToHuman(
        case_id="case-review-1",
        expected_version=expected_version,
        prior_status=ReviewStatus.REVISING,
        target_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
        composite_verdict=VerifierVerdict.REVIEW_REQUIRED,
        lease_owner=lease_owner,
        event_id=event_id,
        event_type=WorkflowEventType.REVISION_PROVIDER_FAILED,
        event_metadata_json=canonical_json({"code": "revision_provider_failed", "retryable": True}),
        occurred_at=occurred_at or NOW + timedelta(seconds=3),
    )


def _semantic_verifier_command(
    *, lease_owner: str | None = "worker-1", occurred_at: datetime | None = None
) -> AppendVerifierRun:
    report = make_verifier_report(kind=VerifierKind.SEMANTIC).model_copy(
        update={
            "started_at": NOW + timedelta(seconds=2),
            "completed_at": NOW + timedelta(seconds=3),
        }
    )
    return AppendVerifierRun(
        case_id="case-review-1",
        expected_version=3,
        prior_status=ReviewStatus.VERIFYING,
        target_status=ReviewStatus.VERIFYING,
        report=report,
        composite_verdict=VerifierVerdict.VERIFIED,
        lease_owner=lease_owner,
        event_id="event-semantic-completed-1",
        event_type=WorkflowEventType.VERIFICATION_COMPLETED,
        event_metadata_json=canonical_json({"verdict": "verified"}),
        occurred_at=occurred_at or report.completed_at,
    )


def _decision_command(
    *,
    decision_id: str,
    reviewer: str,
    idempotency_key: str,
    event_id: str,
) -> ApplyHumanDecision:
    decision = HumanReviewDecision(
        decision_id=decision_id,
        case_id="case-review-1",
        action=DecisionAction.CONFIRM,
        expected_version=3,
        reviewer_label=reviewer,
        created_at=NOW + timedelta(seconds=3),
    )
    return ApplyHumanDecision(
        case_id="case-review-1",
        expected_version=3,
        prior_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
        target_status=ReviewStatus.CONFIRMED,
        decision=decision,
        correction_revision=None,
        idempotency_scope="review.decision:case-review-1",
        idempotency_key=idempotency_key,
        request_sha256=canonical_sha256(
            {"action": "confirm", "reviewer": reviewer, "decision_id": decision_id}
        ),
        event_id=event_id,
        event_type=WorkflowEventType.HUMAN_CONFIRMED,
        event_metadata_json=canonical_json({"reviewer": reviewer}),
        occurred_at=decision.created_at,
    )


def _correction_decision_command() -> ApplyHumanDecision:
    original = make_revision()
    correction = make_revision(
        revision_number=1,
        origin=RevisionOrigin.HUMAN_CORRECTION,
        previous_report_sha256=original.report_sha256,
    )
    verifier_report = make_verifier_report(
        report_sha256=correction.report_sha256
    ).model_copy(
        update={
            "verifier_run_id": "verifier-human-correction-1",
            "revision_number": correction.revision_number,
        }
    )
    decision = HumanReviewDecision(
        decision_id="decision-correction",
        case_id="case-review-1",
        action=DecisionAction.CORRECT,
        expected_version=3,
        reviewer_label="reviewer-a",
        correction=make_correction_draft(),
        resulting_revision_id=correction.revision_id,
        created_at=NOW + timedelta(seconds=3),
    )
    return ApplyHumanDecision(
        case_id="case-review-1",
        expected_version=3,
        prior_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
        target_status=ReviewStatus.CORRECTED,
        decision=decision,
        correction_revision=correction,
        correction_verifier_report=verifier_report,
        idempotency_scope="review.decision:case-review-1",
        idempotency_key="decision-key-correction",
        request_sha256="c" * 64,
        event_id="event-human-correction",
        event_type=WorkflowEventType.HUMAN_CORRECTED,
        event_metadata_json=canonical_json({"reviewer": "reviewer-a"}),
        occurred_at=decision.created_at,
    )


def _revalidate(command: BaseModel, updates: dict[str, object]) -> BaseModel:
    return type(command)(**{**command.model_dump(), **updates})


async def _create_and_verify(repository: SQLiteReviewRepository) -> None:
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(_verifier_command())


async def _create_and_claim_revision(repository: SQLiteReviewRepository) -> None:
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(
        _verifier_command(
            verdict=VerifierVerdict.NEEDS_EVIDENCE,
            target_status=ReviewStatus.REVISION_REQUESTED,
        )
    )
    await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.REVISION_REQUESTED,
            target_status=ReviewStatus.REVISING,
            event_id="event-revision-started-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=2),
        )
    )


async def _create_and_claim_semantic(repository: SQLiteReviewRepository) -> None:
    await repository.create_case(
        _create_command(verification_mode=VerificationMode.HYBRID)
    )
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(_verifier_command())
    await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.VERIFYING,
            target_status=ReviewStatus.VERIFYING,
            event_id="event-semantic-started-1",
            now=NOW + timedelta(seconds=1),
        )
    )


def test_provider_backed_command_models_require_a_lease_owner() -> None:
    provider_commands = (
        _semantic_verifier_command(),
        _revision_command(),
        _revision_failure_command(),
    )

    for command in provider_commands:
        payload = command.model_dump()
        payload.pop("lease_owner")
        with pytest.raises(ValidationError, match="lease_owner"):
            type(command).model_validate(payload)

    assert _verifier_command().lease_owner is None


async def test_review_lease_renewal_is_owner_checked_and_stops_after_transition(
    tmp_path: Path,
) -> None:
    database = tmp_path / "renew-review-lease.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_claim_revision(repository)
    command_type = getattr(review_commands, "RenewReviewLease", None)
    assert command_type is not None, "review lease renewal command is missing"
    work_type = getattr(review_commands, "ReviewLeaseWork", None)
    assert work_type is not None, "review lease work classification is missing"
    command = command_type(
        case_id="case-review-1",
        expected_version=3,
        expected_status=ReviewStatus.REVISING,
        lease_owner="worker-1",
        work=work_type.EVIDENCE_REVISION,
        now=NOW + timedelta(seconds=10),
        lease_expires_at=NOW + timedelta(seconds=40),
    )

    await repository.renew_review_lease(command)
    with sqlite3.connect(database) as connection:
        renewed = connection.execute(
            "SELECT lease_owner, lease_expires_at FROM review_cases WHERE case_id = ?",
            ("case-review-1",),
        ).fetchone()
    assert renewed == ("worker-1", (NOW + timedelta(seconds=40)).isoformat())

    with pytest.raises(ReviewConflictError, match="lease"):
        await repository.renew_review_lease(
            command.model_copy(update={"lease_owner": "stale-worker"})
        )
    await repository.route_revision_failure(_revision_failure_command())
    with pytest.raises(ReviewConflictError):
        await repository.renew_review_lease(command)


async def test_semantic_finalization_requires_current_unexpired_lease_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic-finalization-owner.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_claim_semantic(repository)
    command = _semantic_verifier_command()

    for forged in (
        command.model_copy(update={"lease_owner": None}),
        command.model_copy(update={"lease_owner": "stale-worker"}),
        command.model_copy(update={"occurred_at": NOW + timedelta(seconds=40)}),
    ):
        with pytest.raises(ReviewConflictError, match="lease"):
            await asyncio.to_thread(repository._append_verifier_run, forged)

    completed = await repository.append_verifier_run(command)
    assert completed.semantic_run_id == command.report.verifier_run_id


async def test_revision_finalization_requires_current_unexpired_lease_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "provider-finalization-owner.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_claim_revision(repository)
    command = _revision_command()

    for forged in (
        command.model_copy(update={"lease_owner": None}),
        command.model_copy(update={"lease_owner": "stale-worker"}),
        command.model_copy(update={"occurred_at": NOW + timedelta(seconds=40)}),
    ):
        with pytest.raises(ReviewConflictError, match="lease"):
            await asyncio.to_thread(repository._append_revision, forged)
    assert len((await repository.get_detail("case-review-1")).revisions) == 1

    completed = await repository.append_revision(command)
    assert completed.current_revision_number == 1


async def test_revision_failure_requires_current_unexpired_lease_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "revision-failure-owner.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_claim_revision(repository)
    command = _revision_failure_command()

    for forged in (
        command.model_copy(update={"lease_owner": None}),
        command.model_copy(update={"lease_owner": "stale-worker"}),
        command.model_copy(update={"occurred_at": NOW + timedelta(seconds=40)}),
    ):
        with pytest.raises(ReviewConflictError, match="lease"):
            await asyncio.to_thread(repository._route_revision_failure, forged)

    completed = await repository.route_revision_failure(command)
    assert completed.status is ReviewStatus.AWAITING_HUMAN_REVIEW


def _counts(database: Path) -> dict[str, int]:
    with connect_database(database) as connection:
        tables = (
            "review_cases",
            "review_inputs",
            "diagnosis_revisions",
            "verifier_runs",
            "human_decisions",
            "workflow_events",
            "idempotency_keys",
        )
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


async def test_create_case_atomically_persists_aggregate_and_idempotency(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()

    detail = await repository.create_case(_create_command())

    assert detail.case.case_id == "case-review-1"
    assert detail.case.version == 0
    assert detail.revisions == (make_revision(),)
    assert _counts(database) == {
        "review_cases": 1,
        "review_inputs": 1,
        "diagnosis_revisions": 1,
        "verifier_runs": 0,
        "human_decisions": 0,
        "workflow_events": 1,
        "idempotency_keys": 1,
    }


async def test_create_case_rejects_unsanitized_canonical_snapshot_before_insert(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsafe-review-snapshot.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    snapshot = make_review_snapshot()
    parsed = json.loads(snapshot.view_json)
    sentinel = "direct-snapshot-sentinel-credential"
    parsed["spans"][0]["attributes"]["tool.result"] = {
        "api_key": sentinel,
        "safe": "context must survive only after source sanitation",
    }
    unsafe_snapshot = snapshot.model_copy(
        update={
            "view_json": canonical_json(parsed),
            "input_sha256": canonical_sha256(parsed),
        }
    )
    forged = _create_command().model_copy(update={"snapshot": unsafe_snapshot})

    with pytest.raises(ReviewConflictError, match="invalid create review command"):
        await repository.create_case(forged)

    with connect_database(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_inputs").fetchone()[0] == 0
        stored = "\n".join(
            str(value)
            for row in connection.execute("SELECT * FROM review_inputs").fetchall()
            for value in row
        )
    assert sentinel not in stored


@pytest.mark.parametrize(
    "failure_stage",
    ["review_case", "review_input", "diagnosis_revision", "workflow_event", "idempotency_key"],
)
async def test_create_case_rolls_back_after_every_insert(
    tmp_path: Path, failure_stage: str
) -> None:
    database = tmp_path / f"{failure_stage}.sqlite3"

    def fail_after_insert(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError("forced transaction failure")

    repository = SQLiteReviewRepository(database, failure_injector=fail_after_insert)
    await repository.initialize()

    with pytest.raises(RuntimeError, match="forced transaction failure"):
        await repository.create_case(_create_command())

    assert _counts(database) == {
        "review_cases": 0,
        "review_inputs": 0,
        "diagnosis_revisions": 0,
        "verifier_runs": 0,
        "human_decisions": 0,
        "workflow_events": 0,
        "idempotency_keys": 0,
    }


async def test_create_idempotency_returns_original_and_rejects_changed_fingerprint(
    tmp_path: Path,
) -> None:
    repository = SQLiteReviewRepository(tmp_path / "reviews.sqlite3")
    await repository.initialize()
    command = _create_command()

    original = await repository.create_case(command)
    replay = await repository.create_case(command)
    assert replay == original

    changed = command.model_copy(update={"request_sha256": "b" * 64})
    with pytest.raises(ReviewConflictError, match="idempotency key conflict"):
        await repository.create_case(changed)


@pytest.mark.parametrize(
    "forgery", ["target", "event", "snapshot_binding", "revision_binding"]
)
async def test_create_case_revalidates_forged_command_before_any_insert(
    tmp_path: Path, forgery: str
) -> None:
    command = _create_command()
    forged_commands = {
        "target": command.model_copy(
            update={"target_status": ReviewStatus.AWAITING_HUMAN_REVIEW}
        ),
        "event": command.model_copy(update={"event_type": WorkflowEventType.HUMAN_CONFIRMED}),
        "snapshot_binding": command.model_copy(
            update={
                "snapshot": command.snapshot.model_copy(update={"trace_id": "trace-other"})
            }
        ),
        "revision_binding": command.model_copy(
            update={
                "initial_revision": command.initial_revision.model_copy(
                    update={"case_id": "case-other"}
                )
            }
        ),
    }
    database = tmp_path / f"create-{forgery}.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()

    with pytest.raises(ReviewConflictError) as captured:
        await repository.create_case(forged_commands[forgery])

    assert str(captured.value) == "invalid create review command"
    assert _counts(database) == {
        "review_cases": 0,
        "review_inputs": 0,
        "diagnosis_revisions": 0,
        "verifier_runs": 0,
        "human_decisions": 0,
        "workflow_events": 0,
        "idempotency_keys": 0,
    }


@pytest.mark.parametrize(
    "lease_expires_at",
    [
        NOW,
        NOW.replace(tzinfo=None),
        NOW.replace(tzinfo=timezone(timedelta(hours=1))),
    ],
    ids=["expired", "naive", "non-utc-offset"],
)
async def test_claim_revalidates_forged_lease_before_transition(
    tmp_path: Path, lease_expires_at: datetime
) -> None:
    database = tmp_path / "claim.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    before = await repository.create_case(_create_command())
    forged = _claim_command().model_copy(update={"lease_expires_at": lease_expires_at})

    with pytest.raises(ReviewConflictError) as captured:
        await repository.claim_work(forged)

    assert str(captured.value) == "invalid claim review command"
    assert await repository.get_detail("case-review-1") == before
    assert _counts(database)["workflow_events"] == 1


@pytest.mark.parametrize("forgery", ["origin", "case_binding"])
async def test_append_revision_revalidates_forged_nested_revision_before_insert(
    tmp_path: Path, forgery: str
) -> None:
    database = tmp_path / f"revision-{forgery}.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(
        _verifier_command(
            verdict=VerifierVerdict.NEEDS_EVIDENCE,
            target_status=ReviewStatus.REVISION_REQUESTED,
        )
    )
    before = await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.REVISION_REQUESTED,
            target_status=ReviewStatus.REVISING,
            event_id="event-revision-started-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=2),
        )
    )
    command = _revision_command()
    revision_update = (
        {"origin": RevisionOrigin.INITIAL_DIAGNOSIS}
        if forgery == "origin"
        else {"case_id": "case-other"}
    )
    forged = command.model_copy(
        update={"revision": command.revision.model_copy(update=revision_update)}
    )

    with pytest.raises(ReviewConflictError) as captured:
        await repository.append_revision(forged)

    assert str(captured.value) == "invalid append revision command"
    assert (await repository.get_detail("case-review-1")).case == before
    assert _counts(database)["diagnosis_revisions"] == 1
    assert _counts(database)["workflow_events"] == 4


@pytest.mark.parametrize("forgery", ["expected_version", "correction"])
async def test_human_decision_revalidates_forged_nested_payload_before_insert(
    tmp_path: Path, forgery: str
) -> None:
    database = tmp_path / f"decision-{forgery}.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_verify(repository)
    await repository.route_to_human(_route_command())
    before = await repository.get_detail("case-review-1")
    command = _decision_command(
        decision_id="decision-a",
        reviewer="reviewer-a",
        idempotency_key="decision-key-a",
        event_id="event-human-a",
    )
    decision_update = (
        {"expected_version": 99}
        if forgery == "expected_version"
        else {"correction": make_correction_draft()}
    )
    forged = command.model_copy(
        update={"decision": command.decision.model_copy(update=decision_update)}
    )

    with pytest.raises(ReviewConflictError) as captured:
        await repository.apply_human_decision(forged)

    assert str(captured.value) == "invalid human decision command"
    assert await repository.get_detail("case-review-1") == before
    assert _counts(database)["human_decisions"] == 0
    assert _counts(database)["workflow_events"] == 4


async def test_commands_are_frozen_and_require_aware_utc_timestamps() -> None:
    command = _create_command()
    with pytest.raises(ValidationError, match="frozen"):
        command.case_id = "changed"  # type: ignore[misc]

    with pytest.raises(ValidationError, match="UTC"):
        CreateReviewCase(**{**command.model_dump(), "created_at": NOW.replace(tzinfo=None)})


@pytest.mark.parametrize(
    ("command", "updates", "error"),
    [
        (
            _claim_command(),
            {"target_status": ReviewStatus.AWAITING_HUMAN_REVIEW},
            "invalid claim transition",
        ),
        (
            _claim_command(),
            {
                "prior_status": ReviewStatus.AWAITING_HUMAN_REVIEW,
                "target_status": ReviewStatus.AWAITING_HUMAN_REVIEW,
            },
            "invalid claim transition",
        ),
        (
            _claim_command(),
            {
                "prior_status": ReviewStatus.CONFIRMED,
                "target_status": ReviewStatus.CONFIRMED,
            },
            "invalid claim transition",
        ),
        (
            _verifier_command(),
            {"target_status": ReviewStatus.AWAITING_HUMAN_REVIEW},
            "invalid verifier transition",
        ),
        (
            _verifier_command(),
            {"event_type": WorkflowEventType.REVISION_REQUESTED},
            "invalid verifier transition",
        ),
        (
            _revision_command(),
            {"target_status": ReviewStatus.AWAITING_HUMAN_REVIEW},
            "invalid revision transition",
        ),
        (
            _route_command(),
            {"prior_status": ReviewStatus.PENDING_VERIFICATION},
            "invalid human-route transition",
        ),
    ],
)
def test_command_models_reject_invalid_operation_transition_tuples(
    command: BaseModel, updates: dict[str, object], error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        _revalidate(command, updates)


async def test_repository_rejects_forged_pending_to_human_claim(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    before = await repository.create_case(_create_command())
    forged = _claim_command().model_copy(
        update={"target_status": ReviewStatus.AWAITING_HUMAN_REVIEW}
    )

    with pytest.raises(ReviewConflictError, match="invalid claim transition"):
        await asyncio.to_thread(repository._claim_work, forged)

    assert await repository.get_detail("case-review-1") == before
    assert _counts(database)["workflow_events"] == 1


@pytest.mark.parametrize("terminal", [False, True])
async def test_repository_rejects_forged_same_status_human_or_terminal_claim(
    tmp_path: Path, terminal: bool
) -> None:
    database = tmp_path / f"{terminal}.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_verify(repository)
    await repository.route_to_human(_route_command())
    if terminal:
        await repository.apply_human_decision(
            _decision_command(
                decision_id="decision-a",
                reviewer="reviewer-a",
                idempotency_key="decision-key-a",
                event_id="event-human-a",
            )
        )
    before = await repository.get_detail("case-review-1")
    forged = _claim_command().model_copy(
        update={
            "expected_version": before.case.version,
            "prior_status": before.case.status,
            "target_status": before.case.status,
            "event_id": "event-forged-human-claim",
            "now": NOW + timedelta(seconds=10),
            "lease_expires_at": NOW + timedelta(seconds=40),
        }
    )

    with pytest.raises(ReviewConflictError, match="invalid claim transition"):
        await asyncio.to_thread(repository._claim_work, forged)

    assert await repository.get_detail("case-review-1") == before


async def test_repository_rejects_forged_verifier_transition(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    before = await repository.claim_work(_claim_command())
    forged = _verifier_command().model_copy(
        update={"target_status": ReviewStatus.AWAITING_HUMAN_REVIEW}
    )

    with pytest.raises(ReviewConflictError, match="invalid verifier transition"):
        await asyncio.to_thread(repository._append_verifier_run, forged)

    assert (await repository.get_detail("case-review-1")).case == before
    assert _counts(database)["verifier_runs"] == 0


async def test_repository_rejects_forged_revision_transition(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(
        _verifier_command(
            verdict=VerifierVerdict.NEEDS_EVIDENCE,
            target_status=ReviewStatus.REVISION_REQUESTED,
        ).model_copy(update={"event_type": WorkflowEventType.REVISION_REQUESTED})
    )
    before = await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.REVISION_REQUESTED,
            target_status=ReviewStatus.REVISING,
            event_id="event-revision-started-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=2),
        )
    )
    forged = _revision_command().model_copy(
        update={"target_status": ReviewStatus.AWAITING_HUMAN_REVIEW}
    )

    with pytest.raises(ReviewConflictError, match="invalid revision transition"):
        await asyncio.to_thread(repository._append_revision, forged)

    assert (await repository.get_detail("case-review-1")).case == before
    assert _counts(database)["diagnosis_revisions"] == 1


def test_revision_failure_command_accepts_only_exact_review_transition() -> None:
    command = _revision_failure_command()

    with pytest.raises(ValidationError, match="invalid revision-failure transition"):
        _revalidate(
            command,
            {"prior_status": ReviewStatus.REVISION_REQUESTED},
        )
    with pytest.raises(ValidationError, match="invalid revision-failure transition"):
        _revalidate(
            command,
            {"event_type": WorkflowEventType.PROVIDER_FAILED},
        )
    with pytest.raises(ValidationError, match="revision failure must require human review"):
        _revalidate(
            command,
            {"composite_verdict": VerifierVerdict.NEEDS_EVIDENCE},
        )


async def test_revision_failure_routes_to_human_without_fabricated_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "revision-failure.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_claim_revision(repository)
    before_counts = _counts(database)

    routed = await repository.route_revision_failure(_revision_failure_command())

    assert routed.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert routed.version == 4
    assert routed.composite_verdict is VerifierVerdict.REVIEW_REQUIRED
    assert _counts(database) == {
        **before_counts,
        "workflow_events": before_counts["workflow_events"] + 1,
    }
    with connect_database(database) as connection:
        case_row = connection.execute(
            "SELECT lease_owner, lease_expires_at FROM review_cases WHERE case_id = ?",
            ("case-review-1",),
        ).fetchone()
        event_row = connection.execute(
            "SELECT event_type, metadata_json FROM workflow_events "
            "WHERE case_id = ? ORDER BY event_sequence DESC LIMIT 1",
            ("case-review-1",),
        ).fetchone()
    assert case_row == (None, None)
    assert event_row == (
        "revision_provider_failed",
        canonical_json({"code": "revision_provider_failed", "retryable": True}),
    )


async def test_revision_failure_revalidates_model_copy_and_enforces_cas(
    tmp_path: Path,
) -> None:
    database = tmp_path / "revision-failure-cas.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_claim_revision(repository)
    before = await repository.get_detail("case-review-1")

    forged = _revision_failure_command().model_copy(
        update={"prior_status": ReviewStatus.REVISION_REQUESTED}
    )
    with pytest.raises(ReviewConflictError, match="invalid revision failure command"):
        await repository.route_revision_failure(forged)
    with pytest.raises(ReviewConflictError, match="compare-and-swap conflict"):
        await repository.route_revision_failure(_revision_failure_command(expected_version=99))

    assert await repository.get_detail("case-review-1") == before


async def test_revision_failure_duplicate_is_exactly_once_and_changed_event_conflicts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "revision-failure-duplicate.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_claim_revision(repository)
    command = _revision_failure_command()

    original = await repository.route_revision_failure(command)
    replay = await repository.route_revision_failure(command)
    assert replay == original
    counts = _counts(database)

    changed = command.model_copy(
        update={
            "event_metadata_json": canonical_json(
                {"code": "revision_provider_failed", "retryable": False}
            )
        }
    )
    with pytest.raises(ReviewConflictError, match="duplicate workflow event"):
        await repository.route_revision_failure(changed)
    assert _counts(database) == counts


async def test_revision_failure_rolls_back_state_and_event(tmp_path: Path) -> None:
    database = tmp_path / "revision-failure-rollback.sqlite3"

    def fail_after_event(stage: str) -> None:
        if stage == "workflow_event":
            raise RuntimeError("forced transaction failure")

    setup_repository = SQLiteReviewRepository(database)
    await setup_repository.initialize()
    await _create_and_claim_revision(setup_repository)
    before = await setup_repository.get_detail("case-review-1")
    before_counts = _counts(database)
    repository = SQLiteReviewRepository(database, failure_injector=fail_after_event)

    with pytest.raises(RuntimeError, match="forced transaction failure"):
        await repository.route_revision_failure(_revision_failure_command())

    assert await setup_repository.get_detail("case-review-1") == before
    assert _counts(database) == before_counts


async def test_repository_rejects_forged_route_and_decision_transitions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    created = await repository.create_case(_create_command())
    forged_route = _route_command(expected_version=0).model_copy(
        update={"prior_status": ReviewStatus.PENDING_VERIFICATION}
    )
    with pytest.raises(ReviewConflictError, match="invalid human-route transition"):
        await asyncio.to_thread(repository._route_to_human, forged_route)
    assert await repository.get_detail("case-review-1") == created

    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(_verifier_command())
    await repository.route_to_human(_route_command())
    before = await repository.get_detail("case-review-1")
    forged_decision = _decision_command(
        decision_id="decision-a",
        reviewer="reviewer-a",
        idempotency_key="decision-key-a",
        event_id="event-human-a",
    ).model_copy(update={"target_status": ReviewStatus.AWAITING_HUMAN_REVIEW})
    with pytest.raises(ReviewConflictError, match="invalid human-decision transition"):
        await asyncio.to_thread(repository._apply_human_decision, forged_decision)
    assert await repository.get_detail("case-review-1") == before


async def test_human_decision_requires_awaiting_human_review_and_matching_correction_case() -> None:
    confirm = _decision_command(
        decision_id="decision-a",
        reviewer="reviewer-a",
        idempotency_key="decision-key-a",
        event_id="event-human-a",
    )
    with pytest.raises(ValidationError, match="awaiting_human_review"):
        ApplyHumanDecision(
            **{
                **confirm.model_dump(),
                "prior_status": ReviewStatus.PENDING_VERIFICATION,
            }
        )

    correction_revision = make_revision(
        revision_number=1,
        origin=RevisionOrigin.HUMAN_CORRECTION,
        previous_report_sha256=make_revision().report_sha256,
    ).model_copy(update={"case_id": "case-other"})
    decision = HumanReviewDecision(
        decision_id="decision-correction",
        case_id="case-review-1",
        action=DecisionAction.CORRECT,
        expected_version=3,
        reviewer_label="reviewer-a",
        correction=make_correction_draft(),
        resulting_revision_id=correction_revision.revision_id,
        created_at=NOW + timedelta(seconds=3),
    )
    with pytest.raises(ValidationError, match="correction revision case_id"):
        ApplyHumanDecision(
            case_id="case-review-1",
            expected_version=3,
            prior_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
            target_status=ReviewStatus.CORRECTED,
            decision=decision,
            correction_revision=correction_revision,
            correction_verifier_report=make_verifier_report(
                report_sha256=correction_revision.report_sha256
            ).model_copy(update={"revision_number": correction_revision.revision_number}),
            idempotency_scope="review.decision:case-review-1",
            idempotency_key="decision-key-correction",
            request_sha256="b" * 64,
            event_id="event-human-correction",
            event_type=WorkflowEventType.HUMAN_CORRECTED,
            event_metadata_json=canonical_json({"reviewer": "reviewer-a"}),
            occurred_at=decision.created_at,
        )


def test_correction_binding_uses_decision_action_enum_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = make_revision()
    correction_revision = make_revision(
        revision_number=1,
        origin=RevisionOrigin.HUMAN_CORRECTION,
        previous_report_sha256=original.report_sha256,
    )
    decision = HumanReviewDecision(
        decision_id="decision-correction",
        case_id="case-review-1",
        action=DecisionAction.CORRECT,
        expected_version=3,
        reviewer_label="reviewer-a",
        correction=make_correction_draft(),
        resulting_revision_id=correction_revision.revision_id,
        created_at=NOW + timedelta(seconds=3),
    )
    monkeypatch.setattr(DecisionAction.CORRECT, "_value_", "renamed-correct")

    command = ApplyHumanDecision(
        case_id="case-review-1",
        expected_version=3,
        prior_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
        target_status=ReviewStatus.CORRECTED,
        decision=decision,
        correction_revision=correction_revision,
        correction_verifier_report=make_verifier_report(
            report_sha256=correction_revision.report_sha256
        ).model_copy(update={"revision_number": correction_revision.revision_number}),
        idempotency_scope="review.decision:case-review-1",
        idempotency_key="decision-key-correction",
        request_sha256="c" * 64,
        event_id="event-human-correction",
        event_type=WorkflowEventType.HUMAN_CORRECTED,
        event_metadata_json=canonical_json({"reviewer": "reviewer-a"}),
        occurred_at=decision.created_at,
    )

    assert command.decision.action is DecisionAction.CORRECT


async def test_repository_rejects_human_decision_before_human_review(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    before = await repository.create_case(_create_command())
    command = _decision_command(
        decision_id="decision-a",
        reviewer="reviewer-a",
        idempotency_key="decision-key-a",
        event_id="event-human-a",
    ).model_copy(
        update={
            "expected_version": 0,
            "prior_status": ReviewStatus.PENDING_VERIFICATION,
        }
    )

    with pytest.raises(ReviewConflictError, match="awaiting_human_review"):
        await asyncio.to_thread(repository._apply_human_decision, command)

    assert await repository.get_detail("case-review-1") == before
    assert _counts(database)["human_decisions"] == 0
    assert _counts(database)["workflow_events"] == 1


async def test_missing_case_raises_typed_error_without_database_details(tmp_path: Path) -> None:
    database = tmp_path / "private-name.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()

    with pytest.raises(ReviewNotFoundError) as captured:
        await repository.get_detail("missing")

    assert "private-name" not in str(captured.value)
    assert "SELECT" not in str(captured.value)


async def test_append_revision_keeps_revision_zero_immutable_and_caps_evidence_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    original = await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    verifier = _verifier_command(
        verdict=VerifierVerdict.NEEDS_EVIDENCE,
        target_status=ReviewStatus.REVISION_REQUESTED,
    )
    verifier = verifier.model_copy(
        update={
            "event_type": WorkflowEventType.REVISION_REQUESTED,
            "event_metadata_json": canonical_json({"gaps": ["gap-1"]}),
        }
    )
    await repository.append_verifier_run(verifier)
    await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.REVISION_REQUESTED,
            target_status=ReviewStatus.REVISING,
            event_id="event-revision-started-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=2),
        )
    )
    revision = make_revision(
        revision_number=1,
        previous_report_sha256=original.revisions[0].report_sha256,
        triggering_gap_ids=("gap-1",),
    )
    appended = await repository.append_revision(
        AppendDiagnosisRevision(
            case_id="case-review-1",
            expected_version=3,
            prior_status=ReviewStatus.REVISING,
            target_status=ReviewStatus.VERIFYING,
            revision=revision,
            lease_owner="worker-1",
            event_id="event-revision-completed-1",
            event_type=WorkflowEventType.REVISION_COMPLETED,
            event_metadata_json=canonical_json({"revision": 1}),
            occurred_at=NOW + timedelta(seconds=3),
        )
    )

    detail = await repository.get_detail("case-review-1")
    assert detail.revisions == (original.revisions[0], revision)
    assert appended.current_revision_number == 1
    assert appended.evidence_revision_count == 1
    assert appended.deterministic_run_id is None
    assert appended.semantic_run_id is None
    assert appended.composite_verdict is None

    await repository.claim_work(
        _claim_command(
            expected_version=4,
            prior_status=ReviewStatus.VERIFYING,
            target_status=ReviewStatus.VERIFYING,
            event_id="event-verification-started-2",
            now=NOW + timedelta(seconds=4),
        )
    )
    second_report = make_verifier_report(verdict=VerifierVerdict.NEEDS_EVIDENCE).model_copy(
        update={"verifier_run_id": "verifier-deterministic-2", "revision_number": 1}
    )
    with pytest.raises(ReviewConflictError, match="evidence revision limit"):
        await repository.append_verifier_run(
            _verifier_command(
                verdict=VerifierVerdict.NEEDS_EVIDENCE,
                target_status=ReviewStatus.REVISION_REQUESTED,
                event_id="event-revision-requested-2",
            ).model_copy(
                update={
                    "expected_version": 5,
                    "report": second_report,
                    "occurred_at": second_report.completed_at,
                }
            )
        )

    assert (await repository.get_detail("case-review-1")).revisions == (
        original.revisions[0],
        revision,
    )


async def test_revision_completion_follows_frozen_revising_to_verifying_edge(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(
        _verifier_command(
            verdict=VerifierVerdict.NEEDS_EVIDENCE,
            target_status=ReviewStatus.REVISION_REQUESTED,
        )
    )
    await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.REVISION_REQUESTED,
            target_status=ReviewStatus.REVISING,
            event_id="event-revision-started-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=2),
        )
    )
    command = AppendDiagnosisRevision(
        **{
            **_revision_command().model_dump(),
            "target_status": ReviewStatus.VERIFYING,
        }
    )

    completed = await repository.append_revision(command)

    assert completed.status is ReviewStatus.VERIFYING
    assert completed.version == 4
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT lease_owner, lease_expires_at FROM review_cases WHERE case_id = ?",
            ("case-review-1",),
        ).fetchone() == (None, None)
        assert connection.execute(
            "SELECT from_status, to_status, event_type FROM workflow_events "
            "WHERE case_id = ? ORDER BY event_sequence DESC LIMIT 1",
            ("case-review-1",),
        ).fetchone() == ("revising", "verifying", "revision_completed")


async def test_consumed_evidence_revision_cannot_reenter_revision_requested(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(
        _verifier_command(
            verdict=VerifierVerdict.NEEDS_EVIDENCE,
            target_status=ReviewStatus.REVISION_REQUESTED,
        )
    )
    await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.REVISION_REQUESTED,
            target_status=ReviewStatus.REVISING,
            event_id="event-revision-started-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=2),
        )
    )
    await repository.append_revision(_revision_command())
    before = await repository.get_detail("case-review-1")
    second_report = make_verifier_report(verdict=VerifierVerdict.NEEDS_EVIDENCE).model_copy(
        update={"verifier_run_id": "verifier-deterministic-2", "revision_number": 1}
    )
    command = _verifier_command(
        verdict=VerifierVerdict.NEEDS_EVIDENCE,
        target_status=ReviewStatus.REVISION_REQUESTED,
        event_id="event-revision-requested-2",
    ).model_copy(
        update={
            "expected_version": 4,
            "report": second_report,
            "occurred_at": second_report.completed_at,
        }
    )

    with pytest.raises(ReviewConflictError, match="evidence revision limit"):
        await repository.append_verifier_run(command)

    assert await repository.get_detail("case-review-1") == before
    assert before.case.status is ReviewStatus.VERIFYING
    assert _counts(database)["verifier_runs"] == 1
    assert _counts(database)["workflow_events"] == 5

    completed_report = make_verifier_report().model_copy(
        update={"verifier_run_id": "verifier-deterministic-3", "revision_number": 1}
    )
    await repository.append_verifier_run(
        _verifier_command(event_id="event-verification-completed-2").model_copy(
            update={
                "expected_version": 4,
                "report": completed_report,
                "occurred_at": completed_report.completed_at,
            }
        )
    )
    awaiting = await repository.route_to_human(_route_command(expected_version=5))

    assert awaiting.status is ReviewStatus.AWAITING_HUMAN_REVIEW


async def test_appended_revision_report_must_match_persisted_trace_and_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    original = await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    verifier = _verifier_command(
        verdict=VerifierVerdict.NEEDS_EVIDENCE,
        target_status=ReviewStatus.REVISION_REQUESTED,
    ).model_copy(update={"event_type": WorkflowEventType.REVISION_REQUESTED})
    await repository.append_verifier_run(verifier)
    await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.REVISION_REQUESTED,
            target_status=ReviewStatus.REVISING,
            event_id="event-revision-started-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=2),
        )
    )
    revision = make_revision(
        revision_number=1,
        previous_report_sha256=original.revisions[0].report_sha256,
        triggering_gap_ids=("gap-1",),
    )
    mismatched_report = revision.report.model_copy(
        update={"trace_id": "trace-other", "run_id": "run-other"}
    )
    mismatched_revision = revision.model_copy(
        update={
            "report": mismatched_report,
            "report_sha256": canonical_sha256(mismatched_report),
        }
    )

    with pytest.raises(ReviewConflictError, match="snapshot binding"):
        await repository.append_revision(
            AppendDiagnosisRevision(
                case_id="case-review-1",
                expected_version=3,
                prior_status=ReviewStatus.REVISING,
                target_status=ReviewStatus.VERIFYING,
                revision=mismatched_revision,
                lease_owner="worker-1",
                event_id="event-revision-completed-1",
                event_type=WorkflowEventType.REVISION_COMPLETED,
                event_metadata_json=canonical_json({"revision": 1}),
                occurred_at=NOW + timedelta(seconds=3),
            )
        )

    assert (await repository.get_detail("case-review-1")).revisions == original.revisions
    assert _counts(database)["workflow_events"] == 4


async def test_route_to_human_requires_a_persisted_verifier_run(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    before_case = await repository.claim_work(_claim_command())
    command = _route_command(expected_version=1)

    with pytest.raises(ReviewConflictError, match="verification required"):
        await repository.route_to_human(command)

    assert (await repository.get_detail("case-review-1")).case == before_case
    assert _counts(database)["workflow_events"] == 2


async def test_human_correction_report_must_match_persisted_trace_and_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    created = await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(_verifier_command())
    awaiting = await repository.route_to_human(_route_command())
    correction = make_revision(
        revision_number=1,
        origin=RevisionOrigin.HUMAN_CORRECTION,
        previous_report_sha256=created.revisions[0].report_sha256,
    )
    mismatched_report = correction.report.model_copy(
        update={"trace_id": "trace-other", "run_id": "run-other"}
    )
    correction = correction.model_copy(
        update={
            "report": mismatched_report,
            "report_sha256": canonical_sha256(mismatched_report),
        }
    )
    decision = HumanReviewDecision(
        decision_id="decision-correction",
        case_id="case-review-1",
        action=DecisionAction.CORRECT,
        expected_version=3,
        reviewer_label="reviewer-a",
        correction=make_correction_draft(),
        resulting_revision_id=correction.revision_id,
        created_at=NOW + timedelta(seconds=3),
    )
    command = ApplyHumanDecision(
        case_id="case-review-1",
        expected_version=3,
        prior_status=ReviewStatus.AWAITING_HUMAN_REVIEW,
        target_status=ReviewStatus.CORRECTED,
        decision=decision,
        correction_revision=correction,
        correction_verifier_report=make_verifier_report(
            report_sha256=correction.report_sha256
        ).model_copy(update={"revision_number": correction.revision_number}),
        idempotency_scope="review.decision:case-review-1",
        idempotency_key="decision-key-correction",
        request_sha256="c" * 64,
        event_id="event-human-correction",
        event_type=WorkflowEventType.HUMAN_CORRECTED,
        event_metadata_json=canonical_json({"reviewer": "reviewer-a"}),
        occurred_at=decision.created_at,
    )

    with pytest.raises(ReviewConflictError, match="snapshot binding"):
        await repository.apply_human_decision(command)

    detail = await repository.get_detail("case-review-1")
    assert detail.case == awaiting
    assert detail.revisions == created.revisions
    assert detail.decision is None
    assert _counts(database)["workflow_events"] == 4


async def test_verifier_report_hash_must_bind_to_current_revision_atomically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verifier-binding.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    claimed = await repository.claim_work(_claim_command())
    forged = _verifier_command()
    forged = forged.model_copy(
        update={
            "report": forged.report.model_copy(update={"report_sha256": "f" * 64})
        }
    )

    with pytest.raises(ReviewConflictError, match="binding"):
        await repository.append_verifier_run(forged)

    assert (await repository.get_detail("case-review-1")).case == claimed
    assert _counts(database)["verifier_runs"] == 0
    assert _counts(database)["workflow_events"] == 2


async def test_human_correction_verifier_binding_is_revalidated_before_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "correction-binding.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_verify(repository)
    await repository.route_to_human(_route_command())
    before = await repository.get_detail("case-review-1")
    command = _correction_decision_command()
    assert command.correction_verifier_report is not None
    forged = command.model_copy(
        update={
            "correction_verifier_report": command.correction_verifier_report.model_copy(
                update={"report_sha256": "f" * 64}
            )
        }
    )

    with pytest.raises(ReviewConflictError, match="invalid human decision command"):
        await repository.apply_human_decision(forged)

    assert await repository.get_detail("case-review-1") == before


async def test_human_correction_rolls_back_revision_verifier_and_decision_together(
    tmp_path: Path,
) -> None:
    database = tmp_path / "correction-rollback.sqlite3"
    armed = False

    def fail_after_correction_verifier(stage: str) -> None:
        if armed and stage == "verifier_run":
            raise RuntimeError("forced correction transaction failure")

    repository = SQLiteReviewRepository(
        database, failure_injector=fail_after_correction_verifier
    )
    await repository.initialize()
    await _create_and_verify(repository)
    await repository.route_to_human(_route_command())
    before = await repository.get_detail("case-review-1")
    before_counts = _counts(database)
    armed = True

    with pytest.raises(RuntimeError, match="forced correction transaction failure"):
        await repository.apply_human_decision(_correction_decision_command())

    assert await repository.get_detail("case-review-1") == before
    assert _counts(database) == before_counts


async def test_cas_version_or_status_mismatch_changes_nothing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "version.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_verify(repository)
    before = await repository.get_detail("case-review-1")
    command = _route_command(expected_version=99)

    with pytest.raises(ReviewConflictError, match="compare-and-swap conflict"):
        await repository.route_to_human(command)

    assert await repository.get_detail("case-review-1") == before
    assert _counts(database)["workflow_events"] == 3

    status_database = tmp_path / "status.sqlite3"
    status_repository = SQLiteReviewRepository(status_database)
    await status_repository.initialize()
    await status_repository.create_case(_create_command())
    await status_repository.claim_work(_claim_command())
    await status_repository.append_verifier_run(
        _verifier_command(
            verdict=VerifierVerdict.NEEDS_EVIDENCE,
            target_status=ReviewStatus.REVISION_REQUESTED,
        )
    )
    status_before = await status_repository.get_detail("case-review-1")

    with pytest.raises(ReviewConflictError, match="compare-and-swap conflict"):
        await status_repository.route_to_human(_route_command(expected_version=2))

    assert await status_repository.get_detail("case-review-1") == status_before
    assert _counts(status_database)["workflow_events"] == 3


async def test_active_lease_cannot_be_reclaimed_but_expired_lease_can(tmp_path: Path) -> None:
    repository = SQLiteReviewRepository(tmp_path / "reviews.sqlite3")
    await repository.initialize()
    await repository.create_case(_create_command())
    first = await repository.claim_work(_claim_command())
    assert first.version == 1

    changed_replay = _claim_command().model_copy(
        update={"lease_expires_at": NOW + timedelta(seconds=45)}
    )
    with pytest.raises(ReviewConflictError, match="duplicate work claim"):
        await repository.claim_work(changed_replay)

    active_reclaim = _claim_command(
        expected_version=1,
        prior_status=ReviewStatus.VERIFYING,
        target_status=ReviewStatus.VERIFYING,
        owner="worker-2",
        event_id="event-reclaim-active",
        now=NOW + timedelta(seconds=10),
    )
    with pytest.raises(ReviewConflictError, match="lease is still active"):
        await repository.claim_work(active_reclaim)

    expired_reclaim = _claim_command(
        expected_version=1,
        prior_status=ReviewStatus.VERIFYING,
        target_status=ReviewStatus.VERIFYING,
        owner="worker-2",
        event_id="event-reclaim-expired",
        now=NOW + timedelta(seconds=31),
    )
    reclaimed = await repository.claim_work(expired_reclaim)
    assert reclaimed.version == 2


@pytest.mark.parametrize(
    ("lease_owner", "stored_expiry"),
    [
        (None, "private-stored-value"),
        (None, "2026-07-17T08:00:00"),
        (None, "2026-07-17T08:00:00+01:00"),
        (None, "2026-07-17T08:00:30+00:00"),
        ("worker-1", None),
        ("worker-1", "private-stored-value"),
        ("worker-1", "2026-07-17T08:00:00"),
        ("worker-1", "2026-07-17T08:00:00+01:00"),
    ],
)
async def test_corrupt_persisted_lease_pair_or_timestamp_raises_sanitized_typed_error(
    tmp_path: Path, lease_owner: str | None, stored_expiry: str | None
) -> None:
    database = tmp_path / "private-reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE review_cases SET lease_owner = ?, lease_expires_at = ? WHERE case_id = ?",
            (lease_owner, stored_expiry, "case-review-1"),
        )
        connection.commit()

    with pytest.raises(ReviewPersistenceError) as captured:
        await repository.claim_work(
            _claim_command(
                expected_version=1,
                prior_status=ReviewStatus.VERIFYING,
                target_status=ReviewStatus.VERIFYING,
                owner="worker-2",
                event_id="event-reclaim-malformed",
                now=NOW + timedelta(seconds=31),
            )
        )

    assert str(captured.value) == "stored review data is invalid"
    assert "private" not in str(captured.value)


@pytest.mark.parametrize(
    ("lease_owner", "stored_expiry"),
    [
        (None, "private-stored-value"),
        ("worker-1", None),
        ("worker-1", "2026-07-17T08:00:00"),
    ],
)
async def test_corrupt_lease_is_sanitized_before_claim_replay_comparison(
    tmp_path: Path, lease_owner: str | None, stored_expiry: str | None
) -> None:
    database = tmp_path / "private-replay.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    command = _claim_command()
    await repository.claim_work(command)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE review_cases SET lease_owner = ?, lease_expires_at = ? WHERE case_id = ?",
            (lease_owner, stored_expiry, "case-review-1"),
        )
        connection.commit()

    with pytest.raises(ReviewPersistenceError, match="stored review data is invalid"):
        await repository.claim_work(command)


async def test_unknown_persisted_workflow_event_type_is_sanitized(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private-events.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_events SET event_type = ? WHERE case_id = ?",
            ("private-unknown-event", "case-review-1"),
        )
        connection.commit()

    with pytest.raises(ReviewPersistenceError) as captured:
        await repository.get_detail("case-review-1")

    assert str(captured.value) == "stored review data is invalid"
    assert "private" not in str(captured.value)


async def test_expired_revising_lease_can_be_reclaimed_with_revision_started_event(
    tmp_path: Path,
) -> None:
    repository = SQLiteReviewRepository(tmp_path / "reviews.sqlite3")
    await repository.initialize()
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    await repository.append_verifier_run(
        _verifier_command(
            verdict=VerifierVerdict.NEEDS_EVIDENCE,
            target_status=ReviewStatus.REVISION_REQUESTED,
        )
    )
    await repository.claim_work(
        _claim_command(
            expected_version=2,
            prior_status=ReviewStatus.REVISION_REQUESTED,
            target_status=ReviewStatus.REVISING,
            event_id="event-revision-started-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=2),
        )
    )

    reclaimed = await repository.claim_work(
        _claim_command(
            expected_version=3,
            prior_status=ReviewStatus.REVISING,
            target_status=ReviewStatus.REVISING,
            owner="worker-2",
            event_id="event-revision-resumed-1",
            event_type=WorkflowEventType.REVISION_STARTED,
            now=NOW + timedelta(seconds=33),
        )
    )

    assert reclaimed.status is ReviewStatus.REVISING
    assert reclaimed.version == 4


async def test_duplicate_verifier_effect_and_event_do_not_duplicate_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command())
    await repository.claim_work(_claim_command())
    command = _verifier_command()

    first = await repository.append_verifier_run(command)
    replay = await repository.append_verifier_run(command)
    assert replay == first
    assert _counts(database)["verifier_runs"] == 1
    assert _counts(database)["workflow_events"] == 3

    changed_composite = command.model_copy(
        update={"composite_verdict": VerifierVerdict.REVIEW_REQUIRED}
    )
    with pytest.raises(ReviewConflictError, match="duplicate verifier result"):
        await repository.append_verifier_run(changed_composite)

    different_report = command.report.model_copy(update={"verifier_run_id": "verifier-other"})
    duplicate_event = command.model_copy(
        update={
            "expected_version": 2,
            "report": different_report,
        }
    )
    with pytest.raises(ReviewConflictError, match="duplicate workflow event"):
        await repository.append_verifier_run(duplicate_event)
    assert _counts(database)["verifier_runs"] == 1


async def test_correction_verifier_run_identity_is_scoped_to_case(tmp_path: Path) -> None:
    repository = SQLiteReviewRepository(tmp_path / "case-scoped-corrections.sqlite3")
    await repository.initialize()

    async def prepare_case(case_id: str, suffix: str) -> None:
        create = _create_command()
        revision = create.initial_revision.model_copy(
            update={"case_id": case_id, "revision_id": f"revision-0-{suffix}"}
        )
        await repository.create_case(
            create.model_copy(
                update={
                    "case_id": case_id,
                    "initial_revision": revision,
                    "idempotency_scope": f"review.create:{case_id}",
                    "idempotency_key": f"create-{suffix}",
                    "event_id": f"event-created-{suffix}",
                }
            )
        )
        await repository.claim_work(
            _claim_command().model_copy(
                update={"case_id": case_id, "event_id": f"event-claim-{suffix}"}
            )
        )
        await repository.append_verifier_run(
            _verifier_command().model_copy(
                update={"case_id": case_id, "event_id": f"event-verified-{suffix}"}
            )
        )
        await repository.route_to_human(
            _route_command().model_copy(
                update={"case_id": case_id, "event_id": f"event-human-{suffix}"}
            )
        )

    async def correct_case(case_id: str, suffix: str) -> None:
        command = _correction_decision_command()
        assert command.correction_revision is not None
        assert command.correction_verifier_report is not None
        revision = command.correction_revision.model_copy(
            update={"case_id": case_id, "revision_id": f"revision-correction-{suffix}"}
        )
        verifier_report = command.correction_verifier_report.model_copy(
            update={
                "verifier_run_id": "verifier-shared-content-id",
                "report_sha256": revision.report_sha256,
            }
        )
        decision = command.decision.model_copy(
            update={
                "case_id": case_id,
                "decision_id": f"decision-correction-{suffix}",
                "resulting_revision_id": revision.revision_id,
            }
        )
        await repository.apply_human_decision(
            command.model_copy(
                update={
                    "case_id": case_id,
                    "decision": decision,
                    "correction_revision": revision,
                    "correction_verifier_report": verifier_report,
                    "idempotency_scope": f"review.decision:{case_id}",
                    "idempotency_key": f"correction-{suffix}",
                    "event_id": f"event-correction-{suffix}",
                }
            )
        )

    await prepare_case("case-review-1", "one")
    await prepare_case("case-review-2", "two")
    await correct_case("case-review-1", "one")
    await correct_case("case-review-2", "two")

    assert (await repository.get_detail("case-review-1")).case.status is ReviewStatus.CORRECTED
    assert (await repository.get_detail("case-review-2")).case.status is ReviewStatus.CORRECTED


async def test_exactly_one_terminal_human_decision_wins_concurrent_race(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_verify(repository)
    await repository.route_to_human(_route_command())
    commands = (
        _decision_command(
            decision_id="decision-a",
            reviewer="reviewer-a",
            idempotency_key="decision-key-a",
            event_id="event-human-a",
        ),
        _decision_command(
            decision_id="decision-b",
            reviewer="reviewer-b",
            idempotency_key="decision-key-b",
            event_id="event-human-b",
        ),
    )

    results = await asyncio.gather(
        *(repository.apply_human_decision(command) for command in commands),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ReviewConflictError) for result in results) == 1
    assert _counts(database)["human_decisions"] == 1
    assert _counts(database)["workflow_events"] == 5
    detail = await repository.get_detail("case-review-1")
    assert detail.case.status is ReviewStatus.CONFIRMED
    assert detail.decision is not None
    assert detail.case.terminal_decision_id == detail.decision.decision_id


async def test_reopen_reconstructs_byte_equivalent_aggregate_runtime_and_event_order(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_and_verify(repository)
    runtime = await repository.load_runtime("case-review-1")
    await repository.route_to_human(_route_command())
    await repository.apply_human_decision(
        _decision_command(
            decision_id="decision-a",
            reviewer="reviewer-a",
            idempotency_key="decision-key-a",
            event_id="event-human-a",
        )
    )
    before = await repository.get_detail("case-review-1")
    before_bytes = canonical_json(before).encode()

    reopened = SQLiteReviewRepository(database)
    await reopened.initialize()
    after = await reopened.get_detail("case-review-1")
    reopened_runtime = await reopened.load_runtime("case-review-1")

    assert canonical_json(after).encode() == before_bytes
    assert reopened_runtime.snapshot == runtime.snapshot
    assert reopened_runtime.revisions == runtime.revisions
    with connect_database(database) as connection:
        events = connection.execute(
            "SELECT event_sequence, event_type FROM workflow_events "
            "WHERE case_id = ? ORDER BY event_sequence",
            ("case-review-1",),
        ).fetchall()
    assert events == [
        (0, "case_created"),
        (1, "verification_started"),
        (2, "verification_completed"),
        (3, "awaiting_human_review"),
        (4, "human_confirmed"),
    ]


async def test_repository_transactions_run_in_worker_threads_with_fresh_connections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reviews.sqlite3"
    event_loop_thread = threading.get_ident()
    insert_threads: list[int] = []

    def record_thread(_stage: str) -> None:
        insert_threads.append(threading.get_ident())

    repository = SQLiteReviewRepository(database, failure_injector=record_thread)
    await repository.initialize()
    await repository.create_case(_create_command())

    assert insert_threads
    assert all(thread_id != event_loop_thread for thread_id in insert_threads)
    with connect_database(database) as first, connect_database(database) as second:
        assert first is not second
        assert first.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert second.execute("PRAGMA busy_timeout").fetchone()[0] > 0


def test_schema_foreign_keys_preserve_audit_history(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    repository = SQLiteReviewRepository(database)
    asyncio.run(repository.initialize())
    asyncio.run(repository.create_case(_create_command()))

    with (
        connect_database(database) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute("DELETE FROM review_cases WHERE case_id = 'case-review-1'")


async def test_idempotency_preflight_replays_conflicts_and_sanitizes_corrupt_type(
    tmp_path: Path,
) -> None:
    database = tmp_path / "preflight.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    command = _create_command()
    created = await repository.create_case(command)

    replay = await repository.replay_detail(
        command.idempotency_scope,
        command.idempotency_key,
        command.request_sha256,
        result_type="review_case",
    )
    assert replay == created
    assert (
        await repository.replay_detail(
            command.idempotency_scope,
            "missing-key",
            command.request_sha256,
            result_type="review_case",
        )
        is None
    )
    with pytest.raises(ReviewConflictError, match="idempotency"):
        await repository.replay_detail(
            command.idempotency_scope,
            command.idempotency_key,
            "f" * 64,
            result_type="review_case",
        )

    with connect_database(database) as connection:
        connection.execute(
            "UPDATE idempotency_keys SET result_type = ? WHERE scope = ? AND idempotency_key = ?",
            ("private-corrupt-type", command.idempotency_scope, command.idempotency_key),
        )
        connection.commit()
    with pytest.raises(ReviewPersistenceError) as captured:
        await repository.replay_detail(
            command.idempotency_scope,
            command.idempotency_key,
            command.request_sha256,
            result_type="review_case",
        )
    assert str(captured.value) == "stored review data is invalid"
    assert "private" not in str(captured.value)
