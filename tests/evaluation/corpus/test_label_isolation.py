from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from spanvouch.contracts.trace import SpanKind, SpanStatus, TraceIR, TraceSpan
from spanvouch.contracts.versioning import canonical_bytes, canonical_sha256
from spanvouch.evaluation.corpus import CorpusManifestMetadata, TraceReplayRepository
from spanvouch.evaluation.corpus.generate import generate_phase5_corpus
from spanvouch.evaluation.corpus.gold_specs import GOLD_SPECS, GoldSpec
from spanvouch.evaluation.corpus.labels import generate_phase5_labels
from spanvouch.evaluation.experiments import load_experiment_config
from spanvouch.labs.opslab.templates import build_opslab_templates
from spanvouch.labs.runtime import (
    ExecutionProvenance,
    ExecutionRecord,
    ExecutionStatus,
    FrameworkId,
    LabScenario,
    RuntimeConfig,
    RuntimeState,
)
from spanvouch.labs.supportlab.runtime import build_support_lab_scenarios

ROOT = Path(__file__).resolve().parents[3]
STAGE_A_PATHS = (
    ROOT / "src/spanvouch/evaluation/corpus/inventory.py",
    ROOT / "src/spanvouch/evaluation/corpus/generate.py",
    ROOT / "src/spanvouch/evaluation/run_phase5_corpus.py",
)
FORBIDDEN_MODULE_PARTS = (
    ".corpus.labels",
    ".corpus.gold_specs",
    ".diagnosis_labels",
    ".review_labels",
    ".statistics",
    ".providers",
    ".provider_view",
    ".supportlab.scenarios",
)
FORBIDDEN_NAME_PARTS = ("expected", "gold", "split", "condition", "provider")


