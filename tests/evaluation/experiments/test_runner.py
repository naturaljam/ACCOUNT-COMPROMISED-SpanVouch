from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from spanvouch.contracts.diagnosis import DiagnosisStatus, ProviderUsage
from spanvouch.contracts.verification import VerifierVerdict
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.experiments.config import ConditionId, ModelEndpointConfig
from spanvouch.evaluation.experiments.models import (
    ConditionEvaluationEvidence,
    ConditionPlan,
    ConditionResult,
    ConditionStatus,
    ExperimentFailureCategory,
    ExperimentMatrixManifest,
    ProviderPlanStatus,
    SelectiveAction,
    VerifierEvaluationEvidence,
)
from spanvouch.evaluation.experiments.runner import (
    ExecutionAdmission,
    ExecutionPaused,
    ExperimentRunner,
    OutcomeStatus,
    PolicyNotInvoked,
    ProviderPhaseRepository,
    RunnerExecutionError,
)
from spanvouch.labs.runtime import FrameworkId


def _cell(framework: FrameworkId) -> CorpusCell:
    return CorpusCell(
        domain="supportlab",
        template_id="template-1",
        scenario_id="scenario-1",
        framework_id=framework,
        repetition=1,
        seed=20260720,
    )


def _plans_and_matrix() -> tuple[tuple[ConditionPlan, ...], ExperimentMatrixManifest]:
    cells = (_cell(FrameworkId.AUTOGEN), _cell(FrameworkId.LANGGRAPH))
    plans: list[ConditionPlan] = []
    for cell in cells:
        for condition in ConditionId:
            required = condition not in {ConditionId.B0, ConditionId.B1}
            endpoint = (
                ModelEndpointConfig(
                    provider=(
                        "deepseek"
                        if condition in {ConditionId.B2, ConditionId.B3}
                        else "qwen"
                    ),
                    model="model-1",
                    endpoint_class="offline",
                    prompt_version=f"prompt-{condition.value}",
                    max_tokens=100,
                    temperature=0.0,
                )
                if required else None
            )
            plans.append(
                ConditionPlan.from_payload(
                    experiment_id="phase5-pilot",
                    experiment_config_sha256="1" * 64,
                    corpus_manifest_sha256="2" * 64,
                    cell=cell,
                    record_sha256="3" * 64,
                    trace_sha256="4" * 64,
                    diagnosis_sha256="5" * 64,
                    condition_id=condition,
                    prompt_version=(
                        endpoint.prompt_version if endpoint else f"fixed-{condition.value}"
                    ),
                    provider_status=(
                        ProviderPlanStatus.REQUIRED
                        if required else ProviderPlanStatus.NOT_REQUIRED
                    ),
                    provider=endpoint.provider if endpoint else None,
                    model=endpoint.model if endpoint else None,
                    generation=endpoint,
                )
            )
    ordered = tuple(plans)
    matrix = ExperimentMatrixManifest(
        experiment_id="phase5-pilot",
        experiment_config_sha256="1" * 64,
        corpus_manifest_sha256="2" * 64,
        candidate_manifest_sha256="6" * 64,
        plan_ids=tuple(plan.plan_id for plan in ordered),
        eligible_cells=cells,
        ineligible=(),
        eligible_cell_count=2,
        ineligible_cell_count=0,
        condition_counts={condition: 2 for condition in ConditionId},
    )
    return ordered, matrix


