import asyncio

import pytest

from afc.diagnosis.models import (
    DiagnoserKind,
    DiagnosisDecision,
    DiagnosisExecution,
    DiagnosisProvenance,
    DiagnosisStatus,
)
from afc.diagnosis.service import DiagnosisConflictError, DiagnosisService
from afc.failure_types import FailureType
from tests.diagnosis.test_trace_view import load_trace


class RecordingDiagnoser:
    version_fingerprint = "fake-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_identity = False

    async def diagnose(self, view: object, evidence: object) -> DiagnosisExecution:
        self.calls += 1
        self.seen_identity = hasattr(view, "trace_id") or hasattr(view, "run_id")
        return DiagnosisExecution(
            decision=DiagnosisDecision(
                status=DiagnosisStatus.NO_FAILURE,
                failure_type=FailureType.NO_FAILURE,
                confidence=1.0,
            ),
            provenance=DiagnosisProvenance(
                taxonomy_version="1.0",
                diagnoser_version="fake-v1",
            ),
        )


class FirstCallBlockingDiagnoser(RecordingDiagnoser):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def diagnose(self, view: object, evidence: object) -> DiagnosisExecution:
        execution = await super().diagnose(view, evidence)
        if self.calls == 1:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return execution


class FailOnceDiagnoser(RecordingDiagnoser):
    async def diagnose(self, view: object, evidence: object) -> DiagnosisExecution:
        if self.calls == 0:
            self.calls += 1
            raise RuntimeError("transient diagnosis failure")
        return await super().diagnose(view, evidence)


async def test_service_hides_identity_then_attaches_it_to_report() -> None:
    diagnoser = RecordingDiagnoser()
    service = DiagnosisService({DiagnoserKind.RULES: diagnoser})
    trace = load_trace("clean-01")

    report = await service.diagnose(trace, DiagnoserKind.RULES)

    assert diagnoser.seen_identity is False
    assert report.trace_id == trace.trace_id
    assert report.run_id == trace.run_id
    assert report.diagnoser is DiagnoserKind.RULES


async def test_service_caches_same_completed_fingerprint() -> None:
    diagnoser = RecordingDiagnoser()
    service = DiagnosisService({DiagnoserKind.RULES: diagnoser})
    trace = load_trace("clean-01")

    first = await service.diagnose(trace, DiagnoserKind.RULES, idempotency_key="same")
    second = await service.diagnose(trace, DiagnoserKind.RULES, idempotency_key="same")

    assert first == second
    assert diagnoser.calls == 1


async def test_service_rejects_idempotency_key_reuse_for_different_trace() -> None:
    diagnoser = RecordingDiagnoser()
    service = DiagnosisService({DiagnoserKind.RULES: diagnoser})
    await service.diagnose(
        load_trace("clean-01"), DiagnoserKind.RULES, idempotency_key="collision"
    )

    try:
        await service.diagnose(
            load_trace("clean-02"),
            DiagnoserKind.RULES,
            idempotency_key="collision",
        )
    except DiagnosisConflictError as exc:
        assert "collision" in str(exc)
    else:
        raise AssertionError("expected DiagnosisConflictError")


async def test_different_fingerprints_run_without_waiting_for_each_other() -> None:
    diagnoser = FirstCallBlockingDiagnoser()
    service = DiagnosisService({DiagnoserKind.RULES: diagnoser})
    blocked = asyncio.create_task(
        service.diagnose(load_trace("clean-01"), DiagnoserKind.RULES)
    )
    await asyncio.wait_for(diagnoser.entered.wait(), timeout=0.5)

    try:
        unrelated = await asyncio.wait_for(
            service.diagnose(load_trace("clean-02"), DiagnoserKind.RULES),
            timeout=0.2,
        )
    finally:
        diagnoser.release.set()
        await blocked

    assert unrelated.run_id == "clean-02"
    assert diagnoser.calls == 2


async def test_identical_fingerprints_share_one_inflight_result() -> None:
    diagnoser = FirstCallBlockingDiagnoser()
    service = DiagnosisService({DiagnoserKind.RULES: diagnoser})
    trace = load_trace("clean-01")
    first = asyncio.create_task(service.diagnose(trace, DiagnoserKind.RULES))
    await asyncio.wait_for(diagnoser.entered.wait(), timeout=0.5)
    second = asyncio.create_task(service.diagnose(trace, DiagnoserKind.RULES))
    await asyncio.sleep(0)

    assert diagnoser.calls == 1
    diagnoser.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is second_result
    assert diagnoser.calls == 1


async def test_cancelling_one_waiter_does_not_cancel_shared_diagnosis() -> None:
    diagnoser = FirstCallBlockingDiagnoser()
    service = DiagnosisService({DiagnoserKind.RULES: diagnoser})
    trace = load_trace("clean-01")
    cancelled_waiter = asyncio.create_task(service.diagnose(trace, DiagnoserKind.RULES))
    await asyncio.wait_for(diagnoser.entered.wait(), timeout=0.5)
    surviving_waiter = asyncio.create_task(service.diagnose(trace, DiagnoserKind.RULES))
    await asyncio.sleep(0)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    diagnoser.release.set()
    result = await asyncio.wait_for(surviving_waiter, timeout=0.5)

    assert result.run_id == "clean-01"
    assert diagnoser.calls == 1
    assert diagnoser.cancelled is False


async def test_failed_inflight_diagnosis_is_removed_and_retry_can_succeed() -> None:
    diagnoser = FailOnceDiagnoser()
    service = DiagnosisService({DiagnoserKind.RULES: diagnoser})
    trace = load_trace("clean-01")

    with pytest.raises(RuntimeError, match="transient diagnosis failure"):
        await service.diagnose(trace, DiagnoserKind.RULES, idempotency_key="retry")
    report = await service.diagnose(
        trace,
        DiagnoserKind.RULES,
        idempotency_key="retry",
    )

    assert report.run_id == "clean-01"
    assert diagnoser.calls == 2
