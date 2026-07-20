from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from spanvouch.contracts.trace import SpanKind, SpanStatus, TraceIR, TraceSpan
from spanvouch.contracts.versioning import canonical_bytes, canonical_sha256
from spanvouch.evaluation.corpus import (
    CorpusCell,
    CorpusManifest,
    CorpusManifestMetadata,
    Phase5CorpusPlan,
    TraceReplayRepository,
)
from spanvouch.evaluation.corpus import generate as corpus_generate
from spanvouch.evaluation.corpus import repository as repository_module
from spanvouch.evaluation.corpus.generate import (
    build_corpus_plan,
    generate_phase5_corpus,
)
from spanvouch.evaluation.experiments import (
    ConditionId,
    ExperimentMode,
    FormalFreezePolicy,
    Phase5ExperimentConfig,
    freeze_formal_config,
    load_experiment_config,
)
from spanvouch.labs.runtime import (
    ExecutionProvenance,
    ExecutionRecord,
    ExecutionStatus,
    FrameworkId,
    LabScenario,
    ParityDimension,
    ParityMismatch,
    ParityResult,
    RuntimeConfig,
    RuntimeState,
    logical_execution_payload,
)

SUPPORTLAB_SCENARIOS = 20
OPSLAB_TEMPLATES = 16
FRAMEWORKS = 2
PILOT_REPETITIONS = 3
EXPECTED_PILOT_CELLS = (
    (SUPPORTLAB_SCENARIOS + OPSLAB_TEMPLATES) * FRAMEWORKS * PILOT_REPETITIONS
)
EXPECTED_SCENARIO_ORDER = (
    ("supportlab", "missing_precondition-01", "missing_precondition-01"),
    ("supportlab", "invalid_final_state-01", "invalid_final_state-01"),
    ("supportlab", "clean-01", "clean-01"),
    ("supportlab", "clean-04", "clean-04"),
    ("supportlab", "policy_violation-02", "policy_violation-02"),
    ("supportlab", "invalid_final_state-02", "invalid_final_state-02"),
    ("supportlab", "clean-02", "clean-02"),
    ("supportlab", "ignored_tool_error-02", "ignored_tool_error-02"),
    ("supportlab", "wrong_tool-01", "wrong_tool-01"),
    ("supportlab", "context_corruption-02", "context_corruption-02"),
    ("supportlab", "policy_violation-01", "policy_violation-01"),
    ("supportlab", "invalid_argument-02", "invalid_argument-02"),
    ("supportlab", "invalid_argument-01", "invalid_argument-01"),
    ("supportlab", "clean-03", "clean-03"),
    ("supportlab", "loop_or_budget_exhaustion-01", "loop_or_budget_exhaustion-01"),
    ("supportlab", "context_corruption-01", "context_corruption-01"),
    ("supportlab", "wrong_tool-02", "wrong_tool-02"),
    ("supportlab", "ignored_tool_error-01", "ignored_tool_error-01"),
    ("supportlab", "loop_or_budget_exhaustion-02", "loop_or_budget_exhaustion-02"),
    ("supportlab", "missing_precondition-02", "missing_precondition-02"),
    ("opslab", "timeout-no-retry", "timeout-no-retry"),
    ("opslab", "timeout-unbounded-retry", "timeout-unbounded-retry"),
    ("opslab", "retry-amplification", "retry-amplification"),
    ("opslab", "timeout-control", "timeout-control"),
    ("opslab", "rate-limit-unhandled", "rate-limit-unhandled"),
    ("opslab", "resource-exhaustion", "resource-exhaustion"),
    ("opslab", "degradation-missing", "degradation-missing"),
    ("opslab", "resource-control", "resource-control"),
    ("opslab", "lease-expiry", "lease-expiry"),
    ("opslab", "lock-contention", "lock-contention"),
    ("opslab", "deadlock-cycle", "deadlock-cycle"),
    ("opslab", "concurrency-control", "concurrency-control"),
    ("opslab", "checkpoint-stale", "checkpoint-stale"),
    ("opslab", "resume-duplicate", "resume-duplicate"),
    ("opslab", "workflow-state-drift", "workflow-state-drift"),
    ("opslab", "recovery-control", "recovery-control"),
)
INVALID_CONFIG_UPDATES = (
    ({"repetitions": 1}, "greater than or equal to 3"),
    ({"mode": ExperimentMode.FORMAL}, "formal configuration must be frozen"),
    ({"frameworks": ("langgraph",)}, "both frameworks exactly once"),
    ({"conditions": tuple(ConditionId)[:-1]}, "all six conditions exactly once"),
)


