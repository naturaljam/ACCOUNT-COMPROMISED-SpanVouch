import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
from spanvouch.evaluation.corpus import (
    CorpusCell,
    CorpusEntry,
    CorpusManifestMetadata,
    TraceReplayRepository,
)
from spanvouch.evaluation.experiments.config import ConditionId, load_experiment_config
from spanvouch.evaluation.experiments.diagnosis import (
    DiagnosisCandidateRepository,
    FrozenDiagnosisCandidate,
    generate_and_freeze_diagnosis,
)
from spanvouch.evaluation.experiments.models import (
    ConditionPlan,
    ExperimentMatrixManifest,
    ProviderPlanStatus,
)
from spanvouch.evaluation.experiments.planner import VerificationMatrixPlanner
from spanvouch.labs.runtime import FrameworkId
from tests.evaluation.corpus.conftest import make_record


class _OfflineProvider:
    async def complete(
        self, messages: tuple[ChatMessage, ...], config: GenerationConfig
    ) -> ProviderResponse:
        return ProviderResponse(
            content=json.dumps(
                {
                    "status": "no_failure",
                    "failure_type": "no_failure",
                    "critical_span_ids": [],
                    "causal_chain": [],
                    "confidence": 0.5,
                    "abstain_reason": None,
                }
            ),
            model=config.model,
            response_id="offline-request",
            finish_reason="stop",
            usage=ProviderUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1.0,
                request_id="offline-request",
            ),
        )


async def _candidate_pair(tmp_path: Path) -> tuple[FrozenDiagnosisCandidate, ...]:
    records = tuple(
        make_record(framework_id=framework, repetition=1, seed=20260719)
        for framework in (FrameworkId.LANGGRAPH, FrameworkId.AUTOGEN)
    )
    metadata = CorpusManifestMetadata(
        corpus_id="matrix-fixture",
        mode="pilot",
        experiment_config_sha256="1" * 64,
        git_commit="b" * 40,
        dependency_lock_sha256="c" * 64,
        dataset_manifest_sha256="d" * 64,
        dirty_worktree=False,
        expected_cell_count=2,
        expected_pair_count=0,
        created_at_utc=datetime(2026, 7, 20, tzinfo=UTC),
        parity_results_sha256=canonical_sha256([]),
    )
    corpus = TraceReplayRepository.freeze(
        records=records,
        parity_results=(),
        destination=tmp_path / "corpus",
        manifest_metadata=metadata,
    )
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")
    candidates: list[FrozenDiagnosisCandidate] = []
    for record in records:
        entry = CorpusEntry.from_record(record)
        candidates.append(
            await generate_and_freeze_diagnosis(
                corpus=corpus,
                cell=entry.cell,
                expected_corpus_manifest_sha256=corpus.manifest_sha256,
                expected_record_sha256=entry.record_sha256,
                expected_trace_sha256=entry.trace_sha256,
                provider=_OfflineProvider(),
                generation=GenerationConfig(),
                repository=repository,
                verifier_instruction="Critique evidence sufficiency only.",
            )
        )
    return tuple(candidates)


def _expected_cells(
    candidates: tuple[FrozenDiagnosisCandidate, ...],
) -> tuple[CorpusCell, ...]:
    return tuple(candidate.cell for candidate in candidates)


@pytest.mark.asyncio
async def test_planner_emits_exactly_six_ordered_conditions_per_candidate(
    tmp_path: Path,
) -> None:
    candidates = await _candidate_pair(tmp_path)
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    plans = VerificationMatrixPlanner().plan(
        candidates, config, expected_cells=_expected_cells(candidates)
    )

    assert len(plans) == 12
    for candidate in candidates:
        own = [plan for plan in plans if plan.diagnosis_sha256 == candidate.report_sha256]
        assert [plan.condition_id for plan in own] == list(ConditionId)
    assert [plan.cell.framework_id for plan in plans[:6]] == [FrameworkId.AUTOGEN] * 6
    assert len({plan.plan_id for plan in plans}) == 12

    for plan in plans:
        if plan.condition_id in {ConditionId.B0, ConditionId.B1}:
            assert plan.provider_status is ProviderPlanStatus.NOT_REQUIRED
            assert plan.provider is None and plan.model is None
        elif plan.condition_id in {ConditionId.B2, ConditionId.B3}:
            assert plan.provider == "deepseek"
        else:
            assert plan.provider == "qwen"


