"""Offline post-call join of verified provider outcomes with sealed labels."""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_bytes, canonical_sha256
from spanvouch.evaluation.artifacts import (
    capture_owned_directory_identity,
    create_owned_staging_directory,
    delete_owned_staging_directory,
    publish_directory_no_replace,
    quarantine_owned_staging_directory,
    read_verified_directory_tree,
)
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.corpus.labels import GoldLabel, GoldLabelManifest
from spanvouch.evaluation.experiments.config import ConditionId
from spanvouch.evaluation.experiments.models import (
    ExperimentFailureCategory,
    SelectiveAction,
)
from spanvouch.evaluation.experiments.runner import (
    OutcomeStatus,
    ProviderPhaseRepository,
    ProviderPlanOutcome,
)


class EvaluatedConditionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(pattern=SHA256_PATTERN)
    cell: CorpusCell
    condition_id: ConditionId
    provider_outcome_sha256: str = Field(pattern=SHA256_PATTERN)
    status: OutcomeStatus
    selective_action: SelectiveAction | None
    failure_category: ExperimentFailureCategory | None
    is_correct: bool | None
    diagnosis_correct: bool | None
    causal_chain_correct: bool | None
    grounding_correct: bool | None
    verification_correct: bool | None
    diagnosis_error: bool | None
    verification_error: bool | None
    evaluation_evidence_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    family: str = Field(min_length=1)
    control: bool
    split: Literal["pilot", "train", "validation", "test"]

    @model_validator(mode="after")
    def validate_correctness(self) -> Self:
        operational = {
            ExperimentFailureCategory.FRAMEWORK_EXECUTION,
            ExperimentFailureCategory.FRAMEWORK_INCOMPATIBILITY,
            ExperimentFailureCategory.INFRASTRUCTURE,
            ExperimentFailureCategory.PROVIDER,
            ExperimentFailureCategory.CONTRACT_INVALID,
        }
        if self.status is not OutcomeStatus.COMPLETED and self.is_correct is not None:
            raise ValueError("non-completed outcome cannot count as correct")
        if self.failure_category in operational and self.is_correct is not None:
            raise ValueError("operational failure cannot count as correct abstention")
        return self


class EvaluationResultEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(pattern=SHA256_PATTERN)
    result_sha256: str = Field(pattern=SHA256_PATTERN)
    result_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if self.result_path != f"results/{self.plan_id}.json":
            raise ValueError("evaluation result path does not match plan")
        return self


class EvaluationPhaseManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.evaluation-phase"] = "spanvouch.evaluation-phase"
    schema_version: Literal["1.0"] = "1.0"
    provider_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    sealed_labels_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    labels_sha256: str = Field(pattern=SHA256_PATTERN)
    entries: tuple[EvaluationResultEntry, ...]
    evaluated_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_count(self) -> Self:
        if self.evaluated_count != len(self.entries):
            raise ValueError("evaluated count does not match entries")
        if len({entry.plan_id for entry in self.entries}) != len(self.entries):
            raise ValueError("evaluation plan IDs must be unique")
        return self


def _model_bytes(model: BaseModel) -> bytes:
    return canonical_bytes(cast(JsonValue, model.model_dump(mode="json")))