def _pilot_config() -> Phase5ExperimentConfig:
    return load_experiment_config(Path("evals/configs/phase5-pilot.json"))


def _provenance(*, dirty: bool = False) -> ExecutionProvenance:
    return ExecutionProvenance(
        git_commit="a" * 40,
        package_version="0.2.0",
        dependency_lock_sha256="b" * 64,
        dataset_manifest_sha256="c" * 64,
        environment_sha256="d" * 64,
        tool_versions={"opslab": "1.0", "supportlab": "1.0"},
        runtime_versions={"python": "3.12.10"},
        dirty_worktree=dirty,
    )


class _RecordingAdapter:
    framework_version = "test-1.0"

    def __init__(
        self,
        framework_id: FrameworkId,
        *,
        final_message: str = "complete",
        provenance: ExecutionProvenance | None = None,
    ) -> None:
        self.framework_id = framework_id
        self.final_message = final_message
        self.provenance = provenance or _provenance()
        self.calls: list[tuple[str, int, int]] = []

    async def execute(
        self, scenario: LabScenario, run_config: RuntimeConfig
    ) -> ExecutionRecord:
        self.calls.append((scenario.scenario_id, run_config.repetition, run_config.seed))
        started = datetime(2026, 7, 19, tzinfo=UTC) + timedelta(
            seconds=run_config.repetition
        )
        identity = (
            f"{scenario.scenario_id}:{self.framework_id.value}:"
            f"{run_config.repetition}:{run_config.seed}"
        )
        trace_id = sha256(identity.encode()).hexdigest()[:32]
        trace = TraceIR(
            trace_id=trace_id,
            run_id=scenario.scenario_id,
            spans=[
                TraceSpan(
                    trace_id=trace_id,
                    span_id=sha256(f"span:{identity}".encode()).hexdigest()[:16],
                    name=f"{scenario.domain}.run",
                    kind=SpanKind.AGENT,
                    status=SpanStatus.OK,
                    started_at=started,
                    ended_at=started + timedelta(seconds=1),
                    attributes={"run.outcome": "succeeded"},
                )
            ],
        )
        return ExecutionRecord.from_run(
            scenario=scenario,
            run_config=run_config,
            framework_id=self.framework_id,
            framework_version=self.framework_version,
            trace=trace,
            state=RuntimeState.initial().with_final(self.final_message),
            status=ExecutionStatus.SUCCEEDED,
            failure=None,
            started_at=started,
            completed_at=started + timedelta(seconds=1),
            provenance=self.provenance,
        )


class _FailIfExecutedAdapter:
    framework_version = "must-not-execute"

    def __init__(self, framework_id: FrameworkId) -> None:
        self.framework_id = framework_id

    async def execute(
        self, scenario: LabScenario, run_config: RuntimeConfig
    ) -> ExecutionRecord:
        raise AssertionError("invalid configuration reached adapter execution")


class _MixedProvenanceAdapter(_RecordingAdapter):
    async def execute(
        self, scenario: LabScenario, run_config: RuntimeConfig
    ) -> ExecutionRecord:
        if self.calls:
            self.provenance = self.provenance.model_copy(
                update={
                    "package_version": "0.2.1-mixed",
                    "environment_sha256": "e" * 64,
                    "tool_versions": {"opslab": "2.0", "supportlab": "1.0"},
                    "runtime_versions": {"python": "3.12.11"},
                }
            )
        return await super().execute(scenario, run_config)