def _result(plan: ConditionPlan, *, failed: bool = False) -> ConditionResult:
    started = datetime(2026, 7, 20, tzinfo=UTC)
    if failed:
        from spanvouch.evaluation.experiments.models import ExperimentFailure, FailureSource
        return ConditionResult(
            plan_id=plan.plan_id, cell=plan.cell,
            record_sha256=plan.record_sha256, trace_sha256=plan.trace_sha256,
            diagnosis_sha256=plan.diagnosis_sha256, condition_id=plan.condition_id,
            status=ConditionStatus.FAILED, selective_action=SelectiveAction.ABSTAIN,
            verifier_report_sha256s=(), request_audit_sha256s=("7" * 64,),
            cache_status="failed", started_at_utc=started,
            completed_at_utc=started + timedelta(seconds=1),
            failure=ExperimentFailure(
                category=ExperimentFailureCategory.PROVIDER,
                code="offline-provider-failed", source=FailureSource.PROVIDER_RUNNER,
            ),
        )
    verifier_hashes = () if plan.condition_id is ConditionId.B0 else ("8" * 64,)
    evidence_payload = {
        "diagnosis_report_sha256": plan.diagnosis_sha256,
        "diagnosis_status": DiagnosisStatus.NO_FAILURE,
        "diagnosis_family": "no_failure",
        "causal_stages": (),
        "causal_tokens": (),
        "diagnosis_selectors": ("span-root::attributes.run.outcome",),
        "verifier_reports": tuple(
            VerifierEvaluationEvidence(
                artifact_sha256=digest,
                verdict=VerifierVerdict.VERIFIED,
                finding_codes=(),
                selectors=(),
            )
            for digest in verifier_hashes
        ),
    }
    evidence = ConditionEvaluationEvidence.model_validate(
        {
            **evidence_payload,
            "projection_sha256": canonical_sha256(
                {
                    **evidence_payload,
                    "verifier_reports": [
                        item.model_dump(mode="json")
                        for item in evidence_payload["verifier_reports"]
                    ],
                }
            ),
        }
    )
    return ConditionResult(
        plan_id=plan.plan_id, cell=plan.cell,
        record_sha256=plan.record_sha256, trace_sha256=plan.trace_sha256,
        diagnosis_sha256=plan.diagnosis_sha256, condition_id=plan.condition_id,
        status=ConditionStatus.COMPLETED, selective_action=SelectiveAction.ACCEPT,
        verifier_report_sha256s=verifier_hashes, request_audit_sha256s=("9" * 64,),
        evaluation_evidence=evidence,
        usage=ProviderUsage(input_tokens=2, output_tokens=1, total_tokens=3,
                            latency_ms=1.0, request_id=None),
        cost_cny=Decimal("0.01"), cache_status="miss",
        started_at_utc=started, completed_at_utc=started + timedelta(seconds=1),
    )


class FakeExecutor:
    def __init__(self, behavior: dict[str, object] | None = None) -> None:
        self.behavior = behavior or {}
        self.calls: list[ConditionPlan] = []

    async def execute(self, plan: ConditionPlan) -> ConditionResult:
        self.calls.append(plan)
        behavior = self.behavior.get(plan.plan_id)
        if isinstance(behavior, BaseException):
            raise behavior
        if behavior == "policy":
            raise PolicyNotInvoked("deterministic-prerequisite-failed")
        if behavior == "pause":
            raise ExecutionPaused("budget-paused")
        return _result(plan, failed=behavior == "failed")


class Admission(ExecutionAdmission):
    def __init__(self, reject_condition: ConditionId | None = None) -> None:
        self.reject_condition = reject_condition
        self.groups: list[tuple[ConditionPlan, ...]] = []

    def admit(self, plans: tuple[ConditionPlan, ...]) -> bool:
        self.groups.append(plans)
        return not plans or plans[0].condition_id != self.reject_condition


@pytest.mark.asyncio
async def test_runner_accounts_for_every_plan_and_publishes_verified_manifest(
    tmp_path: Path,
) -> None:
    plans, matrix = _plans_and_matrix()
    behavior = {
        plans[2].plan_id: "failed",
        plans[4].plan_id: "policy",
        plans[9].plan_id: RunnerExecutionError(
            ExperimentFailureCategory.CONTRACT_INVALID, "invalid-verifier-output"
        ),
    }
    executor = FakeExecutor(behavior)
    repository = ProviderPhaseRepository(tmp_path / "provider-results")
    manifest = await ExperimentRunner(executor=executor).run_provider_phase(
        plans=plans, matrix=matrix, repository=repository
    )

    assert len(manifest.entries) == len(plans) == 12
    assert set(manifest.status_counts) == set(OutcomeStatus)
    assert sum(manifest.status_counts.values()) == 12
    assert manifest.provider_phase_complete is True
    assert manifest.total_input_tokens > 0
    assert manifest.total_cost_cny > 0
    verified = repository.verify(expected_manifest_sha256=repository.manifest_sha256)
    assert verified == manifest
    payload = manifest.model_dump_json()
    for forbidden in ("gold", "expected", "split", "label", "is_correct"):
        assert forbidden not in payload.casefold()


