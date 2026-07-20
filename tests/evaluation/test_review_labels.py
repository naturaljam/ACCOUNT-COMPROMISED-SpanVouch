import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

import spanvouch.evaluation.review_labels as review_labels_module
from spanvouch.contracts.diagnosis import DiagnosisReport
from spanvouch.contracts.trace import TraceIR
from spanvouch.contracts.verification import (
    FindingCode,
    ReviewInputSnapshot,
    VerificationInput,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import (
    canonical_json,
    canonical_sha256,
)
from spanvouch.evaluation.generate_review_dataset import generate_review_dataset
from spanvouch.evaluation.review_labels import (
    ReviewGoldLabel,
    load_review_candidates,
    load_review_labels,
    load_review_manifest,
    validate_review_candidate_cohort,
    validate_review_dataset,
    validate_review_join,
    validate_source_run_ids,
)
from spanvouch.labs.supportlab.invariants import supportlab_rules
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine
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


def test_review_gold_labels_reject_impossible_verdict_finding_combinations() -> None:
    with pytest.raises(ValueError, match="candidate_id must not be empty"):
        ReviewGoldLabel(
            candidate_id="",
            expected_verdict=VerifierVerdict.VERIFIED,
            expected_finding_codes=(),
        )
    with pytest.raises(ValueError, match="must be unique"):
        ReviewGoldLabel(
            candidate_id="candidate",
            expected_verdict=VerifierVerdict.REVIEW_REQUIRED,
            expected_finding_codes=(FindingCode.CLAIM_NOT_GROUNDED,) * 2,
        )
    with pytest.raises(ValueError, match="must not expect findings"):
        ReviewGoldLabel(
            candidate_id="candidate",
            expected_verdict=VerifierVerdict.VERIFIED,
            expected_finding_codes=(FindingCode.CLAIM_NOT_GROUNDED,),
        )
    with pytest.raises(ValueError, match="must expect findings"):
        ReviewGoldLabel(
            candidate_id="candidate",
            expected_verdict=VerifierVerdict.REVIEW_REQUIRED,
            expected_finding_codes=(),
        )


@pytest.mark.parametrize(
    ("content", "message"),
    (
        (b"{}\r\n", "CRLF"),
        (b"{}", "end with LF"),
        (b"{}\n\n", "blank JSON line"),
    ),
)
def test_review_label_reader_rejects_noncanonical_line_framing(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = tmp_path / "labels.jsonl"
    path.write_bytes(content)
    with pytest.raises(ValueError, match=message):
        load_review_labels(path)


def test_review_loaders_reject_duplicate_labels_and_multiline_manifest(
    tmp_path: Path,
) -> None:
    label = (
        '{"candidate_id":"candidate","expected_finding_codes":[],'
        '"expected_verdict":"verified"}'
    )
    labels = tmp_path / "labels.jsonl"
    labels.write_text(f"{label}\n{label}\n", encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="duplicate review label candidate_id"):
        load_review_labels(labels)

    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n{}\n", encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="must be one JSON line"):
        load_review_manifest(manifest)


def test_review_join_rejects_duplicates_on_either_side() -> None:
    candidate = load_review_candidates(DATASET / "review-candidates-v1.jsonl")[0]
    label = load_review_labels(DATASET / "review-labels-v1.jsonl")[0]
    with pytest.raises(ValueError, match="duplicate review candidate_id"):
        validate_review_join((candidate, candidate), (label,))
    with pytest.raises(ValueError, match="duplicate review label candidate_id"):
        validate_review_join((candidate,), (label, label))


def test_candidate_cohort_rejects_source_and_identity_drift() -> None:
    candidates = load_review_candidates(DATASET / "review-candidates-v1.jsonl")
    source_ids = {trace.run_id: trace.trace_id for trace in _source_traces()}

    with pytest.raises(ValueError, match="source run IDs"):
        validate_review_candidate_cohort(candidates, dict(tuple(source_ids.items())[1:]))

    unknown = candidates[0].model_copy(update={"source_run_id": "unknown-run"})
    with pytest.raises(ValueError, match="unknown source run IDs"):
        validate_review_candidate_cohort((unknown, *candidates[1:]), source_ids)

    forged_id = candidates[0].model_copy(update={"candidate_id": "forged-id"})
    with pytest.raises(ValueError, match="identity does not match"):
        validate_review_candidate_cohort((forged_id, *candidates[1:]), source_ids)

    forged_report = candidates[0].model_copy(
        update={"report": candidates[0].report.model_copy(update={"run_id": "other-run"})}
    )
    with pytest.raises(ValueError, match="report run_id"):
        validate_review_candidate_cohort((forged_report, *candidates[1:]), source_ids)


def test_candidate_cohort_rejects_a_count_preserving_mutation_pair_swap() -> None:
    candidates = list(load_review_candidates(DATASET / "review-candidates-v1.jsonl"))
    first_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate.mutation_kind.value == "invalid_selector"
    )
    second_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate.mutation_kind.value == "diagnosis_conflict"
    )
    first = candidates[first_index]
    second = candidates[second_index]
    candidates[first_index] = first.model_copy(
        update={
            "mutation_kind": second.mutation_kind,
            "candidate_id": f"{first.source_run_id}--{second.mutation_kind.value}",
        }
    )
    candidates[second_index] = second.model_copy(
        update={
            "mutation_kind": first.mutation_kind,
            "candidate_id": f"{second.source_run_id}--{first.mutation_kind.value}",
        }
    )
    source_ids = {trace.run_id: trace.trace_id for trace in _source_traces()}
    with pytest.raises(ValueError, match="source/mutation pairs"):
        validate_review_candidate_cohort(tuple(candidates), source_ids)