def test_pilot_corpus_plan_is_complete() -> None:
    plan = build_corpus_plan(_pilot_config())

    assert len(plan) == EXPECTED_PILOT_CELLS == 216
    assert len({cell.identity for cell in plan}) == 216


@pytest.mark.parametrize(("update", "message"), INVALID_CONFIG_UPDATES)
def test_build_corpus_plan_round_trip_revalidates_copied_config(
    update: dict[str, object],
    message: str,
) -> None:
    invalid = _pilot_config().model_copy(update=update)

    with pytest.raises(ValueError, match=message):
        build_corpus_plan(invalid)


@pytest.mark.parametrize(("update", "message"), INVALID_CONFIG_UPDATES)
async def test_generation_round_trip_revalidates_copied_config_before_execution(
    tmp_path: Path,
    update: dict[str, object],
    message: str,
) -> None:
    invalid = _pilot_config().model_copy(update=update)
    destination = tmp_path / "invalid-corpus"

    with pytest.raises(ValueError, match=message):
        await generate_phase5_corpus(
            config=invalid,
            destination=destination,
            adapters={
                FrameworkId.LANGGRAPH: _FailIfExecutedAdapter(FrameworkId.LANGGRAPH),
                FrameworkId.AUTOGEN: _FailIfExecutedAdapter(FrameworkId.AUTOGEN),
            },
            provenance=_provenance(),
        )

    assert not destination.exists()


def test_pilot_corpus_plan_orders_each_seeded_framework_pair_together() -> None:
    plan = build_corpus_plan(_pilot_config())

    pairs = tuple(zip(plan[::2], plan[1::2], strict=True))
    assert all(left.scenario == right.scenario for left, right in pairs)
    assert all(left.repetition == right.repetition for left, right in pairs)
    assert all(left.seed == right.seed for left, right in pairs)
    assert all(
        (left.framework_id, right.framework_id)
        == (FrameworkId.LANGGRAPH, FrameworkId.AUTOGEN)
        for left, right in pairs
    )


def test_pilot_corpus_plan_locks_the_full_ordered_identity_and_seed_sequence() -> None:
    plan = build_corpus_plan(_pilot_config())
    expected_identities: list[str] = []
    expected_seeds: list[int] = []
    seed = 20260719
    for domain, template_id, scenario_id in EXPECTED_SCENARIO_ORDER:
        for repetition in (1, 2, 3):
            for framework in ("langgraph", "autogen"):
                expected_identities.append(
                    f"{domain}:{template_id}:{scenario_id}:{framework}:{repetition}:{seed}"
                )
                expected_seeds.append(seed)
            seed += 1

    assert tuple(cell.identity for cell in plan) == tuple(expected_identities)
    assert tuple(cell.seed for cell in plan) == tuple(expected_seeds)


def test_formal_plan_binding_uses_validated_frozen_repetitions() -> None:
    pilot = _pilot_config()
    policy = FormalFreezePolicy.model_validate_json(
        Path("evals/configs/phase5-formal-policy.json").read_text(encoding="utf-8")
    )
    formal = freeze_formal_config(
        pilot,
        policy,
        repetitions=policy.minimum_repetitions,
        coverage_loss_tolerance=0.05,
        frozen_at_utc=datetime(2026, 7, 19, tzinfo=UTC),
    )
    plan = build_corpus_plan(formal)
    config_sha256 = canonical_sha256(formal.model_dump(mode="json"))
    binding = Phase5CorpusPlan.from_cells(
        mode="formal",
        repetitions=formal.repetitions,
        seed=formal.seed,
        experiment_config_sha256=config_sha256,
        ordered_cells=tuple(
            CorpusCell(
                domain=cell.scenario.domain,
                template_id=cell.scenario.template_id,
                scenario_id=cell.scenario.scenario_id,
                framework_id=cell.framework_id,
                repetition=cell.repetition,
                seed=cell.seed,
            )
            for cell in plan
        ),
    )

    assert binding.repetitions == policy.minimum_repetitions == 5
    assert len(binding.ordered_cells) == 36 * 2 * binding.repetitions == 360
    copied = binding.model_copy(update={"repetitions": 6})
    with pytest.raises(ValueError, match="plan_identity_sha256"):
        Phase5CorpusPlan.model_validate(copied.model_dump(mode="python"))


