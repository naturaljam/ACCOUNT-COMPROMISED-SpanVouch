from pathlib import Path

import pytest

from afc.evals.generate_review_dataset import MutationKind
from afc.evals.review_labels import validate_review_dataset
from afc.evals.review_metrics import evaluate_review_candidates
from afc.invariants.engine import InvariantEngine
from afc.invariants.supportlab import supportlab_rules
from afc.review.evidence_verifier import EvidenceVerifier
from afc.trace_ir.models import TraceIR

DATASET = Path("evals/datasets/supportlab-review-v1")
SOURCE_DATASET = Path("evals/datasets/supportlab-v1")


def _traces() -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in (SOURCE_DATASET / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )


async def test_deterministic_verifier_meets_frozen_review_hard_gate() -> None:
    candidates, labels, _ = validate_review_dataset(DATASET, SOURCE_DATASET)
    verifier = EvidenceVerifier(
        InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
    )

    report = await evaluate_review_candidates(
        candidates=candidates,
        labels=labels,
        traces=_traces(),
        verifier=verifier,
        policy_version="review-policy-v1",
    )

    assert report.status == "complete"
    assert report.candidate_count == 36
    assert len(report.samples) == 36
    assert report.metrics.valid_report_pass_rate == 1.0
    assert report.metrics.hard_defect_recall == 1.0
    assert report.metrics.false_block_rate == 0.0
    assert report.metrics.unsupported_scope_detection_rate == 1.0
    assert report.metrics.claim_grounding_detection_rate == 1.0
    assert report.metrics.critical_grounding_detection_rate == 1.0
    assert report.metrics.evidence_gap_precision == 1.0
    assert report.metrics.structured_output_success_rate == 1.0
    assert report.metrics.operational_error_rate == 0.0
    assert report.usage.provider_sample_count == 0
    assert report.usage.total_tokens == 0


async def test_all_sixteen_mutations_have_exact_expected_findings() -> None:
    candidates, labels, _ = validate_review_dataset(DATASET, SOURCE_DATASET)
    report = await evaluate_review_candidates(
        candidates=candidates,
        labels=labels,
        traces=_traces(),
        verifier=EvidenceVerifier(
            InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
        ),
        policy_version="review-policy-v1",
    )
    labels_by_id = {label.candidate_id: label for label in labels}

    mutated = tuple(sample for sample in report.samples if sample.mutation_kind != "unmodified")
    assert len(mutated) == 16
    for sample in mutated:
        gold = labels_by_id[sample.candidate_id]
        assert sample.verifier_report is not None
        assert sample.verifier_report.verdict is gold.expected_verdict
        assert {finding.code for finding in sample.verifier_report.findings} == set(
            gold.expected_finding_codes
        )


async def test_twenty_valid_reports_pass_without_false_blocks_or_findings() -> None:
    candidates, labels, _ = validate_review_dataset(DATASET, SOURCE_DATASET)
    report = await evaluate_review_candidates(
        candidates=candidates,
        labels=labels,
        traces=_traces(),
        verifier=EvidenceVerifier(
            InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
        ),
        policy_version="review-policy-v1",
    )

    valid = tuple(sample for sample in report.samples if sample.mutation_kind == "unmodified")
    assert len(valid) == 20
    assert all(sample.verifier_report is not None for sample in valid)
    assert all(
        sample.verifier_report.verdict == "verified"
        for sample in valid
        if sample.verifier_report
    )
    assert all(not sample.verifier_report.findings for sample in valid if sample.verifier_report)


async def test_evaluation_report_samples_do_not_embed_gold_labels() -> None:
    candidates, labels, _ = validate_review_dataset(DATASET, SOURCE_DATASET)
    report = await evaluate_review_candidates(
        candidates=candidates,
        labels=labels,
        traces=_traces(),
        verifier=EvidenceVerifier(
            InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
        ),
        policy_version="review-policy-v1",
    )

    for sample in report.model_dump(mode="json")["samples"]:
        assert "expected_verdict" not in sample
        assert "expected_finding_codes" not in sample
        assert "gold" not in sample


