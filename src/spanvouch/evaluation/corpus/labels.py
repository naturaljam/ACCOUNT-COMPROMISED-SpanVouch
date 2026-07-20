"""Separate-process sealed label generation for a verified Phase 5 corpus."""

from __future__ import annotations

import os
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
)
from spanvouch.evaluation.corpus.gold_specs import GOLD_SPECS
from spanvouch.evaluation.corpus.models import CorpusCell
from spanvouch.evaluation.corpus.repository import TraceReplayRepository

_MANIFEST = "manifest.json"


class GoldLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_identity: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    expected_failure_type: str = Field(min_length=1)
    causal_chain_expectations: tuple[str, ...]
    evidence_expectations: tuple[str, ...]
    control: bool
    split: Literal["pilot", "train", "validation", "test"]
    record_sha256: str = Field(pattern=SHA256_PATTERN)
    trace_sha256: str = Field(pattern=SHA256_PATTERN)


class GoldLabelManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.phase5-gold-labels"] = (
        "spanvouch.phase5-gold-labels"
    )
    schema_version: Literal["1.0"] = "1.0"
    corpus_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    labels: tuple[GoldLabel, ...] = Field(min_length=1)
    labels_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_labels(self) -> Self:
        identities = tuple(label.cell_identity for label in self.labels)
        if len(set(identities)) != len(identities):
            raise ValueError("gold label cell identities must be unique")
        payload = cast(
            JsonValue,
            [label.model_dump(mode="json") for label in self.labels],
        )
        if self.labels_sha256 != canonical_sha256(payload):
            raise ValueError("labels_sha256 does not match gold labels")
        return self


@dataclass(frozen=True)
class GoldLabelGenerationResult:
    output_dir: Path
    manifest: GoldLabelManifest
    manifest_sha256: str


def generate_phase5_labels(
    *, corpus_dir: Path, output_dir: Path | None = None
) -> GoldLabelGenerationResult:
    """Join evaluator-only gold specs to verified corpus hashes and seal them."""
    corpus_root = corpus_dir.resolve(strict=True)
    destination = output_dir or corpus_dir.with_name(
        f"{corpus_dir.name}-labels-sealed"
    )
    destination_resolved = destination.resolve(strict=False)
    if destination_resolved == corpus_root or destination_resolved.is_relative_to(
        corpus_root
    ):
        raise ValueError("sealed labels must be written outside the corpus root")
    if os.path.lexists(destination):
        raise FileExistsError("sealed label destination must not already exist")

    repository = TraceReplayRepository(corpus_root)
    corpus_manifest = repository.verify()
    labels = tuple(
        _label_from_entry(
            entry.cell,
            entry.record_sha256,
            entry.trace_sha256,
            corpus_manifest.metadata.mode,
        )
        for entry in corpus_manifest.entries
    )
    manifest = GoldLabelManifest(
        corpus_manifest_sha256=repository.manifest_sha256,
        labels=labels,
        labels_sha256=canonical_sha256(
            cast(
                JsonValue,
                [label.model_dump(mode="json") for label in labels],
            )
        ),
    )
    content = canonical_bytes(manifest)
    staging, root_identity = create_owned_staging_directory(destination)
    identity = None
    try:
        manifest_path = staging / _MANIFEST
        with manifest_path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        identity = capture_owned_directory_identity(staging)
        publish_directory_no_replace(staging, destination)
        if capture_owned_directory_identity(destination) != identity:
            raise RuntimeError("published sealed-label ownership verification failed")
    except Exception:
        if os.path.lexists(staging):
            if identity is not None:
                delete_owned_staging_directory(staging, identity)
            else:
                quarantine_owned_staging_directory(staging, root_identity)
        raise
    return GoldLabelGenerationResult(
        output_dir=destination,
        manifest=manifest,
        manifest_sha256=sha256(content).hexdigest(),
    )


def _label_from_entry(
    cell: CorpusCell,
    record_sha256: str,
    trace_sha256: str,
    mode: Literal["pilot", "formal"],
) -> GoldLabel:
    try:
        spec = GOLD_SPECS[cell.scenario_id]
    except KeyError as error:
        raise ValueError(f"missing sealed gold spec: {cell.scenario_id}") from error
    return GoldLabel(
        cell_identity=_cell_identity(cell),
        scenario_id=cell.scenario_id,
        expected_failure_type=spec.expected_failure_type,
        causal_chain_expectations=spec.causal_chain_expectations,
        evidence_expectations=spec.evidence_expectations,
        control=spec.control,
        split="pilot" if mode == "pilot" else spec.split,
        record_sha256=record_sha256,
        trace_sha256=trace_sha256,
    )


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