def test_phase5_plan_rejects_fully_rehashed_invented_inventory_cells() -> None:
    config = _pilot_config()
    plan = build_corpus_plan(config)
    binding = Phase5CorpusPlan.from_cells(
        mode="pilot",
        repetitions=config.repetitions,
        seed=config.seed,
        experiment_config_sha256=canonical_sha256(config.model_dump(mode="json")),
        ordered_cells=tuple(
            CorpusCell(
                domain=cell.scenario.domain,
                template_id=cell.scenario.template_id,
                scenario_id=cell.scenario.scenario_id,
                framework_id=cell.framework_id,
                repetition=cell.repetition,
                seed=cell.seed,
            )
            for cell in plan
        ),
    )
    payload = binding.model_dump(mode="json")
    payload["ordered_cells"][0]["template_id"] = "invented-template"
    payload["ordered_cells"][0]["scenario_id"] = "invented-scenario"
    payload["ordered_cells"][1]["template_id"] = "invented-template"
    payload["ordered_cells"][1]["scenario_id"] = "invented-scenario"
    payload["ordered_cells_sha256"] = canonical_sha256(payload["ordered_cells"])
    payload["plan_identity_sha256"] = canonical_sha256(
        {
            "experiment_config_sha256": payload["experiment_config_sha256"],
            "mode": payload["mode"],
            "ordered_cells_sha256": payload["ordered_cells_sha256"],
            "repetitions": payload["repetitions"],
            "schema_name": payload["schema_name"],
            "schema_version": payload["schema_version"],
            "seed": payload["seed"],
        }
    )

    with pytest.raises(ValueError, match="authoritative Phase 5 inventory"):
        Phase5CorpusPlan.model_validate(payload)


def test_phase5_plan_from_cells_round_trip_revalidates_nested_cells() -> None:
    config = _pilot_config()
    plan = build_corpus_plan(config)
    cells = tuple(
        CorpusCell(
            domain=cell.scenario.domain,
            template_id=cell.scenario.template_id,
            scenario_id=cell.scenario.scenario_id,
            framework_id=cell.framework_id,
            repetition=cell.repetition,
            seed=cell.seed,
        )
        for cell in plan
    )
    invalid = (
        cells[0].model_copy(update={"scenario_id": ""}),
        cells[1].model_copy(update={"scenario_id": ""}),
        *cells[2:],
    )

    with pytest.raises(ValueError, match="at least 1 character"):
        Phase5CorpusPlan.from_cells(
            mode="pilot",
            repetitions=config.repetitions,
            seed=config.seed,
            experiment_config_sha256=canonical_sha256(config.model_dump(mode="json")),
            ordered_cells=invalid,
        )
    assert all(
        not ({"condition", "condition_id", "model", "model_id"} & set(cell.__dict__))
        for cell in plan
    )