async def test_direct_evaluation_rejects_misleading_family_denominators() -> None:
    candidates, labels, _ = validate_review_dataset(DATASET, SOURCE_DATASET)
    target = next(
        candidate
        for candidate in candidates
        if candidate.mutation_kind is MutationKind.INVALID_SELECTOR
    )
    replacement_id = f"{target.source_run_id}--diagnosis_conflict"
    reshaped = target.model_copy(
        update={
            "candidate_id": replacement_id,
            "mutation_kind": MutationKind.DIAGNOSIS_CONFLICT,
        }
    )
    reshaped_candidates = tuple(
        reshaped if candidate.candidate_id == target.candidate_id else candidate
        for candidate in candidates
    )
    reshaped_labels = tuple(
        label.model_copy(update={"candidate_id": replacement_id})
        if label.candidate_id == target.candidate_id
        else label
        for label in labels
    )

    with pytest.raises(ValueError, match="mutation family counts"):
        await evaluate_review_candidates(
            candidates=reshaped_candidates,
            labels=reshaped_labels,
            traces=_traces(),
            verifier=EvidenceVerifier(
                InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
            ),
            policy_version="review-policy-v1",
        )


async def test_direct_evaluation_rejects_duplicate_candidate_and_label_ids() -> None:
    candidates, labels, _ = validate_review_dataset(DATASET, SOURCE_DATASET)
    first, second = tuple(
        candidate
        for candidate in candidates
        if candidate.mutation_kind is MutationKind.UNMODIFIED
    )[:2]
    first_label = next(label for label in labels if label.candidate_id == first.candidate_id)
    duplicated_candidates = tuple(
        first if candidate.candidate_id == second.candidate_id else candidate
        for candidate in candidates
    )
    duplicated_labels = tuple(
        first_label if label.candidate_id == second.candidate_id else label
        for label in labels
    )

    assert len(duplicated_candidates) == 36
    assert len({candidate.candidate_id for candidate in duplicated_candidates}) == 35
    assert len(duplicated_labels) == 36
    with pytest.raises(ValueError, match="duplicate review candidate_id"):
        await evaluate_review_candidates(
            candidates=duplicated_candidates,
            labels=duplicated_labels,
            traces=_traces(),
            verifier=EvidenceVerifier(
                InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
            ),
            policy_version="review-policy-v1",
        )


async def test_direct_evaluation_rejects_swapped_unsupported_source_ids() -> None:
    candidates, labels, _ = validate_review_dataset(DATASET, SOURCE_DATASET)
    replacements = {
        "context_corruption-01": "clean-01",
        "context_corruption-02": "clean-02",
    }
    changed_ids: dict[str, str] = {}
    reshaped_candidates = []
    for candidate in candidates:
        replacement = replacements.get(candidate.source_run_id)
        if replacement is None or candidate.mutation_kind is not MutationKind.UNSUPPORTED_SCOPE:
            reshaped_candidates.append(candidate)
            continue
        new_id = f"{replacement}--unsupported_scope"
        changed_ids[candidate.candidate_id] = new_id
        reshaped_candidates.append(
            candidate.model_copy(
                update={
                    "candidate_id": new_id,
                    "source_run_id": replacement,
                    "report": candidate.report.model_copy(update={"run_id": replacement}),
                }
            )
        )
    reshaped_labels = tuple(
        label.model_copy(update={"candidate_id": changed_ids[label.candidate_id]})
        if label.candidate_id in changed_ids
        else label
        for label in labels
    )

    assert len(reshaped_candidates) == 36
    assert len({candidate.candidate_id for candidate in reshaped_candidates}) == 36
    with pytest.raises(ValueError, match="unsupported source run IDs"):
        await evaluate_review_candidates(
            candidates=tuple(reshaped_candidates),
            labels=reshaped_labels,
            traces=_traces(),
            verifier=EvidenceVerifier(
                InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
            ),
            policy_version="review-policy-v1",
        )


async def test_direct_evaluation_rejects_report_bound_to_another_trace_id() -> None:
    candidates, labels, _ = validate_review_dataset(DATASET, SOURCE_DATASET)
    traces = _traces()
    trace_ids = {trace.run_id: trace.trace_id for trace in traces}
    target = next(
        candidate
        for candidate in candidates
        if candidate.candidate_id == "clean-01--unmodified"
    )
    mismatched = target.model_copy(
        update={
            "report": target.report.model_copy(
                update={"trace_id": trace_ids["clean-02"]}
            )
        }
    )
    reshaped = tuple(
        mismatched if candidate.candidate_id == target.candidate_id else candidate
        for candidate in candidates
    )

    with pytest.raises(ValueError, match="trace_id"):
        await evaluate_review_candidates(
            candidates=reshaped,
            labels=labels,
            traces=traces,
            verifier=EvidenceVerifier(
                InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
            ),
            policy_version="review-policy-v1",
        )
