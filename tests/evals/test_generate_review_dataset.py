import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from afc.diagnosis.models import DiagnosisReport
from afc.evals.generate_review_dataset import (
    CANDIDATES_FILENAME,
    LABELS_FILENAME,
    MANIFEST_FILENAME,
    ReviewCandidate,
    generate_review_dataset,
)

SOURCE_DATASET = Path("evals/datasets/supportlab-v1")
COMMITTED_DATASET = Path("evals/datasets/supportlab-review-v1")
FROZEN_SOURCE_FILES = (
    "traces.jsonl",
    "labels.jsonl",
    "manifest.json",
    "diagnosis-labels-v1.jsonl",
    "diagnosis-manifest-v1.json",
)


def _jsonl(path: Path) -> tuple[dict[str, object], ...]:
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())


def _source_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((SOURCE_DATASET / name).read_bytes()).hexdigest()
        for name in FROZEN_SOURCE_FILES
    }


@pytest.mark.asyncio
async def test_generated_review_cohort_has_exact_required_shape(tmp_path: Path) -> None:
    manifest = await generate_review_dataset(tmp_path, seed=20260717)
    rows = _jsonl(tmp_path / CANDIDATES_FILENAME)
    candidates = tuple(ReviewCandidate.model_validate(row) for row in rows)

    assert manifest.candidate_count == 36
    assert manifest.valid_count == 20
    assert manifest.mutation_count == 16
    assert len(candidates) == 36
    assert Counter(candidate.mutation_kind for candidate in candidates) == {
        "unmodified": 20,
        "invalid_selector": 2,
        "evidence_value_hash_mismatch": 2,
        "claim_not_grounded": 2,
        "critical_span_not_grounded": 2,
        "diagnosis_conflict": 2,
        "unsupported_scope": 6,
    }
    assert all(isinstance(candidate.report, DiagnosisReport) for candidate in candidates)


@pytest.mark.asyncio
async def test_unsupported_mutations_cover_both_examples_of_each_family(
    tmp_path: Path,
) -> None:
    await generate_review_dataset(tmp_path, seed=20260717)
    candidates = tuple(
        ReviewCandidate.model_validate(row)
        for row in _jsonl(tmp_path / CANDIDATES_FILENAME)
    )

    unsupported = {
        candidate.source_run_id
        for candidate in candidates
        if candidate.mutation_kind == "unsupported_scope"
    }
    assert unsupported == {
        "context_corruption-01",
        "context_corruption-02",
        "ignored_tool_error-01",
        "ignored_tool_error-02",
        "missing_precondition-01",
        "missing_precondition-02",
    }


@pytest.mark.asyncio
async def test_critical_span_mutation_keeps_claim_path_and_adds_one_gap(
    tmp_path: Path,
) -> None:
    await generate_review_dataset(tmp_path, seed=20260717)
    candidates = tuple(
        ReviewCandidate.model_validate(row)
        for row in _jsonl(tmp_path / CANDIDATES_FILENAME)
    )
    by_id = {candidate.candidate_id: candidate for candidate in candidates}

    for run_id in ("policy_violation-01", "policy_violation-02"):
        original = by_id[f"{run_id}--unmodified"].report
        mutated = by_id[f"{run_id}--critical_span_not_grounded"].report
        assert set(original.critical_span_ids) < set(mutated.critical_span_ids)
        assert len(mutated.critical_span_ids) == len(original.critical_span_ids) + 1


@pytest.mark.asyncio
async def test_generation_is_ordered_deterministic_lf_and_source_read_only(
    tmp_path: Path,
) -> None:
    before = _source_hashes()
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_manifest = await generate_review_dataset(first, seed=20260717)
    second_manifest = await generate_review_dataset(second, seed=20260717)

    assert first_manifest == second_manifest
    assert _source_hashes() == before
    for filename in (CANDIDATES_FILENAME, LABELS_FILENAME, MANIFEST_FILENAME):
        first_bytes = (first / filename).read_bytes()
        assert first_bytes == (second / filename).read_bytes()
        assert first_bytes.endswith(b"\n")
        assert b"\r" not in first_bytes

    rows = _jsonl(first / CANDIDATES_FILENAME)
    keys = tuple((row["source_run_id"], row["mutation_kind"]) for row in rows)
    assert keys == tuple(sorted(keys))
    assert all(
        row["candidate_id"] == f'{row["source_run_id"]}--{row["mutation_kind"]}'
        for row in rows
    )
    assert all(row["report"]["run_id"] == row["source_run_id"] for row in rows)


@pytest.mark.asyncio
async def test_committed_review_dataset_matches_seeded_generation(tmp_path: Path) -> None:
    await generate_review_dataset(tmp_path, seed=20260717)

    for filename in (CANDIDATES_FILENAME, LABELS_FILENAME, MANIFEST_FILENAME):
        assert (tmp_path / filename).read_bytes() == (COMMITTED_DATASET / filename).read_bytes()
