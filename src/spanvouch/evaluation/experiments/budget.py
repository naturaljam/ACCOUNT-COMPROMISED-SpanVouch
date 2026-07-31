"""Persistent append-only budget accounting for Phase 5 provider experiments."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.versioning import (
    SHA256_PATTERN,
    canonical_bytes,
    canonical_sha256,
)
from spanvouch.evaluation.experiments.config import (
    BudgetPolicy,
    ExperimentMode,
    GpuLeaseApproval,
)

_ZERO = Decimal("0")
_MILLION = Decimal("1000000")


class BudgetExceededError(RuntimeError):
    """Raised before a reservation that would cross its preregistered stop rule."""


class BudgetOverrunError(BudgetExceededError):
    """Raised after an over-reservation charge is durably recorded and stops the month."""

    def __init__(
        self,
        *,
        reservation_id: str,
        reserved_amount: Decimal,
        actual_amount: Decimal,
    ) -> None:
        super().__init__("actual provider charge exceeded reservation; budget is stopped")
        self.reservation_id = reservation_id
        self.reserved_amount = reserved_amount
        self.actual_amount = actual_amount


class BudgetBusyError(RuntimeError):
    """Raised when another process owns the SQLite budget write lock."""


class ProviderRequestClaimError(RuntimeError):
    """Raised when an identical paid request is active in the global ledger."""


class UnknownPriceError(ValueError):
    """Raised rather than guessing a provider or model price."""


class GpuLeaseConflictError(ValueError):
    """Raised when a lease ID is reused with different immutable provenance."""


class CurrencyConversion(BaseModel):
    """Frozen native-currency conversion used for conservative CNY accounting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    budget_currency: Literal["CNY"]
    reference_cny_per_native_unit: Decimal = Field(gt=0)
    reserve_cny_per_native_unit: Decimal = Field(gt=0)
    buffer_fraction: Decimal = Field(ge=0, le=Decimal("1"))
    rounding_increment: Decimal = Field(gt=0)
    effective_date: date
    source_urls: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reserve_rate(self) -> Self:
        if any(not item.strip() for item in self.source_urls):
            raise ValueError("currency conversion source URLs must be non-empty")
        buffered = self.reference_cny_per_native_unit * (
            Decimal("1") + self.buffer_fraction
        )
        units = (buffered / self.rounding_increment).to_integral_value(
            rounding=ROUND_CEILING
        )
        if units * self.rounding_increment != self.reserve_cny_per_native_unit:
            raise ValueError("currency conversion reserve rate is inconsistent")
        return self


class Pricing(BaseModel):
    """User-supplied pricing metadata; no instance is claimed to be current."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    currency: Literal["CNY", "USD"]
    effective_date: date
    source_url: str = Field(min_length=1)
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    gpu_hourly: Decimal = Field(ge=0)
    amounts: Literal["billed", "estimated"]
    conversion: CurrencyConversion | None = None
    max_input_tokens: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_currency_conversion(self) -> Self:
        if self.currency == "USD" and self.conversion is None:
            raise ValueError("USD pricing requires currency conversion provenance")
        if self.currency == "CNY" and self.conversion is not None:
            raise ValueError("CNY pricing must not contain currency conversion")
        return self

    @property
    def _cny_per_native_unit(self) -> Decimal:
        if self.conversion is None:
            return Decimal("1")
        return self.conversion.reserve_cny_per_native_unit

    def require_endpoint(self, provider: str, model: str) -> None:
        if (provider, model) != (self.provider, self.model):
            raise UnknownPriceError("no configured price for provider/model")

    def provider_cost(self, *, input_tokens: int, output_tokens: int) -> Decimal:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        if (
            self.max_input_tokens is not None
            and input_tokens > self.max_input_tokens
        ):
            raise UnknownPriceError("input token count exceeds frozen pricing tier")
        raw = (
            Decimal(input_tokens) * self.input_per_million
            + Decimal(output_tokens) * self.output_per_million
        )
        return (raw * self._cny_per_native_unit / _MILLION).quantize(
            Decimal("0.000001")
        )

    def gpu_cost(self, hours: Decimal) -> Decimal:
        if hours < 0:
            raise ValueError("GPU lease hours must be non-negative")
        return (hours * self.gpu_hourly * self._cny_per_native_unit).quantize(
            Decimal("0.000001")
        )


class BudgetReservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reservation_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    experiment_id: str = Field(min_length=1)
    month_key: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")
    amount: Decimal = Field(gt=0)
    mode: ExperimentMode


class GpuLeaseRecord(BaseModel):
    """Canonical runtime facts for an already-created GPU lease."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lease_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    cloud_provider: str = Field(min_length=1)
    region: str = Field(min_length=1)
    instance_type: str = Field(min_length=1)
    started_at_utc: datetime
    ended_at_utc: datetime
    duration_hours: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        for value in (self.started_at_utc, self.ended_at_utc):
            if value.utcoffset() != UTC.utcoffset(value):
                raise ValueError("GPU lease timestamps must be UTC")
        if self.ended_at_utc <= self.started_at_utc:
            raise ValueError("GPU lease end must follow start")
        actual = Decimal(str((self.ended_at_utc - self.started_at_utc).total_seconds()))
        actual /= Decimal("3600")
        if actual != self.duration_hours:
            raise ValueError("GPU lease duration does not match timestamps")
        return self

    @property
    def sha256(self) -> str:
        return canonical_sha256(cast(JsonValue, self.model_dump(mode="json")))


