import hashlib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spanvouch.diagnosis.models import DiagnosisStatus
from spanvouch.evals.diagnosis_labels import (
    DiagnosisDatasetManifest,
    build_diagnosis_manifest,
    load_diagnosis_labels,
)
from spanvouch.evals.generate_review_dataset import (
    CANDIDATES_FILENAME,
    EXPECTED_MUTATION_COUNTS,
    EXPECTED_MUTATION_KIND_BY_SOURCE,
    EXPECTED_SOURCE_RUN_IDS,
    EXPECTED_UNSUPPORTED_SOURCE_RUN_IDS,
    GENERATOR_VERSION,
    LABELS_FILENAME,
    MANIFEST_FILENAME,
    MutationKind,
    ReviewCandidate,
    ReviewDatasetManifest,
    validate_source_dataset,
)
from spanvouch.review.models import FindingCode, VerifierVerdict
from spanvouch.trace_ir.models import TraceIR


class ReviewGoldLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    expected_verdict: VerifierVerdict
    expected_finding_codes: tuple[FindingCode, ...]

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        if not value:
            raise ValueError("candidate_id must not be empty")
        return value

    @model_validator(mode="after")
    def validate_finding_codes(self) -> Self:
        if len(self.expected_finding_codes) != len(set(self.expected_finding_codes)):
            raise ValueError("expected finding codes must be unique")
        if self.expected_verdict is VerifierVerdict.VERIFIED and self.expected_finding_codes:
            raise ValueError("verified labels must not expect findings")
        if self.expected_verdict is not VerifierVerdict.VERIFIED and not (
            self.expected_finding_codes
        ):
            raise ValueError("blocking labels must expect findings")
        return self


def _read_lf(path: Path) -> tuple[str, ...]:
    content = path.read_bytes()
    if b"\r" in content:
        raise ValueError(f"CRLF output is forbidden: {path.name}")
    if not content.endswith(b"\n"):
        raise ValueError(f"file must end with LF: {path.name}")
    lines = content.decode("utf-8").splitlines()
    if not lines or any(not line for line in lines):
        raise ValueError(f"blank JSON line is forbidden: {path.name}")
    return tuple(lines)


def load_review_candidates(path: Path) -> tuple[ReviewCandidate, ...]:
    candidates = tuple(ReviewCandidate.model_validate_json(line) for line in _read_lf(path))
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("duplicate review candidate_id")
    return candidates


def load_review_labels(path: Path) -> tuple[ReviewGoldLabel, ...]:
    labels = tuple(ReviewGoldLabel.model_validate_json(line) for line in _read_lf(path))
    candidate_ids = tuple(label.candidate_id for label in labels)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("duplicate review label candidate_id")
    return labels


def load_review_manifest(path: Path) -> ReviewDatasetManifest:
    lines = _read_lf(path)
    if len(lines) != 1:
        raise ValueError("review manifest must be one JSON line")
    return ReviewDatasetManifest.model_validate_json(lines[0])


def validate_review_join(
    candidates: tuple[ReviewCandidate, ...], labels: tuple[ReviewGoldLabel, ...]
) -> None:
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    label_ids = tuple(label.candidate_id for label in labels)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("duplicate review candidate_id")
    if len(label_ids) != len(set(label_ids)):
        raise ValueError("duplicate review label candidate_id")
    if len(candidate_ids) != len(label_ids) or Counter(candidate_ids) != Counter(label_ids):
        missing = sorted(set(candidate_ids) - set(label_ids))
        extra = sorted(set(label_ids) - set(candidate_ids))
        raise ValueError(f"review label join mismatch: missing={missing}, extra={extra}")


def validate_source_run_ids(
    candidates: tuple[ReviewCandidate, ...], traces: tuple[TraceIR, ...]
) -> None:
    known = {trace.run_id for trace in traces}
    unknown = sorted({candidate.source_run_id for candidate in candidates} - known)
    if unknown:
        raise ValueError(f"unknown source run IDs: {', '.join(unknown)}")


