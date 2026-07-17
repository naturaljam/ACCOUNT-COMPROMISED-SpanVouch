import asyncio
import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TypeVar, cast

from pydantic import BaseModel, ValidationError

from afc.diagnosis.models import (
    DiagnoserKind,
    DiagnosisProvenance,
    DiagnosisReport,
    EvidenceSelector,
)
from afc.review.commands import (
    AppendDiagnosisRevision,
    AppendVerifierRun,
    ApplyHumanDecision,
    ClaimReviewWork,
    CreateReviewCase,
    RouteRevisionFailureToHuman,
    RouteToHumanReview,
    TransitionCommand,
)
from afc.review.errors import (
    ReviewConflictError,
    ReviewError,
    ReviewNotFoundError,
    ReviewPersistenceError,
    ReviewSchemaError,
)
from afc.review.models import (
    CorrectionClaim,
    DecisionAction,
    DiagnosisCorrectionDraft,
    DiagnosisReviewCase,
    DiagnosisReviewDetail,
    DiagnosisRevision,
    HumanReviewDecision,
    ReviewInputSnapshot,
    ReviewRuntimeBundle,
    ReviewStatus,
    RevisionOrigin,
    VerificationMode,
    VerifierKind,
    VerifierReport,
    VerifierVerdict,
    WorkflowEvent,
    WorkflowEventType,
    canonical_json,
)
from afc.review.schema import connect_database, initialize_database

FailureInjector = Callable[[str], None]
CommandT = TypeVar("CommandT", bound=BaseModel)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("stored timestamp must be aware UTC")
    return parsed


