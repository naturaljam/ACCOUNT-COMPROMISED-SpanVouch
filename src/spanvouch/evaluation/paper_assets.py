"""Deterministic, provenance-bound Phase 5 paper asset generation."""

from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Any, Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.versioning import canonical_bytes, canonical_sha256
from spanvouch.evaluation.artifacts import publish_directory_no_replace
from spanvouch.evaluation.statistics import (
    ClusterBootstrapResult,
    ConditionMetrics,
    ConditionObservation,
    compute_condition_metrics,
    risk_coverage_curve,
)

_ASSET_NAMES = (
    "claim-gates.json",
    "failure-accounting.csv",
    "main-results.md",
    "metrics-by-condition.csv",
    "paired-effects.csv",
    "risk-coverage.csv",
    "risk-coverage.svg",
)
_FAILURES = (
    "framework_execution_failure",
    "framework_incompatibility",
    "infrastructure_failure",
    "provider_failure",
    "contract_invalid",
    "diagnosis_error",
    "verification_error",
)
_FORBIDDEN_ARTIFACT_TEXT = (
    "authorization:",
    "bearer ",
    "api_key",
    "api-key",
    "raw_prompt",
    "raw_response",
    "hidden_reasoning",
    "chain_of_thought",
)


class BundleConfigLike(Protocol):
    experiment_id: str
    mode: Literal["pilot", "formal"]
    evaluated_results_manifest_sha256: str
    analysis_seed: int
    bootstrap_draws: int
    policy_versions: tuple[str, ...]

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]: ...


class ClaimGateReportLike(Protocol):
    def model_dump(self, *, mode: str = "python") -> dict[str, Any]: ...


class AnalysisObservation(BaseModel):
    """One joined result with the experiment domain kept outside statistical records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: Literal["supportlab", "opslab"]
    observation: ConditionObservation


class Phase5AnalysisInput(BaseModel):
    """Offline-only joined results and statistical records consumed by Task 17."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.phase5-analysis-input"] = (
        "spanvouch.phase5-analysis-input"
    )
    schema_version: Literal["1.0"] = "1.0"
    observations: tuple[AnalysisObservation, ...] = Field(min_length=1)
    paired_effects: tuple[ClusterBootstrapResult, ...]
    claim_gate_evidence: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        identities = tuple(
            (item.domain, item.observation.observation_id) for item in self.observations
        )
        if len(identities) != len(set(identities)):
            raise ValueError("analysis observations must have unique domain identities")
        comparison_ids = tuple(item.effect.comparison_id for item in self.paired_effects)
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("paired effects must have unique comparison identities")
        return self