@pytest.mark.asyncio
async def test_plan_id_changes_for_every_causal_binding(tmp_path: Path) -> None:
    candidates = await _candidate_pair(tmp_path)
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    plan = VerificationMatrixPlanner().plan(
        candidates, config, expected_cells=_expected_cells(candidates)
    )[2]
    payload = plan.model_dump(mode="python", exclude={"plan_id"})

    for field, value in (
        ("experiment_id", "phase5-other"),
        ("trace_sha256", "a" * 64),
        ("diagnosis_sha256", "b" * 64),
        ("prompt_version", "changed-v2"),
        ("provider", "other-provider"),
        ("model", "other-model"),
        ("generation", plan.generation.model_copy(update={"max_tokens": 2047})),
    ):
        changed = {**payload, field: value}
        if field in {"prompt_version", "provider", "model"}:
            assert plan.generation is not None
            changed["generation"] = plan.generation.model_copy(update={field: value})
        changed_plan = ConditionPlan.from_payload(**changed)
        assert changed_plan.plan_id != plan.plan_id


@pytest.mark.asyncio
async def test_validator_rejects_missing_duplicate_drift_and_unpaired_cells(
    tmp_path: Path,
) -> None:
    candidates = await _candidate_pair(tmp_path)
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    planner = VerificationMatrixPlanner()
    expected_cells = _expected_cells(candidates)
    plans = planner.plan(candidates, config, expected_cells=expected_cells)

    with pytest.raises(ValueError, match="six conditions"):
        planner.validate(
            plans[:-1], candidates, config, expected_cells=expected_cells
        )
    with pytest.raises(ValueError, match="duplicate"):
        planner.validate(
            plans[:-1] + (plans[0],),
            candidates,
            config,
            expected_cells=expected_cells,
        )
    with pytest.raises(ValueError, match="paired"):
        planner.plan(candidates[:1], config, expected_cells=expected_cells)

    drifted_payload = plans[2].model_dump(mode="python", exclude={"plan_id"})
    drifted_payload["provider"] = "drifted"
    assert plans[2].generation is not None
    drifted_payload["generation"] = plans[2].generation.model_copy(
        update={"provider": "drifted"}
    )
    drifted = ConditionPlan.from_payload(**drifted_payload)
    with pytest.raises(ValueError, match="provider"):
        planner.validate(
            (plans[0], plans[1], drifted, *plans[3:]),
            candidates,
            config,
            expected_cells=expected_cells,
        )


@pytest.mark.asyncio
async def test_planner_rejects_whole_pair_omitted_from_expected_universe(
    tmp_path: Path,
) -> None:
    candidates = await _candidate_pair(tmp_path)
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    omitted_pair = tuple(
        candidate.cell.model_copy(update={"repetition": 2}) for candidate in candidates
    )

    with pytest.raises(ValueError, match="exactly partition"):
        VerificationMatrixPlanner().plan(
            candidates,
            config,
            expected_cells=(*_expected_cells(candidates), *omitted_pair),
        )


@pytest.mark.asyncio
async def test_manifest_is_complete_and_contains_no_label_identity(tmp_path: Path) -> None:
    candidates = await _candidate_pair(tmp_path)
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    planner = VerificationMatrixPlanner()
    expected_cells = _expected_cells(candidates)
    plans = planner.plan(candidates, config, expected_cells=expected_cells)
    manifest = ExperimentMatrixManifest.from_plans(
        plans=plans,
        candidates=candidates,
        config=config,
        candidate_manifest_sha256="e" * 64,
        ineligible=(),
        expected_cells=expected_cells,
    )

    assert manifest.plan_ids == tuple(plan.plan_id for plan in plans)
    assert manifest.eligible_cell_count == 2
    assert manifest.ineligible_cell_count == 0
    assert set(manifest.condition_counts) == set(ConditionId)
    assert set(manifest.condition_counts.values()) == {2}
    serialized = manifest.model_dump_json().lower()
    assert "label" not in serialized
    assert "gold" not in serialized
    assert "split" not in serialized

    payload = manifest.model_dump(mode="python")
    payload["plan_ids"] = payload["plan_ids"][:-1]
    with pytest.raises(ValidationError):
        ExperimentMatrixManifest.model_validate(payload)
