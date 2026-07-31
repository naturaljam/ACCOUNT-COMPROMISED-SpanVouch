from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.diagnosis.protocols import (
    ChatMessage,
    GenerationConfig,
    ProviderResponse,
)
from spanvouch.evaluation.experiments.budget import (
    BudgetLedger,
    BudgetOverrunError,
    Pricing,
    ProviderRequestClaimError,
    UnknownPriceError,
)
from spanvouch.evaluation.experiments.config import BudgetPolicy, ExperimentMode
from spanvouch.evaluation.experiments.provider import (
    CacheIntegrityError,
    GuardedProvider,
    PaidRunAuthorization,
    ProviderConfigurationError,
    ProviderInProgressError,
    ProviderResultCache,
    RequestIdentity,
)

MESSAGES = (
    ChatMessage(role="system", content="Return JSON only."),
    ChatMessage(role="user", content="Inspect the frozen candidate."),
)
GENERATION = GenerationConfig(model="deepseek-chat", max_tokens=200, temperature=0.0)


class CountingProvider:
    def __init__(self, *, fail: bool = False, gate: asyncio.Event | None = None) -> None:
        self.calls = 0
        self.fail = fail
        self.gate = gate

    async def complete(
        self, messages: tuple[ChatMessage, ...], config: GenerationConfig
    ) -> ProviderResponse:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("offline fake failure")
        return ProviderResponse(
            content='{"status":"ok"}',
            model=config.model,
            response_id="response-raw-id",
            finish_reason="stop",
            usage=ProviderUsage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                latency_ms=1.0,
                request_id="request-raw-id",
            ),
        )