class SQLiteReviewRepository:
    """SQLite-backed authoritative store for review use-case transactions."""

    def __init__(
        self,
        database: str | Path,
        *,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        value = os.fspath(database)
        if value == ":memory:" or value.startswith("file:"):
            raise ValueError(
                "review database must be a filesystem path; "
                "SQLite memory databases and file: URIs are unsupported"
            )
        self._database = Path(value)
        self._failure_injector = failure_injector

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def create_case(self, command: CreateReviewCase) -> DiagnosisReviewDetail:
        command = self._revalidate_command(
            CreateReviewCase, command, "invalid create review command"
        )
        return await asyncio.to_thread(self._create_case, command)

    async def reserve_create(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        *,
        reservation_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DiagnosisReviewDetail | None:
        return await asyncio.to_thread(
            self._reserve_create,
            scope,
            idempotency_key,
            request_sha256,
            reservation_id,
            now,
            lease_expires_at,
        )

    async def renew_create_reservation(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        *,
        reservation_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._renew_create_reservation,
            scope,
            idempotency_key,
            request_sha256,
            reservation_id,
            now,
            lease_expires_at,
        )

    async def replay_detail(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        *,
        result_type: str,
    ) -> DiagnosisReviewDetail | None:
        return await asyncio.to_thread(
            self._replay_detail,
            scope,
            idempotency_key,
            request_sha256,
            result_type,
        )

    async def get_detail(self, case_id: str) -> DiagnosisReviewDetail:
        return await asyncio.to_thread(self._get_detail, case_id)

    async def load_runtime(self, case_id: str) -> ReviewRuntimeBundle:
        return await asyncio.to_thread(self._load_runtime, case_id)

    async def claim_work(self, command: ClaimReviewWork) -> DiagnosisReviewCase:
        command = self._revalidate_command(
            ClaimReviewWork, command, "invalid claim review command"
        )
        return await asyncio.to_thread(self._claim_work, command)

    async def append_verifier_run(self, command: AppendVerifierRun) -> DiagnosisReviewCase:
        command = self._revalidate_command(
            AppendVerifierRun, command, "invalid append verifier command"
        )
        return await asyncio.to_thread(self._append_verifier_run, command)

    async def append_revision(self, command: AppendDiagnosisRevision) -> DiagnosisReviewCase:
        command = self._revalidate_command(
            AppendDiagnosisRevision, command, "invalid append revision command"
        )
        return await asyncio.to_thread(self._append_revision, command)

    async def route_to_human(self, command: RouteToHumanReview) -> DiagnosisReviewCase:
        command = self._revalidate_command(
            RouteToHumanReview, command, "invalid route to human command"
        )
        return await asyncio.to_thread(self._route_to_human, command)

    async def route_revision_failure(
        self, command: RouteRevisionFailureToHuman
    ) -> DiagnosisReviewCase:
        command = self._revalidate_command(
            RouteRevisionFailureToHuman,
            command,
            "invalid revision failure command",
        )
        return await asyncio.to_thread(self._route_revision_failure, command)

    async def apply_human_decision(
        self, command: ApplyHumanDecision
    ) -> DiagnosisReviewDetail:
        command = self._revalidate_command(
            ApplyHumanDecision, command, "invalid human decision command"
        )
        return await asyncio.to_thread(self._apply_human_decision, command)

    def _initialize(self) -> None:
        try:
            initialize_database(self._database)
        except ReviewSchemaError:
            raise
        except sqlite3.Error:
            raise ReviewPersistenceError("review persistence operation failed") from None

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        try:
            connection = connect_database(self._database)
        except sqlite3.Error:
            raise ReviewPersistenceError("review persistence operation failed") from None
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            raise ReviewConflictError("review persistence constraint conflict") from None
        except sqlite3.OperationalError:
            connection.rollback()
            raise ReviewPersistenceError("review persistence operation failed") from None
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _after_insert(self, stage: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(stage)

    def _reserve_create(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        reservation_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DiagnosisReviewDetail | None:
        if lease_expires_at <= now:
            raise ReviewConflictError("idempotency reservation lease must be positive")
        with self._transaction(write=True) as connection:
            row = self._idempotency_row(connection, scope, idempotency_key)
            if row is None:
                connection.execute(
                    "INSERT INTO idempotency_keys("
                    "scope, idempotency_key, request_sha256, result_type, result_id, "
                    "reservation_id, lease_expires_at, created_at, updated_at"
                    ") VALUES (?, ?, ?, 'review_case', NULL, ?, ?, ?, ?)",
                    (
                        scope,
                        idempotency_key,
                        request_sha256,
                        reservation_id,
                        _timestamp(lease_expires_at),
                        _timestamp(now),
                        _timestamp(now),
                    ),
                )
                return None
            if str(row["request_sha256"]) != request_sha256:
                raise ReviewConflictError("idempotency key conflict")
            if str(row["result_type"]) != "review_case":
                raise ReviewPersistenceError("stored review data is invalid")
            result_id = row["result_id"]
            if result_id is not None:
                try:
                    return self._read_detail(connection, str(result_id))
                except ReviewNotFoundError:
                    raise ReviewPersistenceError("stored review data is invalid") from None
            stored_lease = row["lease_expires_at"]
            if stored_lease is None:
                raise ReviewPersistenceError("stored review data is invalid")
            if _parse_timestamp(str(stored_lease)) > now:
                raise ReviewConflictError("idempotency request is in progress")
            cursor = connection.execute(
                "UPDATE idempotency_keys SET reservation_id = ?, lease_expires_at = ?, "
                "updated_at = ? WHERE scope = ? AND idempotency_key = ? "
                "AND request_sha256 = ? AND result_id IS NULL",
                (
                    reservation_id,
                    _timestamp(lease_expires_at),
                    _timestamp(now),
                    scope,
                    idempotency_key,
                    request_sha256,
                ),
            )
            self._require_updated(cursor)
            return None

    def _renew_create_reservation(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        reservation_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        if lease_expires_at <= now:
            raise ReviewConflictError("idempotency reservation lease must be positive")
        with self._transaction(write=True) as connection:
            row = self._idempotency_row(connection, scope, idempotency_key)
            if row is None:
                raise ReviewConflictError("idempotency reservation is missing")
            if str(row["request_sha256"]) != request_sha256:
                raise ReviewConflictError("idempotency key conflict")
            if str(row["result_type"]) != "review_case":
                raise ReviewPersistenceError("stored review data is invalid")
            if row["result_id"] is not None or str(row["reservation_id"]) != reservation_id:
                raise ReviewConflictError("idempotency reservation is not owned")
            cursor = connection.execute(
                "UPDATE idempotency_keys SET lease_expires_at = ?, updated_at = ? "
                "WHERE scope = ? AND idempotency_key = ? AND request_sha256 = ? "
                "AND result_type = 'review_case' AND result_id IS NULL "
                "AND reservation_id = ?",
                (
                    _timestamp(lease_expires_at),
                    _timestamp(now),
                    scope,
                    idempotency_key,
                    request_sha256,
                    reservation_id,
                ),
            )
            self._require_updated(cursor)

    def _create_case(self, command: CreateReviewCase) -> DiagnosisReviewDetail:
        with self._transaction(write=True) as connection:
            idempotency_row = self._idempotency_row(
                connection, command.idempotency_scope, command.idempotency_key
            )
            if idempotency_row is not None:
                if str(idempotency_row["request_sha256"]) != command.request_sha256:
                    raise ReviewConflictError("idempotency key conflict")
                if str(idempotency_row["result_type"]) != "review_case":
                    raise ReviewPersistenceError("stored review data is invalid")
                result_id = idempotency_row["result_id"]
                if result_id is not None:
                    return self._read_detail(connection, str(result_id))
                if (
                    command.idempotency_reservation_id is None
                    or str(idempotency_row["reservation_id"])
                    != command.idempotency_reservation_id
                ):
                    raise ReviewConflictError("idempotency reservation is not owned")
            elif command.idempotency_reservation_id is not None:
                raise ReviewConflictError("idempotency reservation is missing")

            connection.execute(
                "INSERT INTO review_cases("
                "case_id, status, version, verification_mode, diagnoser, "
                "current_revision_number, evidence_revision_count, created_at, updated_at"
                ") VALUES (?, ?, 0, ?, ?, 0, 0, ?, ?)",
                (
                    command.case_id,
                    command.target_status.value,
                    command.verification_mode.value,
                    command.diagnoser.value,
                    _timestamp(command.created_at),
                    _timestamp(command.created_at),
                ),
            )
            self._after_insert("review_case")
            snapshot = command.snapshot
            connection.execute(
                "INSERT INTO review_inputs("
                "case_id, trace_id, run_id, view_json, input_sha256, catalog_version, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    command.case_id,
                    snapshot.trace_id,
                    snapshot.run_id,
                    snapshot.view_json,
                    snapshot.input_sha256,
                    snapshot.catalog_version,
                    _timestamp(snapshot.created_at),
                ),
            )
            self._after_insert("review_input")
            self._insert_revision(connection, command.initial_revision)
            self._after_insert("diagnosis_revision")
            self._insert_event(
                connection,
                case_id=command.case_id,
                event_id=command.event_id,
                event_type=command.event_type.value,
                from_status=None,
                to_status=command.target_status,
                case_version=0,
                metadata_json=command.event_metadata_json,
                created_at=command.created_at,
            )
            self._after_insert("workflow_event")
            if idempotency_row is None:
                self._insert_idempotency(
                    connection,
                    scope=command.idempotency_scope,
                    key=command.idempotency_key,
                    fingerprint=command.request_sha256,
                    result_type="review_case",
                    result_id=command.case_id,
                    created_at=command.created_at,
                )
            else:
                cursor = connection.execute(
                    "UPDATE idempotency_keys SET result_id = ?, reservation_id = NULL, "
                    "lease_expires_at = NULL, updated_at = ? "
                    "WHERE scope = ? AND idempotency_key = ? AND request_sha256 = ? "
                    "AND reservation_id = ? AND result_id IS NULL",
                    (
                        command.case_id,
                        _timestamp(command.created_at),
                        command.idempotency_scope,
                        command.idempotency_key,
                        command.request_sha256,
                        command.idempotency_reservation_id,
                    ),
                )
                self._require_updated(cursor)
            self._after_insert("idempotency_key")
            return self._read_detail(connection, command.case_id)

    def _get_detail(self, case_id: str) -> DiagnosisReviewDetail:
        with self._transaction(write=False) as connection:
            return self._read_detail(connection, case_id)

    def _replay_detail(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        result_type: str,
    ) -> DiagnosisReviewDetail | None:
        with self._transaction(write=False) as connection:
            replay = self._idempotency_replay(connection, scope, idempotency_key)
            if replay is None:
                return None
            fingerprint, stored_result_type, result_id = replay
            if fingerprint != request_sha256:
                raise ReviewConflictError("idempotency key conflict")
            if stored_result_type != result_type:
                raise ReviewPersistenceError("stored review data is invalid")
            try:
                return self._read_detail(connection, result_id)
            except ReviewNotFoundError:
                raise ReviewPersistenceError("stored review data is invalid") from None

    def _load_runtime(self, case_id: str) -> ReviewRuntimeBundle:
        with self._transaction(write=False) as connection:
            try:
                case = self._read_case(connection, case_id)
                snapshot = self._read_snapshot(connection, case_id)
                revisions = self._read_revisions(connection, case_id)
                reports = self._read_verifier_reports(connection, case_id)
                return ReviewRuntimeBundle(
                    case=case,
                    snapshot=snapshot,
                    revisions=revisions,
                    verifier_reports=reports,
                )
            except ReviewError:
                raise
            except (KeyError, TypeError, ValueError):
                raise ReviewPersistenceError("stored review data is invalid") from None

    def _claim_work(self, command: ClaimReviewWork) -> DiagnosisReviewCase:
        with self._transaction(write=True) as connection:
            self._require_valid_transition(command)
            if self._event_exists(connection, command.event_id):
                if self._event_matches(
                    connection,
                    event_id=command.event_id,
                    command=command,
                    case_version=command.expected_version + 1,
                ):
                    state = self._require_state(connection, command.case_id)
                    lease_owner, lease_expires_at = self._decode_lease(state)
                    if (
                        int(state["version"]) == command.expected_version + 1
                        and str(state["status"]) == command.target_status.value
                        and lease_owner == command.lease_owner
                        and lease_expires_at == command.lease_expires_at
                    ):
                        return self._read_case(connection, command.case_id)
                    raise ReviewConflictError("duplicate work claim")
                raise ReviewConflictError("duplicate workflow event")
            state = self._require_state(connection, command.case_id)
            self._require_cas(state, command.expected_version, command.prior_status)
            _, lease_expires_at = self._decode_lease(state)
            if lease_expires_at is not None and command.now < lease_expires_at:
                raise ReviewConflictError("lease is still active")
            cursor = connection.execute(
                "UPDATE review_cases SET status = ?, version = version + 1, lease_owner = ?, "
                "lease_expires_at = ?, updated_at = ? "
                "WHERE case_id = ? AND version = ? AND status = ? "
                "AND (lease_expires_at IS NULL OR lease_expires_at <= ?)",
                (
                    command.target_status.value,
                    command.lease_owner,
                    _timestamp(command.lease_expires_at),
                    _timestamp(command.occurred_at),
                    command.case_id,
                    command.expected_version,
                    command.prior_status.value,
                    _timestamp(command.now),
                ),
            )
            self._require_updated(cursor)
            self._insert_transition_event(connection, command)
            return self._read_case(connection, command.case_id)

    def _append_verifier_run(self, command: AppendVerifierRun) -> DiagnosisReviewCase:
        with self._transaction(write=True) as connection:
            self._require_valid_transition(command)
            existing = connection.execute(
                "SELECT report_json FROM verifier_runs "
                "WHERE case_id = ? AND verifier_run_id = ?",
                (command.case_id, command.report.verifier_run_id),
            ).fetchone()
            event_exists = self._event_exists(connection, command.event_id)
            if existing is not None:
                if (
                    str(existing["report_json"]) != canonical_json(command.report)
                    or not event_exists
                    or not self._event_matches(
                        connection,
                        event_id=command.event_id,
                        command=command,
                        case_version=command.expected_version + 1,
                    )
                ):
                    raise ReviewConflictError("duplicate verifier result")
                state = self._require_state(connection, command.case_id)
                run_column = (
                    "deterministic_run_id"
                    if command.report.verifier_kind is VerifierKind.DETERMINISTIC
                    else "semantic_run_id"
                )
                if (
                    int(state["version"]) != command.expected_version + 1
                    or str(state["status"]) != command.target_status.value
                    or state[run_column] != command.report.verifier_run_id
                    or state["composite_verdict"] != command.composite_verdict.value
                ):
                    raise ReviewConflictError("duplicate verifier result")
                return self._read_case(connection, command.case_id)
            if event_exists:
                raise ReviewConflictError("duplicate workflow event")

            state = self._require_state(connection, command.case_id)
            self._require_cas(state, command.expected_version, command.prior_status)
            if command.report.revision_number != int(state["current_revision_number"]):
                raise ReviewConflictError("verifier revision conflict")
            current_revision = self._revision_row(
                connection, command.case_id, int(state["current_revision_number"])
            )
            if command.report.report_sha256 != str(current_revision["report_sha256"]):
                raise ReviewConflictError("verifier report binding conflict")
            if (
                str(state["verification_mode"]) == VerificationMode.DETERMINISTIC.value
                and command.report.verifier_kind is VerifierKind.SEMANTIC
            ):
                raise ReviewConflictError("verifier mode conflict")
            if (
                command.target_status is ReviewStatus.REVISION_REQUESTED
                and int(state["evidence_revision_count"]) >= 1
            ):
                raise ReviewConflictError("evidence revision limit reached")
            self._insert_verifier_report(connection, command.report, command.case_id)
            self._after_insert("verifier_run")
            deterministic_run_id = (
                command.report.verifier_run_id
                if command.report.verifier_kind is VerifierKind.DETERMINISTIC
                else None
            )
            semantic_run_id = (
                command.report.verifier_run_id
                if command.report.verifier_kind is VerifierKind.SEMANTIC
                else None
            )
            cursor = connection.execute(
                "UPDATE review_cases SET status = ?, version = version + 1, "
                "deterministic_run_id = COALESCE(?, deterministic_run_id), "
                "semantic_run_id = COALESCE(?, semantic_run_id), composite_verdict = ?, "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE case_id = ? AND version = ? AND status = ?",
                (
                    command.target_status.value,
                    deterministic_run_id,
                    semantic_run_id,
                    command.composite_verdict.value,
                    _timestamp(command.occurred_at),
                    command.case_id,
                    command.expected_version,
                    command.prior_status.value,
                ),
            )
            self._require_updated(cursor)
            self._insert_transition_event(connection, command)
            return self._read_case(connection, command.case_id)

    def _append_revision(self, command: AppendDiagnosisRevision) -> DiagnosisReviewCase:
        with self._transaction(write=True) as connection:
            self._require_valid_transition(command)
            existing = connection.execute(
                "SELECT * FROM diagnosis_revisions WHERE revision_id = ?",
                (command.revision.revision_id,),
            ).fetchone()
            event_exists = self._event_exists(connection, command.event_id)
            if existing is not None:
                if (
                    self._decode_revision(existing) != command.revision
                    or not event_exists
                    or not self._event_matches(
                        connection,
                        event_id=command.event_id,
                        command=command,
                        case_version=command.expected_version + 1,
                    )
                ):
                    raise ReviewConflictError("duplicate diagnosis revision")
                state = self._require_state(connection, command.case_id)
                if (
                    int(state["version"]) != command.expected_version + 1
                    or str(state["status"]) != command.target_status.value
                    or int(state["current_revision_number"])
                    != command.revision.revision_number
                    or state["deterministic_run_id"] is not None
                    or state["semantic_run_id"] is not None
                    or state["composite_verdict"] is not None
                ):
                    raise ReviewConflictError("duplicate diagnosis revision")
                return self._read_case(connection, command.case_id)
            if event_exists:
                raise ReviewConflictError("duplicate workflow event")

            state = self._require_state(connection, command.case_id)
            self._require_cas(state, command.expected_version, command.prior_status)
            self._require_revision_snapshot_binding(
                connection, command.case_id, command.revision
            )
            if int(state["evidence_revision_count"]) >= 1:
                raise ReviewConflictError("evidence revision limit reached")
            expected_revision_number = int(state["current_revision_number"]) + 1
            if command.revision.revision_number != expected_revision_number:
                raise ReviewConflictError("diagnosis revision sequence conflict")
            previous = self._revision_row(
                connection, command.case_id, int(state["current_revision_number"])
            )
            if command.revision.previous_report_sha256 != str(previous["report_sha256"]):
                raise ReviewConflictError("diagnosis revision chain conflict")
            self._insert_revision(connection, command.revision)
            self._after_insert("diagnosis_revision")
            cursor = connection.execute(
                "UPDATE review_cases SET status = ?, version = version + 1, "
                "current_revision_number = ?, evidence_revision_count = 1, "
                "deterministic_run_id = NULL, semantic_run_id = NULL, "
                "composite_verdict = NULL, "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE case_id = ? AND version = ? AND status = ?",
                (
                    command.target_status.value,
                    command.revision.revision_number,
                    _timestamp(command.occurred_at),
                    command.case_id,
                    command.expected_version,
                    command.prior_status.value,
                ),
            )
            self._require_updated(cursor)
            self._insert_transition_event(connection, command)
            return self._read_case(connection, command.case_id)

    def _route_to_human(self, command: RouteToHumanReview) -> DiagnosisReviewCase:
        with self._transaction(write=True) as connection:
            self._require_valid_transition(command)
            if self._event_exists(connection, command.event_id):
                if self._event_matches(
                    connection,
                    event_id=command.event_id,
                    command=command,
                    case_version=command.expected_version + 1,
                ):
                    return self._read_case(connection, command.case_id)
                raise ReviewConflictError("duplicate workflow event")
            state = self._require_state(connection, command.case_id)
            self._require_cas(state, command.expected_version, command.prior_status)
            verified_current_revision = connection.execute(
                "SELECT 1 FROM verifier_runs WHERE case_id = ? AND revision_number = ? LIMIT 1",
                (command.case_id, int(state["current_revision_number"])),
            ).fetchone()
            if verified_current_revision is None:
                raise ReviewConflictError("verification required before human review")
            cursor = connection.execute(
                "UPDATE review_cases SET status = ?, version = version + 1, "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE case_id = ? AND version = ? AND status = ?",
                (
                    command.target_status.value,
                    _timestamp(command.occurred_at),
                    command.case_id,
                    command.expected_version,
                    command.prior_status.value,
                ),
            )
            self._require_updated(cursor)
            self._insert_transition_event(connection, command)
            return self._read_case(connection, command.case_id)

    def _route_revision_failure(
        self, command: RouteRevisionFailureToHuman
    ) -> DiagnosisReviewCase:
        with self._transaction(write=True) as connection:
            self._require_valid_transition(command)
            if self._event_exists(connection, command.event_id):
                if self._event_matches(
                    connection,
                    event_id=command.event_id,
                    command=command,
                    case_version=command.expected_version + 1,
                ):
                    state = self._require_state(connection, command.case_id)
                    if (
                        int(state["version"]) == command.expected_version + 1
                        and str(state["status"]) == command.target_status.value
                        and state["composite_verdict"] == command.composite_verdict.value
                        and state["lease_owner"] is None
                        and state["lease_expires_at"] is None
                    ):
                        return self._read_case(connection, command.case_id)
                raise ReviewConflictError("duplicate workflow event")
            state = self._require_state(connection, command.case_id)
            self._require_cas(state, command.expected_version, command.prior_status)
            cursor = connection.execute(
                "UPDATE review_cases SET status = ?, version = version + 1, "
                "composite_verdict = ?, lease_owner = NULL, lease_expires_at = NULL, "
                "updated_at = ? WHERE case_id = ? AND version = ? AND status = ?",
                (
                    command.target_status.value,
                    command.composite_verdict.value,
                    _timestamp(command.occurred_at),
                    command.case_id,
                    command.expected_version,
                    command.prior_status.value,
                ),
            )
            self._require_updated(cursor)
            self._insert_transition_event(connection, command)
            return self._read_case(connection, command.case_id)

    def _apply_human_decision(self, command: ApplyHumanDecision) -> DiagnosisReviewDetail:
        with self._transaction(write=True) as connection:
            self._require_valid_transition(command)
            replay = self._idempotency_replay(
                connection, command.idempotency_scope, command.idempotency_key
            )
            if replay is not None:
                fingerprint, result_type, result_id = replay
                if fingerprint != command.request_sha256:
                    raise ReviewConflictError("idempotency key conflict")
                if result_type != "review_detail":
                    raise ReviewPersistenceError("stored review data is invalid")
                return self._read_detail(connection, result_id)
            if self._event_exists(connection, command.event_id):
                raise ReviewConflictError("duplicate workflow event")

            state = self._require_state(connection, command.case_id)
            self._require_cas(state, command.expected_version, command.prior_status)
            correction_number: int | None = None
            correction_verifier_run_id: str | None = None
            if command.correction_revision is not None:
                correction = command.correction_revision
                correction_verifier = command.correction_verifier_report
                if correction_verifier is None:
                    raise ReviewConflictError("human correction verification missing")
                self._require_revision_snapshot_binding(
                    connection, command.case_id, correction
                )
                if command.decision.correction != self._correction_from_revision(correction):
                    raise ReviewConflictError("human correction revision conflict")
                correction_number = correction.revision_number
                expected_revision_number = int(state["current_revision_number"]) + 1
                if correction_number != expected_revision_number:
                    raise ReviewConflictError("diagnosis revision sequence conflict")
                previous = self._revision_row(
                    connection, command.case_id, int(state["current_revision_number"])
                )
                if correction.previous_report_sha256 != str(previous["report_sha256"]):
                    raise ReviewConflictError("diagnosis revision chain conflict")
                self._insert_revision(connection, correction)
                self._after_insert("diagnosis_revision")
                if correction_verifier.report_sha256 != correction.report_sha256:
                    raise ReviewConflictError("human correction verification binding conflict")
                existing_verifier = connection.execute(
                    "SELECT 1 FROM verifier_runs "
                    "WHERE case_id = ? AND verifier_run_id = ?",
                    (command.case_id, correction_verifier.verifier_run_id),
                ).fetchone()
                if existing_verifier is not None:
                    raise ReviewConflictError("duplicate verifier result")
                self._insert_verifier_report(
                    connection, correction_verifier, command.case_id
                )
                self._after_insert("verifier_run")
                correction_verifier_run_id = correction_verifier.verifier_run_id

            decision = command.decision
            connection.execute(
                "INSERT INTO human_decisions("
                "decision_id, case_id, action, reviewer_label, reason, expected_version, "
                "correction_revision_number, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    command.case_id,
                    decision.action.value,
                    decision.reviewer_label,
                    decision.reason,
                    decision.expected_version,
                    correction_number,
                    _timestamp(decision.created_at),
                ),
            )
            self._after_insert("human_decision")
            cursor = connection.execute(
                "UPDATE review_cases SET status = ?, version = version + 1, "
                "current_revision_number = COALESCE(?, current_revision_number), "
                "deterministic_run_id = COALESCE(?, deterministic_run_id), "
                "semantic_run_id = CASE WHEN ? IS NULL THEN semantic_run_id ELSE NULL END, "
                "composite_verdict = COALESCE(?, composite_verdict), "
                "terminal_decision_id = ?, lease_owner = NULL, lease_expires_at = NULL, "
                "updated_at = ? WHERE case_id = ? AND version = ? AND status = ?",
                (
                    command.target_status.value,
                    correction_number,
                    correction_verifier_run_id,
                    correction_verifier_run_id,
                    (
                        VerifierVerdict.VERIFIED.value
                        if correction_verifier_run_id is not None
                        else None
                    ),
                    decision.decision_id,
                    _timestamp(command.occurred_at),
                    command.case_id,
                    command.expected_version,
                    command.prior_status.value,
                ),
            )
            self._require_updated(cursor)
            self._insert_transition_event(connection, command)
            self._insert_idempotency(
                connection,
                scope=command.idempotency_scope,
                key=command.idempotency_key,
                fingerprint=command.request_sha256,
                result_type="review_detail",
                result_id=command.case_id,
                created_at=command.occurred_at,
            )
            self._after_insert("idempotency_key")
            return self._read_detail(connection, command.case_id)

    @staticmethod
    def _revalidate_command(
        model: type[CommandT], command: BaseModel, message: str
    ) -> CommandT:
        try:
            return model.model_validate(command.model_dump())
        except (TypeError, ValidationError):
            raise ReviewConflictError(message) from None

    @staticmethod
    def _require_valid_transition(command: TransitionCommand) -> None:
        try:
            command.require_valid_transition()
        except ValueError as error:
            raise ReviewConflictError(str(error)) from None

    @staticmethod
    def _idempotency_row(
        connection: sqlite3.Connection, scope: str, key: str
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM idempotency_keys "
                "WHERE scope = ? AND idempotency_key = ?",
                (scope, key),
            ).fetchone(),
        )

    @classmethod
    def _idempotency_replay(
        cls, connection: sqlite3.Connection, scope: str, key: str
    ) -> tuple[str, str, str] | None:
        row = cls._idempotency_row(connection, scope, key)
        if row is None:
            return None
        result_id = row["result_id"]
        if result_id is None:
            raise ReviewConflictError("idempotency request is in progress")
        return (
            str(row["request_sha256"]),
            str(row["result_type"]),
            str(result_id),
        )

    @staticmethod
    def _insert_idempotency(
        connection: sqlite3.Connection,
        *,
        scope: str,
        key: str,
        fingerprint: str,
        result_type: str,
        result_id: str,
        created_at: datetime,
    ) -> None:
        connection.execute(
            "INSERT INTO idempotency_keys("
            "scope, idempotency_key, request_sha256, result_type, result_id, "
            "reservation_id, lease_expires_at, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
            (
                scope,
                key,
                fingerprint,
                result_type,
                result_id,
                _timestamp(created_at),
                _timestamp(created_at),
            ),
        )

    @staticmethod
    def _require_state(connection: sqlite3.Connection, case_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM review_cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise ReviewNotFoundError("review case not found")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _decode_lease(state: sqlite3.Row) -> tuple[str | None, datetime | None]:
        owner_value = state["lease_owner"]
        expiry_value = state["lease_expires_at"]
        parsed_expiry: datetime | None = None
        if expiry_value is not None:
            try:
                parsed_expiry = _parse_timestamp(str(expiry_value))
            except ValueError:
                raise ReviewPersistenceError("stored review data is invalid") from None
        if (owner_value is None) != (expiry_value is None):
            raise ReviewPersistenceError("stored review data is invalid")
        owner = str(owner_value) if owner_value is not None else None
        return owner, parsed_expiry

    @staticmethod
    def _require_cas(state: sqlite3.Row, version: int, status: ReviewStatus) -> None:
        if int(state["version"]) != version or str(state["status"]) != status.value:
            raise ReviewConflictError("compare-and-swap conflict")

    @staticmethod
    def _require_updated(cursor: sqlite3.Cursor) -> None:
        if cursor.rowcount != 1:
            raise ReviewConflictError("compare-and-swap conflict")

    @staticmethod
    def _revision_row(
        connection: sqlite3.Connection, case_id: str, revision_number: int
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM diagnosis_revisions WHERE case_id = ? AND revision_number = ?",
            (case_id, revision_number),
        ).fetchone()
        if row is None:
            raise ReviewPersistenceError("stored review data is invalid")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _require_revision_snapshot_binding(
        connection: sqlite3.Connection,
        case_id: str,
        revision: DiagnosisRevision,
    ) -> None:
        row = connection.execute(
            "SELECT trace_id, run_id FROM review_inputs WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise ReviewPersistenceError("stored review data is invalid")
        if (revision.report.trace_id, revision.report.run_id) != (
            str(row["trace_id"]),
            str(row["run_id"]),
        ):
            raise ReviewConflictError("diagnosis revision snapshot binding conflict")

    @staticmethod
    def _insert_revision(connection: sqlite3.Connection, revision: DiagnosisRevision) -> None:
        connection.execute(
            "INSERT INTO diagnosis_revisions("
            "revision_id, case_id, revision_number, origin, previous_report_sha256, "
            "report_json, report_sha256, triggering_gap_ids_json, provenance_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision.revision_id,
                revision.case_id,
                revision.revision_number,
                revision.origin.value,
                revision.previous_report_sha256,
                canonical_json(revision.report),
                revision.report_sha256,
                canonical_json(list(revision.triggering_gap_ids)),
                canonical_json(revision.provenance),
                _timestamp(revision.created_at),
            ),
        )

    @staticmethod
    def _insert_verifier_report(
        connection: sqlite3.Connection, report: VerifierReport, case_id: str
    ) -> None:
        connection.execute(
            "INSERT INTO verifier_runs("
            "verifier_run_id, case_id, revision_number, verifier_kind, report_json, verdict, "
            "usage_json, operational_error_json, started_at, completed_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report.verifier_run_id,
                case_id,
                report.revision_number,
                report.verifier_kind.value,
                canonical_json(report),
                report.verdict.value,
                canonical_json(report.usage) if report.usage is not None else None,
                canonical_json(report.operational_error)
                if report.operational_error is not None
                else None,
                _timestamp(report.started_at),
                _timestamp(report.completed_at),
            ),
        )

    def _insert_transition_event(
        self, connection: sqlite3.Connection, command: TransitionCommand
    ) -> None:
        self._insert_event(
            connection,
            case_id=command.case_id,
            event_id=command.event_id,
            event_type=command.event_type.value,
            from_status=command.prior_status,
            to_status=command.target_status,
            case_version=command.expected_version + 1,
            metadata_json=command.event_metadata_json,
            created_at=command.occurred_at,
        )
        self._after_insert("workflow_event")

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        case_id: str,
        event_id: str,
        event_type: str,
        from_status: ReviewStatus | None,
        to_status: ReviewStatus,
        case_version: int,
        metadata_json: str,
        created_at: datetime,
    ) -> None:
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(event_sequence), -1) + 1 FROM workflow_events "
                "WHERE case_id = ?",
                (case_id,),
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO workflow_events("
            "event_id, case_id, event_sequence, event_type, from_status, to_status, "
            "case_version, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                case_id,
                sequence,
                event_type,
                from_status.value if from_status is not None else None,
                to_status.value,
                case_version,
                metadata_json,
                _timestamp(created_at),
            ),
        )

    @staticmethod
    def _event_exists(connection: sqlite3.Connection, event_id: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM workflow_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _event_matches(
        connection: sqlite3.Connection,
        *,
        event_id: str,
        command: TransitionCommand,
        case_version: int,
    ) -> bool:
        row = connection.execute(
            "SELECT * FROM workflow_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None and (
            str(row["case_id"]),
            str(row["event_type"]),
            str(row["from_status"]) if row["from_status"] is not None else None,
            str(row["to_status"]),
            int(row["case_version"]),
            str(row["metadata_json"]),
            str(row["created_at"]),
        ) == (
            command.case_id,
            command.event_type.value,
            command.prior_status.value,
            command.target_status.value,
            case_version,
            command.event_metadata_json,
            _timestamp(command.occurred_at),
        )

    def _read_detail(
        self, connection: sqlite3.Connection, case_id: str
    ) -> DiagnosisReviewDetail:
        try:
            return DiagnosisReviewDetail(
                case=self._read_case(connection, case_id),
                revisions=self._read_revisions(connection, case_id),
                verifier_reports=self._read_verifier_reports(connection, case_id),
                events=self._read_events(connection, case_id),
                decision=self._read_decision(connection, case_id),
            )
        except ReviewError:
            raise
        except (KeyError, TypeError, ValueError):
            raise ReviewPersistenceError("stored review data is invalid") from None

    @staticmethod
    def _read_case(connection: sqlite3.Connection, case_id: str) -> DiagnosisReviewCase:
        row = connection.execute(
            "SELECT c.*, i.trace_id, i.run_id FROM review_cases AS c "
            "JOIN review_inputs AS i ON i.case_id = c.case_id WHERE c.case_id = ?",
            (case_id,),
        ).fetchone()
        if row is None:
            raise ReviewNotFoundError("review case not found")
        try:
            return DiagnosisReviewCase(
                case_id=str(row["case_id"]),
                trace_id=str(row["trace_id"]),
                run_id=str(row["run_id"]),
                status=ReviewStatus(str(row["status"])),
                version=int(row["version"]),
                verification_mode=VerificationMode(str(row["verification_mode"])),
                diagnoser=DiagnoserKind(str(row["diagnoser"])),
                current_revision_number=int(row["current_revision_number"]),
                evidence_revision_count=int(row["evidence_revision_count"]),
                deterministic_run_id=row["deterministic_run_id"],
                semantic_run_id=row["semantic_run_id"],
                composite_verdict=(
                    VerifierVerdict(str(row["composite_verdict"]))
                    if row["composite_verdict"] is not None
                    else None
                ),
                terminal_decision_id=row["terminal_decision_id"],
                created_at=_parse_timestamp(str(row["created_at"])),
                updated_at=_parse_timestamp(str(row["updated_at"])),
            )
        except (KeyError, TypeError, ValueError):
            raise ReviewPersistenceError("stored review data is invalid") from None

    @staticmethod
    def _read_snapshot(connection: sqlite3.Connection, case_id: str) -> ReviewInputSnapshot:
        row = connection.execute(
            "SELECT * FROM review_inputs WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise ReviewPersistenceError("stored review data is invalid")
        return ReviewInputSnapshot(
            trace_id=str(row["trace_id"]),
            run_id=str(row["run_id"]),
            view_json=str(row["view_json"]),
            input_sha256=str(row["input_sha256"]),
            catalog_version=str(row["catalog_version"]),
            created_at=_parse_timestamp(str(row["created_at"])),
        )

    def _read_revisions(
        self, connection: sqlite3.Connection, case_id: str
    ) -> tuple[DiagnosisRevision, ...]:
        rows = connection.execute(
            "SELECT * FROM diagnosis_revisions WHERE case_id = ? "
            "ORDER BY revision_number, revision_id",
            (case_id,),
        ).fetchall()
        return tuple(self._decode_revision(row) for row in rows)

    @staticmethod
    def _decode_revision(row: sqlite3.Row) -> DiagnosisRevision:
        try:
            diagnosis_report = DiagnosisReport.model_validate_json(str(row["report_json"]))
            provenance = DiagnosisProvenance.model_validate_json(str(row["provenance_json"]))
            return DiagnosisRevision(
                revision_id=str(row["revision_id"]),
                case_id=str(row["case_id"]),
                revision_number=int(row["revision_number"]),
                origin=RevisionOrigin(str(row["origin"])),
                previous_report_sha256=row["previous_report_sha256"],
                report=diagnosis_report,
                report_sha256=str(row["report_sha256"]),
                triggering_gap_ids=tuple(json.loads(str(row["triggering_gap_ids_json"]))),
                provenance=provenance,
                created_at=_parse_timestamp(str(row["created_at"])),
            )
        except ReviewError:
            raise
        except (KeyError, TypeError, ValueError):
            raise ReviewPersistenceError("stored review data is invalid") from None

    @staticmethod
    def _read_verifier_reports(
        connection: sqlite3.Connection, case_id: str
    ) -> tuple[VerifierReport, ...]:
        rows = connection.execute(
            "SELECT * FROM verifier_runs WHERE case_id = ? "
            "ORDER BY revision_number, started_at, verifier_kind, verifier_run_id",
            (case_id,),
        ).fetchall()
        reports: list[VerifierReport] = []
        for row in rows:
            report = VerifierReport.model_validate_json(str(row["report_json"]))
            if (
                report.verifier_run_id != str(row["verifier_run_id"])
                or report.revision_number != int(row["revision_number"])
                or report.verifier_kind.value != str(row["verifier_kind"])
                or report.verdict.value != str(row["verdict"])
                or (
                    canonical_json(report.usage) if report.usage is not None else None
                )
                != row["usage_json"]
                or (
                    canonical_json(report.operational_error)
                    if report.operational_error is not None
                    else None
                )
                != row["operational_error_json"]
                or _timestamp(report.started_at) != str(row["started_at"])
                or _timestamp(report.completed_at) != str(row["completed_at"])
            ):
                raise ReviewPersistenceError("stored review data is invalid")
            reports.append(report)
        return tuple(reports)

    def _read_decision(
        self, connection: sqlite3.Connection, case_id: str
    ) -> HumanReviewDecision | None:
        row = connection.execute(
            "SELECT * FROM human_decisions WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row is None:
            return None
        action = DecisionAction(str(row["action"]))
        correction_number = row["correction_revision_number"]
        correction: DiagnosisCorrectionDraft | None = None
        resulting_revision_id: str | None = None
        if action is DecisionAction.CORRECT:
            if correction_number is None:
                raise ReviewPersistenceError("stored review data is invalid")
            revision = self._decode_revision(
                self._revision_row(connection, case_id, int(correction_number))
            )
            correction = self._correction_from_revision(revision)
            resulting_revision_id = revision.revision_id
        return HumanReviewDecision(
            decision_id=str(row["decision_id"]),
            case_id=case_id,
            action=action,
            expected_version=int(row["expected_version"]),
            reviewer_label=str(row["reviewer_label"]),
            reason=row["reason"],
            correction=correction,
            resulting_revision_id=resulting_revision_id,
            created_at=_parse_timestamp(str(row["created_at"])),
        )

    @staticmethod
    def _read_events(
        connection: sqlite3.Connection, case_id: str
    ) -> tuple[WorkflowEvent, ...]:
        rows = connection.execute(
            "SELECT event_id, case_id, event_sequence, event_type, from_status, "
            "to_status, case_version, created_at FROM workflow_events "
            "WHERE case_id = ? ORDER BY event_sequence",
            (case_id,),
        ).fetchall()
        return tuple(
            WorkflowEvent(
                event_id=str(row["event_id"]),
                case_id=str(row["case_id"]),
                event_sequence=int(row["event_sequence"]),
                event_type=WorkflowEventType(str(row["event_type"])),
                from_status=(
                    ReviewStatus(str(row["from_status"]))
                    if row["from_status"] is not None
                    else None
                ),
                to_status=ReviewStatus(str(row["to_status"])),
                case_version=int(row["case_version"]),
                created_at=_parse_timestamp(str(row["created_at"])),
            )
            for row in rows
        )

    @staticmethod
    def _correction_from_revision(revision: DiagnosisRevision) -> DiagnosisCorrectionDraft:
        evidence = {item.evidence_id: item for item in revision.report.evidence}
        claims = tuple(
            CorrectionClaim(
                stage=claim.stage,
                statement=claim.statement,
                selectors=tuple(
                    sorted(
                        (
                            EvidenceSelector(
                                span_id=evidence[evidence_id].span_id,
                                field_path=evidence[evidence_id].field_path,
                            )
                            for evidence_id in claim.evidence_ids
                        ),
                        key=lambda selector: selector.canonical,
                    )
                ),
            )
            for claim in revision.report.causal_chain
        )
        return DiagnosisCorrectionDraft(
            status=revision.report.status,
            failure_type=revision.report.failure_type,
            critical_span_ids=revision.report.critical_span_ids,
            causal_chain=claims,
            confidence=revision.report.confidence,
            abstain_reason=revision.report.abstain_reason,
        )
