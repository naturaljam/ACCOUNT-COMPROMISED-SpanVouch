from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from spanvouch.evaluation.experiments.budget import (
    BudgetExceededError,
    BudgetLedger,
    BudgetOverrunError,
    GpuLeaseConflictError,
    GpuLeaseRecord,
    Pricing,
    ProviderRequestClaimError,
    UnknownPriceError,
)
from spanvouch.evaluation.experiments.config import (
    BudgetPolicy,
    ExperimentMode,
    GpuLeaseApproval,
)


def pricing() -> Pricing:
    return Pricing(
        provider="deepseek",
        model="deepseek-chat",
        currency="CNY",
        effective_date="2026-07-01",
        source_url="https://example.invalid/pricing",
        input_per_million=Decimal("2.00"),
        output_per_million=Decimal("8.00"),
        gpu_hourly=Decimal("5.00"),
        amounts="estimated",
    )


def usd_pricing() -> Pricing:
    return Pricing(
        provider="deepseek",
        model="deepseek-v4-flash",
        currency="USD",
        effective_date="2026-07-31",
        source_url="https://api-docs.deepseek.com/quick_start/pricing/",
        input_per_million=Decimal("0.14"),
        output_per_million=Decimal("0.28"),
        gpu_hourly=Decimal("0"),
        amounts="estimated",
        conversion={
            "budget_currency": "CNY",
            "reference_cny_per_native_unit": (
                "6.7550540257929592192401533635"
            ),
            "reserve_cny_per_native_unit": "7.10",
            "buffer_fraction": "0.05",
            "rounding_increment": "0.10",
            "effective_date": "2026-07-30",
            "source_urls": [
                "https://data-api.ecb.europa.eu/service/data/EXR/D.CNY.EUR.SP00.A",
                "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
            ],
        },
    )


def policy() -> BudgetPolicy:
    return BudgetPolicy(
        monthly_cap_cny=Decimal("100.00"),
        pilot_fraction=Decimal("0.10"),
        stop_fraction=Decimal("0.80"),
    )


def test_decimal_pricing_and_unknown_price() -> None:
    value = pricing().provider_cost(input_tokens=250_000, output_tokens=125_000)
    assert value == Decimal("1.500000")
    assert pricing().gpu_cost(Decimal("1.5")) == Decimal("7.500000")
    with pytest.raises(UnknownPriceError):
        pricing().require_endpoint("qwen", "other")


def test_usd_pricing_converts_with_validated_conservative_rate() -> None:
    assert usd_pricing().provider_cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    ) == Decimal("2.982000")


def test_pricing_rejects_missing_or_inconsistent_conversion() -> None:
    payload = usd_pricing().model_dump(mode="python")
    with pytest.raises(ValueError, match="USD pricing requires"):
        Pricing.model_validate({**payload, "conversion": None})

    conversion = dict(payload["conversion"])
    with pytest.raises(ValueError, match="reserve rate"):
        Pricing.model_validate(
            {
                **payload,
                "conversion": {
                    **conversion,
                    "reserve_cny_per_native_unit": "7.09",
                },
            }
        )
    with pytest.raises(ValueError, match="source URLs"):
        Pricing.model_validate(
            {
                **payload,
                "conversion": {**conversion, "source_urls": [""]},
            }
        )
    with pytest.raises(ValueError, match="CNY pricing must not"):
        Pricing.model_validate({**payload, "currency": "CNY"})


def test_pricing_rejects_input_above_frozen_tier() -> None:
    limited = pricing().model_copy(update={"max_input_tokens": 256_000})
    with pytest.raises(UnknownPriceError, match="pricing tier"):
        limited.provider_cost(input_tokens=256_001, output_tokens=1)


def test_rejects_nonpersistent_sqlite_paths() -> None:
    with pytest.raises(ValueError, match="persistent filesystem path"):
        BudgetLedger(Path(":memory:"), policy())
    with pytest.raises(ValueError, match="persistent filesystem path"):
        BudgetLedger(Path("file:phase5?mode=memory&cache=shared"), policy())