class BlockingProvider(CountingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(
        self, messages: tuple[ChatMessage, ...], config: GenerationConfig
    ) -> ProviderResponse:
        self.started.set()
        await self.release.wait()
        return await super().complete(messages, config)


def pricing() -> Pricing:
    return Pricing(
        provider="deepseek",
        model="deepseek-chat",
        currency="CNY",
        effective_date="2026-07-01",
        source_url="https://example.invalid/pricing",
        input_per_million=Decimal("2"),
        output_per_million=Decimal("8"),
        gpu_hourly=Decimal("5"),
        amounts="estimated",
    )


def base_identity() -> RequestIdentity:
    return RequestIdentity.from_request(
        experiment_id="phase5-pilot",
        experiment_config_sha256="e" * 64,
        deployment_provenance_sha256="f" * 64,
        trace_sha256="a" * 64,
        diagnosis_sha256="b" * 64,
        condition_id="b2_deepseek_shared",
        prompt_version="verify-v1",
        prompt_sha256="c" * 64,
        provider="deepseek",
        model="deepseek-chat",
        messages=MESSAGES,
        generation=GENERATION,
    )


def test_request_identity_changes_for_every_causal_input() -> None:
    base = base_identity()
    fields = (
        "experiment_id",
        "experiment_config_sha256",
        "deployment_provenance_sha256",
        "trace_sha256",
        "diagnosis_sha256",
        "condition_id",
        "prompt_version",
        "prompt_sha256",
        "messages_sha256",
        "provider",
        "model",
        "generation_config_sha256",
    )
    for field in fields:
        assert base.model_copy(update={field: "d" * 64}).sha256 != base.sha256


@pytest.mark.parametrize(
    ("mode", "authorization", "allowed"),
    [
        (ExperimentMode.PILOT, PaidRunAuthorization(experiment_id="x"), False),
        (
            ExperimentMode.PILOT,
            PaidRunAuthorization(experiment_id="x", allow_live_provider=True),
            True,
        ),
        (
            ExperimentMode.FORMAL,
            PaidRunAuthorization(experiment_id="x", allow_live_provider=True),
            False,
        ),
        (
            ExperimentMode.FORMAL,
            PaidRunAuthorization(
                experiment_id="x",
                allow_live_provider=True,
                formal_run=True,
                frozen_manifest_sha256="a" * 64,
            ),
            True,
        ),
    ],
)
def test_paid_authorization_combinations(
    mode: ExperimentMode, authorization: PaidRunAuthorization, allowed: bool
) -> None:
    if allowed:
        authorization.require(mode)
    else:
        with pytest.raises(ProviderConfigurationError):
            authorization.require(mode)


def guarded(
    tmp_path: Path,
    delegate: CountingProvider,
    *,
    authorization: PaidRunAuthorization | None = None,
) -> GuardedProvider:
    db = tmp_path / "phase5.sqlite3"
    return GuardedProvider(
        delegate=delegate,
        cache=ProviderResultCache(db),
        ledger=BudgetLedger(
            db,
            BudgetPolicy(
                monthly_cap_cny=Decimal("100"),
                pilot_fraction=Decimal("0.10"),
                stop_fraction=Decimal("0.80"),
            ),
        ),
        pricing=pricing(),
        authorization=authorization
        or PaidRunAuthorization(
            experiment_id="phase5-pilot", allow_live_provider=True
        ),
        mode=ExperimentMode.PILOT,
        identity=base_identity(),
        at_utc=lambda: datetime(2026, 7, 20, tzinfo=UTC),
    )


def test_guarded_provider_allows_matrix_cache_and_global_ledger_paths(
    tmp_path: Path,
) -> None:
    cache = ProviderResultCache(tmp_path / "matrix-cache.sqlite3")
    ledger = BudgetLedger(tmp_path / "global-budget.sqlite3", BudgetPolicy(
        monthly_cap_cny=Decimal("100"),
        pilot_fraction=Decimal("0.10"),
        stop_fraction=Decimal("0.80"),
    ))

    provider = GuardedProvider(
        delegate=CountingProvider(),
        cache=cache,
        ledger=ledger,
        pricing=pricing(),
        authorization=PaidRunAuthorization(
            experiment_id="phase5-pilot", allow_live_provider=True
        ),
        mode=ExperimentMode.PILOT,
        identity=base_identity(),
    )

    assert provider.cache.path != provider.ledger.path


@pytest.mark.asyncio
async def test_global_ledger_allows_only_one_call_across_manifest_caches(
    tmp_path: Path,
) -> None:
    ledger = BudgetLedger(
        tmp_path / "global.sqlite3",
        BudgetPolicy(
            monthly_cap_cny=Decimal("100"),
            pilot_fraction=Decimal("0.10"),
            stop_fraction=Decimal("0.80"),
        ),
    )

    def provider(cache_name: str, delegate: CountingProvider) -> GuardedProvider:
        return GuardedProvider(
            delegate=delegate,
            cache=ProviderResultCache(tmp_path / cache_name),
            ledger=ledger,
            pricing=pricing(),
            authorization=PaidRunAuthorization(
                experiment_id="phase5-pilot", allow_live_provider=True
            ),
            mode=ExperimentMode.PILOT,
            identity=base_identity(),
            at_utc=lambda: datetime(2026, 7, 20, tzinfo=UTC),
        )

    winner_delegate = BlockingProvider()
    winner = asyncio.create_task(
        provider("matrix-a.sqlite3", winner_delegate).complete(MESSAGES, GENERATION)
    )
    await winner_delegate.started.wait()
    loser_delegate = CountingProvider()
    with pytest.raises(
        ProviderRequestClaimError,
        match="provider request is already active globally",
    ):
        await provider("matrix-b.sqlite3", loser_delegate).complete(MESSAGES, GENERATION)
    assert loser_delegate.calls == 0

    winner_delegate.release.set()
    await winner
    assert winner_delegate.calls == 1
    assert ledger.committed_total(datetime(2026, 7, 20, tzinfo=UTC)) == Decimal(
        "0.000360"
    )


@pytest.mark.asyncio
async def test_released_global_request_claim_can_retry_from_another_cache(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "global.sqlite3"
    policy = BudgetPolicy(
        monthly_cap_cny=Decimal("100"),
        pilot_fraction=Decimal("0.10"),
        stop_fraction=Decimal("0.80"),
    )

    def provider(cache_name: str, delegate: CountingProvider) -> GuardedProvider:
        return GuardedProvider(
            delegate=delegate,
            cache=ProviderResultCache(tmp_path / cache_name),
            ledger=BudgetLedger(ledger_path, policy),
            pricing=pricing(),
            authorization=PaidRunAuthorization(
                experiment_id="phase5-pilot", allow_live_provider=True
            ),
            mode=ExperimentMode.PILOT,
            identity=base_identity(),
            at_utc=lambda: datetime(2026, 7, 20, tzinfo=UTC),
        )

    with pytest.raises(RuntimeError, match="offline fake failure"):
        await provider("failed.sqlite3", CountingProvider(fail=True)).complete(MESSAGES, GENERATION)
    retry = CountingProvider()
    await provider("retry.sqlite3", retry).complete(MESSAGES, GENERATION)
    assert retry.calls == 1


@pytest.mark.asyncio
async def test_cache_hit_preserves_usage_cost_and_makes_no_second_call(
    tmp_path: Path,
) -> None:
    delegate = CountingProvider()
    provider = guarded(tmp_path, delegate)
    first = await provider.complete(MESSAGES, GENERATION)
    provider.authorization = PaidRunAuthorization(experiment_id="phase5-pilot")
    second = await provider.complete(MESSAGES, GENERATION)
    assert delegate.calls == 1
    assert second.response == first.response
    assert second.cache_hit is True
    assert second.original_usage == first.original_usage
    assert second.cost_cny == first.cost_cny
    assert "raw-id" not in second.model_dump_json()


@pytest.mark.asyncio
async def test_cache_miss_without_authorization_makes_zero_calls(tmp_path: Path) -> None:
    delegate = CountingProvider()
    provider = guarded(
        tmp_path, delegate, authorization=PaidRunAuthorization(experiment_id="phase5-pilot")
    )
    with pytest.raises(ProviderConfigurationError):
        await provider.complete(MESSAGES, GENERATION)
    assert delegate.calls == 0


@pytest.mark.asyncio
async def test_price_tier_rejects_before_delegate_cache_or_charge(
    tmp_path: Path,
) -> None:
    delegate = CountingProvider()
    provider = guarded(tmp_path, delegate)
    provider.pricing = pricing().model_copy(update={"max_input_tokens": 1})
    at = datetime(2026, 7, 20, tzinfo=UTC)

    with pytest.raises(UnknownPriceError, match="pricing tier"):
        await provider.complete(MESSAGES, GENERATION)

    assert delegate.calls == 0
    assert provider.cache.get(base_identity()) is None
    assert provider.ledger.committed_total(at) == Decimal("0")
    assert provider.ledger.active_reserved_total(at) == Decimal("0")


@pytest.mark.asyncio
async def test_provider_failure_releases_reservation(tmp_path: Path) -> None:
    delegate = CountingProvider(fail=True)
    provider = guarded(tmp_path, delegate)
    with pytest.raises(RuntimeError, match="offline fake failure"):
        await provider.complete(MESSAGES, GENERATION)
    assert provider.ledger.active_reserved_total(datetime(2026, 7, 20, tzinfo=UTC)) == 0


@pytest.mark.asyncio
async def test_provider_overrun_records_charge_without_masking_typed_stop(
    tmp_path: Path,
) -> None:
    class OverrunProvider(CountingProvider):
        async def complete(
            self, messages: tuple[ChatMessage, ...], config: GenerationConfig
        ) -> ProviderResponse:
            response = await super().complete(messages, config)
            return response.model_copy(
                update={
                    "usage": response.usage.model_copy(
                        update={"output_tokens": 10_000, "total_tokens": 10_100}
                    )
                }
            )

    delegate = OverrunProvider()
    provider = guarded(tmp_path, delegate)
    at = datetime(2026, 7, 20, tzinfo=UTC)

    with pytest.raises(BudgetOverrunError):
        await provider.complete(MESSAGES, GENERATION)

    assert delegate.calls == 1
    assert provider.ledger.committed_total(at) > 0
    assert provider.ledger.active_reserved_total(at) == 0


@pytest.mark.asyncio
async def test_same_identity_is_single_flight_and_never_double_charged(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    delegate = CountingProvider(gate=gate)
    provider = guarded(tmp_path, delegate)
    first = asyncio.create_task(provider.complete(MESSAGES, GENERATION))
    await asyncio.sleep(0)
    with pytest.raises(ProviderInProgressError):
        await provider.complete(MESSAGES, GENERATION)
    gate.set()
    await first
    assert delegate.calls == 1


def test_cache_detects_payload_tampering(tmp_path: Path) -> None:
    cache = ProviderResultCache(tmp_path / "phase5.sqlite3")
    cache._insert_test_tamper(base_identity(), b"{}", "0" * 64)
    with pytest.raises(CacheIntegrityError):
        cache.get(base_identity())


@pytest.mark.asyncio
async def test_secret_provider_content_is_not_cached(tmp_path: Path) -> None:
    class SecretProvider(CountingProvider):
        async def complete(
            self, messages: tuple[ChatMessage, ...], config: GenerationConfig
        ) -> ProviderResponse:
            result = await super().complete(messages, config)
            return result.model_copy(
                update={"content": "Authorization: Bearer stolen-provider-secret"}
            )

    delegate = SecretProvider()
    provider = guarded(tmp_path, delegate)
    with pytest.raises(ValueError, match="unsafe provider response content"):
        await provider.complete(MESSAGES, GENERATION)
    assert provider.cache.get(base_identity()) is None


@pytest.mark.asyncio
async def test_cancellation_releases_reservation_and_claim(tmp_path: Path) -> None:
    gate = asyncio.Event()
    delegate = CountingProvider(gate=gate)
    provider = guarded(tmp_path, delegate)
    task = asyncio.create_task(provider.complete(MESSAGES, GENERATION))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.ledger.active_reserved_total(
        datetime(2026, 7, 20, tzinfo=UTC)
    ) == 0
    assert provider.cache.get(base_identity()) is None


@pytest.mark.asyncio
async def test_different_identities_can_run_concurrently(tmp_path: Path) -> None:
    db = tmp_path / "phase5.sqlite3"
    gate = asyncio.Event()
    first_delegate = CountingProvider(gate=gate)
    second_delegate = CountingProvider(gate=gate)
    common = {
        "cache": ProviderResultCache(db),
        "ledger": BudgetLedger(
            db,
            BudgetPolicy(
                monthly_cap_cny=Decimal("100"),
                pilot_fraction=Decimal("0.10"),
                stop_fraction=Decimal("0.80"),
            ),
        ),
        "pricing": pricing(),
        "authorization": PaidRunAuthorization(
            experiment_id="phase5-pilot", allow_live_provider=True
        ),
        "mode": ExperimentMode.PILOT,
        "at_utc": lambda: datetime(2026, 7, 20, tzinfo=UTC),
    }
    first = GuardedProvider(
        delegate=first_delegate, identity=base_identity(), **common
    )
    second_identity = base_identity().model_copy(update={"condition_id": "b3_isolated"})
    second = GuardedProvider(
        delegate=second_delegate, identity=second_identity, **common
    )
    first_task = asyncio.create_task(first.complete(MESSAGES, GENERATION))
    second_task = asyncio.create_task(second.complete(MESSAGES, GENERATION))
    await asyncio.sleep(0)
    assert first_delegate.calls == second_delegate.calls == 1
    gate.set()
    await asyncio.gather(first_task, second_task)


@pytest.mark.asyncio
async def test_operational_database_never_stores_messages_or_raw_ids(
    tmp_path: Path,
) -> None:
    provider = guarded(tmp_path, CountingProvider())
    await provider.complete(MESSAGES, GENERATION)
    connection = sqlite3.connect(provider.cache.path)
    try:
        dump = "\n".join(connection.iterdump())
    finally:
        connection.close()
    for forbidden in (
        "Return JSON only",
        "Inspect the frozen candidate",
        "response-raw-id",
        "request-raw-id",
        "Authorization",
        "expected_findings",
        '"labels"',
        '"split"',
    ):
        assert forbidden not in dump


@pytest.mark.asyncio
async def test_missing_cache_after_charge_pauses_without_rebilling(tmp_path: Path) -> None:
    delegate = CountingProvider()
    provider = guarded(tmp_path, delegate)
    await provider.complete(MESSAGES, GENERATION)
    connection = sqlite3.connect(provider.cache.path)
    try:
        connection.execute("DELETE FROM provider_results")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CacheIntegrityError, match="charged provider request"):
        await provider.complete(MESSAGES, GENERATION)
    assert delegate.calls == 1