def _imports(path: Path) -> tuple[tuple[str, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, alias.name.rsplit(".", 1)[-1]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend((module, alias.name) for alias in node.names)
    return tuple(imports)


@pytest.mark.parametrize("path", STAGE_A_PATHS, ids=lambda path: path.name)
def test_stage_a_source_is_ast_isolated_from_labels_and_providers(path: Path) -> None:
    imports = _imports(path)

    assert all(
        part not in module.lower()
        for module, _ in imports
        for part in FORBIDDEN_MODULE_PARTS
    )
    assert all(
        part not in name.lower()
        for _, name in imports
        for part in FORBIDDEN_NAME_PARTS
    )
    assert all(name != "build_scenarios" for _, name in imports)


def test_execution_inventory_does_not_import_framework_adapters() -> None:
    inventory = ROOT / "src/spanvouch/evaluation/corpus/inventory.py"

    assert all(".labs.frameworks" not in module for module, _ in _imports(inventory))


def test_gold_specs_cover_only_the_execution_inventory() -> None:
    inventory = {
        scenario.scenario_id for scenario in build_support_lab_scenarios()
    } | {template.template_id for template in build_opslab_templates()}

    assert len(GOLD_SPECS) == len(inventory) == 36
    assert set(GOLD_SPECS) == inventory


async def _write_test_corpus(path: Path) -> None:
    provenance = ExecutionProvenance(
        git_commit="a" * 40,
        package_version="0.2.0",
        dependency_lock_sha256="b" * 64,
        dataset_manifest_sha256="c" * 64,
        environment_sha256="d" * 64,
        tool_versions={"opslab": "1.0", "supportlab": "1.0"},
        runtime_versions={"python": "3.12.10"},
        dirty_worktree=False,
    )

    class Adapter:
        framework_version = "test-1.0"

        def __init__(self, framework_id: FrameworkId) -> None:
            self.framework_id = framework_id

        async def execute(
            self, scenario: LabScenario, run_config: RuntimeConfig
        ) -> ExecutionRecord:
            identity = (
                f"{scenario.scenario_id}:{self.framework_id.value}:"
                f"{run_config.repetition}:{run_config.seed}"
            )
            trace_id = sha256(identity.encode()).hexdigest()[:32]
            started = datetime(2026, 7, 19, tzinfo=UTC) + timedelta(
                seconds=run_config.repetition
            )
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
                state=RuntimeState.initial().with_final("complete"),
                status=ExecutionStatus.SUCCEEDED,
                failure=None,
                started_at=started,
                completed_at=started + timedelta(seconds=1),
                provenance=provenance,
            )

    await generate_phase5_corpus(
        config=load_experiment_config(Path("evals/configs/phase5-pilot.json")),
        destination=path,
        adapters={
            FrameworkId.LANGGRAPH: Adapter(FrameworkId.LANGGRAPH),
            FrameworkId.AUTOGEN: Adapter(FrameworkId.AUTOGEN),
        },
        provenance=provenance,
    )


async def test_sealed_labels_bind_every_verified_corpus_cell_outside_corpus(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "pilot-corpus"
    await _write_test_corpus(corpus)

    result = generate_phase5_labels(corpus_dir=corpus)

    assert result.output_dir == tmp_path / "pilot-corpus-labels-sealed"
    assert not result.output_dir.is_relative_to(corpus)
    assert len(result.manifest.labels) == 216
    assert len({label.cell_identity for label in result.manifest.labels}) == 216
    assert all(label.record_sha256 for label in result.manifest.labels)
    assert all(label.trace_sha256 for label in result.manifest.labels)
    assert {label.split for label in result.manifest.labels} == {"pilot"}
    assert (result.output_dir / "manifest.json").is_file()
    assert list(corpus.rglob("*labels*")) == []


async def test_sealed_labels_refuse_overwrite_and_output_under_corpus(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "pilot-corpus"
    await _write_test_corpus(corpus)
    output = tmp_path / "sealed"
    output.mkdir()

    with pytest.raises(FileExistsError, match="must not already exist"):
        generate_phase5_labels(corpus_dir=corpus, output_dir=output)
    with pytest.raises(ValueError, match="outside the corpus root"):
        generate_phase5_labels(
            corpus_dir=corpus,
            output_dir=corpus / "labels-sealed",
        )


async def test_sealed_labels_fail_closed_on_a_tampered_corpus(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "pilot-corpus"
    await _write_test_corpus(corpus)
    record = next((corpus / "records/sha256").glob("*.json"))
    record.write_text("{}", encoding="utf-8")
    output = tmp_path / "sealed"

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        generate_phase5_labels(corpus_dir=corpus, output_dir=output)

    assert not output.exists()


def test_sealed_labels_reject_verified_generic_formal_looking_corpus(
    tmp_path: Path,
    record: ExecutionRecord,
) -> None:
    known_scenario_id = next(iter(GOLD_SPECS))
    runtime_config = record.runtime_config.model_copy(
        update={"repetition": 5, "seed": 20260799}
    )
    probe = record.model_copy(
        update={
            "scenario_id": known_scenario_id,
            "template_id": known_scenario_id,
            "repetition": 5,
            "seed": 20260799,
            "runtime_config": runtime_config,
            "runtime_config_sha256": canonical_sha256(runtime_config),
        }
    )
    corpus = tmp_path / "generic-formal-corpus"
    TraceReplayRepository.freeze(
        records=(probe,),
        parity_results=(),
        destination=corpus,
        manifest_metadata=CorpusManifestMetadata(
            corpus_id="generic-formal-probe",
            mode="formal",
            experiment_config_sha256="1" * 64,
            git_commit=probe.provenance.git_commit,
            dependency_lock_sha256=probe.provenance.dependency_lock_sha256,
            dataset_manifest_sha256=probe.provenance.dataset_manifest_sha256,
            dirty_worktree=probe.provenance.dirty_worktree,
            expected_cell_count=1,
            expected_pair_count=0,
            created_at_utc=datetime(2026, 7, 20, tzinfo=UTC),
            parity_results_sha256=canonical_sha256([]),
        ),
    )
    output = tmp_path / "sealed-generic-labels"

    with pytest.raises(ValueError, match="canonical Phase 5 corpus"):
        generate_phase5_labels(corpus_dir=corpus, output_dir=output)

    assert not output.exists()


async def test_sealed_labels_reject_wrong_phase5_corpus_id_without_output(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "pilot-corpus"
    await _write_test_corpus(corpus)
    manifest_path = corpus / "manifest.json"
    payload = json.loads(manifest_path.read_bytes())
    payload["metadata"]["corpus_id"] = "phase5-wrong"
    manifest_path.write_bytes(canonical_bytes(payload))
    output = tmp_path / "sealed-wrong-id-labels"

    with pytest.raises(ValueError, match="canonical Phase 5 corpus"):
        generate_phase5_labels(corpus_dir=corpus, output_dir=output)

    assert not output.exists()


async def test_injected_gold_sentinel_never_enters_stage_a_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spanvouch.evaluation.corpus import gold_specs

    sentinel = "GOLD_SENTINEL_EXPECTED_FAILURE"
    monkeypatch.setattr(
        gold_specs,
        "GOLD_SPECS",
        {
            "GOLD_SENTINEL_SCENARIO": GoldSpec(
                expected_failure_type=sentinel,
                causal_chain_expectations=(sentinel,),
                evidence_expectations=(sentinel,),
                control=False,
                split="test",
            )
        },
    )
    corpus = tmp_path / "pilot-corpus"

    await _write_test_corpus(corpus)

    persisted = b"\n".join(
        path.read_bytes() for path in corpus.rglob("*") if path.is_file()
    )
    assert sentinel.encode() not in persisted
