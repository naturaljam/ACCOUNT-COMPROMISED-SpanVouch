from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.paper_assets import (
    AnalysisObservation,
    Phase5AnalysisInput,
    render_risk_coverage_svg,
    write_paper_assets,
)
from spanvouch.evaluation.statistics import (
    ClusterBootstrapResult,
    ConditionObservation,
    PairedEffect,
)


class _BundleConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str
    mode: str
    config_sha256: str
    corpus_manifest_sha256: str
    candidate_manifest_sha256: str
    matrix_manifest_sha256: str
    provider_manifest_sha256: str
    evaluated_results_manifest_sha256: str
    analysis_seed: int
    bootstrap_draws: int
    policy_versions: tuple[str, ...]


class _ClaimDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: str
    outcome: str
    artifact_ids: tuple[str, ...]
    rationale: str


class _ClaimReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: str = "spanvouch.claim-gate-report"
    schema_version: str = "1.0"
    decisions: tuple[_ClaimDecision, ...]


def bundle_config(*, evaluated_results_sha256: str = "6" * 64) -> _BundleConfig:
    return _BundleConfig(
        experiment_id="phase5-formal-fixture",
        mode="formal",
        config_sha256="1" * 64,
        corpus_manifest_sha256="2" * 64,
        candidate_manifest_sha256="3" * 64,
        matrix_manifest_sha256="4" * 64,
        provider_manifest_sha256="5" * 64,
        evaluated_results_manifest_sha256=evaluated_results_sha256,
        analysis_seed=20260720,
        bootstrap_draws=100,
        policy_versions=("phase5-analysis-v1",),
    )


def claim_report() -> _ClaimReport:
    return _ClaimReport(
        decisions=tuple(
            _ClaimDecision(
                hypothesis_id=f"H{index}",
                outcome="unresolved" if index in {3, 5} else "supported",
                artifact_ids=("paired-effects.csv", "risk-coverage.csv"),
                rationale="Fixture-only decision with uncertainty retained.",
            )
            for index in range(1, 6)
        )
    )


def _row(
    *,
    domain: str,
    framework: str,
    condition: str,
    correct: bool,
    confidence: float,
) -> AnalysisObservation:
    suffix = f"{domain}-{framework}-{condition}"
    return AnalysisObservation(
        domain=domain,
        observation=ConditionObservation(
            observation_id=suffix,
            cell_id=suffix,
            cluster_id=f"{domain}-template-1",
            condition_id=condition,
            framework_id=framework,
            candidate_exists=True,
            accepted=True,
            correct=correct,
            confidence=confidence,
            completion=True,
            family_correct=correct,
            causal_correct=correct,
            grounded=True,
            disagreement=not correct,
            joint_error=not correct,
            input_tokens=10,
            output_tokens=4,
            cost_cny="0.012345",
            latency_ms=12.5,
        ),
    )


def analysis_input() -> Phase5AnalysisInput:
    rows = tuple(
        _row(
            domain=domain,
            framework=framework,
            condition=condition,
            correct=not (condition == "b2_deepseek_shared" and framework == "autogen"),
            confidence=0.75 if condition == "b2_deepseek_shared" else 0.9,
        )
        for domain in ("supportlab", "opslab")
        for framework in ("langgraph", "autogen")
        for condition in ("b2_deepseek_shared", "b3_deepseek_isolated")
    )
    effect = ClusterBootstrapResult(
        effect=PairedEffect(
            comparison_id="h2-risk-b3-minus-b2",
            metric="risk",
            reference_condition="b2_deepseek_shared",
            candidate_condition="b3_deepseek_isolated",
            estimate=-0.25,
        ),
        seed=20260720,
        draws=100,
        defined_draws=100,
        undefined_draws=0,
        undefined_draw_rate=0.0,
        distinct_defined_estimates=3,
        confidence_level=0.95,
        lower=-0.5,
        upper=-0.1,
        cluster_count=2,
        row_count=8,
        operational_failure_explains_gain=False,
        claim_gate_passed=True,
    )
    return Phase5AnalysisInput(
        observations=rows,
        paired_effects=(effect,),
        claim_gate_evidence={"fixture": True},
    )


