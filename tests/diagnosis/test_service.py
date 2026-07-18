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