def test_pilot_cap_global_stop_and_month_rollover(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "phase5.sqlite3", policy())
    july = datetime(2026, 7, 31, 23, tzinfo=UTC)
    august = datetime(2026, 8, 1, tzinfo=UTC)
    first = ledger.reserve(
        request_sha256="a" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("9.50"),
        mode=ExperimentMode.PILOT,
        at_utc=july,
    )
    with pytest.raises(BudgetExceededError):
        ledger.reserve(
            request_sha256="b" * 64,
            experiment_id="phase5-pilot",
            amount=Decimal("0.51"),
            mode=ExperimentMode.PILOT,
            at_utc=july,
        )
    ledger.release(first, at_utc=july)
    formal = ledger.reserve(
        request_sha256="c" * 64,
        experiment_id="phase5-formal",
        amount=Decimal("80.00"),
        mode=ExperimentMode.FORMAL,
        at_utc=july,
    )
    with pytest.raises(BudgetExceededError):
        ledger.reserve(
            request_sha256="d" * 64,
            experiment_id="phase5-formal",
            amount=Decimal("0.01"),
            mode=ExperimentMode.FORMAL,
            at_utc=july,
        )
    ledger.release(formal, at_utc=july)
    assert ledger.reserve(
        request_sha256="e" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("10.00"),
        mode=ExperimentMode.PILOT,
        at_utc=august,
    )


def test_reserve_many_is_all_or_nothing_for_paired_matrix(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "phase5.sqlite3", policy())
    at = datetime(2026, 7, 20, tzinfo=UTC)
    with pytest.raises(BudgetExceededError):
        ledger.reserve_many(
            requests=(("a" * 64, Decimal("6.00")), ("b" * 64, Decimal("6.00"))),
            experiment_id="phase5-pilot",
            mode=ExperimentMode.PILOT,
            at_utc=at,
        )
    assert ledger.active_reserved_total(at) == Decimal("0")


def test_commit_overrun_is_charged_and_stops_subsequent_work(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "phase5.sqlite3", policy())
    at = datetime(2026, 7, 20, tzinfo=UTC)
    reservation = ledger.reserve(
        request_sha256="a" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1.00"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    with pytest.raises(BudgetOverrunError, match="exceeded reservation"):
        ledger.commit(reservation, actual_amount=Decimal("1.25"), at_utc=at)
    assert ledger.committed_total(at) == Decimal("1.25")
    assert ledger.active_reserved_total(at) == Decimal("0")
    with pytest.raises(ValueError, match="closed"):
        ledger.release(reservation, at_utc=at)
    with pytest.raises(BudgetExceededError, match="stopped"):
        ledger.reserve(
            request_sha256="b" * 64,
            experiment_id="phase5-pilot",
            amount=Decimal("1.00"),
            mode=ExperimentMode.PILOT,
            at_utc=at,
        )


def test_gpu_lease_is_charged_and_participates_in_stop_rule(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "phase5.sqlite3", policy())
    at = datetime(2026, 7, 20, tzinfo=UTC)
    cost = ledger.record_gpu_lease(
        lease_id="gpu-1",
        experiment_id="phase5-formal",
        mode=ExperimentMode.FORMAL,
        hours=Decimal("2"),
        pricing=pricing(),
        at_utc=at,
    )
    assert cost == Decimal("10.000000")
    assert ledger.committed_total(at) == Decimal("10.000000")


def test_pilot_gpu_lease_uses_pilot_cap(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "phase5.sqlite3", policy())
    at = datetime(2026, 7, 20, tzinfo=UTC)

    with pytest.raises(BudgetExceededError):
        ledger.record_gpu_lease(
            lease_id="gpu-pilot-over-cap",
            experiment_id="phase5-pilot",
            mode=ExperimentMode.PILOT,
            hours=Decimal("2.01"),
            pricing=pricing(),
            at_utc=at,
        )

    assert ledger.committed_total(at) == Decimal("0")


def _gpu_approval() -> GpuLeaseApproval:
    return GpuLeaseApproval(
        cloud_provider="example-cloud",
        region="test-region-1",
        instance_type="gpu-48gb",
        maximum_hours=Decimal("2"),
        maximum_cost_cny=Decimal("10"),
    )


def _gpu_record() -> GpuLeaseRecord:
    return GpuLeaseRecord(
        lease_id="lease-test-001",
        cloud_provider="example-cloud",
        region="test-region-1",
        instance_type="gpu-48gb",
        started_at_utc=datetime(2026, 7, 20, tzinfo=UTC),
        ended_at_utc=datetime(2026, 7, 20, 1, tzinfo=UTC),
        duration_hours=Decimal("1"),
    )


def test_gpu_lease_record_is_atomic_idempotent_and_conflict_safe(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "global.sqlite3", policy())
    record = _gpu_record()
    kwargs = {
        "record": record,
        "approval": _gpu_approval(),
        "experiment_id": "phase5-pilot",
        "matrix_manifest_sha256": "a" * 64,
        "mode": ExperimentMode.PILOT,
        "pricing": pricing(),
    }

    assert ledger.record_gpu_lease_record(**kwargs) == Decimal("5.000000")
    assert ledger.record_gpu_lease_record(**kwargs) == Decimal("5.000000")
    assert ledger.committed_total(record.ended_at_utc) == Decimal("5.000000")

    with pytest.raises(GpuLeaseConflictError, match="conflict"):
        ledger.record_gpu_lease_record(
            **{**kwargs, "record": record.model_copy(update={"region": "other"})}
        )


def test_gpu_lease_record_rejects_approval_and_monthly_limit_drift(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "global.sqlite3", policy())
    record = _gpu_record()
    with pytest.raises(ValueError, match="approval"):
        ledger.record_gpu_lease_record(
            record=record,
            approval=_gpu_approval().model_copy(update={"maximum_hours": Decimal("0.5")}),
            experiment_id="phase5-pilot",
            matrix_manifest_sha256="a" * 64,
            mode=ExperimentMode.PILOT,
            pricing=pricing(),
        )
    assert ledger.committed_total(record.ended_at_utc) == 0


def test_concurrent_reservation_cannot_overspend(tmp_path: Path) -> None:
    path = tmp_path / "phase5.sqlite3"
    at = datetime(2026, 7, 20, tzinfo=UTC)

    def reserve(index: int) -> bool:
        ledger = BudgetLedger(path, policy())
        try:
            ledger.reserve(
                request_sha256=f"{index:064x}",
                experiment_id="phase5-pilot",
                amount=Decimal("6.00"),
                mode=ExperimentMode.PILOT,
                at_utc=at,
            )
        except BudgetExceededError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(reserve, range(2)))
    assert sorted(results) == [False, True]