def test_writer_emits_eight_byte_identical_manifest_bound_assets(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for destination in (first, second):
        write_paper_assets(
            destination,
            analysis=analysis_input(),
            bundle_config=bundle_config(),
            claim_gate_report=claim_report(),
        )

    expected = {
        "metrics-by-condition.csv",
        "paired-effects.csv",
        "failure-accounting.csv",
        "risk-coverage.csv",
        "claim-gates.json",
        "main-results.md",
        "risk-coverage.svg",
        "manifest.json",
    }
    assert {path.name for path in first.iterdir()} == expected
    for name in expected:
        assert (first / name).read_bytes() == (second / name).read_bytes()

    manifest = json.loads((first / "manifest.json").read_text("utf-8"))
    assert manifest["analysis_seed"] == 20260720
    assert manifest["bootstrap_draws"] == 100
    assert manifest["evaluated_results_manifest_sha256"] == "6" * 64
    assert manifest["risk_coverage_svg_source_sha256"] == hashlib.sha256(
        (first / "risk-coverage.csv").read_bytes()
    ).hexdigest()
    assert {item["path"] for item in manifest["assets"]} == expected - {"manifest.json"}
    for item in manifest["assets"]:
        content = (first / item["path"]).read_bytes()
        digest = (
            canonical_sha256(json.loads(content))
            if item["path"].endswith(".json")
            else hashlib.sha256(content).hexdigest()
        )
        assert item["sha256"] == digest


def test_assets_bind_numbers_to_sources_and_preserve_limitations(tmp_path: Path) -> None:
    destination = tmp_path / "assets"
    write_paper_assets(
        destination,
        analysis=analysis_input(),
        bundle_config=bundle_config(),
        claim_gate_report=claim_report(),
    )

    metrics = list(
        csv.DictReader(
            io.StringIO((destination / "metrics-by-condition.csv").read_text("utf-8"))
        )
    )
    assert metrics
    assert all(row["source_artifact_sha256"] == "6" * 64 for row in metrics)
    assert all(row["risk_numerator"] and row["risk_denominator"] for row in metrics)
    paired = (destination / "paired-effects.csv").read_text("utf-8")
    assert "nearest_rank" in paired
    assert "20260720" in paired
    results = (destination / "main-results.md").read_text("utf-8")
    for required in (
        "SupportLab primary",
        "OpsLab preliminary",
        "portability is not equivalence",
        "disagreement is not correctness",
        "operational cross-model",
        "null and negative outcomes remain visible",
        "Missing cells",
        "| H3 | unresolved |",
        "6" * 64,
    ):
        assert required in results
    combined = b"\n".join(path.read_bytes() for path in destination.iterdir()).lower()
    for forbidden in (
        b"authorization:",
        b"bearer ",
        b"api_key",
        b"raw_prompt",
        b"raw_response",
        b"hidden_reasoning",
    ):
        assert forbidden not in combined


def test_svg_is_an_accessible_projection_of_risk_coverage_csv() -> None:
    csv_bytes = (
        b"domain,framework,condition_id,threshold,risk_value,coverage_value,"
        b"source_artifact_sha256\n"
        + b"supportlab,langgraph,b3,0.500000,0.250000,0.750000,"
        + b"6" * 64
        + b"\n"
    )
    svg = render_risk_coverage_svg(csv_bytes)
    assert b"<title>Phase 5 risk-coverage curves</title>" in svg
    assert b"<desc>" in svg
    assert b"supportlab / langgraph / b3" in svg
    assert render_risk_coverage_svg(csv_bytes.replace(b"0.250000", b"0.500000")) != svg


def test_paper_skeletons_and_ledger_keep_claims_gated() -> None:
    root = Path(__file__).parents[2]
    method = (root / "docs/paper/method.md").read_text("utf-8")
    setup = (root / "docs/paper/experiment-setup.md").read_text("utf-8")
    results = (root / "docs/paper/results.md").read_text("utf-8")
    ledger = (root / "docs/research/ivad-claim-evidence-ledger.md").read_text("utf-8")

    assert "IVAD" in method and "B0-B5" in method and "two-stage" in method
    assert "config hash" in setup and "budget" in setup and "exclusions" in setup
    assert "<!-- PHASE5_GENERATED_RESULTS_START -->" in results
    assert "<!-- PHASE5_GENERATED_RESULTS_END -->" in results
    assert "analysis manifest" in ledger
    assert "planned; no Phase 5 evidence yet" in ledger
