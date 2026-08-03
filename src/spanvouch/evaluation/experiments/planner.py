"""Deterministic planning for the complete Phase 5 B0-B5 matrix."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import cast

from pydantic import JsonValue

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.experiments.config import (
    ConditionId,
    ModelEndpointConfig,
    Phase5ExperimentConfig,
)
from spanvouch.evaluation.experiments.diagnosis import FrozenDiagnosisCandidate
from spanvouch.evaluation.experiments.models import (
    ConditionPlan,
    IneligibleCell,
    ProviderPlanStatus,
)
from spanvouch.labs.runtime import FrameworkId

_NO_PROVIDER_PROMPTS = {
    ConditionId.B0: "phase5-no-verifier-v1",
    ConditionId.B1: "phase5-deterministic-v1",
}


class VerificationMatrixPlanner:
    """Emit all preregistered conditions without cost-based selective omission."""

    def plan(
        self,
        candidates: tuple[FrozenDiagnosisCandidate, ...],
        config: Phase5ExperimentConfig,
        *,
        expected_cells: tuple[CorpusCell, ...],
        ineligible: tuple[IneligibleCell, ...] = (),
    ) -> tuple[ConditionPlan, ...]:
        validated_config = Phase5ExperimentConfig.model_validate(
            config.model_dump(mode="python")
        )
        validated_candidates = tuple(
            FrozenDiagnosisCandidate.model_validate(candidate.model_dump(mode="python"))
            for candidate in candidates
        )
        validated_expected = tuple(
            CorpusCell.model_validate(cell.model_dump(mode="python"))
            for cell in expected_cells
        )
        validated_ineligible = tuple(
            IneligibleCell.model_validate(item.model_dump(mode="python"))
            for item in ineligible
        )
        self._validate_candidate_set(validated_candidates, validated_ineligible)
        self._validate_expected_partition(
            validated_candidates, validated_ineligible, validated_expected
        )
        config_sha256 = canonical_sha256(
            cast(JsonValue, validated_config.model_dump(mode="json"))
        )
        plans: list[ConditionPlan] = []
        for candidate in sorted(validated_candidates, key=lambda item: item.cell.sort_key()):
            for condition in ConditionId:
                endpoint = self._endpoint_for(condition, validated_config)
                if endpoint is None:
                    plans.append(
                        ConditionPlan.from_payload(
                            experiment_id=validated_config.experiment_id,
                            experiment_config_sha256=config_sha256,
                            corpus_manifest_sha256=candidate.corpus_manifest_sha256,
                            cell=candidate.cell,
                            record_sha256=candidate.record_sha256,
                            trace_sha256=candidate.trace_sha256,
                            diagnosis_sha256=candidate.report_sha256,
                            condition_id=condition,
                            prompt_version=_NO_PROVIDER_PROMPTS[condition],
                            provider_status=ProviderPlanStatus.NOT_REQUIRED,
                            provider=None,
                            model=None,
                            generation=None,
                        )
                    )
                else:
                    plans.append(
                        ConditionPlan.from_payload(
                            experiment_id=validated_config.experiment_id,
                            experiment_config_sha256=config_sha256,
                            corpus_manifest_sha256=candidate.corpus_manifest_sha256,
                            cell=candidate.cell,
                            record_sha256=candidate.record_sha256,
                            trace_sha256=candidate.trace_sha256,
                            diagnosis_sha256=candidate.report_sha256,
                            condition_id=condition,
                            prompt_version=endpoint.prompt_version,
                            provider_status=ProviderPlanStatus.REQUIRED,
                            provider=endpoint.provider,
                            model=endpoint.model,
                            generation=endpoint,
                        )
                    )
        return tuple(plans)

    def validate(
        self,
        plans: tuple[ConditionPlan, ...],
        candidates: tuple[FrozenDiagnosisCandidate, ...],
        config: Phase5ExperimentConfig,
        *,
        expected_cells: tuple[CorpusCell, ...],
        ineligible: tuple[IneligibleCell, ...] = (),
    ) -> None:
        validated_plans = tuple(
            ConditionPlan.model_validate(plan.model_dump(mode="python")) for plan in plans
        )
        if len({plan.plan_id for plan in validated_plans}) != len(validated_plans):
            raise ValueError("matrix contains duplicate plan IDs")
        by_cell: dict[object, list[ConditionPlan]] = defaultdict(list)
        for plan in validated_plans:
            by_cell[plan.cell].append(plan)
        for cell_plans in by_cell.values():
            counts = Counter(plan.condition_id for plan in cell_plans)
            if any(count > 1 for count in counts.values()):
                raise ValueError("matrix contains duplicate conditions")
            if set(counts) != set(ConditionId):
                raise ValueError("matrix must contain all six conditions per candidate")

        expected = self.plan(
            candidates,
            config,
            expected_cells=expected_cells,
            ineligible=ineligible,
        )
        expected_by_id = {(plan.cell, plan.condition_id): plan for plan in expected}
        if set(by_cell) != {candidate.cell for candidate in candidates}:
            raise ValueError("matrix candidate cells do not match eligible candidates")
        for plan in validated_plans:
            expected_plan = expected_by_id.get((plan.cell, plan.condition_id))
            if expected_plan is None:
                raise ValueError("matrix contains an unknown condition plan")
            if (
                plan.provider_status != expected_plan.provider_status
                or plan.provider != expected_plan.provider
                or plan.model != expected_plan.model
                or plan.generation != expected_plan.generation
                or plan.prompt_version != expected_plan.prompt_version
            ):
                raise ValueError("condition provider configuration drift")
            if plan.trace_sha256 != expected_plan.trace_sha256:
                raise ValueError("condition trace hash mismatch")
            if plan.diagnosis_sha256 != expected_plan.diagnosis_sha256:
                raise ValueError("condition diagnosis hash mismatch")
            if (
                plan.record_sha256 != expected_plan.record_sha256
                or plan.corpus_manifest_sha256 != expected_plan.corpus_manifest_sha256
                or plan.experiment_config_sha256 != expected_plan.experiment_config_sha256
                or plan.experiment_id != expected_plan.experiment_id
            ):
                raise ValueError("condition parent configuration mismatch")
        if validated_plans != expected:
            raise ValueError("matrix ordering or plan identity does not match deterministic plan")

    @staticmethod
    def _endpoint_for(
        condition: ConditionId,
        config: Phase5ExperimentConfig,
    ) -> ModelEndpointConfig | None:
        if condition in {ConditionId.B0, ConditionId.B1}:
            return None
        if condition is ConditionId.B2:
            return config.shared_verifier
        if condition is ConditionId.B3:
            return config.isolated_verifier
        return config.cross_model_verifier

    @staticmethod
    def _validate_candidate_set(
        candidates: tuple[FrozenDiagnosisCandidate, ...],
        ineligible: tuple[IneligibleCell, ...] = (),
    ) -> None:
        if not candidates:
            raise ValueError("matrix requires at least one eligible candidate")
        cells = tuple(candidate.cell for candidate in candidates)
        if len(cells) != len(set(cells)):
            raise ValueError("matrix contains duplicate eligible candidates")
        corpus_hashes = {candidate.corpus_manifest_sha256 for candidate in candidates}
        if len(corpus_hashes) != 1:
            raise ValueError("eligible candidates must share a corpus manifest")
        pairs: dict[str, set[FrameworkId]] = defaultdict(set)
        pair_counts: Counter[str] = Counter()
        for cell in cells:
            pairs[cell.pair_identity].add(cell.framework_id)
            pair_counts[cell.pair_identity] += 1
        for item in ineligible:
            pairs[item.cell.pair_identity].add(item.cell.framework_id)
            pair_counts[item.cell.pair_identity] += 1
        required = {FrameworkId.LANGGRAPH, FrameworkId.AUTOGEN}
        if any(frameworks != required for frameworks in pairs.values()) or any(
            count != 2 for count in pair_counts.values()
        ):
            raise ValueError("eligible candidates must contain paired framework/repetition cells")

    @staticmethod
    def _validate_expected_partition(
        candidates: tuple[FrozenDiagnosisCandidate, ...],
        ineligible: tuple[IneligibleCell, ...],
        expected_cells: tuple[CorpusCell, ...],
    ) -> None:
        if not expected_cells or len(set(expected_cells)) != len(expected_cells):
            raise ValueError("expected cells must be a non-empty unique universe")
        eligible_cells = {candidate.cell for candidate in candidates}
        ineligible_cells = {item.cell for item in ineligible}
        if len(ineligible_cells) != len(ineligible) or eligible_cells & ineligible_cells:
            raise ValueError("eligible and ineligible cells must be a disjoint partition")
        if eligible_cells | ineligible_cells != set(expected_cells):
            raise ValueError("eligible and ineligible cells must exactly partition expected cells")