def test_concurrent_identical_request_has_one_global_reservation(tmp_path: Path) -> None:
    path = tmp_path / "phase5.sqlite3"
    at = datetime(2026, 7, 20, tzinfo=UTC)

    def reserve(_: int) -> object | None:
        ledger = BudgetLedger(path, policy())
        try:
            return ledger.reserve(
                request_sha256="a" * 64,
                experiment_id="phase5-pilot",
                amount=Decimal("1"),
                mode=ExperimentMode.PILOT,
                at_utc=at,
            )
        except ProviderRequestClaimError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = tuple(pool.map(reserve, range(2)))
    assert sum(item is not None for item in reservations) == 1


def test_batch_claim_conflict_rolls_back_every_new_request(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "phase5.sqlite3", policy())
    at = datetime(2026, 7, 20, tzinfo=UTC)
    active = ledger.reserve(
        request_sha256="a" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    with pytest.raises(
        ProviderRequestClaimError,
        match="provider request is already active globally",
    ):
        ledger.reserve_many(
            requests=(("a" * 64, Decimal("1")), ("b" * 64, Decimal("1"))),
            experiment_id="phase5-pilot",
            mode=ExperimentMode.PILOT,
            at_utc=at,
        )
    ledger.release(active, at_utc=at)
    retry = ledger.reserve(
        request_sha256="b" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    ledger.release(retry, at_utc=at)


def _remove_provider_claims_table(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE provider_request_claims")


def test_legacy_active_claim_is_backfilled_and_can_release_or_commit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "phase5.sqlite3"
    at = datetime(2026, 7, 20, tzinfo=UTC)
    legacy = BudgetLedger(path, policy())
    released = legacy.reserve(
        request_sha256="a" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    committed = legacy.reserve(
        request_sha256="b" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    _remove_provider_claims_table(path)

    upgraded = BudgetLedger(path, policy())
    with pytest.raises(ProviderRequestClaimError):
        upgraded.reserve(
            request_sha256="a" * 64,
            experiment_id="phase5-pilot",
            amount=Decimal("1"),
            mode=ExperimentMode.PILOT,
            at_utc=at,
        )
    upgraded.release(released, at_utc=at)
    upgraded.commit(committed, actual_amount=Decimal("0.5"), at_utc=at)

    retry = upgraded.reserve(
        request_sha256="a" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    upgraded.release(retry, at_utc=at)
    with pytest.raises(ProviderRequestClaimError):
        upgraded.reserve(
            request_sha256="b" * 64,
            experiment_id="phase5-pilot",
            amount=Decimal("1"),
            mode=ExperimentMode.PILOT,
            at_utc=at,
        )


def test_legacy_committed_claim_is_backfilled_but_released_is_retryable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "phase5.sqlite3"
    at = datetime(2026, 7, 20, tzinfo=UTC)
    legacy = BudgetLedger(path, policy())
    charged = legacy.reserve(
        request_sha256="a" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    legacy.commit(charged, actual_amount=Decimal("0.5"), at_utc=at)
    closed = legacy.reserve(
        request_sha256="b" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    legacy.release(closed, at_utc=at)
    _remove_provider_claims_table(path)

    upgraded = BudgetLedger(path, policy())
    with pytest.raises(ProviderRequestClaimError):
        upgraded.reserve(
            request_sha256="a" * 64,
            experiment_id="phase5-pilot",
            amount=Decimal("1"),
            mode=ExperimentMode.PILOT,
            at_utc=at,
        )
    retry = upgraded.reserve(
        request_sha256="b" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    upgraded.release(retry, at_utc=at)


def test_legacy_duplicate_active_claims_fail_without_partial_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "phase5.sqlite3"
    at = datetime(2026, 7, 20, tzinfo=UTC)
    legacy = BudgetLedger(path, policy())
    first = legacy.reserve(
        request_sha256="a" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    other = legacy.reserve(
        request_sha256="b" * 64,
        experiment_id="phase5-pilot",
        amount=Decimal("1"),
        mode=ExperimentMode.PILOT,
        at_utc=at,
    )
    _remove_provider_claims_table(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO reservation_events
               (reservation_id, request_sha256, experiment_id, month_key,
                amount, mode, action, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?)""",
            (
                "legacy-conflict",
                first.request_sha256,
                first.experiment_id,
                first.month_key,
                str(first.amount),
                first.mode.value,
                at.isoformat(),
            ),
        )

    with pytest.raises(ProviderRequestClaimError, match="legacy"):
        BudgetLedger(path, policy())
    with sqlite3.connect(path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM provider_request_claims"
        ).fetchone()
    assert count == (0,)
    assert other.request_sha256 != first.request_sha256


def test_busy_database_fails_typed_instead_of_bypassing_reservation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "phase5.sqlite3"
    ledger = BudgetLedger(path, policy())
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        from spanvouch.evaluation.experiments.budget import BudgetBusyError

        with pytest.raises(BudgetBusyError):
            ledger.reserve(
                request_sha256="a" * 64,
                experiment_id="phase5-pilot",
                amount=Decimal("1"),
                mode=ExperimentMode.PILOT,
                at_utc=datetime(2026, 7, 20, tzinfo=UTC),
            )
    finally:
        blocker.rollback()
        blocker.close()


def test_pricing_example_and_ignore_rules_are_explicit() -> None:
    root = Path(__file__).parents[3]
    payload = json.loads(
        (root / "evals/configs/phase5-pricing.example.json").read_text("utf-8")
    )
    assert set(payload) == {
        "amounts",
        "currency",
        "effective_date",
        "gpu_hourly",
        "input_per_million",
        "model",
        "output_per_million",
        "provider",
        "source_url",
    }
    assert "current" not in json.dumps(payload).casefold()
    ignored = (root / ".gitignore").read_text("utf-8")
    assert ".cache/phase5/" in ignored
    assert "*.provider-credentials.json" in ignored
    assert "*.gpu-credentials.json" in ignored
