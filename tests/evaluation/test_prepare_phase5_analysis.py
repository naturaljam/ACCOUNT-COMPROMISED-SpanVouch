from __future__ import annotations

import json
from pathlib import Path

import pytest

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.experiments.runner import OutcomeStatus, ProviderPlanOutcome
from spanvouch.evaluation.paper_assets import AnalysisObservation
from spanvouch.evaluation.prepare_phase5_analysis import (
    _framework_completion_rows,
    _observation,
    _publish_analysis_input,
)
from spanvouch.evaluation.statistics import ConditionObservation
from tests.evaluation.experiments.test_runner import _plans_and_matrix
from tests.evaluation.test_paper_assets import analysis_input


def _observation_row(*, framework: str, cell_id: str, cluster_id: str) -> AnalysisObservation:
    row = ConditionObservation(
        observation_id=f"{framework}-{cell_id}",
        cell_id=cell_id,
        cluster_id=cluster_id,
        condition_id="b0_no_verifier",
        framework_id=framework,  # type: ignore[arg-type]
        candidate_exists=True,
        accepted=True,
        correct=True,
        confidence=1.0,
        completion=True,
    )
    return AnalysisObservation(domain="supportlab", observation=row)


def test_framework_completion_rows_exclude_incomplete_pairs() -> None:
    complete_autogen = _observation_row(
        framework="autogen", cell_id="supportlab:t:s:autogen:1:1", cluster_id="supportlab:t:s"
    )
    complete_langgraph = _observation_row(
        framework="langgraph", cell_id="supportlab:t:s:langgraph:1:1", cluster_id="supportlab:t:s"
    )
    incomplete = _observation_row(
        framework="autogen",
        cell_id="supportlab:other:other:autogen:1:2",
        cluster_id="supportlab:other:other",
    )

    rows = _framework_completion_rows((complete_autogen, complete_langgraph, incomplete))

    assert len(rows) == 2
    assert {row.condition_id for row in rows} == {"autogen", "langgraph"}
    assert {row.cell_id for row in rows} == {"supportlab:t:s:1:1"}


def test_policy_skipped_result_is_a_noncompleted_candidate_observation() -> None:
    plans, _ = _plans_and_matrix()
    plan = next(item for item in plans if item.condition_id.value == "b4_qwen_isolated")
    outcome = ProviderPlanOutcome(
        plan=plan,
        status=OutcomeStatus.NOT_INVOKED_BY_POLICY,
        terminal_code="deepseek_only_policy",
    )
    from spanvouch.evaluation.evaluate_phase5_matrix import EvaluatedConditionResult

    result = EvaluatedConditionResult(
        plan_id=plan.plan_id,
        cell=plan.cell,
        condition_id=plan.condition_id,
        provider_outcome_sha256="a" * 64,
        status=OutcomeStatus.NOT_INVOKED_BY_POLICY,
        selective_action=None,
        failure_category=None,
        is_correct=None,
        diagnosis_correct=None,
        causal_chain_correct=None,
        grounding_correct=None,
        verification_correct=None,
        diagnosis_error=None,
        verification_error=None,
        family="no_failure",
        control=True,
        split="test",
    )

    observation = _observation(result, outcome).observation

    assert observation.candidate_exists is True
    assert observation.completion is False
    assert observation.accepted is False
    assert observation.correct is None


def test_publish_analysis_input_binds_payload_and_rejects_replacement(tmp_path: Path) -> None:
    analysis = analysis_input()
    output = tmp_path / "analysis-input"
    paths = _publish_analysis_input(output, analysis=analysis)

    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    manifest = json.loads(paths[1].read_text(encoding="utf-8"))
    assert manifest["analysis_input_sha256"] == canonical_sha256(payload)
    assert tuple(path.name for path in paths) == ("analysis-input.json", "manifest.json")

    with pytest.raises(FileExistsError):
        _publish_analysis_input(output, analysis=analysis)
