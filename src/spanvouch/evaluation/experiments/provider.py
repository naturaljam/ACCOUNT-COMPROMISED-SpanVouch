"""Guarded, cached and budgeted provider execution for Phase 5 experiments."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.versioning import (
    SHA256_PATTERN,
    canonical_bytes,
    canonical_sha256,
)
from spanvouch.diagnosis.protocols import (
    ChatMessage,
    GenerationConfig,
    ModelProvider,
    ProviderResponse,
)
from spanvouch.evaluation.artifacts import require_safe_artifact_content
from spanvouch.evaluation.experiments.budget import (
    BudgetLedger,
    BudgetOverrunError,
    BudgetReservation,
    Pricing,
)
from spanvouch.evaluation.experiments.config import ExperimentMode


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderInProgressError(RuntimeError):
    pass


class CacheIntegrityError(ValueError):
    pass


class PaidRunAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str = Field(min_length=1)
    allow_live_provider: bool = False
    formal_run: bool = False
    frozen_manifest_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    def require(self, mode: ExperimentMode) -> None:
        if not self.allow_live_provider:
            raise ProviderConfigurationError("live provider access is disabled")
        if mode is ExperimentMode.FORMAL and (
            not self.formal_run or self.frozen_manifest_sha256 is None
        ):
            raise ProviderConfigurationError(
                "formal live run requires frozen manifest"
            )


class RequestIdentity(BaseModel):
    """Every causal request input represented by values or canonical hashes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str = Field(min_length=1)
    trace_sha256: str = Field(pattern=SHA256_PATTERN)
    diagnosis_sha256: str = Field(pattern=SHA256_PATTERN)
    condition_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    messages_sha256: str = Field(pattern=SHA256_PATTERN)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    generation_config_sha256: str = Field(pattern=SHA256_PATTERN)

    @property
    def sha256(self) -> str:
        return canonical_sha256(self)

    @classmethod
    def from_request(
        cls,
        *,
        experiment_id: str,
        trace_sha256: str,
        diagnosis_sha256: str,
        condition_id: str,
        prompt_version: str,
        prompt_sha256: str,
        provider: str,
        model: str,
        messages: tuple[ChatMessage, ...],
        generation: GenerationConfig,
    ) -> Self:
        validated_messages = tuple(
            ChatMessage.model_validate(message.model_dump(mode="python"))
            for message in messages
        )
        validated_generation = GenerationConfig.model_validate(
            generation.model_dump(mode="python")
        )
        if validated_generation.model != model:
            raise ValueError("request model does not match generation config")
        message_payload = cast(
            JsonValue,
            [message.model_dump(mode="json") for message in validated_messages],
        )
        return cls(
            experiment_id=experiment_id,
            trace_sha256=trace_sha256,
            diagnosis_sha256=diagnosis_sha256,
            condition_id=condition_id,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            messages_sha256=canonical_sha256(message_payload),
            provider=provider,
            model=model,
            generation_config_sha256=canonical_sha256(validated_generation),
        )


class ProviderRequestAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_sha256: str = Field(pattern=SHA256_PATTERN)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    field_names: tuple[str, ...]
    started_at_utc: datetime
    completed_at_utc: datetime
    status: Literal["completed", "cache_hit", "failed"]
    leakage_scan_passed: bool

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        for value in (self.started_at_utc, self.completed_at_utc):
            if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                raise ValueError("provider audit timestamps must be UTC")
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("provider audit completion precedes start")
        return self


class GuardedProviderResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    response: ProviderResponse
    cache_hit: bool
    original_usage: ProviderUsage
    cost_cny: Decimal = Field(ge=0)
    audit: ProviderRequestAudit


class _CachedResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    response: ProviderResponse
    original_usage: ProviderUsage
    cost_cny: Decimal = Field(ge=0)


def _cached_result_bytes(result: _CachedResult) -> bytes:
    return canonical_bytes(cast(JsonValue, result.model_dump(mode="json")))


def _persistent_path(path: Path) -> Path:
    raw = str(path)
    if raw == ":memory:" or raw.startswith("file:"):
        raise ValueError("provider cache requires a persistent filesystem path")
    return path.resolve()


