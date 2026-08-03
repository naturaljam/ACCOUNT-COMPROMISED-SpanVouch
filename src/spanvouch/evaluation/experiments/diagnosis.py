"""Offline-verifiable freezing of generated diagnosis candidates."""

from __future__ import annotations

import os
import re
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.diagnosis import (
    DiagnosisExecution,
    DiagnosisReport,
    EvidenceSelector,
    ProviderUsage,
)
from spanvouch.contracts.trace import DiagnosticContext
from spanvouch.contracts.versioning import (
    SHA256_PATTERN,
    canonical_bytes,
    canonical_sha256,
)
from spanvouch.diagnosis.llm_diagnoser import LlmDiagnoser
from spanvouch.diagnosis.prompting import DiagnosisPromptBuilder
from spanvouch.diagnosis.protocols import (
    ChatMessage,
    GenerationConfig,
    ModelProvider,
)
from spanvouch.evaluation.artifacts import (
    capture_owned_directory_identity,
    create_owned_staging_directory,
    delete_owned_staging_directory,
    publish_directory_no_replace,
    quarantine_owned_staging_directory,
    read_verified_directory_tree,
    require_safe_artifact_content,
)
from spanvouch.evaluation.corpus import CorpusCell, CorpusEntry, TraceReplayRepository
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog


class DiagnosisExperimentFailureCode(StrEnum):
    INPUT_INTEGRITY_FAILURE = "input_integrity_failure"
    PROVIDER_FAILURE = "provider_failure"
    CONTRACT_FAILURE = "contract_failure"
    UNSAFE_ARTIFACT_CONTENT = "unsafe_artifact_content"


