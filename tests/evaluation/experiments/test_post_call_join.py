from __future__ import annotations

from pathlib import Path

import pytest

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.corpus.labels import GoldLabel, GoldLabelManifest
from spanvouch.evaluation.evaluate_phase5_matrix import (
    EvaluationPhaseRepository,
    PostCallEvaluator,
)
from spanvouch.evaluation.experiments.config import ConditionId
from spanvouch.evaluation.experiments.runner import (
    ExperimentRunner,
    ProviderPhaseRepository,
)

from .test_runner import (
    Admission,
    FakeExecutor,
    _plans_and_matrix,
)


def _identity(cell: object) -> str:
    from spanvouch.evaluation.corpus.models import CorpusCell
    validated = CorpusCell.model_validate(cell)
    return ":".join((
        validated.domain, validated.template_id, validated.scenario_id,
        validated.framework_id.value, str(validated.repetition), str(validated.seed),
    ))


def _labels(matrix: object) -> GoldLabelManifest:
    from spanvouch.evaluation.experiments.models import ExperimentMatrixManifest
    validated = ExperimentMatrixManifest.model_validate(matrix)
    labels = tuple(
        GoldLabel(
            cell_identity=_identity(cell), scenario_id=cell.scenario_id,
            expected_failure_type="no_failure",
            causal_chain_expectations=(), evidence_expectations=(),
            control=True, split="pilot", record_sha256="3" * 64,
            trace_sha256="4" * 64,
        )
        for cell in validated.eligible_cells
    )
    return GoldLabelManifest(
        corpus_manifest_sha256=validated.corpus_manifest_sha256,
        labels=labels,
        labels_sha256=canonical_sha256(
            [label.model_dump(mode="json") for label in labels]
        ),
    )


@pytest.mark.asyncio
async def test_join_attaches_gold_only_in_separate_verified_evaluation_directory(
    tmp_path: Path,
) -> None:
    plans, matrix = _plans_and_matrix()
    provider_repository = ProviderPhaseRepository(tmp_path / "provider-results")
    await ExperimentRunner(executor=FakeExecutor()).run_provider_phase(
        plans=plans, matrix=matrix, repository=provider_repository
    )
    labels = _labels(matrix)
    evaluation_repository = EvaluationPhaseRepository(tmp_path / "evaluated-results")
    manifest = PostCallEvaluator().join(
        provider_repository=provider_repository,
        expected_provider_manifest_sha256=provider_repository.manifest_sha256,
        sealed_labels=labels,
        sealed_labels_manifest_sha256=canonical_sha256(labels),
        repository=evaluation_repository,
    )
    assert manifest.evaluated_count == 12
    assert manifest.labels_sha256 == labels.labels_sha256
    evaluated = evaluation_repository.load(plans[0].plan_id)
    assert evaluated.control is True
    assert evaluated.split == "pilot"
    assert evaluated.family == "no_failure"
    assert evaluated.is_correct is True
    provider_bytes = "".join(
        path.read_text("utf-8")
        for path in (tmp_path / "provider-results").rglob("*.json")
    ).casefold()
    assert all(word not in provider_bytes for word in ("is_correct", '"split"', "expected_failure"))


@pytest.mark.asyncio
async def test_join_refuses_incomplete_provider_phase(tmp_path: Path) -> None:
    plans, matrix = _plans_and_matrix()
    provider_repository = ProviderPhaseRepository(tmp_path / "provider-results")
    await ExperimentRunner(
        executor=FakeExecutor(), admission=Admission(ConditionId.B2)
    ).run_provider_phase(plans=plans, matrix=matrix, repository=provider_repository)
    with pytest.raises(ValueError, match="provider phase is incomplete"):
        PostCallEvaluator().join(
            provider_repository=provider_repository,
            expected_provider_manifest_sha256=provider_repository.manifest_sha256,
            sealed_labels=_labels(matrix),
            sealed_labels_manifest_sha256=canonical_sha256(_labels(matrix)),
            repository=EvaluationPhaseRepository(tmp_path / "evaluated-results"),
        )


@pytest.mark.asyncio
async def test_join_requires_exact_label_cell_set_and_trusted_hashes(tmp_path: Path) -> None:
    plans, matrix = _plans_and_matrix()
    provider_repository = ProviderPhaseRepository(tmp_path / "provider-results")
    await ExperimentRunner(executor=FakeExecutor()).run_provider_phase(
        plans=plans, matrix=matrix, repository=provider_repository
    )
    labels = _labels(matrix)
    one_label = labels.labels[:1]
    incomplete = GoldLabelManifest(
        corpus_manifest_sha256=labels.corpus_manifest_sha256,
        labels=one_label,
        labels_sha256=canonical_sha256(
            [label.model_dump(mode="json") for label in one_label]
        ),
    )
    with pytest.raises(ValueError, match="label cell set"):
        PostCallEvaluator().join(
            provider_repository=provider_repository,
            expected_provider_manifest_sha256=provider_repository.manifest_sha256,
            sealed_labels=incomplete,
            sealed_labels_manifest_sha256=canonical_sha256(incomplete),
            repository=EvaluationPhaseRepository(tmp_path / "evaluated-results"),
        )
    with pytest.raises(ValueError, match="sealed label manifest SHA-256"):
        PostCallEvaluator().join(
            provider_repository=provider_repository,
            expected_provider_manifest_sha256=provider_repository.manifest_sha256,
            sealed_labels=labels,
            sealed_labels_manifest_sha256="f" * 64,
            repository=EvaluationPhaseRepository(tmp_path / "evaluated-results-2"),
        )


def test_post_call_evaluator_signature_has_no_provider_or_live_arguments() -> None:
    import inspect
    signature = str(inspect.signature(PostCallEvaluator.join)).casefold()
    assert "endpoint" not in signature
    assert "api_key" not in signature
    assert "allow_live" not in signature
    assert "formal_run" not in signature