def validate_review_cohort(
    candidates: tuple[ReviewCandidate, ...],
    labels: tuple[ReviewGoldLabel, ...],
    source_trace_ids: Mapping[str, str],
) -> None:
    validate_review_join(candidates, labels)
    if frozenset(source_trace_ids) != EXPECTED_SOURCE_RUN_IDS:
        raise ValueError("Phase 2 source run IDs do not match frozen 20-run contract")
    unknown = sorted(
        {candidate.source_run_id for candidate in candidates} - set(source_trace_ids)
    )
    if unknown:
        raise ValueError(f"unknown source run IDs: {', '.join(unknown)}")
    actual = Counter(candidate.mutation_kind for candidate in candidates)
    if actual != Counter(EXPECTED_MUTATION_COUNTS):
        raise ValueError(
            "review mutation family counts do not match exact 20/2/2/2/2/2/6 contract"
        )
    unsupported = frozenset(
        candidate.source_run_id
        for candidate in candidates
        if candidate.mutation_kind is MutationKind.UNSUPPORTED_SCOPE
    )
    if unsupported != EXPECTED_UNSUPPORTED_SOURCE_RUN_IDS:
        raise ValueError("review unsupported source run IDs do not match frozen contract")
    for candidate in candidates:
        expected_id = f"{candidate.source_run_id}--{candidate.mutation_kind.value}"
        if candidate.candidate_id != expected_id:
            raise ValueError("review candidate identity does not match source/mutation pair")
        if candidate.report.run_id != candidate.source_run_id:
            raise ValueError("review candidate report run_id does not match source_run_id")
        if candidate.report.trace_id != source_trace_ids[candidate.source_run_id]:
            raise ValueError("review candidate report trace_id does not match source trace_id")
    unmodified = tuple(
        candidate.source_run_id
        for candidate in candidates
        if candidate.mutation_kind is MutationKind.UNMODIFIED
    )
    if Counter(unmodified) != Counter({run_id: 1 for run_id in EXPECTED_SOURCE_RUN_IDS}):
        raise ValueError("review cohort requires one unmodified candidate per source run")
    pairs = tuple(
        (candidate.source_run_id, candidate.mutation_kind) for candidate in candidates
    )
    if len(pairs) != len(set(pairs)):
        raise ValueError("duplicate review source_run_id/mutation_kind pair")
    expected_pairs = {
        (run_id, MutationKind.UNMODIFIED) for run_id in EXPECTED_SOURCE_RUN_IDS
    } | set(EXPECTED_MUTATION_KIND_BY_SOURCE.items())
    if set(pairs) != expected_pairs:
        raise ValueError("review candidate source/mutation pairs do not match frozen contract")


def _expected_unsupported_source_run_ids(source_dataset: Path) -> frozenset[str]:
    labels_path = source_dataset / "diagnosis-labels-v1.jsonl"
    manifest = DiagnosisDatasetManifest.model_validate_json(
        (source_dataset / "diagnosis-manifest-v1.json").read_text(encoding="utf-8")
    )
    if build_diagnosis_manifest(labels_path) != manifest:
        raise ValueError("Phase 2 diagnosis labels do not match diagnosis manifest")
    labels = load_diagnosis_labels(labels_path)
    expected = frozenset(
        label.run_id
        for label in labels
        if label.expected_status is DiagnosisStatus.ABSTAINED
    )
    if len(expected) != 6:
        raise ValueError("Phase 2 unsupported source contract must contain six run IDs")
    return expected


def _validate_phase2_unsupported_contract(source_dataset: Path) -> None:
    expected = _expected_unsupported_source_run_ids(source_dataset)
    if expected != EXPECTED_UNSUPPORTED_SOURCE_RUN_IDS:
        raise ValueError(
            "frozen unsupported source run IDs do not match Phase 2 label contract"
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_source_traces(source_dataset: Path) -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in _read_lf(source_dataset / "traces.jsonl")
    )


def validate_review_dataset(
    dataset: Path, source_dataset: Path
) -> tuple[
    tuple[ReviewCandidate, ...],
    tuple[ReviewGoldLabel, ...],
    ReviewDatasetManifest,
]:
    candidates_path = dataset / CANDIDATES_FILENAME
    labels_path = dataset / LABELS_FILENAME
    manifest_path = dataset / MANIFEST_FILENAME
    for path in (candidates_path, labels_path, manifest_path):
        content = path.read_bytes()
        if b"\r" in content:
            raise ValueError(f"CRLF output is forbidden: {path.name}")
        if not content.endswith(b"\n"):
            raise ValueError(f"file must end with LF: {path.name}")

    manifest = load_review_manifest(manifest_path)
    if _sha256(candidates_path) != manifest.candidates_sha256:
        raise ValueError("candidate file SHA-256 does not match manifest")
    if _sha256(labels_path) != manifest.labels_sha256:
        raise ValueError("label file SHA-256 does not match manifest")
    if _sha256(source_dataset / "manifest.json") != manifest.source_manifest_sha256:
        raise ValueError("Phase 2 source manifest SHA-256 does not match review manifest")
    validate_source_dataset(source_dataset)

    candidates = load_review_candidates(candidates_path)
    labels = load_review_labels(labels_path)
    traces = _load_source_traces(source_dataset)
    source_run_ids = tuple(trace.run_id for trace in traces)
    if len(source_run_ids) != len(set(source_run_ids)):
        raise ValueError("duplicate Phase 2 source run_id")
    _validate_phase2_unsupported_contract(source_dataset)
    validate_review_cohort(
        candidates, labels, {trace.run_id: trace.trace_id for trace in traces}
    )

    valid_count = sum(
        candidate.mutation_kind is MutationKind.UNMODIFIED for candidate in candidates
    )
    mutation_count = len(candidates) - valid_count
    if (
        len(candidates),
        valid_count,
        mutation_count,
    ) != (
        manifest.candidate_count,
        manifest.valid_count,
        manifest.mutation_count,
    ):
        raise ValueError("review manifest cohort counts do not match candidate file")
    if (manifest.candidate_count, manifest.valid_count, manifest.mutation_count) != (
        36,
        20,
        16,
    ):
        raise ValueError("review cohort must contain exactly 36/20/16 records")
    if manifest.generator_version != GENERATOR_VERSION:
        raise ValueError("unknown review dataset generator version")
    return candidates, labels, manifest