async def test_generation_executes_pairs_validates_parity_and_freezes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    langgraph = _RecordingAdapter(FrameworkId.LANGGRAPH)
    autogen = _RecordingAdapter(FrameworkId.AUTOGEN)
    freeze_calls = 0
    original_freeze = TraceReplayRepository.freeze.__func__

    def counting_freeze(cls: type[TraceReplayRepository], **kwargs: object):
        nonlocal freeze_calls
        freeze_calls += 1
        return original_freeze(cls, **kwargs)

    monkeypatch.setattr(TraceReplayRepository, "freeze", classmethod(counting_freeze))
    destination = tmp_path / "pilot-corpus"

    result = await generate_phase5_corpus(
        config=_pilot_config(),
        destination=destination,
        adapters={
            FrameworkId.LANGGRAPH: langgraph,
            FrameworkId.AUTOGEN: autogen,
        },
        provenance=_provenance(),
        created_at_utc=datetime(2026, 7, 19, 12, tzinfo=UTC),
    )

    assert freeze_calls == 1
    assert len(langgraph.calls) == len(autogen.calls) == 108
    assert langgraph.calls == autogen.calls
    assert len(result.parity_results) == 108
    assert all(item.is_match for item in result.parity_results)
    assert len(result.repository.verify().entries) == 216
    manifest = result.repository.verify()
    assert manifest.metadata.expected_cell_count == 216
    assert manifest.metadata.expected_pair_count == 108
    framework_provenance = manifest.metadata.framework_provenance
    assert framework_provenance is not None
    assert set(framework_provenance) == {FrameworkId.LANGGRAPH, FrameworkId.AUTOGEN}
    phase5_plan = manifest.metadata.phase5_plan
    assert phase5_plan is not None
    assert phase5_plan.mode == "pilot"
    assert phase5_plan.repetitions == 3
    assert len(phase5_plan.ordered_cells) == 216
    assert len(phase5_plan.ordered_cells_sha256) == 64
    assert len(phase5_plan.plan_identity_sha256) == 64
    expected_plan_cells = tuple(
        CorpusCell(
            domain=cell.scenario.domain,
            template_id=cell.scenario.template_id,
            scenario_id=cell.scenario.scenario_id,
            framework_id=cell.framework_id,
            repetition=cell.repetition,
            seed=cell.seed,
        )
        for cell in build_corpus_plan(_pilot_config())
    )
    assert phase5_plan.ordered_cells == expected_plan_cells
    assert len(manifest.parity_entries) == 108
    assert len({entry.pair_identity for entry in manifest.parity_entries}) == 108
    assert {
        cell
        for entry in manifest.parity_entries
        for cell in (entry.reference_cell, entry.candidate_cell)
    } == {entry.cell for entry in manifest.entries}
    assert all(
        result.repository.load_parity(entry.pair_identity).result.is_match
        for entry in manifest.parity_entries
    )
    with monkeypatch.context() as parity_patch:
        parity_patch.setattr(
            repository_module.ScenarioParityValidator,
            "validate",
            lambda _self, _reference, _candidate: ParityResult(
                status="mismatched",
                mismatches=(
                    ParityMismatch(
                        dimension=ParityDimension.OUTCOME,
                        reference_sha256="1" * 64,
                        candidate_sha256="2" * 64,
                    ),
                ),
            ),
        )
        with pytest.raises(ValueError, match="recomputed parity"):
            result.repository.verify()
    manifest_payload = manifest.model_dump(mode="json")
    incomplete = {
        **manifest_payload,
        "parity_entries": manifest_payload["parity_entries"][:-1],
    }
    with pytest.raises(ValueError, match="complete corpus pair coverage"):
        CorpusManifest.model_validate(incomplete)
    duplicate = {
        **manifest_payload,
        "parity_entries": (
            manifest_payload["parity_entries"][0],
            *manifest_payload["parity_entries"][:-1],
        ),
    }
    with pytest.raises(ValueError, match="parity pair identities must be unique"):
        CorpusManifest.model_validate(duplicate)

    copied_metadata = manifest.metadata.model_copy(update={"expected_cell_count": 1})
    with pytest.raises(ValueError, match="Phase 5.*cell count"):
        CorpusManifestMetadata.model_validate(copied_metadata.model_dump(mode="python"))

    manifest_path = destination / "manifest.json"
    original_manifest_bytes = manifest_path.read_bytes()
    plan_tamper = deepcopy(manifest_payload)
    plan_tamper["metadata"]["phase5_plan"]["ordered_cells"][0]["seed"] += 1
    manifest_path.write_bytes(canonical_bytes(plan_tamper))
    with pytest.raises(ValueError, match="ordered_cells_sha256"):
        TraceReplayRepository(destination).verify()
    manifest_path.write_bytes(original_manifest_bytes)

    count_tamper = deepcopy(manifest_payload)
    count_tamper["metadata"]["expected_cell_count"] = 1
    manifest_path.write_bytes(canonical_bytes(count_tamper))
    with pytest.raises(ValueError, match="Phase 5.*cell count"):
        TraceReplayRepository(destination).verify()
    manifest_path.write_bytes(original_manifest_bytes)

    parity_path = destination / manifest.parity_entries[0].result_path
    parity_path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="parity payload SHA-256 mismatch"):
        result.repository.verify()
    assert result.has_unapproved_parity_mismatches is False
    assert len(result.logical_payload_sha256) == 64