class DiagnosisExperimentFailure(RuntimeError):
    """A stable, sanitized experiment failure that cannot be mistaken for a diagnosis."""

    def __init__(self, code: DiagnosisExperimentFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code.value


class FrozenEvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    selector: str = Field(min_length=1)
    value_sha256: str = Field(pattern=SHA256_PATTERN)


def _freeze_evidence(catalog: EvidenceCatalog) -> tuple[FrozenEvidenceItem, ...]:
    items: list[FrozenEvidenceItem] = []
    for canonical in catalog.selectors:
        span_id, separator, field_path = canonical.partition("::")
        if not separator or not span_id or not field_path:
            raise ValueError("evidence catalog contains invalid selector")
        resolved = catalog.resolve(
            EvidenceSelector(span_id=span_id, field_path=field_path),
            description="frozen diagnosis input",
        )
        items.append(
            FrozenEvidenceItem(
                selector=canonical,
                value_sha256=resolved.value_sha256,
            )
        )
    return tuple(items)


class FrozenDiagnosisCandidate(BaseModel):
    """Sanitized, reconstructable diagnosis output bound to one corpus cell."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: str = Field(default="spanvouch.frozen-diagnosis-candidate")
    schema_version: str = Field(default="1.0")
    cell: CorpusCell
    corpus_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    record_sha256: str = Field(pattern=SHA256_PATTERN)
    trace_sha256: str = Field(pattern=SHA256_PATTERN)
    diagnostic_context: DiagnosticContext
    diagnostic_context_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_catalog: tuple[FrozenEvidenceItem, ...]
    evidence_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    report: DiagnosisReport
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    generation: GenerationConfig
    generation_sha256: str = Field(pattern=SHA256_PATTERN)
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    generator_provider: str = Field(min_length=1)
    generator_model: str = Field(min_length=1)
    usage: ProviderUsage
    request_id_sha256: str = Field(pattern=SHA256_PATTERN)
    shared_verifier_messages_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        if canonical_sha256(self.diagnostic_context) != self.diagnostic_context_sha256:
            raise ValueError("diagnostic_context_sha256 does not match context")
        rebuilt_catalog = EvidenceCatalog.from_context(self.diagnostic_context)
        rebuilt_evidence = _freeze_evidence(rebuilt_catalog)
        if self.evidence_catalog != rebuilt_evidence:
            raise ValueError("frozen evidence catalog does not match context")
        evidence_payload = cast(
            JsonValue,
            [item.model_dump(mode="json") for item in self.evidence_catalog],
        )
        if canonical_sha256(evidence_payload) != self.evidence_catalog_sha256:
            raise ValueError("evidence_catalog_sha256 does not match catalog")
        if canonical_sha256(self.report) != self.report_sha256:
            raise ValueError("report_sha256 does not match report")
        if canonical_sha256(self.generation) != self.generation_sha256:
            raise ValueError("generation_sha256 does not match generation")
        prepared = DiagnosisPromptBuilder().prepare(
            self.diagnostic_context, rebuilt_catalog, self.generation
        )
        if (
            prepared.prompt_version != self.prompt_version
            or prepared.prompt_sha256 != self.prompt_sha256
        ):
            raise ValueError("frozen prompt binding does not match reconstructed prompt")
        if self.report.trace_id != self.diagnostic_context.trace_id or (
            self.report.run_id != self.diagnostic_context.run_id
        ):
            raise ValueError("diagnosis report does not match diagnostic context")
        provenance = self.report.provenance
        if (
            provenance.prompt_version != self.prompt_version
            or provenance.prompt_sha256 != self.prompt_sha256
            or provenance.provider != self.generator_provider
            or provenance.model != self.generator_model
            or self.generator_model != self.generation.model
        ):
            raise ValueError("diagnosis provenance does not match frozen generation")
        if self.usage.request_id is not None or self.report.usage != self.usage:
            raise ValueError("frozen usage must omit raw request ID and match report")
        return self


class DiagnosisCandidateRepository:
    """No-replace content-addressed candidates, one immutable value per corpus cell.

    It inherits the Task 8 threat model: static replacement, traversal, reparse
    traversal, tampering, and overwrite are defended; a same-account active swap in
    the final validation-to-rename interval remains out of scope.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @staticmethod
    def _cell_identity(cell: CorpusCell) -> str:
        validated = CorpusCell.model_validate(cell.model_dump(mode="python"))
        # Keep Windows publication paths below the legacy MAX_PATH boundary. Any
        # truncated-hash collision fails closed because load validates the full cell.
        return canonical_sha256(validated)[:16]

    def _destination(self, cell: CorpusCell) -> Path:
        return self._root / "cells" / self._cell_identity(cell)

    def exists(self, cell: CorpusCell) -> bool:
        return os.path.lexists(self._destination(cell))

    def verify_existing(
        self,
        *,
        entries: tuple[CorpusEntry, ...],
        expected_corpus_manifest_sha256: str,
        expected_generation: GenerationConfig,
        expected_provider: str,
        expected_model: str,
    ) -> dict[CorpusCell, FrozenDiagnosisCandidate]:
        """Verify every existing cell from one pinned repository snapshot."""
        if not os.path.lexists(self._root):
            return {}
        if re.fullmatch(SHA256_PATTERN, expected_corpus_manifest_sha256) is None:
            raise ValueError("expected_corpus_manifest_sha256 must be a SHA-256 digest")
        generation = GenerationConfig.model_validate(expected_generation.model_dump(mode="python"))
        if not expected_provider or not expected_model:
            raise ValueError("expected provider and model must be non-empty")
        if generation.model != expected_model:
            raise ValueError("expected generation model does not match expected model")

        validated_entries = tuple(
            CorpusEntry.model_validate(entry.model_dump(mode="python")) for entry in entries
        )
        entries_by_identity = {
            self._cell_identity(entry.cell): entry for entry in validated_entries
        }
        if len(entries_by_identity) != len(validated_entries):
            raise ValueError("candidate repository cell identity collision")

        snapshot = read_verified_directory_tree(self._root)
        if "cells" not in snapshot.directories:
            raise ValueError("candidate repository has unexpected layout")

        candidates: dict[CorpusCell, FrozenDiagnosisCandidate] = {}
        populated_directories: set[str] = set()
        for relative, content in snapshot.files.items():
            if relative == "ineligible.json":
                continue
            parts = relative.split("/")
            if len(parts) != 3 or parts[0] != "cells":
                raise ValueError("candidate repository contains unknown layout")
            cell_identity, filename = parts[1:]
            try:
                entry = entries_by_identity[cell_identity]
            except KeyError as error:
                raise ValueError("candidate repository contains unknown cell") from error
            if (
                not filename.endswith(".json")
                or re.fullmatch(SHA256_PATTERN, filename.removesuffix(".json")) is None
            ):
                raise ValueError("candidate repository has invalid content address")
            digest = sha256(content).hexdigest()
            if filename != f"{digest}.json":
                raise ValueError("candidate content address SHA-256 mismatch")
            candidate = FrozenDiagnosisCandidate.model_validate_json(content)
            if canonical_bytes(candidate) != content:
                raise ValueError("candidate payload is not canonical JSON")
            if candidate.cell != entry.cell:
                raise ValueError("candidate cell does not match corpus entry")
            if candidate.corpus_manifest_sha256 != expected_corpus_manifest_sha256:
                raise ValueError("candidate corpus manifest SHA-256 mismatch")
            if candidate.record_sha256 != entry.record_sha256:
                raise ValueError("candidate record SHA-256 mismatch")
            if candidate.trace_sha256 != entry.trace_sha256:
                raise ValueError("candidate trace SHA-256 mismatch")
            if candidate.generation != generation:
                raise ValueError("candidate generation does not match expected generation")
            if candidate.generator_provider != expected_provider:
                raise ValueError("candidate provider does not match expected provider")
            if candidate.generator_model != expected_model:
                raise ValueError("candidate model does not match expected model")
            if entry.cell in candidates:
                raise ValueError("candidate repository contains duplicate cell")
            candidates[entry.cell] = candidate
            populated_directories.add(f"cells/{cell_identity}")

        expected_directories = frozenset({"cells", *populated_directories})
        if snapshot.directories != expected_directories:
            raise ValueError("candidate repository contains unknown or empty layout")
        if not candidates:
            raise ValueError("candidate repository contains no candidates")
        return candidates

    def publish(self, candidate: FrozenDiagnosisCandidate) -> str:
        validated = FrozenDiagnosisCandidate.model_validate(candidate.model_dump(mode="python"))
        destination = self._destination(validated.cell)
        destination.parent.mkdir(parents=True, exist_ok=True)
        candidate_bytes = canonical_bytes(validated)
        candidate_sha256 = sha256(candidate_bytes).hexdigest()
        try:
            staging, root_identity = create_owned_staging_directory(destination)
        except FileExistsError as error:
            raise FileExistsError("candidate already exists for corpus cell") from error
        identity = None
        try:
            payload = staging / f"{candidate_sha256}.json"
            with payload.open("xb") as stream:
                stream.write(candidate_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            if payload.read_bytes() != candidate_bytes:
                raise ValueError("candidate write verification failed")
            identity = capture_owned_directory_identity(staging)
            publish_directory_no_replace(staging, destination)
            if capture_owned_directory_identity(destination) != identity:
                raise RuntimeError("published candidate ownership verification failed")
        except FileExistsError as error:
            if os.path.lexists(staging):
                if identity is None:
                    quarantine_owned_staging_directory(staging, root_identity)
                else:
                    delete_owned_staging_directory(staging, identity)
            raise FileExistsError("candidate already exists for corpus cell") from error
        except Exception:
            if os.path.lexists(staging):
                if identity is None:
                    quarantine_owned_staging_directory(staging, root_identity)
                else:
                    delete_owned_staging_directory(staging, identity)
            raise
        return candidate_sha256

    def load(
        self,
        cell: CorpusCell,
        *,
        expected_candidate_sha256: str,
        expected_corpus_manifest_sha256: str,
    ) -> FrozenDiagnosisCandidate:
        if re.fullmatch(SHA256_PATTERN, expected_candidate_sha256) is None:
            raise ValueError("expected_candidate_sha256 must be a SHA-256 digest")
        if re.fullmatch(SHA256_PATTERN, expected_corpus_manifest_sha256) is None:
            raise ValueError("expected_corpus_manifest_sha256 must be a SHA-256 digest")
        destination = self._destination(cell)
        snapshot = read_verified_directory_tree(destination)
        if snapshot.directories or len(snapshot.files) != 1:
            raise ValueError("candidate repository cell has unexpected layout")
        relative, content = next(iter(snapshot.files.items()))
        digest = sha256(content).hexdigest()
        if digest != expected_candidate_sha256:
            raise ValueError("trusted candidate SHA-256 mismatch")
        if relative != f"{digest}.json":
            raise ValueError("candidate content address does not match payload")
        candidate = FrozenDiagnosisCandidate.model_validate_json(content)
        if canonical_bytes(candidate) != content:
            raise ValueError("candidate payload is not canonical JSON")
        if candidate.cell != CorpusCell.model_validate(cell.model_dump(mode="python")):
            raise ValueError("candidate cell does not match repository identity")
        if candidate.corpus_manifest_sha256 != expected_corpus_manifest_sha256:
            raise ValueError("trusted corpus manifest SHA-256 mismatch")
        return candidate


def reconstruct_shared_verifier_messages(
    candidate: FrozenDiagnosisCandidate,
    verifier_instruction: str,
) -> tuple[ChatMessage, ...]:
    """Rebuild B2 inputs and prove they equal the audited pre-call message hash."""
    validated = FrozenDiagnosisCandidate.model_validate(candidate.model_dump(mode="python"))
    catalog = EvidenceCatalog.from_context(validated.diagnostic_context)
    builder = DiagnosisPromptBuilder()
    prepared = builder.prepare(validated.diagnostic_context, catalog, validated.generation)
    messages = builder.shared_verifier_messages(prepared, validated.report, verifier_instruction)
    messages_payload = cast(
        JsonValue,
        [message.model_dump(mode="json") for message in messages],
    )
    if canonical_sha256(messages_payload) != validated.shared_verifier_messages_sha256:
        raise ValueError("shared verifier pre-call audit hash mismatch")
    return messages


async def generate_and_freeze_diagnosis(
    *,
    corpus: TraceReplayRepository,
    cell: CorpusCell,
    expected_corpus_manifest_sha256: str,
    expected_record_sha256: str,
    expected_trace_sha256: str,
    provider: ModelProvider,
    generation: GenerationConfig,
    repository: DiagnosisCandidateRepository,
    verifier_instruction: str,
) -> FrozenDiagnosisCandidate:
    """Make exactly one provider call after verifying every frozen corpus input."""
    if repository.exists(cell):
        raise FileExistsError("candidate already exists for corpus cell")
    builder = DiagnosisPromptBuilder()
    try:
        builder.validate_verifier_instruction(verifier_instruction)
    except (TypeError, ValueError) as error:
        raise DiagnosisExperimentFailure(
            DiagnosisExperimentFailureCode.CONTRACT_FAILURE,
            "invalid verifier instruction",
        ) from error
    try:
        manifest = corpus.verify()
        if corpus.manifest_sha256 != expected_corpus_manifest_sha256:
            raise ValueError("corpus manifest hash mismatch")
        record = corpus.load(cell)
        entry = CorpusEntry.from_record(record)
        if entry not in manifest.entries:
            raise ValueError("corpus entry is not bound to verified manifest")
        if entry.record_sha256 != expected_record_sha256:
            raise ValueError("record hash mismatch")
        if entry.trace_sha256 != expected_trace_sha256:
            raise ValueError("trace hash mismatch")
    except (KeyError, ValueError) as error:
        raise DiagnosisExperimentFailure(
            DiagnosisExperimentFailureCode.INPUT_INTEGRITY_FAILURE,
            str(error),
        ) from error

    context = TraceProjector().project(record.trace)
    context = DiagnosticContext.model_validate(context.model_dump(mode="python"))
    evidence = EvidenceCatalog.from_context(context)
    validated_generation = GenerationConfig.model_validate(generation.model_dump(mode="python"))
    prepared = builder.prepare(context, evidence, validated_generation)
    diagnoser = LlmDiagnoser(
        provider,
        generation=validated_generation,
        prompt_version=prepared.prompt_version,
    )
    try:
        execution = await diagnoser.diagnose_prepared(prepared, context, evidence)
    except Exception as error:
        raise DiagnosisExperimentFailure(
            DiagnosisExperimentFailureCode.PROVIDER_FAILURE,
            "provider call failed",
        ) from error
    if execution.provenance.model != validated_generation.model or (
        execution.provenance.provider != "deepseek"
    ):
        raise DiagnosisExperimentFailure(
            DiagnosisExperimentFailureCode.CONTRACT_FAILURE,
            "provider response provenance mismatch",
        )
    raw_usage = execution.usage
    if raw_usage is None or not raw_usage.request_id:
        raise DiagnosisExperimentFailure(
            DiagnosisExperimentFailureCode.CONTRACT_FAILURE,
            "provider response omitted required usage identity",
        )
    sanitized_usage = ProviderUsage(
        **raw_usage.model_dump(mode="python", exclude={"request_id"}),
        request_id=None,
    )
    sanitized_execution = DiagnosisExecution(
        decision=execution.decision,
        provenance=execution.provenance,
        usage=sanitized_usage,
    )
    report = DiagnosisReport.from_execution(
        trace_id=context.trace_id,
        run_id=context.run_id,
        diagnoser="deepseek",
        execution=sanitized_execution,
    )
    try:
        for claim in report.causal_chain:
            require_safe_artifact_content("model_derived_text", claim.statement)
        for item in report.evidence:
            require_safe_artifact_content("model_derived_text", item.description)
    except ValueError as error:
        raise DiagnosisExperimentFailure(
            DiagnosisExperimentFailureCode.UNSAFE_ARTIFACT_CONTENT,
            "model-derived diagnosis content is unsafe",
        ) from error
    shared_messages = builder.shared_verifier_messages(prepared, report, verifier_instruction)
    frozen_evidence = _freeze_evidence(evidence)
    candidate = FrozenDiagnosisCandidate(
        cell=entry.cell,
        corpus_manifest_sha256=expected_corpus_manifest_sha256,
        record_sha256=entry.record_sha256,
        trace_sha256=entry.trace_sha256,
        diagnostic_context=context,
        diagnostic_context_sha256=canonical_sha256(context),
        evidence_catalog=frozen_evidence,
        evidence_catalog_sha256=canonical_sha256(
            cast(
                JsonValue,
                [item.model_dump(mode="json") for item in frozen_evidence],
            )
        ),
        report=report,
        report_sha256=canonical_sha256(report),
        generation=validated_generation,
        generation_sha256=canonical_sha256(validated_generation),
        prompt_version=prepared.prompt_version,
        prompt_sha256=prepared.prompt_sha256,
        generator_provider="deepseek",
        generator_model=validated_generation.model,
        usage=sanitized_usage,
        request_id_sha256=sha256(raw_usage.request_id.encode("utf-8")).hexdigest(),
        shared_verifier_messages_sha256=canonical_sha256(
            cast(
                JsonValue,
                [message.model_dump(mode="json") for message in shared_messages],
            )
        ),
    )
    repository.publish(candidate)
    return candidate
