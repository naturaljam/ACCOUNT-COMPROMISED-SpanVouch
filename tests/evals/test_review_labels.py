import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spanvouch.contracts.trace import TraceIR
from spanvouch.diagnosis.models import DiagnosisReport
from spanvouch.evals.generate_review_dataset import generate_review_dataset
from spanvouch.evals.review_labels import (
    load_review_candidates,
    load_review_labels,
    validate_review_dataset,
    validate_review_join,
    validate_source_run_ids,
)
from spanvouch.invariants.engine import InvariantEngine
from spanvouch.invariants.supportlab import supportlab_rules
from spanvouch.review.evidence_verifier import EvidenceVerifier
from spanvouch.review.models import (
    ReviewInputSnapshot,
    VerificationInput,
    canonical_json,
    canonical_sha256,
)
from tests.trace.test_diagnostic_view import project_trace

DATASET = Path("evals/datasets/supportlab-review-v1")
SOURCE_DATASET = Path("evals/datasets/supportlab-v1")


def _copy_dataset(tmp_path: Path) -> Path:
    target = tmp_path / "review"
    shutil.copytree(DATASET, target)
    return target


def _source_traces() -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in (SOURCE_DATASET / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.write_text(content, encoding="utf-8", newline="\n")


def _refresh_review_hashes(dataset: Path) -> None:
    candidates = dataset / "review-candidates-v1.jsonl"
    labels = dataset / "review-labels-v1.jsonl"
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidates_sha256"] = hashlib.sha256(candidates.read_bytes()).hexdigest()
    manifest["labels_sha256"] = hashlib.sha256(labels.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _tamper_source_trace_whitespace(source: Path) -> None:
    traces = source / "traces.jsonl"
    traces.write_bytes(traces.read_bytes().replace(b"\n", b" \n", 1))


def test_loader_rejects_duplicate_candidate_ids(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    first = (DATASET / "review-candidates-v1.jsonl").read_text(encoding="utf-8").splitlines()[0]
    path.write_text(f"{first}\n{first}\n", encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match="duplicate review candidate_id"):
        load_review_candidates(path)


def test_source_join_rejects_unknown_run_ids() -> None:
    candidate = load_review_candidates(DATASET / "review-candidates-v1.jsonl")[0]
    report = DiagnosisReport.model_validate(
        {**candidate.report.model_dump(mode="python"), "run_id": "unknown-run"}
    )
    unknown = candidate.model_copy(
        update={
            "candidate_id": "unknown-run--unmodified",
            "source_run_id": "unknown-run",
            "report": report,
        }
    )

    with pytest.raises(ValueError, match="unknown source run IDs: unknown-run"):
        validate_source_run_ids((unknown,), _source_traces())


def test_label_candidate_join_rejects_missing_and_extra_ids() -> None:
    candidates = load_review_candidates(DATASET / "review-candidates-v1.jsonl")
    labels = load_review_labels(DATASET / "review-labels-v1.jsonl")

    with pytest.raises(ValueError, match="review label join mismatch"):
        validate_review_join(candidates, labels[1:])


def test_label_loader_rejects_invalid_expected_finding_code(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    path.write_text(
        '{"candidate_id":"candidate","expected_finding_codes":["not_a_code"],'
        '"expected_verdict":"review_required"}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="not_a_code"):
        load_review_labels(path)


def test_manifest_validation_rejects_changed_candidate_hash(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    candidates = dataset / "review-candidates-v1.jsonl"
    candidates.write_bytes(candidates.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="candidate file SHA-256"):
        validate_review_dataset(dataset, SOURCE_DATASET)


def test_manifest_validation_rejects_changed_phase2_source_hash(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    source = tmp_path / "source"
    shutil.copytree(SOURCE_DATASET, source)
    manifest = source / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="Phase 2 source manifest SHA-256"):
        validate_review_dataset(dataset, source)


@pytest.mark.asyncio
async def test_generation_rejects_phase2_trace_bytes_that_drift_from_manifest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    shutil.copytree(SOURCE_DATASET, source)
    _tamper_source_trace_whitespace(source)

    with pytest.raises(ValueError, match="Phase 2 traces SHA-256"):
        await generate_review_dataset(
            tmp_path / "generated", seed=20260717, source_dataset=source
        )


def test_validation_rejects_phase2_trace_bytes_that_drift_from_manifest(
    tmp_path: Path,
) -> None:
    dataset = _copy_dataset(tmp_path)
    source = tmp_path / "source"
    shutil.copytree(SOURCE_DATASET, source)
    _tamper_source_trace_whitespace(source)

    with pytest.raises(ValueError, match="Phase 2 traces SHA-256"):
        validate_review_dataset(dataset, source)


def test_validation_rejects_self_consistent_wrong_mutation_family_counts(
    tmp_path: Path,
) -> None:
    dataset = _copy_dataset(tmp_path)
    candidates_path = dataset / "review-candidates-v1.jsonl"
    labels_path = dataset / "review-labels-v1.jsonl"
    candidates = [json.loads(line) for line in candidates_path.read_text().splitlines()]
    labels = [json.loads(line) for line in labels_path.read_text().splitlines()]
    changed = next(row for row in candidates if row["mutation_kind"] == "invalid_selector")
    old_id = changed["candidate_id"]
    changed["mutation_kind"] = "diagnosis_conflict"
    changed["candidate_id"] = f'{changed["source_run_id"]}--diagnosis_conflict'
    next(label for label in labels if label["candidate_id"] == old_id)[
        "candidate_id"
    ] = changed["candidate_id"]
    _write_jsonl(candidates_path, candidates)
    _write_jsonl(labels_path, labels)
    _refresh_review_hashes(dataset)

    with pytest.raises(ValueError, match="mutation family counts"):
        validate_review_dataset(dataset, SOURCE_DATASET)


def test_validation_rejects_wrong_unsupported_source_family_contract(
    tmp_path: Path,
) -> None:
    dataset = _copy_dataset(tmp_path)
    candidates_path = dataset / "review-candidates-v1.jsonl"
    labels_path = dataset / "review-labels-v1.jsonl"
    candidates = [json.loads(line) for line in candidates_path.read_text().splitlines()]
    labels = [json.loads(line) for line in labels_path.read_text().splitlines()]
    changed = next(
        row
        for row in candidates
        if row["candidate_id"] == "context_corruption-01--unsupported_scope"
    )
    old_id = changed["candidate_id"]
    changed["source_run_id"] = "clean-01"
    changed["candidate_id"] = "clean-01--unsupported_scope"
    changed["report"]["run_id"] = "clean-01"
    next(label for label in labels if label["candidate_id"] == old_id)[
        "candidate_id"
    ] = changed["candidate_id"]
    _write_jsonl(candidates_path, candidates)
    _write_jsonl(labels_path, labels)
    _refresh_review_hashes(dataset)

    with pytest.raises(ValueError, match="unsupported source run IDs"):
        validate_review_dataset(dataset, SOURCE_DATASET)


def test_validation_rejects_report_bound_to_another_source_trace_id(
    tmp_path: Path,
) -> None:
    dataset = _copy_dataset(tmp_path)
    candidates_path = dataset / "review-candidates-v1.jsonl"
    candidates = [json.loads(line) for line in candidates_path.read_text().splitlines()]
    source_trace_ids = {trace.run_id: trace.trace_id for trace in _source_traces()}
    changed = next(
        row
        for row in candidates
        if row["candidate_id"] == "clean-01--unmodified"
    )
    changed["report"]["trace_id"] = source_trace_ids["clean-02"]
    _write_jsonl(candidates_path, candidates)
    _refresh_review_hashes(dataset)

    with pytest.raises(ValueError, match="trace_id"):
        validate_review_dataset(dataset, SOURCE_DATASET)


def test_manifest_validation_rejects_crlf_even_with_matching_hash(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    candidates = dataset / "review-candidates-v1.jsonl"
    candidates.write_bytes(candidates.read_bytes().replace(b"\n", b"\r\n"))
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidates_sha256"] = hashlib.sha256(candidates.read_bytes()).hexdigest()
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    manifest_path.write_text(canonical + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match="CRLF"):
        validate_review_dataset(dataset, SOURCE_DATASET)


@pytest.mark.asyncio
async def test_generation_and_verification_do_not_load_gold_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import spanvouch.evals.review_labels as review_labels

    def fail_if_loaded(path: Path) -> object:
        raise AssertionError(f"gold labels leaked from {path}")

    monkeypatch.setattr(review_labels, "load_review_labels", fail_if_loaded)
    await generate_review_dataset(tmp_path / "generated", seed=20260717)

    candidate = next(
        item
        for item in load_review_candidates(DATASET / "review-candidates-v1.jsonl")
        if item.mutation_kind == "unmodified"
    )
    trace = next(item for item in _source_traces() if item.run_id == candidate.source_run_id)
    view = project_trace(trace)
    view_json = canonical_json(view)
    snapshot = ReviewInputSnapshot(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        view_json=view_json,
        input_sha256=canonical_sha256(view),
        catalog_version="evidence-catalog-v1",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    verifier = EvidenceVerifier(
        InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
    )

    await verifier.verify(
        VerificationInput(
            snapshot=snapshot,
            report=candidate.report,
            report_sha256=canonical_sha256(candidate.report),
        )
    )