async def test_generation_retains_parity_mismatches_for_nonzero_cli_status(
    tmp_path: Path,
) -> None:
    result = await generate_phase5_corpus(
        config=_pilot_config(),
        destination=tmp_path / "pilot-corpus",
        adapters={
            FrameworkId.LANGGRAPH: _RecordingAdapter(FrameworkId.LANGGRAPH),
            FrameworkId.AUTOGEN: _RecordingAdapter(
                FrameworkId.AUTOGEN, final_message="different"
            ),
        },
        provenance=_provenance(),
        created_at_utc=datetime(2026, 7, 19, 12, tzinfo=UTC),
    )

    assert result.has_unapproved_parity_mismatches is True
    assert all(item.status == "mismatched" for item in result.parity_results)


@pytest.mark.parametrize(
    "record_provenance",
    (
        _provenance().model_copy(update={"git_commit": "e" * 40}),
        _provenance().model_copy(update={"dependency_lock_sha256": "e" * 64}),
        _provenance().model_copy(update={"dataset_manifest_sha256": "e" * 64}),
        _provenance(dirty=True),
    ),
    ids=("stale-commit", "mixed-lock", "mixed-dataset", "dirty-record"),
)
async def test_generation_rejects_stale_dirty_or_mixed_record_provenance(
    tmp_path: Path,
    record_provenance: ExecutionProvenance,
) -> None:
    destination = tmp_path / "pilot-corpus"

    with pytest.raises(ValueError, match="execution record provenance"):
        await generate_phase5_corpus(
            config=_pilot_config(),
            destination=destination,
            adapters={
                FrameworkId.LANGGRAPH: _RecordingAdapter(
                    FrameworkId.LANGGRAPH,
                    provenance=record_provenance,
                ),
                FrameworkId.AUTOGEN: _RecordingAdapter(FrameworkId.AUTOGEN),
            },
            provenance=_provenance(),
        )

    assert not destination.exists()