@pytest.mark.asyncio
async def test_budget_admission_pauses_whole_framework_pair_before_calls(
    tmp_path: Path,
) -> None:
    plans, matrix = _plans_and_matrix()
    executor = FakeExecutor()
    admission = Admission(ConditionId.B3)
    manifest = await ExperimentRunner(executor=executor, admission=admission).run_provider_phase(
        plans=plans,
        matrix=matrix,
        repository=ProviderPhaseRepository(tmp_path / "provider-results"),
    )
    b3 = [entry for entry in manifest.entries if entry.condition_id is ConditionId.B3]
    assert [entry.status for entry in b3] == [OutcomeStatus.PAUSED] * 2
    assert not [plan for plan in executor.calls if plan.condition_id is ConditionId.B3]
    assert manifest.provider_phase_complete is False


@pytest.mark.asyncio
async def test_paused_manifest_resumes_without_rebilling_completed_plans(
    tmp_path: Path,
) -> None:
    plans, matrix = _plans_and_matrix()
    repository = ProviderPhaseRepository(tmp_path / "provider-results")
    first_executor = FakeExecutor()
    first = await ExperimentRunner(
        executor=first_executor,
        admission=Admission(ConditionId.B3),
    ).run_provider_phase(plans=plans, matrix=matrix, repository=repository)
    first_manifest_sha256 = repository.manifest_sha256
    retained = {
        entry.plan_id: entry.outcome_sha256
        for entry in first.entries
        if entry.status is not OutcomeStatus.PAUSED
    }

    resumed_executor = FakeExecutor()
    resumed = await ExperimentRunner(executor=resumed_executor).run_provider_phase(
        plans=plans,
        matrix=matrix,
        repository=repository,
    )

    assert resumed.provider_phase_complete is True
    assert resumed.supersedes_manifest_sha256 == first_manifest_sha256
    assert {plan.condition_id for plan in resumed_executor.calls} == {ConditionId.B3}
    assert {
        entry.plan_id: entry.outcome_sha256
        for entry in resumed.entries
        if entry.plan_id in retained
    } == retained
    assert repository.verify_superseded_manifest(first_manifest_sha256) == first
    for entry in first.entries:
        if entry.status is OutcomeStatus.PAUSED:
            outcome = repository.load(entry.plan_id)
            assert outcome.supersedes_outcome_sha256 == entry.outcome_sha256
            archived = repository.load_superseded(entry.outcome_sha256)
            assert archived.status is OutcomeStatus.PAUSED
            assert archived.plan.plan_id == entry.plan_id
    never_paused = next(
        entry for entry in first.entries if entry.status is OutcomeStatus.COMPLETED
    )
    assert repository.latest_superseded(never_paused.plan_id) is None

    paused_entry = next(
        entry for entry in first.entries if entry.status is OutcomeStatus.PAUSED
    )
    archived_path = (
        repository.root
        / "history"
        / "r"
        / paused_entry.outcome_sha256[:12]
        / f"{paused_entry.outcome_sha256}.json"
    )
    archived_path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="superseded provider outcome"):
        repository.verify(expected_manifest_sha256=repository.manifest_sha256)


@pytest.mark.asyncio
async def test_executor_pause_stops_remaining_paid_pair_before_second_call(
    tmp_path: Path,
) -> None:
    plans, matrix = _plans_and_matrix()
    first_b2 = next(plan for plan in plans if plan.condition_id is ConditionId.B2)
    executor = FakeExecutor({first_b2.plan_id: "pause"})
    manifest = await ExperimentRunner(executor=executor).run_provider_phase(
        plans=plans,
        matrix=matrix,
        repository=ProviderPhaseRepository(tmp_path / "provider-results"),
    )
    b2 = [entry for entry in manifest.entries if entry.condition_id is ConditionId.B2]
    assert [entry.status for entry in b2] == [OutcomeStatus.PAUSED] * 2
    assert len([plan for plan in executor.calls if plan.condition_id is ConditionId.B2]) == 1