class EvaluationPhaseRepository:
    """One immutable, atomically published evaluation directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        if os.path.lexists(self.root):
            raise FileExistsError("evaluation output directory must not already exist")

    def publish(
        self,
        *,
        results: tuple[EvaluatedConditionResult, ...],
        manifest: EvaluationPhaseManifest,
    ) -> None:
        staging, root_identity = create_owned_staging_directory(self.root)
        identity = None
        try:
            results_dir = staging / "results"
            results_dir.mkdir()
            for result in results:
                (results_dir / f"{result.plan_id}.json").write_bytes(_model_bytes(result))
            (staging / "manifest.json").write_bytes(_model_bytes(manifest))
            identity = capture_owned_directory_identity(staging)
            publish_directory_no_replace(staging, self.root)
            if capture_owned_directory_identity(self.root) != identity:
                raise RuntimeError("published evaluation directory identity changed")
        except Exception:
            if os.path.lexists(staging):
                if identity is None:
                    quarantine_owned_staging_directory(staging, root_identity)
                else:
                    delete_owned_staging_directory(staging, identity)
            raise

    def load(self, plan_id: str) -> EvaluatedConditionResult:
        if re.fullmatch(SHA256_PATTERN, plan_id) is None:
            raise ValueError("plan_id must be a SHA-256 digest")
        content = (self.root / "results" / f"{plan_id}.json").read_bytes()
        result = EvaluatedConditionResult.model_validate_json(content)
        if _model_bytes(result) != content or result.plan_id != plan_id:
            raise ValueError("evaluation result failed canonical verification")
        return result

    def verify(self) -> EvaluationPhaseManifest:
        snapshot = read_verified_directory_tree(self.root)
        content = snapshot.files.get("manifest.json")
        if content is None:
            raise ValueError("evaluation manifest is missing")
        manifest = EvaluationPhaseManifest.model_validate_json(content)
        if _model_bytes(manifest) != content:
            raise ValueError("evaluation manifest is not canonical")
        expected_files = {"manifest.json", *(entry.result_path for entry in manifest.entries)}
        if set(snapshot.files) != expected_files or snapshot.directories != {"results"}:
            raise ValueError("evaluation directory layout does not match manifest")
        for entry in manifest.entries:
            result_content = snapshot.files[entry.result_path]
            if sha256(result_content).hexdigest() != entry.result_sha256:
                raise ValueError("evaluation result hash mismatch")
            if self.load(entry.plan_id).plan_id != entry.plan_id:
                raise ValueError("evaluation result plan mismatch")
        return manifest


class PostCallEvaluator:
    """Network-free evaluation after provider completion and artifact verification."""

    def join(
        self,
        *,
        provider_repository: ProviderPhaseRepository,
        expected_provider_manifest_sha256: str,
        sealed_labels: GoldLabelManifest,
        sealed_labels_manifest_sha256: str,
        repository: EvaluationPhaseRepository,
    ) -> EvaluationPhaseManifest:
        labels = GoldLabelManifest.model_validate(sealed_labels.model_dump(mode="python"))
        if canonical_sha256(labels) != sealed_labels_manifest_sha256:
            raise ValueError("sealed label manifest SHA-256 mismatch")
        provider = provider_repository.verify(
            expected_manifest_sha256=expected_provider_manifest_sha256
        )
        if not provider.provider_phase_complete:
            raise ValueError("provider phase is incomplete")
        if labels.corpus_manifest_sha256 != provider.corpus_manifest_sha256:
            raise ValueError("sealed labels belong to another corpus")
        labels_by_identity = {label.cell_identity: label for label in labels.labels}
        provider_identities = {_cell_identity(cell) for cell in provider.corpus_cells}
        if set(labels_by_identity) != provider_identities:
            raise ValueError("label cell set does not match provider corpus")

        evaluated: list[EvaluatedConditionResult] = []
        for entry in provider.entries:
            outcome = provider_repository.load(entry.plan_id)
            label = labels_by_identity[_cell_identity(outcome.plan.cell)]
            _require_label_binding(outcome, label)
            evaluated.append(_evaluate(outcome, entry.outcome_sha256, label))
        results = tuple(evaluated)
        entries = tuple(
            EvaluationResultEntry(
                plan_id=result.plan_id,
                result_sha256=sha256(_model_bytes(result)).hexdigest(),
                result_path=f"results/{result.plan_id}.json",
            )
            for result in results
        )
        manifest = EvaluationPhaseManifest(
            provider_manifest_sha256=expected_provider_manifest_sha256,
            sealed_labels_manifest_sha256=sealed_labels_manifest_sha256,
            labels_sha256=labels.labels_sha256,
            entries=entries,
            evaluated_count=len(results),
        )
        repository.publish(results=results, manifest=manifest)
        repository.verify()
        return manifest


def _cell_identity(cell: CorpusCell) -> str:
    return ":".join(
        (
            cell.domain,
            cell.template_id,
            cell.scenario_id,
            cell.framework_id.value,
            str(cell.repetition),
            str(cell.seed),
        )
    )


def _require_label_binding(outcome: ProviderPlanOutcome, label: GoldLabel) -> None:
    if (
        label.record_sha256 != outcome.plan.record_sha256
        or label.trace_sha256 != outcome.plan.trace_sha256
    ):
        raise ValueError("sealed label hashes do not match provider plan")


def _evaluate(
    outcome: ProviderPlanOutcome,
    outcome_sha256: str,
    label: GoldLabel,
) -> EvaluatedConditionResult:
    result = outcome.result
    failure = result.failure if result is not None else None
    action = result.selective_action if result is not None else None
    category = failure.category if failure is not None else None
    evidence = result.evaluation_evidence if result is not None else None
    diagnosis_correct: bool | None = None
    causal_correct: bool | None = None
    grounding_correct: bool | None = None
    verification_correct: bool | None = None
    is_correct: bool | None = None
    diagnosis_error: bool | None = None
    verification_error: bool | None = None
    operational = {
        ExperimentFailureCategory.FRAMEWORK_EXECUTION,
        ExperimentFailureCategory.FRAMEWORK_INCOMPATIBILITY,
        ExperimentFailureCategory.INFRASTRUCTURE,
        ExperimentFailureCategory.PROVIDER,
        ExperimentFailureCategory.CONTRACT_INVALID,
    }
    if outcome.status is OutcomeStatus.COMPLETED and category not in operational:
        family_correct = (
            evidence is not None
            and evidence.diagnosis_family == label.expected_failure_type
        )
        causal_correct = evidence is not None and _ordered_token_subset(
            label.causal_chain_expectations,
            evidence.causal_tokens,
        )
        grounding_correct = evidence is not None and all(
            any(
                selector.partition("::")[2].endswith(expected)
                for selector in evidence.diagnosis_selectors
            )
            for expected in label.evidence_expectations
        )
        diagnosis_correct = family_correct and causal_correct and grounding_correct
        diagnosis_error = not diagnosis_correct
        if outcome.plan.condition_id is ConditionId.B0:
            is_correct = diagnosis_correct
        else:
            has_verifier = evidence is not None and bool(evidence.verifier_reports)
            verification_correct = (
                has_verifier
                and action is not None
                and (action is SelectiveAction.ACCEPT) == diagnosis_correct
            )
            verification_error = not verification_correct
            is_correct = verification_correct
        if verification_error:
            category = ExperimentFailureCategory.VERIFICATION
        elif diagnosis_error:
            category = ExperimentFailureCategory.DIAGNOSIS
    return EvaluatedConditionResult(
        plan_id=outcome.plan.plan_id,
        cell=outcome.plan.cell,
        condition_id=outcome.plan.condition_id,
        provider_outcome_sha256=outcome_sha256,
        status=outcome.status,
        selective_action=action,
        failure_category=category,
        is_correct=is_correct,
        diagnosis_correct=diagnosis_correct,
        causal_chain_correct=causal_correct,
        grounding_correct=grounding_correct,
        verification_correct=verification_correct,
        diagnosis_error=diagnosis_error,
        verification_error=verification_error,
        evaluation_evidence_sha256=(
            evidence.projection_sha256 if evidence is not None else None
        ),
        family=label.expected_failure_type,
        control=label.control,
        split=label.split,
    )


def _ordered_token_subset(expected: tuple[str, ...], actual: tuple[str, ...]) -> bool:
    required = tuple(
        token
        for item in expected
        for token in re.findall(r"[a-z][a-z0-9_]*", item.casefold())
    )
    if not required:
        return True
    cursor = iter(actual)
    return all(any(candidate == token for candidate in cursor) for token in required)


@dataclass(frozen=True)
class EvaluationRequest:
    provider_results: Path
    sealed_labels: Path
    output_dir: Path


EvaluationCommand = Callable[[EvaluationRequest], None]


def _default_command(request: EvaluationRequest) -> None:
    if not request.provider_results.is_dir():
        raise ValueError("provider results must be an existing directory")
    label_path = request.sealed_labels / "manifest.json"
    content = label_path.read_bytes()
    labels = GoldLabelManifest.model_validate_json(content)
    if canonical_bytes(labels) != content:
        raise ValueError("sealed label manifest is not canonical")
    labels_sha256 = sha256(content).hexdigest()
    provider_repository = ProviderPhaseRepository(request.provider_results)
    PostCallEvaluator().join(
        provider_repository=provider_repository,
        expected_provider_manifest_sha256=provider_repository.manifest_sha256,
        sealed_labels=labels,
        sealed_labels_manifest_sha256=labels_sha256,
        repository=EvaluationPhaseRepository(request.output_dir),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch experiments evaluate")
    parser.add_argument("--provider-results", required=True, type=Path)
    parser.add_argument("--sealed-labels", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    command: EvaluationCommand | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
    request = EvaluationRequest(
        provider_results=arguments.provider_results,
        sealed_labels=arguments.sealed_labels,
        output_dir=arguments.output_dir,
    )
    (command or _default_command)(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