def _persistent_path(path: Path) -> Path:
    raw = str(path)
    if raw == ":memory:" or raw.startswith("file:"):
        raise ValueError("budget ledger requires a persistent filesystem path")
    return path.resolve()


def _month_key(at_utc: datetime) -> str:
    if at_utc.tzinfo is None or at_utc.utcoffset() != UTC.utcoffset(at_utc):
        raise ValueError("budget timestamps must be UTC")
    return at_utc.strftime("%Y-%m")


class BudgetLedger:
    """SQLite ledger using BEGIN IMMEDIATE for cross-process reservations."""

    def __init__(self, path: Path, policy: BudgetPolicy) -> None:
        self.path = _persistent_path(path)
        self.policy = BudgetPolicy.model_validate(policy.model_dump(mode="python"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reservation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('reserved','released','committed')),
                    created_at_utc TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_reservation_open
                    ON reservation_events(reservation_id, action);
                CREATE TABLE IF NOT EXISTS provider_request_claims (
                    request_sha256 TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL UNIQUE,
                    experiment_id TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active','committed')),
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS charges (
                    charge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id TEXT,
                    request_sha256 TEXT,
                    experiment_id TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    category TEXT NOT NULL CHECK(category IN ('provider','gpu')),
                    created_at_utc TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_provider_charge
                    ON charges(reservation_id) WHERE reservation_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS one_gpu_lease
                    ON charges(request_sha256) WHERE category = 'gpu';
                CREATE TABLE IF NOT EXISTS gpu_leases (
                    lease_id TEXT PRIMARY KEY,
                    provenance_json BLOB NOT NULL,
                    provenance_sha256 TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    matrix_manifest_sha256 TEXT NOT NULL,
                    cost_cny TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gpu_lease_bindings (
                    lease_id TEXT NOT NULL,
                    matrix_manifest_sha256 TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    approval_sha256 TEXT NOT NULL,
                    PRIMARY KEY (lease_id, matrix_manifest_sha256)
                );
                CREATE TABLE IF NOT EXISTS budget_stop_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_sha256 TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    reason TEXT NOT NULL CHECK(reason = 'reservation_overrun'),
                    reserved_amount TEXT NOT NULL,
                    actual_amount TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_budget_stop_per_request
                    ON budget_stop_events(request_sha256, reason);
                """
            )
            self._migrate_provider_request_claims(connection)

    def _migrate_provider_request_claims(
        self, connection: sqlite3.Connection
    ) -> None:
        """Backfill the global request state machine from pre-claim ledgers."""
        self._begin(connection)
        try:
            active = connection.execute(
                """SELECT reserved.request_sha256, reserved.reservation_id,
                          reserved.experiment_id, reserved.month_key,
                          'active', reserved.created_at_utc
                   FROM reservation_events AS reserved
                   WHERE reserved.action = 'reserved'
                     AND NOT EXISTS (
                       SELECT 1 FROM reservation_events AS closed
                       WHERE closed.reservation_id = reserved.reservation_id
                         AND closed.action IN ('released','committed')
                     )"""
            ).fetchall()
            committed = connection.execute(
                """SELECT request_sha256, reservation_id, experiment_id,
                          month_key, 'committed', created_at_utc
                   FROM charges
                   WHERE category = 'provider'
                     AND request_sha256 IS NOT NULL
                     AND reservation_id IS NOT NULL"""
            ).fetchall()
            claims: dict[str, tuple[str, str, str, str, str]] = {}
            reservations: dict[str, str] = {}
            for row in (*active, *committed):
                request_sha256 = str(row[0])
                claim = (
                    str(row[1]),
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[5]),
                )
                reservation_id = claim[0]
                previous = claims.get(request_sha256)
                reservation_request = reservations.get(reservation_id)
                if (
                    (previous is not None and previous != claim)
                    or (
                        reservation_request is not None
                        and reservation_request != request_sha256
                    )
                ):
                    raise ProviderRequestClaimError(
                        "legacy provider request claims conflict"
                    )
                claims[request_sha256] = claim
                reservations[reservation_id] = request_sha256

            for request_sha256, claim in claims.items():
                reservation_id, experiment_id, month_key, state, created_at = claim
                existing = connection.execute(
                    """SELECT reservation_id, experiment_id, month_key, state
                       FROM provider_request_claims
                       WHERE request_sha256 = ?""",
                    (request_sha256,),
                ).fetchone()
                expected = (reservation_id, experiment_id, month_key, state)
                if existing is not None:
                    if existing != expected:
                        raise ProviderRequestClaimError(
                            "legacy provider request claims conflict"
                        )
                    continue
                try:
                    connection.execute(
                        """INSERT INTO provider_request_claims
                           (request_sha256, reservation_id, experiment_id,
                            month_key, state, created_at_utc, updated_at_utc)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            request_sha256,
                            reservation_id,
                            experiment_id,
                            month_key,
                            state,
                            created_at,
                            created_at,
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise ProviderRequestClaimError(
                        "legacy provider request claims conflict"
                    ) from None
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=1.0, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _begin(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if "locked" in str(error).casefold():
                raise BudgetBusyError("budget ledger is busy") from error
            raise

    def _limit(self, mode: ExperimentMode) -> Decimal:
        fraction = (
            self.policy.pilot_fraction
            if mode is ExperimentMode.PILOT
            else self.policy.stop_fraction
        )
        return self.policy.monthly_cap_cny * min(fraction, self.policy.stop_fraction)

    @staticmethod
    def _sum(connection: sqlite3.Connection, query: str, parameter: str) -> Decimal:
        return sum(
            (Decimal(row[0]) for row in connection.execute(query, (parameter,))),
            start=_ZERO,
        )

    def _committed(self, connection: sqlite3.Connection, month: str) -> Decimal:
        return self._sum(
            connection,
            "SELECT amount FROM charges WHERE month_key = ?",
            month,
        )

    def _active(self, connection: sqlite3.Connection, month: str) -> Decimal:
        return self._sum(
            connection,
            """SELECT amount FROM reservation_events AS reserved
               WHERE month_key = ? AND action = 'reserved'
               AND NOT EXISTS (
                 SELECT 1 FROM reservation_events AS closed
                 WHERE closed.reservation_id = reserved.reservation_id
                   AND closed.action IN ('released','committed')
               )""",
            month,
        )

    @staticmethod
    def _require_not_stopped(connection: sqlite3.Connection, month: str) -> None:
        stopped = connection.execute(
            "SELECT 1 FROM budget_stop_events WHERE month_key = ? LIMIT 1",
            (month,),
        ).fetchone()
        if stopped is not None:
            raise BudgetExceededError("Phase 5 budget is stopped for this month")

    def reserve(
        self,
        *,
        request_sha256: str,
        experiment_id: str,
        amount: Decimal,
        mode: ExperimentMode,
        at_utc: datetime,
    ) -> BudgetReservation:
        return self.reserve_many(
            requests=((request_sha256, amount),),
            experiment_id=experiment_id,
            mode=mode,
            at_utc=at_utc,
        )[0]

    def reserve_many(
        self,
        *,
        requests: tuple[tuple[str, Decimal], ...],
        experiment_id: str,
        mode: ExperimentMode,
        at_utc: datetime,
    ) -> tuple[BudgetReservation, ...]:
        if not requests:
            raise ValueError("reserve_many requires at least one request")
        if len({item[0] for item in requests}) != len(requests):
            raise ValueError("reservation request hashes must be unique")
        reservations = tuple(
            BudgetReservation(
                reservation_id=uuid.uuid4().hex,
                request_sha256=request_sha256,
                experiment_id=experiment_id,
                month_key=_month_key(at_utc),
                amount=amount,
                mode=mode,
            )
            for request_sha256, amount in requests
        )
        requested = sum((item.amount for item in reservations), start=_ZERO)
        with self._connect() as connection:
            self._begin(connection)
            month = reservations[0].month_key
            try:
                self._require_not_stopped(connection, month)
            except BudgetExceededError:
                connection.rollback()
                raise
            projected = (
                self._committed(connection, month)
                + self._active(connection, month)
                + requested
            )
            if projected > self._limit(mode):
                connection.rollback()
                raise BudgetExceededError("Phase 5 budget stop rule reached")
            try:
                for item in reservations:
                    connection.execute(
                        """INSERT INTO provider_request_claims
                           (request_sha256, reservation_id, experiment_id, month_key,
                            state, created_at_utc, updated_at_utc)
                           VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                        (
                            item.request_sha256,
                            item.reservation_id,
                            item.experiment_id,
                            item.month_key,
                            at_utc.isoformat(),
                            at_utc.isoformat(),
                        ),
                    )
                    connection.execute(
                        """INSERT INTO reservation_events
                           (reservation_id, request_sha256, experiment_id, month_key,
                            amount, mode, action, created_at_utc)
                           VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?)""",
                        (
                            item.reservation_id,
                            item.request_sha256,
                            item.experiment_id,
                            item.month_key,
                            str(item.amount),
                            item.mode.value,
                            at_utc.isoformat(),
                        ),
                    )
            except sqlite3.IntegrityError:
                connection.rollback()
                raise ProviderRequestClaimError(
                    "provider request is already active globally"
                ) from None
            connection.commit()
        return reservations

    @staticmethod
    def _require_active(
        connection: sqlite3.Connection, reservation: BudgetReservation
    ) -> None:
        row = connection.execute(
            """SELECT action FROM reservation_events
               WHERE reservation_id = ? ORDER BY event_id DESC LIMIT 1""",
            (reservation.reservation_id,),
        ).fetchone()
        if row is None or row[0] != "reserved":
            raise ValueError("budget reservation is closed or unknown")

    def release(self, reservation: BudgetReservation, *, at_utc: datetime) -> None:
        self._close(reservation, action="released", at_utc=at_utc)

    def _close(
        self,
        reservation: BudgetReservation,
        *,
        action: Literal["released", "committed"],
        at_utc: datetime,
    ) -> None:
        validated = BudgetReservation.model_validate(
            reservation.model_dump(mode="python")
        )
        _month_key(at_utc)
        with self._connect() as connection:
            self._begin(connection)
            self._require_active(connection, validated)
            connection.execute(
                """INSERT INTO reservation_events
                   (reservation_id, request_sha256, experiment_id, month_key,
                    amount, mode, action, created_at_utc)
                   VALUES (?, ?, ?, ?, '0', ?, ?, ?)""",
                (
                    validated.reservation_id,
                    validated.request_sha256,
                    validated.experiment_id,
                    validated.month_key,
                    validated.mode.value,
                    action,
                    at_utc.isoformat(),
                ),
            )
            if action == "released":
                deleted = connection.execute(
                    """DELETE FROM provider_request_claims
                       WHERE request_sha256 = ? AND reservation_id = ?
                         AND state = 'active'""",
                    (validated.request_sha256, validated.reservation_id),
                )
                if deleted.rowcount != 1:
                    connection.rollback()
                    raise ValueError("global provider request claim is missing")
            connection.commit()

    def commit(
        self,
        reservation: BudgetReservation,
        *,
        actual_amount: Decimal,
        at_utc: datetime,
    ) -> None:
        if actual_amount < 0:
            raise ValueError("actual charge must be non-negative")
        validated = BudgetReservation.model_validate(
            reservation.model_dump(mode="python")
        )
        _month_key(at_utc)
        with self._connect() as connection:
            self._begin(connection)
            self._require_active(connection, validated)
            overrun = actual_amount > validated.amount
            connection.execute(
                """INSERT INTO charges
                   (reservation_id, request_sha256, experiment_id, month_key,
                    amount, category, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, 'provider', ?)""",
                (
                    validated.reservation_id,
                    validated.request_sha256,
                    validated.experiment_id,
                    validated.month_key,
                    str(actual_amount),
                    at_utc.isoformat(),
                ),
            )
            connection.execute(
                """INSERT INTO reservation_events
                   (reservation_id, request_sha256, experiment_id, month_key,
                    amount, mode, action, created_at_utc)
                   VALUES (?, ?, ?, ?, '0', ?, 'committed', ?)""",
                (
                    validated.reservation_id,
                    validated.request_sha256,
                    validated.experiment_id,
                    validated.month_key,
                    validated.mode.value,
                    at_utc.isoformat(),
                ),
            )
            claimed = connection.execute(
                """UPDATE provider_request_claims
                   SET state = 'committed', updated_at_utc = ?
                   WHERE request_sha256 = ? AND reservation_id = ?
                     AND state = 'active'""",
                (
                    at_utc.isoformat(),
                    validated.request_sha256,
                    validated.reservation_id,
                ),
            )
            if claimed.rowcount != 1:
                connection.rollback()
                raise ValueError("global provider request claim is missing")
            if overrun:
                connection.execute(
                    """INSERT INTO budget_stop_events
                       (request_sha256, experiment_id, month_key, reason,
                        reserved_amount, actual_amount, created_at_utc)
                       VALUES (?, ?, ?, 'reservation_overrun', ?, ?, ?)""",
                    (
                        validated.request_sha256,
                        validated.experiment_id,
                        validated.month_key,
                        str(validated.amount),
                        str(actual_amount),
                        at_utc.isoformat(),
                    ),
                )
            connection.commit()
        if overrun:
            raise BudgetOverrunError(
                reservation_id=validated.reservation_id,
                reserved_amount=validated.amount,
                actual_amount=actual_amount,
            )

    def record_gpu_lease(
        self,
        *,
        lease_id: str,
        experiment_id: str,
        mode: ExperimentMode,
        hours: Decimal,
        pricing: Pricing,
        at_utc: datetime,
    ) -> Decimal:
        month = _month_key(at_utc)
        cost = pricing.gpu_cost(hours)
        with self._connect() as connection:
            self._begin(connection)
            try:
                self._require_not_stopped(connection, month)
            except BudgetExceededError:
                connection.rollback()
                raise
            projected = (
                self._committed(connection, month)
                + self._active(connection, month)
                + cost
            )
            if projected > self._limit(mode):
                connection.rollback()
                raise BudgetExceededError("Phase 5 budget stop rule reached")
            connection.execute(
                """INSERT INTO charges
                   (reservation_id, request_sha256, experiment_id, month_key,
                    amount, category, created_at_utc)
                   VALUES (NULL, ?, ?, ?, ?, 'gpu', ?)""",
                (lease_id, experiment_id, month, str(cost), at_utc.isoformat()),
            )
            connection.commit()
        return cost

    def record_gpu_lease_record(
        self,
        *,
        record: GpuLeaseRecord,
        approval: GpuLeaseApproval,
        experiment_id: str,
        matrix_manifest_sha256: str,
        mode: ExperimentMode,
        pricing: Pricing,
    ) -> Decimal:
        """Atomically persist canonical lease facts and their global budget charge."""
        validated = GpuLeaseRecord.model_validate(record.model_dump(mode="python"))
        approved = GpuLeaseApproval.model_validate(approval.model_dump(mode="python"))
        cost = pricing.gpu_cost(validated.duration_hours)
        payload = canonical_bytes(cast(JsonValue, validated.model_dump(mode="json")))
        payload_sha256 = sha256(payload).hexdigest()
        approval_sha256 = canonical_sha256(cast(JsonValue, {
            "approval": approved.model_dump(mode="json"),
            "mode": mode.value,
            "pricing_sha256": canonical_sha256(
                cast(JsonValue, pricing.model_dump(mode="json"))
            ),
        }))
        exceeds_approval = (
            (validated.cloud_provider, validated.region, validated.instance_type)
            != (approved.cloud_provider, approved.region, approved.instance_type)
            or validated.duration_hours > approved.maximum_hours
            or cost > approved.maximum_cost_cny
        )
        month = _month_key(validated.ended_at_utc)
        with self._connect() as connection:
            self._begin(connection)
            existing = connection.execute(
                """SELECT provenance_json, provenance_sha256, cost_cny
                   FROM gpu_leases WHERE lease_id = ?""",
                (validated.lease_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing[0] != payload
                    or existing[1] != payload_sha256
                    or Decimal(existing[2]) != cost
                ):
                    connection.rollback()
                    raise GpuLeaseConflictError("GPU lease ID provenance conflict")
                if exceeds_approval:
                    connection.rollback()
                    raise ValueError("GPU lease exceeds frozen approval")
                binding = connection.execute(
                    """SELECT experiment_id, approval_sha256
                       FROM gpu_lease_bindings
                       WHERE lease_id = ? AND matrix_manifest_sha256 = ?""",
                    (validated.lease_id, matrix_manifest_sha256),
                ).fetchone()
                if binding is not None and binding != (experiment_id, approval_sha256):
                    connection.rollback()
                    raise GpuLeaseConflictError("GPU lease approval binding conflict")
                connection.execute(
                    "INSERT OR IGNORE INTO gpu_lease_bindings VALUES (?, ?, ?, ?)",
                    (validated.lease_id, matrix_manifest_sha256,
                     experiment_id, approval_sha256),
                )
                connection.commit()
                return Decimal(existing[2])
            try:
                if exceeds_approval:
                    raise ValueError("GPU lease exceeds frozen approval")
                self._require_not_stopped(connection, month)
                projected = (
                    self._committed(connection, month)
                    + self._active(connection, month)
                    + cost
                )
                if projected > self._limit(mode):
                    raise BudgetExceededError("Phase 5 budget stop rule reached")
                connection.execute(
                    "INSERT INTO gpu_leases VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (validated.lease_id, payload, payload_sha256, experiment_id,
                     matrix_manifest_sha256, str(cost), validated.ended_at_utc.isoformat()),
                )
                connection.execute(
                    "INSERT INTO gpu_lease_bindings VALUES (?, ?, ?, ?)",
                    (validated.lease_id, matrix_manifest_sha256,
                     experiment_id, approval_sha256),
                )
                connection.execute(
                    """INSERT INTO charges
                       (reservation_id, request_sha256, experiment_id, month_key,
                        amount, category, created_at_utc)
                       VALUES (NULL, ?, ?, ?, ?, 'gpu', ?)""",
                    (validated.lease_id, experiment_id, month, str(cost),
                     validated.ended_at_utc.isoformat()),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return cost

    def committed_total(self, at_utc: datetime) -> Decimal:
        with self._connect() as connection:
            return self._committed(connection, _month_key(at_utc))

    def active_reserved_total(self, at_utc: datetime) -> Decimal:
        with self._connect() as connection:
            return self._active(connection, _month_key(at_utc))

    def committed_request_cost(self, request_sha256: str) -> Decimal | None:
        """Return an existing provider charge so a missing cache cannot rebill it."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT amount FROM charges
                   WHERE request_sha256 = ? AND category = 'provider'""",
                (request_sha256,),
            ).fetchone()
        return None if row is None else Decimal(row[0])
