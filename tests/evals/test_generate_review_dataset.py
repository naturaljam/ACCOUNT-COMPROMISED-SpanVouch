import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from spanvouch.contracts.diagnosis import DiagnosisReport
from spanvouch.evals.generate_review_dataset import (
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
    for candidate in candidates:
        if candidate.mutation_kind == "unsupported_scope":
            statements = tuple(claim.statement for claim in candidate.report.causal_chain)
            assert statements == ("The selected tool caused the request to fail.",)
            assert all("mutation" not in statement.lower() for statement in statements)
            assert all("unsupported trace" not in statement.lower() for statement in statements)


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
async def test_phase4_generation_does_not_rewrite_frozen_phase3_dataset(
    tmp_path: Path,
) -> None:
    await generate_review_dataset(tmp_path, seed=20260717)

    frozen_hashes = {
        CANDIDATES_FILENAME: "ee04d8d0f1e608fd81c202fca39eeb799f764b3099cfb03d7d94a4ab7eb73bd2",
        LABELS_FILENAME: "d41a87247456264863d70f807256a5d1b6f24ab84422dc406a92ef867e36b305",
        MANIFEST_FILENAME: "677e0075f5b4149db73538411376bf994caa5ba0fdb8ff29b33b487a5fe02076",
    }
    for filename, expected in frozen_hashes.items():
        assert hashlib.sha256((COMMITTED_DATASET / filename).read_bytes()).hexdigest() == expected

    generated = tuple(
        ReviewCandidate.model_validate_json(line)
        for line in (tmp_path / CANDIDATES_FILENAME).read_text(encoding="utf-8").splitlines()
    )
    assert all(candidate.report.schema_name == "spanvouch.diagnosis" for candidate in generated)
    assert all(
        candidate.report.provenance.taxonomy.taxonomy_id == "supportlab"
        for candidate in generated
    )
    assert (tmp_path / LABELS_FILENAME).read_bytes() == (
        COMMITTED_DATASET / LABELS_FILENAME
    ).read_bytes()