async def test_generation_rejects_mixed_full_provenance_within_one_framework(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "pilot-corpus"

    with pytest.raises(ValueError, match="same-framework execution provenance"):
        await generate_phase5_corpus(
            config=_pilot_config(),
            destination=destination,
            adapters={
                FrameworkId.LANGGRAPH: _MixedProvenanceAdapter(FrameworkId.LANGGRAPH),
                FrameworkId.AUTOGEN: _RecordingAdapter(FrameworkId.AUTOGEN),
            },
            provenance=_provenance(),
        )

    assert not destination.exists()


async def test_formal_generation_refuses_a_dirty_worktree_before_execution(
    tmp_path: Path,
) -> None:
    pilot = _pilot_config()
    policy = FormalFreezePolicy.model_validate_json(
        Path("evals/configs/phase5-formal-policy.json").read_text(encoding="utf-8")
    )
    formal = freeze_formal_config(
        pilot,
        policy,
        repetitions=policy.minimum_repetitions,
        coverage_loss_tolerance=0.05,
        frozen_at_utc=datetime(2026, 7, 19, tzinfo=UTC),
    )
    langgraph = _RecordingAdapter(FrameworkId.LANGGRAPH)
    autogen = _RecordingAdapter(FrameworkId.AUTOGEN)

    with pytest.raises(ValueError, match="clean worktree"):
        await generate_phase5_corpus(
            config=formal,
            destination=tmp_path / "formal-corpus",
            adapters={
                FrameworkId.LANGGRAPH: langgraph,
                FrameworkId.AUTOGEN: autogen,
            },
            provenance=_provenance(dirty=True),
        )

    assert langgraph.calls == autogen.calls == []
    assert not (tmp_path / "formal-corpus").exists()


async def test_logical_projection_normalizes_only_task6_physical_fields() -> None:
    cell = build_corpus_plan(_pilot_config())[0]
    config = RuntimeConfig(
        seed=cell.seed,
        repetition=cell.repetition,
        max_steps=8,
        timeout_seconds=5.0,
        max_retries=0,
        max_tool_calls=8,
    )
    record = await _RecordingAdapter(FrameworkId.LANGGRAPH).execute(
        cell.scenario, config
    )
    shifted = timedelta(days=1)
    physical_trace = record.trace.model_copy(
        update={
            "trace_id": "f" * 32,
            "spans": [
                span.model_copy(
                    update={
                        "trace_id": "f" * 32,
                        "span_id": "e" * 16,
                        "started_at": span.started_at + shifted,
                        "ended_at": span.ended_at + shifted,
                    }
                )
                for span in record.trace.spans
            ],
        }
    )
    physical_only = record.model_copy(
        update={
            "trace": physical_trace,
            "framework_version": "different-physical-version",
            "started_at": record.started_at + shifted,
            "completed_at": record.completed_at + shifted,
            "latency_seconds": record.latency_seconds + 99,
        }
    )

    assert logical_execution_payload(record) == logical_execution_payload(
        physical_only
    )

    drifts = (
        record.model_copy(update={"evidence_selector_sha256": "e" * 64}),
        record.model_copy(update={"injection_trigger_sha256": "e" * 64}),
        record.model_copy(update={"final_message": "different outcome"}),
        record.model_copy(
            update={
                "runtime_config": config.model_copy(update={"max_steps": 9})
            }
        ),
    )
    assert all(
        logical_execution_payload(record) != logical_execution_payload(drift)
        for drift in drifts
    )


async def test_logical_corpus_payload_retains_counts_and_non_git_provenance() -> None:
    cell = build_corpus_plan(_pilot_config())[0]
    config = RuntimeConfig(
        seed=cell.seed,
        repetition=cell.repetition,
        max_steps=8,
        timeout_seconds=5.0,
        max_retries=0,
        max_tool_calls=8,
    )
    record = await _RecordingAdapter(FrameworkId.LANGGRAPH).execute(
        cell.scenario,
        config,
    )
    project = corpus_generate.logical_corpus_record_payload
    baseline = project(record)

    semantic_drifts = (
        record.model_copy(update={"steps": record.steps + 1}),
        record.model_copy(update={"tool_calls": record.tool_calls + 1}),
        record.model_copy(update={"framework_version": "changed"}),
        record.model_copy(
            update={
                "provenance": record.provenance.model_copy(
                    update={"package_version": "0.2.1"}
                )
            }
        ),
        record.model_copy(
            update={
                "provenance": record.provenance.model_copy(
                    update={"environment_sha256": "e" * 64}
                )
            }
        ),
        record.model_copy(
            update={
                "provenance": record.provenance.model_copy(
                    update={"tool_versions": {"supportlab": "2.0"}}
                )
            }
        ),
        record.model_copy(
            update={
                "provenance": record.provenance.model_copy(
                    update={"runtime_versions": {"python": "3.12.11"}}
                )
            }
        ),
    )
    assert all(project(drift) != baseline for drift in semantic_drifts)
    git_only = record.model_copy(
        update={
            "provenance": record.provenance.model_copy(update={"git_commit": "f" * 40})
        }
    )
    assert project(git_only) == baseline