def test_phase2_unsupported_contract_rejects_manifest_and_cardinality_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        review_labels_module,
        "build_diagnosis_manifest",
        lambda _path: object(),
    )
    with pytest.raises(ValueError, match="diagnosis labels do not match"):
        review_labels_module._expected_unsupported_source_run_ids(SOURCE_DATASET)

    manifest = review_labels_module.DiagnosisDatasetManifest.model_validate_json(
        (SOURCE_DATASET / "diagnosis-manifest-v1.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(
        review_labels_module, "build_diagnosis_manifest", lambda _path: manifest
    )
    labels = review_labels_module.load_diagnosis_labels(
        SOURCE_DATASET / "diagnosis-labels-v1.jsonl"
    )
    monkeypatch.setattr(review_labels_module, "load_diagnosis_labels", lambda _path: labels[:1])
    with pytest.raises(ValueError, match="must contain six run IDs"):
        review_labels_module._expected_unsupported_source_run_ids(SOURCE_DATASET)

    monkeypatch.setattr(
        review_labels_module,
        "_expected_unsupported_source_run_ids",
        lambda _path: frozenset({"wrong-run"}),
    )
    with pytest.raises(ValueError, match="do not match Phase 2"):
        review_labels_module._validate_phase2_unsupported_contract(SOURCE_DATASET)


def test_dataset_validation_rejects_label_hash_counts_and_generator_drift(
    tmp_path: Path,
) -> None:
    dataset = _copy_dataset(tmp_path)
    labels = dataset / "review-labels-v1.jsonl"
    labels.write_bytes(labels.read_bytes() + b" \n")
    with pytest.raises(ValueError, match="label file SHA-256"):
        validate_review_dataset(dataset, SOURCE_DATASET)

    dataset = _copy_dataset(tmp_path / "counts")
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(candidate_count=37, mutation_count=17)
    _write_jsonl(manifest_path, [manifest])
    with pytest.raises(ValueError, match="cohort counts"):
        validate_review_dataset(dataset, SOURCE_DATASET)

    dataset = _copy_dataset(tmp_path / "version")
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generator_version"] = "unknown-version"
    _write_jsonl(manifest_path, [manifest])
    with pytest.raises(ValueError, match="generator version"):
        validate_review_dataset(dataset, SOURCE_DATASET)


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
    import spanvouch.evaluation.review_labels as review_labels

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
    verifier = DeterministicVerifier(
        InvariantEngine(supportlab_rules()), policy_version="review-policy-v1"
    )

    await verifier.verify(
        VerificationInput(
            snapshot=snapshot,
            report=candidate.report,
            report_sha256=canonical_sha256(candidate.report),
        )
    )
