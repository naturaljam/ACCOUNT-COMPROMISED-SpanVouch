from collections import Counter
from pathlib import Path

import pytest

from spanvouch.contracts.trace import TraceIR
from spanvouch.diagnosis.models import DiagnosisStatus, EvidenceSelector
from spanvouch.evals.diagnosis_labels import (
    DiagnosisDatasetManifest,
    build_diagnosis_manifest,
    load_diagnosis_labels,
    validate_dataset_join,
)

DATASET_DIR = Path("evals/datasets/supportlab-v1")


def load_traces() -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in (DATASET_DIR / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )


def test_gold_sidecar_covers_all_frozen_traces_without_duplicates() -> None:
    labels = load_diagnosis_labels(DATASET_DIR / "diagnosis-labels-v1.jsonl")

    validate_dataset_join(load_traces(), labels)

    counts = Counter(label.expected_status for label in labels)
    assert len(labels) == 20
    assert counts[DiagnosisStatus.DIAGNOSED] == 10
    assert counts[DiagnosisStatus.NO_FAILURE] == 4
    assert counts[DiagnosisStatus.ABSTAINED] == 6


def test_gold_evidence_selectors_resolve_to_real_spans() -> None:
    traces = {trace.run_id: trace for trace in load_traces()}
    labels = load_diagnosis_labels(DATASET_DIR / "diagnosis-labels-v1.jsonl")

    validate_dataset_join(tuple(traces.values()), labels)

    for label in labels:
        span_ids = {span.span_id for span in traces[label.run_id].spans}
        assert set(label.acceptable_critical_span_ids) <= span_ids
        assert {selector.span_id for selector in label.acceptable_evidence} <= span_ids


def test_dataset_join_rejects_run_id_set_mismatch() -> None:
    traces = load_traces()
    labels = load_diagnosis_labels(DATASET_DIR / "diagnosis-labels-v1.jsonl")

    with pytest.raises(ValueError, match="diagnosis label join mismatch"):
        validate_dataset_join(traces[:-1], labels)


def test_dataset_join_rejects_unknown_critical_span_and_evidence_selector() -> None:
    traces = load_traces()
    labels = load_diagnosis_labels(DATASET_DIR / "diagnosis-labels-v1.jsonl")
    diagnosed_index = next(
        index
        for index, label in enumerate(labels)
        if label.expected_status is DiagnosisStatus.DIAGNOSED
    )
    diagnosed = labels[diagnosed_index]

    unknown_span_labels = list(labels)
    unknown_span_labels[diagnosed_index] = diagnosed.model_copy(
        update={"acceptable_critical_span_ids": ("missing-span",)}
    )
    with pytest.raises(ValueError, match="unknown critical span"):
        validate_dataset_join(traces, tuple(unknown_span_labels))

    unknown_selector_labels = list(labels)
    unknown_selector_labels[diagnosed_index] = diagnosed.model_copy(
        update={
            "acceptable_evidence": (
                EvidenceSelector(span_id="missing-span", field_path="name"),
            )
        }
    )
    with pytest.raises(ValueError, match="unknown evidence selector"):
        validate_dataset_join(traces, tuple(unknown_selector_labels))


def test_loader_rejects_duplicate_run_ids(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.jsonl"
    record = (
        '{"run_id":"same","failure_type":"no_failure",'
        '"expected_status":"no_failure","acceptable_critical_span_ids":[],'
        '"acceptable_evidence":[],"rationale":"clean"}'
    )
    duplicate.write_text(f"{record}\n{record}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate diagnosis label run_id"):
        load_diagnosis_labels(duplicate)


def test_manifest_matches_exact_sidecar_bytes() -> None:
    manifest = build_diagnosis_manifest(DATASET_DIR / "diagnosis-labels-v1.jsonl")
    committed = DiagnosisDatasetManifest.model_validate_json(
        (DATASET_DIR / "diagnosis-manifest-v1.json").read_text(encoding="utf-8")
    )

    assert committed == manifest
    assert manifest.name == "supportlab-v1-diagnosis-labels"
    assert manifest.schema_version == "1.0"
    assert manifest.label_count == 20
    assert len(manifest.labels_sha256) == 64
