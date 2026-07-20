from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Self, TypeVar, cast

from pydantic import BaseModel, JsonValue

from spanvouch.contracts.trace import TraceIR
from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_bytes, canonical_sha256
from spanvouch.evaluation.artifacts import (
    capture_owned_directory_identity,
    create_owned_staging_directory,
    delete_owned_staging_directory,
    publish_directory_no_replace,
    quarantine_owned_staging_directory,
    read_verified_directory_tree,
    require_safe_artifact_content,
)
from spanvouch.evaluation.corpus.models import (
    CorpusCell,
    CorpusEntry,
    CorpusManifest,
    CorpusManifestMetadata,
    CorpusParityEntry,
    CorpusParityPayload,
)
from spanvouch.labs.runtime import ExecutionRecord, ParityResult

_MANIFEST = "manifest.json"
_DIRECTORIES = frozenset(
    {
        "parity",
        "parity/sha256",
        "records",
        "records/sha256",
        "traces",
        "traces/sha256",
    }
)
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class TraceReplayRepository:
    """Verified read/replay access to an immutable content-addressed trace corpus.

    Security boundary: the repository defends path traversal, symlink/reparse
    traversal, static source replacement before and after publication, byte/hash/
    model tampering, and destination overwrite.  Publication uses OS no-replace
    semantics and verifies the published tree against the captured source identity.
    A same-account active source swap in the final validation-to-OS-rename interval
    is out of scope because the supported OS APIs do not condition rename on inode.
    """

    def __init__(
        self,
        root: Path,
        *,
        expected_manifest_sha256: str | None = None,
    ) -> None:
        if expected_manifest_sha256 is not None:
            import re

            if re.fullmatch(SHA256_PATTERN, expected_manifest_sha256) is None:
                raise ValueError("expected_manifest_sha256 must be a SHA-256 digest")
        self._root = root
        self._expected_manifest_sha256 = expected_manifest_sha256

    @property
    def root(self) -> Path:
        return self._root

    @property
    def manifest_sha256(self) -> str:
        snapshot = read_verified_directory_tree(self._root)
        try:
            manifest = snapshot.files[_MANIFEST]
        except KeyError as error:
            raise ValueError("missing corpus manifest") from error
        return sha256(manifest).hexdigest()

    @property
    def read_only(self) -> bool:
        return self.verify().metadata.mode == "formal"

    @classmethod
    def freeze(
        cls,
        *,
        records: Iterable[ExecutionRecord],
        parity_results: Iterable[ParityResult],
        destination: Path,
        manifest_metadata: CorpusManifestMetadata,
    ) -> Self:
        validated_records = tuple(
            ExecutionRecord.model_validate(record.model_dump(mode="python"))
            for record in records
        )
        if not validated_records:
            raise ValueError("trace replay corpus requires at least one record")
        validated_parity = tuple(
            ParityResult.model_validate(result.model_dump(mode="python"))
            for result in parity_results
        )
        metadata = CorpusManifestMetadata.model_validate(
            manifest_metadata.model_dump(mode="python")
        )
        for record in validated_records:
            cls._require_record_matches_manifest(record, metadata)
        parity_sha256 = canonical_sha256(cast(JsonValue, list(validated_parity)))
        if metadata.parity_results_sha256 != parity_sha256:
            raise ValueError("parity_results_sha256 does not match parity results")
        phase5_corpus = metadata.corpus_id.startswith("phase5-")
        if phase5_corpus and len(validated_records) != len(validated_parity) * 2:
            raise ValueError("parity results must cover every corpus record pair")

        parity_payloads = tuple(
            CorpusParityPayload(
                pair_identity=reference_cell.pair_identity,
                reference_cell=reference_cell,
                candidate_cell=candidate_cell,
                result=result,
            )
            for reference, candidate, result in zip(
                validated_records[::2],
                validated_records[1::2],
                validated_parity,
                strict=True,
            )
            for reference_cell, candidate_cell in (
                (CorpusEntry.from_record(reference).cell, CorpusEntry.from_record(candidate).cell),
            )
        ) if phase5_corpus else ()
        parity_entries = tuple(
            CorpusParityEntry.from_payload(payload) for payload in parity_payloads
        )

        entries = tuple(CorpusEntry.from_record(record) for record in validated_records)
        manifest = CorpusManifest.from_entries(
            entries=entries,
            parity_entries=parity_entries,
            metadata=metadata,
        )
        require_safe_artifact_content("corpus_manifest", manifest.model_dump(mode="python"))
        require_safe_artifact_content(
            "corpus_parity_results",
            [payload.model_dump(mode="python") for payload in parity_payloads],
        )

        payloads: dict[str, bytes] = {_MANIFEST: canonical_bytes(manifest)}
        for record, entry in zip(validated_records, entries, strict=True):
            require_safe_artifact_content("corpus_record", record.model_dump(mode="python"))
            require_safe_artifact_content(
                "corpus_trace", record.trace.model_dump(mode="python")
            )
            cls._bind_payload(payloads, entry.record_path, canonical_bytes(record))
            cls._bind_payload(payloads, entry.trace_path, canonical_bytes(record.trace))
        for payload, parity_entry in zip(parity_payloads, parity_entries, strict=True):
            cls._bind_payload(
                payloads,
                parity_entry.result_path,
                canonical_bytes(payload),
            )

        staging, root_identity = create_owned_staging_directory(destination)
        identity = None
        try:
            for relative in sorted(_DIRECTORIES, key=lambda value: (value.count("/"), value)):
                (staging / relative).mkdir()
            for relative, content in sorted(payloads.items()):
                cls._write_synced(staging / relative, content)
            cls._sync_directories(staging)
            identity = capture_owned_directory_identity(staging)
            publish_directory_no_replace(staging, destination)
            if capture_owned_directory_identity(destination) != identity:
                raise RuntimeError("published corpus ownership verification failed")
            _fsync_directory(destination.parent)
        except Exception:
            if os.path.lexists(staging):
                if identity is not None:
                    delete_owned_staging_directory(staging, identity)
                else:
                    quarantine_owned_staging_directory(staging, root_identity)
            raise
        return cls(
            destination,
            expected_manifest_sha256=sha256(canonical_bytes(manifest)).hexdigest(),
        )

    @staticmethod
    def _bind_payload(payloads: dict[str, bytes], relative: str, content: bytes) -> None:
        existing = payloads.setdefault(relative, content)
        if existing != content:
            raise ValueError("content-addressed payload collision")

    @staticmethod
    def _write_synced(path: Path, content: bytes) -> None:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != content:
            raise ValueError(f"corpus write verification failed: {path.name}")

    @staticmethod
    def _sync_directories(root: Path) -> None:
        directories = sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in (*directories, root):
            _fsync_directory(directory)

    def verify(self) -> CorpusManifest:
        snapshot = read_verified_directory_tree(self._root)
        try:
            manifest_bytes = snapshot.files[_MANIFEST]
        except KeyError as error:
            raise ValueError("missing corpus manifest") from error
        manifest_digest = sha256(manifest_bytes).hexdigest()
        if (
            self._expected_manifest_sha256 is not None
            and manifest_digest != self._expected_manifest_sha256
        ):
            raise ValueError("corpus manifest SHA-256 mismatch")
        manifest = self._parse_model(manifest_bytes, CorpusManifest)
        if canonical_bytes(manifest) != manifest_bytes:
            raise ValueError("corpus manifest is not canonical JSON")

        expected = {
            entry.record_path: entry.record_sha256 for entry in manifest.entries
        } | {entry.trace_path: entry.trace_sha256 for entry in manifest.entries} | {
            entry.result_path: entry.result_sha256 for entry in manifest.parity_entries
        }
        actual_files = set(snapshot.files)
        actual_directories = snapshot.directories
        if actual_directories != _DIRECTORIES:
            raise ValueError("unknown corpus payload directory")
        missing = set(expected) - actual_files
        if missing:
            raise ValueError(f"missing corpus payload: {min(missing)}")
        unknown = actual_files - set(expected) - {_MANIFEST}
        expected_digests = set(expected.values())
        for relative in sorted(unknown):
            digest = sha256(snapshot.files[relative]).hexdigest()
            if digest in expected_digests:
                raise ValueError(f"duplicate corpus payload content: {relative}")
        if unknown:
            raise ValueError(f"unknown corpus payload: {min(unknown)}")

        for entry in manifest.entries:
            record_bytes = self._read_hashed_payload(
                snapshot.files, entry.record_path, entry.record_sha256
            )
            trace_bytes = self._read_hashed_payload(
                snapshot.files, entry.trace_path, entry.trace_sha256
            )
            record = self._parse_model(record_bytes, ExecutionRecord)
            trace = self._parse_model(trace_bytes, TraceIR)
            if canonical_bytes(record) != record_bytes or canonical_bytes(trace) != trace_bytes:
                raise ValueError("corpus payload is not canonical JSON")
            if record.trace != trace:
                raise ValueError("record trace does not equal trace payload")
            if CorpusEntry.from_record(record) != entry:
                raise ValueError("corpus entry does not match execution record")
            self._require_record_matches_manifest(record, manifest.metadata)
        parity_results: list[ParityResult] = []
        for parity_entry in manifest.parity_entries:
            payload_bytes = self._read_hashed_payload(
                snapshot.files,
                parity_entry.result_path,
                parity_entry.result_sha256,
                payload_kind="parity payload",
            )
            payload = self._parse_model(payload_bytes, CorpusParityPayload)
            if canonical_bytes(payload) != payload_bytes:
                raise ValueError("corpus parity payload is not canonical JSON")
            if CorpusParityEntry.from_payload(payload) != parity_entry:
                raise ValueError("corpus parity entry does not match parity payload")
            parity_results.append(payload.result)
        parity_payload_values = [result.model_dump(mode="json") for result in parity_results]
        if manifest.parity_entries and canonical_sha256(
            cast(JsonValue, parity_payload_values)
        ) != manifest.metadata.parity_results_sha256:
            raise ValueError("parity_results_sha256 does not match parity payloads")
        return manifest

    def load(self, cell: CorpusCell) -> ExecutionRecord:
        validated_cell = CorpusCell.model_validate(cell.model_dump(mode="python"))
        manifest = self.verify()
        try:
            entry = next(entry for entry in manifest.entries if entry.cell == validated_cell)
        except StopIteration as error:
            raise KeyError(validated_cell) from error
        snapshot = read_verified_directory_tree(self._root)
        record_bytes = self._read_hashed_payload(
            snapshot.files, entry.record_path, entry.record_sha256
        )
        trace_bytes = self._read_hashed_payload(
            snapshot.files, entry.trace_path, entry.trace_sha256
        )
        record = self._parse_model(record_bytes, ExecutionRecord)
        trace = self._parse_model(trace_bytes, TraceIR)
        if record.trace != trace:
            raise ValueError("record trace does not equal trace payload")
        return record

    def load_parity(self, pair_identity: str) -> CorpusParityPayload:
        manifest = self.verify()
        try:
            entry = next(
                entry
                for entry in manifest.parity_entries
                if entry.pair_identity == pair_identity
            )
        except StopIteration as error:
            raise KeyError(pair_identity) from error
        snapshot = read_verified_directory_tree(self._root)
        payload_bytes = self._read_hashed_payload(
            snapshot.files,
            entry.result_path,
            entry.result_sha256,
            payload_kind="parity payload",
        )
        payload = self._parse_model(payload_bytes, CorpusParityPayload)
        if canonical_bytes(payload) != payload_bytes:
            raise ValueError("corpus parity payload is not canonical JSON")
        return payload

    @staticmethod
    def _read_hashed_payload(
        files: Mapping[str, bytes],
        relative: str,
        expected_sha256: str,
        *,
        payload_kind: str = "payload",
    ) -> bytes:
        content = files[relative]
        if sha256(content).hexdigest() != expected_sha256:
            raise ValueError(f"{payload_kind} SHA-256 mismatch: {relative}")
        return content

    @staticmethod
    def _parse_model(content: bytes, model: type[_ModelT]) -> _ModelT:
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("corpus payload is not valid JSON") from error
        return model.model_validate(payload)

    @staticmethod
    def _require_record_matches_manifest(
        record: ExecutionRecord,
        metadata: CorpusManifestMetadata,
    ) -> None:
        provenance = record.provenance
        if provenance.dirty_worktree or (
            provenance.git_commit,
            provenance.dependency_lock_sha256,
            provenance.dataset_manifest_sha256,
            provenance.dirty_worktree,
        ) != (
            metadata.git_commit,
            metadata.dependency_lock_sha256,
            metadata.dataset_manifest_sha256,
            metadata.dirty_worktree,
        ):
            raise ValueError("execution record provenance does not match corpus manifest")


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync; Windows safely degrades when unsupported."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
    finally:
        os.close(descriptor)