class ProviderResultCache:
    """Canonical hash-verified SQLite cache with append-only audits."""

    def __init__(self, path: Path) -> None:
        self.path = _persistent_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_requests (
                    request_sha256 TEXT PRIMARY KEY,
                    identity_json BLOB NOT NULL,
                    identity_sha256 TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_results (
                    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_sha256 TEXT NOT NULL UNIQUE,
                    payload_json BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_inflight (
                    request_sha256 TEXT PRIMARY KEY,
                    started_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_audits (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_sha256 TEXT NOT NULL,
                    audit_json BLOB NOT NULL,
                    audit_sha256 TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=1.0, isolation_level=None)

    def get(self, identity: RequestIdentity) -> _CachedResult | None:
        validated = RequestIdentity.model_validate(identity.model_dump(mode="python"))
        with self._connect() as connection:
            row = connection.execute(
                """SELECT r.identity_json, r.identity_sha256,
                          p.payload_json, p.payload_sha256
                   FROM provider_requests r
                   JOIN provider_results p USING (request_sha256)
                   WHERE r.request_sha256 = ?""",
                (validated.sha256,),
            ).fetchone()
        if row is None:
            return None
        identity_bytes, identity_sha, payload_bytes, payload_sha = row
        if sha256(identity_bytes).hexdigest() != identity_sha or (
            identity_bytes != canonical_bytes(validated)
        ):
            raise CacheIntegrityError("cached request identity failed verification")
        if sha256(payload_bytes).hexdigest() != payload_sha:
            raise CacheIntegrityError("cached provider result failed verification")
        try:
            result = _CachedResult.model_validate_json(payload_bytes)
        except ValueError as error:
            raise CacheIntegrityError("cached provider result is contract-invalid") from error
        if _cached_result_bytes(result) != payload_bytes:
            raise CacheIntegrityError("cached provider result is not canonical JSON")
        return result

    def claim(self, identity: RequestIdentity, at_utc: datetime) -> None:
        identity_bytes = canonical_bytes(identity)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO provider_requests
                   (request_sha256, identity_json, identity_sha256, created_at_utc)
                   VALUES (?, ?, ?, ?)""",
                (
                    identity.sha256,
                    identity_bytes,
                    sha256(identity_bytes).hexdigest(),
                    at_utc.isoformat(),
                ),
            )
            stored = connection.execute(
                "SELECT identity_json FROM provider_requests WHERE request_sha256 = ?",
                (identity.sha256,),
            ).fetchone()
            if stored is None or stored[0] != identity_bytes:
                connection.rollback()
                raise CacheIntegrityError("request identity hash collision")
            try:
                connection.execute(
                    "INSERT INTO provider_inflight VALUES (?, ?)",
                    (identity.sha256, at_utc.isoformat()),
                )
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise ProviderInProgressError("provider request is already in progress") from error
            connection.commit()

    def release_claim(self, identity: RequestIdentity) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM provider_inflight WHERE request_sha256 = ?",
                (identity.sha256,),
            )
            connection.commit()

    def put(self, identity: RequestIdentity, result: _CachedResult, at_utc: datetime) -> None:
        validated = _CachedResult.model_validate(result.model_dump(mode="python"))
        payload = _cached_result_bytes(validated)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO provider_results
                   (request_sha256, payload_json, payload_sha256, created_at_utc)
                   VALUES (?, ?, ?, ?)""",
                (
                    identity.sha256,
                    payload,
                    sha256(payload).hexdigest(),
                    at_utc.isoformat(),
                ),
            )
            connection.commit()

    def record_audit(self, audit: ProviderRequestAudit) -> None:
        payload = canonical_bytes(audit)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO provider_audits
                   (request_sha256, audit_json, audit_sha256, created_at_utc)
                   VALUES (?, ?, ?, ?)""",
                (
                    audit.request_sha256,
                    payload,
                    sha256(payload).hexdigest(),
                    audit.completed_at_utc.isoformat(),
                ),
            )

    def _insert_test_tamper(
        self, identity: RequestIdentity, payload: bytes, declared_sha256: str
    ) -> None:
        now = datetime.now(UTC).isoformat()
        identity_bytes = canonical_bytes(identity)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO provider_requests VALUES (?, ?, ?, ?)",
                (
                    identity.sha256,
                    identity_bytes,
                    sha256(identity_bytes).hexdigest(),
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO provider_results
                   (request_sha256, payload_json, payload_sha256, created_at_utc)
                   VALUES (?, ?, ?, ?)""",
                (identity.sha256, payload, declared_sha256, now),
            )


class GuardedProvider:
    """Cache-first provider wrapper that reserves cost before external I/O."""

    def __init__(
        self,
        *,
        delegate: ModelProvider,
        cache: ProviderResultCache,
        ledger: BudgetLedger,
        pricing: Pricing,
        authorization: PaidRunAuthorization,
        mode: ExperimentMode,
        identity: RequestIdentity,
        at_utc: Callable[[], datetime] | None = None,
    ) -> None:
        if cache.path != ledger.path:
            raise ValueError("provider cache and budget ledger must share one SQLite file")
        self.delegate = delegate
        self.cache = cache
        self.ledger = ledger
        self.pricing = pricing
        self.authorization = authorization
        self.mode = mode
        self.identity = RequestIdentity.model_validate(identity.model_dump(mode="python"))
        self._at_utc = at_utc or (lambda: datetime.now(UTC))

    def _audit(
        self,
        *,
        started: datetime,
        completed: datetime,
        status: Literal["completed", "cache_hit", "failed"],
        leakage_scan_passed: bool,
    ) -> ProviderRequestAudit:
        return ProviderRequestAudit(
            request_sha256=self.identity.sha256,
            provider=self.identity.provider,
            model=self.identity.model,
            field_names=tuple(RequestIdentity.model_fields),
            started_at_utc=started,
            completed_at_utc=completed,
            status=status,
            leakage_scan_passed=leakage_scan_passed,
        )

    def _validate_call(
        self, messages: tuple[ChatMessage, ...], generation: GenerationConfig
    ) -> None:
        rebuilt = RequestIdentity.from_request(
            experiment_id=self.identity.experiment_id,
            trace_sha256=self.identity.trace_sha256,
            diagnosis_sha256=self.identity.diagnosis_sha256,
            condition_id=self.identity.condition_id,
            prompt_version=self.identity.prompt_version,
            prompt_sha256=self.identity.prompt_sha256,
            provider=self.identity.provider,
            model=self.identity.model,
            messages=messages,
            generation=generation,
        )
        if rebuilt != self.identity:
            raise ProviderConfigurationError("provider call does not match request identity")

    @staticmethod
    def _sanitize_response(response: ProviderResponse) -> ProviderResponse:
        try:
            require_safe_artifact_content("provider_cache_content", response.content)
        except ValueError as error:
            raise ValueError("unsafe provider response content") from error
        request_id = response.usage.request_id
        sanitized_usage = response.usage.model_copy(
            update={
                "request_id": (
                    f"sha256-{sha256(request_id.encode()).hexdigest()}"
                    if request_id is not None
                    else None
                )
            }
        )
        return response.model_copy(
            update={
                "response_id": f"sha256-{sha256(response.response_id.encode()).hexdigest()}",
                "usage": sanitized_usage,
            }
        )

    async def complete(
        self,
        messages: tuple[ChatMessage, ...],
        generation: GenerationConfig,
    ) -> GuardedProviderResult:
        started = self._at_utc()
        self._validate_call(messages, generation)
        cached = self.cache.get(self.identity)
        if cached is not None:
            audit = self._audit(
                started=started,
                completed=self._at_utc(),
                status="cache_hit",
                leakage_scan_passed=True,
            )
            self.cache.record_audit(audit)
            return GuardedProviderResult(
                response=cached.response,
                cache_hit=True,
                original_usage=cached.original_usage,
                cost_cny=cached.cost_cny,
                audit=audit,
            )

        if self.ledger.committed_request_cost(self.identity.sha256) is not None:
            raise CacheIntegrityError("charged provider request is missing its cache result")

        if self.authorization.experiment_id != self.identity.experiment_id:
            raise ProviderConfigurationError("authorization experiment does not match request")
        self.authorization.require(self.mode)
        self.pricing.require_endpoint(self.identity.provider, self.identity.model)
        self.cache.claim(self.identity, started)
        reservation: BudgetReservation | None = None
        try:
            estimated_input_tokens = sum(len(item.content.encode("utf-8")) for item in messages)
            maximum = self.pricing.provider_cost(
                input_tokens=estimated_input_tokens,
                output_tokens=generation.max_tokens,
            )
            reservation = self.ledger.reserve(
                request_sha256=self.identity.sha256,
                experiment_id=self.identity.experiment_id,
                amount=maximum,
                mode=self.mode,
                at_utc=started,
            )
            raw_response = await self.delegate.complete(messages, generation)
            response = self._sanitize_response(
                ProviderResponse.model_validate(raw_response.model_dump(mode="python"))
            )
            actual = self.pricing.provider_cost(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
            cached_result = _CachedResult(
                response=response,
                original_usage=response.usage,
                cost_cny=actual,
            )
            try:
                self.ledger.commit(
                    reservation, actual_amount=actual, at_utc=self._at_utc()
                )
            except BudgetOverrunError:
                reservation = None
                raise
            reservation = None
            self.cache.put(self.identity, cached_result, self._at_utc())
            audit = self._audit(
                started=started,
                completed=self._at_utc(),
                status="completed",
                leakage_scan_passed=True,
            )
            self.cache.record_audit(audit)
            return GuardedProviderResult(
                response=response,
                cache_hit=False,
                original_usage=response.usage,
                cost_cny=actual,
                audit=audit,
            )
        except BaseException:
            if reservation is not None:
                self.ledger.release(reservation, at_utc=self._at_utc())
            failed_at = self._at_utc()
            self.cache.record_audit(
                self._audit(
                    started=started,
                    completed=failed_at,
                    status="failed",
                    leakage_scan_passed=False,
                )
            )
            raise
        finally:
            self.cache.release_claim(self.identity)
