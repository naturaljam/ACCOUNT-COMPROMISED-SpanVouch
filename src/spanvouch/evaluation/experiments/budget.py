"""Persistent append-only budget accounting for Phase 5 provider experiments."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from spanvouch.contracts.versioning import SHA256_PATTERN
from spanvouch.evaluation.experiments.config import BudgetPolicy, ExperimentMode

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


class UnknownPriceError(ValueError):
    """Raised rather than guessing a provider or model price."""


class Pricing(BaseModel):
    """User-supplied pricing metadata; no instance is claimed to be current."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    currency: Literal["CNY"]
    effective_date: date
    source_url: str = Field(min_length=1)
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    gpu_hourly: Decimal = Field(ge=0)
    amounts: Literal["billed", "estimated"]

    def require_endpoint(self, provider: str, model: str) -> None:
        if (provider, model) != (self.provider, self.model):
            raise UnknownPriceError("no configured price for provider/model")

    def provider_cost(self, *, input_tokens: int, output_tokens: int) -> Decimal:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        raw = (
            Decimal(input_tokens) * self.input_per_million
            + Decimal(output_tokens) * self.output_per_million
        )
        return (raw / _MILLION).quantize(Decimal("0.000001"))

    def gpu_cost(self, hours: Decimal) -> Decimal:
        if hours < 0:
            raise ValueError("GPU lease hours must be non-negative")
        return (hours * self.gpu_hourly).quantize(Decimal("0.000001"))


class BudgetReservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reservation_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    experiment_id: str = Field(min_length=1)
    month_key: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")
    amount: Decimal = Field(gt=0)
    mode: ExperimentMode


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
            for item in reservations:
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