@pytest.mark.asyncio
async def test_interrupted_run_resumes_only_verified_exact_plan_results(
    tmp_path: Path,
) -> None:
    plans, matrix = _plans_and_matrix()

    class CrashingExecutor(FakeExecutor):
        async def execute(self, plan: ConditionPlan) -> ConditionResult:
            if len(self.calls) == 3:
                raise KeyboardInterrupt
            return await super().execute(plan)

    repository = ProviderPhaseRepository(tmp_path / "provider-results")
    first = CrashingExecutor()
    with pytest.raises(KeyboardInterrupt):
        await ExperimentRunner(executor=first).run_provider_phase(
            plans=plans, matrix=matrix, repository=repository
        )
    resumed = FakeExecutor()
    manifest = await ExperimentRunner(executor=resumed).run_provider_phase(
        plans=plans, matrix=matrix, repository=repository
    )
    assert manifest.provider_phase_complete
    assert len(resumed.calls) == len(plans) - 3
    assert len(manifest.entries) == len(plans)


@pytest.mark.asyncio
async def test_cancellation_and_typed_exceptions_never_disappear(
    tmp_path: Path,
) -> None:
    plans, matrix = _plans_and_matrix()
    executor = FakeExecutor({
        plans[0].plan_id: asyncio.CancelledError(),
        plans[1].plan_id: RunnerExecutionError(
            ExperimentFailureCategory.FRAMEWORK_INCOMPATIBILITY,
            "framework-incompatible",
        ),
    })
    manifest = await ExperimentRunner(executor=executor).run_provider_phase(
        plans=plans, matrix=matrix,
        repository=ProviderPhaseRepository(tmp_path / "provider-results"),
    )
    assert len(manifest.entries) == 12
    assert manifest.status_counts[OutcomeStatus.FAILED] == 2
    for entry in manifest.entries[:2]:
        outcome = ProviderPhaseRepository(tmp_path / "provider-results").load(entry.plan_id)
        assert outcome.result is not None
        assert outcome.result.selective_action is SelectiveAction.ABSTAIN
        assert outcome.result.failure is not None
        assert outcome.result.failure.is_correct is None


def test_provider_runner_signature_and_process_imports_have_no_label_boundary() -> None:
    signature = str(inspect.signature(ExperimentRunner.run_provider_phase)).casefold()
    assert all(word not in signature for word in ("label", "split", "expected", "gold"))
    script = (
        "import sys; import spanvouch.evaluation.experiments.runner; "
        "assert 'spanvouch.evaluation.corpus.labels' not in sys.modules"
    )
    completed = subprocess.run([sys.executable, "-c", script], check=False)
    assert completed.returncode == 0


@pytest.mark.asyncio
async def test_provider_phase_never_opens_or_serializes_label_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "gold-sentinel-must-not-reach-provider"
    sealed_path = tmp_path / f"sealed-labels-{sentinel}" / "manifest.json"
    sealed_path.parent.mkdir()
    sealed_path.write_text(sentinel, encoding="utf-8")
    opened: list[str] = []
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object) -> object:
        rendered = str(path)
        opened.append(rendered)
        if sentinel in rendered:
            raise AssertionError("provider phase opened sealed labels")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    class CapturingExecutor(FakeExecutor):
        async def execute(self, plan: ConditionPlan) -> ConditionResult:
            payload = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
            assert sentinel not in payload
            return await super().execute(plan)

    plans, matrix = _plans_and_matrix()
    output = tmp_path / "provider-results"
    await ExperimentRunner(executor=CapturingExecutor()).run_provider_phase(
        plans=plans,
        matrix=matrix,
        repository=ProviderPhaseRepository(output),
    )
    assert all(sentinel not in path for path in opened)
    artifact_bytes = b"".join(path.read_bytes() for path in output.rglob("*.json"))
    assert sentinel.encode() not in artifact_bytes