def _fmt(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return f"{value:.6f}"


def _csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def _grouped_observations(
    analysis: Phase5AnalysisInput,
) -> list[tuple[str, str, str, tuple[ConditionObservation, ...]]]:
    grouped: dict[tuple[str, str, str], list[ConditionObservation]] = defaultdict(list)
    for item in analysis.observations:
        observation = item.observation
        grouped[(item.domain, observation.framework_id, observation.condition_id)].append(
            observation
        )
    return [
        (*key, tuple(sorted(rows, key=lambda row: row.observation_id)))
        for key, rows in sorted(grouped.items())
    ]


def _metrics_csv(
    analysis: Phase5AnalysisInput, source: str
) -> tuple[bytes, list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    for domain, framework, condition, observations in _grouped_observations(analysis):
        metrics: ConditionMetrics = compute_condition_metrics(observations)
        rows.append(
            {
                "domain": domain,
                "framework": framework,
                "condition_id": condition,
                "scheduled_count": metrics.scheduled_count,
                "eligible_count": metrics.eligible_candidate_count,
                "accepted_count": metrics.accepted_count,
                "risk_numerator": metrics.false_acceptance_risk.numerator,
                "risk_denominator": metrics.false_acceptance_risk.denominator,
                "risk_value": _fmt(metrics.false_acceptance_risk.value),
                "coverage_numerator": metrics.coverage.numerator,
                "coverage_denominator": metrics.coverage.denominator,
                "coverage_value": _fmt(metrics.coverage.value),
                "input_tokens": metrics.input_tokens,
                "output_tokens": metrics.output_tokens,
                "cost_cny": f"{metrics.cost_cny:.6f}",
                "mean_latency_ms": _fmt(metrics.mean_latency_ms),
                "review_required": str(metrics.review_required).lower(),
                "source_artifact_sha256": source,
            }
        )
    fields = (
        "domain",
        "framework",
        "condition_id",
        "scheduled_count",
        "eligible_count",
        "accepted_count",
        "risk_numerator",
        "risk_denominator",
        "risk_value",
        "coverage_numerator",
        "coverage_denominator",
        "coverage_value",
        "input_tokens",
        "output_tokens",
        "cost_cny",
        "mean_latency_ms",
        "review_required",
        "source_artifact_sha256",
    )
    return _csv_bytes(fields, rows), rows


def _paired_csv(analysis: Phase5AnalysisInput, source: str) -> bytes:
    rows = [
        {
            "comparison_id": result.effect.comparison_id,
            "metric": result.effect.metric,
            "reference_condition": result.effect.reference_condition,
            "candidate_condition": result.effect.candidate_condition,
            "estimate": _fmt(result.effect.estimate),
            "lower": _fmt(result.lower),
            "upper": _fmt(result.upper),
            "confidence_level": _fmt(result.confidence_level),
            "interval_method": result.percentile_method,
            "seed": result.seed,
            "draws": result.draws,
            "defined_draws": result.defined_draws,
            "undefined_draws": result.undefined_draws,
            "cluster_count": result.cluster_count,
            "row_count": result.row_count,
            "operational_failure_explains_gain": str(
                result.operational_failure_explains_gain
            ).lower(),
            "claim_gate_passed": str(result.claim_gate_passed).lower(),
            "source_artifact_sha256": source,
        }
        for result in sorted(
            analysis.paired_effects, key=lambda item: item.effect.comparison_id
        )
    ]
    return _csv_bytes(tuple(rows[0]) if rows else (
        "comparison_id",
        "metric",
        "reference_condition",
        "candidate_condition",
        "estimate",
        "lower",
        "upper",
        "confidence_level",
        "interval_method",
        "seed",
        "draws",
        "defined_draws",
        "undefined_draws",
        "cluster_count",
        "row_count",
        "operational_failure_explains_gain",
        "claim_gate_passed",
        "source_artifact_sha256",
    ), rows)


def _failure_csv(
    analysis: Phase5AnalysisInput, source: str
) -> tuple[bytes, list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    for domain, framework, condition, observations in _grouped_observations(analysis):
        denominator = len(observations)
        for category in _FAILURES:
            count = sum(row.operational_failure == category for row in observations)
            rows.append(
                {
                    "domain": domain,
                    "framework": framework,
                    "condition_id": condition,
                    "failure_category": category,
                    "numerator": count,
                    "denominator": denominator,
                    "rate": _fmt(count / denominator),
                    "source_artifact_sha256": source,
                }
            )
    fields = (
        "domain",
        "framework",
        "condition_id",
        "failure_category",
        "numerator",
        "denominator",
        "rate",
        "source_artifact_sha256",
    )
    return _csv_bytes(fields, rows), rows


def _risk_coverage_csv(analysis: Phase5AnalysisInput, source: str) -> bytes:
    rows: list[dict[str, object]] = []
    for domain, framework, condition, observations in _grouped_observations(analysis):
        continuous = any(row.confidence is not None for row in observations)
        for point in risk_coverage_curve(observations, continuous=continuous):
            rows.append(
                {
                    "domain": domain,
                    "framework": framework,
                    "condition_id": condition,
                    "threshold": _fmt(point.threshold),
                    "risk_numerator": point.risk.numerator,
                    "risk_denominator": point.risk.denominator,
                    "risk_value": _fmt(point.risk.value),
                    "coverage_numerator": point.coverage.numerator,
                    "coverage_denominator": point.coverage.denominator,
                    "coverage_value": _fmt(point.coverage.value),
                    "accepted_count": point.accepted_count,
                    "fixed_operating_point": str(point.fixed_operating_point).lower(),
                    "source_artifact_sha256": source,
                }
            )
    fields = (
        "domain",
        "framework",
        "condition_id",
        "threshold",
        "risk_numerator",
        "risk_denominator",
        "risk_value",
        "coverage_numerator",
        "coverage_denominator",
        "coverage_value",
        "accepted_count",
        "fixed_operating_point",
        "source_artifact_sha256",
    )
    return _csv_bytes(fields, rows)


def render_risk_coverage_svg(risk_coverage_csv: bytes) -> bytes:
    """Render only the supplied CSV, keeping metric computation outside the writer."""
    text = risk_coverage_csv.decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("risk-coverage CSV requires at least one data row")
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        if not row.get("risk_value") or not row.get("coverage_value"):
            continue
        try:
            risk = float(row["risk_value"])
            coverage = float(row["coverage_value"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("risk-coverage CSV contains undefined plotted values") from error
        label = f"{row['domain']} / {row['framework']} / {row['condition_id']}"
        grouped[label].append((coverage, risk))
    if not grouped:
        raise ValueError("risk-coverage CSV has no defined plotted values")
    palette = ("#005f73", "#9b2226", "#0a9396", "#ca6702", "#3a0ca3", "#6a994e")
    elements = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="520" '
        'viewBox="0 0 800 520" role="img">',
        "<title>Phase 5 risk-coverage curves</title>",
        "<desc>False-acceptance risk against coverage, generated from risk-coverage.csv.</desc>",
        '<rect width="800" height="520" fill="white"/>',
        '<line x1="80" y1="440" x2="740" y2="440" stroke="#222"/>',
        '<line x1="80" y1="440" x2="80" y2="40" stroke="#222"/>',
        '<text x="390" y="495" text-anchor="middle">Coverage</text>',
        '<text x="20" y="240" transform="rotate(-90 20 240)" text-anchor="middle">Risk</text>',
    ]
    for index, (label, points) in enumerate(sorted(grouped.items())):
        color = palette[index % len(palette)]
        ordered = sorted(points)
        coordinates = " ".join(
            f"{80 + 660 * coverage:.2f},{440 - 400 * risk:.2f}"
            for coverage, risk in ordered
        )
        elements.append(
            f'<polyline points="{coordinates}" fill="none" stroke="{color}" '
            'stroke-width="2"/>'
        )
        elements.append(
            f'<text x="500" y="{55 + index * 18}" fill="{color}">{escape(label)}</text>'
        )
    elements.append("</svg>")
    return ("\n".join(elements) + "\n").encode("utf-8")


def _main_results(
    metrics_rows: list[dict[str, object]],
    failure_rows: list[dict[str, object]],
    claim_payload: Mapping[str, object],
    source_sha256: str,
    missing_cells: object,
) -> bytes:
    lines = [
        "# Phase 5 Main Results",
        "",
        f"All displayed values below derive from evaluated-results artifact `{source_sha256}`.",
        "Ratios retain numerators and denominators in `metrics-by-condition.csv`; paired intervals "
        "use the method, seed, and draw count in `paired-effects.csv`.",
        "",
        "## SupportLab primary",
        "",
        "| Framework | Condition | Risk | Coverage | Cost CNY | Source |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for domain in ("supportlab", "opslab"):
        if domain == "opslab":
            lines.extend(
                [
                    "",
                    "## OpsLab preliminary",
                    "",
                    "| Framework | Condition | Risk | Coverage | Cost CNY | Source |",
                    "| --- | --- | ---: | ---: | ---: | --- |",
                ]
            )
        for row in metrics_rows:
            if row["domain"] != domain:
                continue
            risk = (
                f"{row['risk_numerator']}/{row['risk_denominator']} "
                f"({row['risk_value'] or 'undefined'})"
            )
            coverage = (
                f"{row['coverage_numerator']}/{row['coverage_denominator']} "
                f"({row['coverage_value'] or 'undefined'})"
            )
            lines.append(
                f"| {row['framework']} | {row['condition_id']} | {risk} | {coverage} | "
                f"{row['cost_cny']} | `{source_sha256}` |"
            )
    lines.extend(
        [
            "",
            "## Failure and missingness accounting",
            "",
            f"Missing cells: `{missing_cells}` (source `{source_sha256}`).",
            "",
            "| Domain | Framework | Condition | Failure | Count | Scheduled | Source |",
            "| --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    nonzero_failures = [
        row for row in failure_rows if int(cast(str, row["numerator"])) > 0
    ]
    if nonzero_failures:
        for row in nonzero_failures:
            lines.append(
                f"| {row['domain']} | {row['framework']} | {row['condition_id']} | "
                f"{row['failure_category']} | {row['numerator']} | {row['denominator']} | "
                f"`{source_sha256}` |"
            )
    else:
        scheduled = sum(
            int(cast(str, row["denominator"])) for row in failure_rows
        ) // len(_FAILURES)
        lines.append(
            f"| all | all | all | none recorded | 0 | "
            f"{scheduled} | "
            f"`{source_sha256}` |"
        )
    lines.extend(
        [
            "",
            "## Claim gates",
            "",
            "The machine-readable decisions are in `claim-gates.json`; null and negative "
            "outcomes remain visible.",
            "",
            "| Hypothesis | Outcome | Scope or rationale | Source |",
            "| --- | --- | --- | --- |",
        ]
    )
    decisions = claim_payload.get("decisions", [])
    if isinstance(decisions, list):
        for decision in decisions:
            if not isinstance(decision, Mapping):
                continue
            detail = decision.get("scope") or decision.get("rationale") or "not supplied"
            sources = decision.get("source_artifact_sha256s") or (source_sha256,)
            if not isinstance(sources, (list, tuple)):
                sources = (source_sha256,)
            rendered_sources = ", ".join(f"`{item}`" for item in sources)
            lines.append(
                f"| {decision.get('hypothesis_id', 'unknown')} | "
                f"{decision.get('outcome', 'unresolved')} | {detail} | {rendered_sources} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation constraints",
            "",
            "- Framework portability is not equivalence.",
            "- OpsLab is preliminary replication, not broad generalization.",
            "- Verifier disagreement is not correctness.",
            "- B4 is operational cross-model evidence, not a pure model-identity intervention.",
            "- Missingness, provider failures, invalid outputs, costs, nulls, and "
            "contradictions remain reported.",
            "",
            f"Claim report schema: `{claim_payload.get('schema_name', 'unknown')}`.",
        ]
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _media_type(path: str) -> str:
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".csv"):
        return "text/csv"
    if path.endswith(".svg"):
        return "image/svg+xml"
    return "text/markdown"


def _digest(path: str, content: bytes) -> str:
    if path.endswith(".json"):
        return canonical_sha256(json.loads(content))
    return sha256(content).hexdigest()


def _require_safe_assets(contents: Mapping[str, bytes]) -> None:
    combined = b"\n".join(contents.values()).decode("utf-8").casefold()
    if any(forbidden in combined for forbidden in _FORBIDDEN_ARTIFACT_TEXT):
        raise ValueError("unsafe Phase 5 analysis artifact content")


def write_paper_assets(
    destination: Path,
    *,
    analysis: Phase5AnalysisInput,
    bundle_config: BundleConfigLike,
    claim_gate_report: ClaimGateReportLike,
) -> tuple[Path, ...]:
    """Publish exactly eight deterministic analysis assets without replacement."""
    validated = Phase5AnalysisInput.model_validate(analysis.model_dump(mode="python"))
    config_payload = cast(JsonValue, bundle_config.model_dump(mode="json"))
    if any(
        effect.seed != bundle_config.analysis_seed
        or effect.draws != bundle_config.bootstrap_draws
        for effect in validated.paired_effects
    ):
        raise ValueError("paired-effect analysis policy does not match bundle config")
    claim_payload = cast(JsonValue, claim_gate_report.model_dump(mode="json"))
    source = bundle_config.evaluated_results_manifest_sha256
    metrics, metric_rows = _metrics_csv(validated, source)
    risk_csv = _risk_coverage_csv(validated, source)
    failure_csv, failure_rows = _failure_csv(validated, source)
    contents: dict[str, bytes] = {
        "metrics-by-condition.csv": metrics,
        "paired-effects.csv": _paired_csv(validated, source),
        "failure-accounting.csv": failure_csv,
        "risk-coverage.csv": risk_csv,
        "claim-gates.json": canonical_bytes(claim_payload) + b"\n",
        "main-results.md": _main_results(
            metric_rows,
            failure_rows,
            cast(Mapping[str, object], claim_payload),
            source,
            validated.claim_gate_evidence.get("missing_cells", "not supplied"),
        ),
        "risk-coverage.svg": render_risk_coverage_svg(risk_csv),
    }
    _require_safe_assets(contents)
    manifest = {
        "schema_name": "spanvouch.phase5-analysis-manifest",
        "schema_version": "1.0",
        "experiment_id": bundle_config.experiment_id,
        "mode": bundle_config.mode,
        "bundle_config_sha256": canonical_sha256(config_payload),
        "analysis_input_sha256": canonical_sha256(
            cast(JsonValue, validated.model_dump(mode="json"))
        ),
        "evaluated_results_manifest_sha256": source,
        "risk_coverage_svg_source_sha256": _digest("risk-coverage.csv", risk_csv),
        "claim_gate_report_sha256": canonical_sha256(claim_payload),
        "analysis_seed": bundle_config.analysis_seed,
        "bootstrap_draws": bundle_config.bootstrap_draws,
        "policy_versions": list(bundle_config.policy_versions),
        "assets": [
            {
                "path": path,
                "sha256": _digest(path, contents[path]),
                "media_type": _media_type(path),
            }
            for path in sorted(contents)
        ],
    }
    contents["manifest.json"] = canonical_bytes(cast(JsonValue, manifest)) + b"\n"
    _require_safe_assets(contents)
    if tuple(sorted(contents)) != tuple(sorted((*_ASSET_NAMES, "manifest.json"))):
        raise RuntimeError("Phase 5 analysis bundle has an unexpected asset set")
    if destination.exists():
        raise FileExistsError(f"analysis output directory already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            dir=destination.parent,
            prefix=f".{destination.name}.tmp-",
        )
    )
    try:
        for path, content in sorted(contents.items()):
            target = temporary / path
            with target.open("xb") as stream:
                stream.write(content)
            if target.read_bytes() != content:
                raise ValueError(f"analysis asset write verification failed: {path}")
        publish_directory_no_replace(temporary, destination)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return tuple(destination / path for path in sorted(contents))
