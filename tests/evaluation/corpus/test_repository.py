from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from spanvouch.contracts.versioning import canonical_bytes, canonical_sha256
from spanvouch.evaluation.corpus import (
    CorpusEntry,
    CorpusManifest,
    CorpusManifestMetadata,
    TraceReplayRepository,
)
from spanvouch.labs.runtime import ExecutionRecord, ParityResult


def _freeze(
    destination: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    metadata: CorpusManifestMetadata,
) -> TraceReplayRepository:
    return TraceReplayRepository.freeze(
        records=(record,),
        parity_results=parity_results,
        destination=destination,
        manifest_metadata=metadata,
    )


def _create_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks are unavailable")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        pytest.skip("directory reparse points are unavailable")


def test_freeze_atomically_publishes_exact_content_addressed_layout(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    destination = tmp_path / "corpus"
    repository = _freeze(destination, record, parity_results, manifest_metadata)
    entry = CorpusEntry.from_record(record)
    assert repository.verify().entries == (entry,)
    assert {path.relative_to(destination).as_posix() for path in destination.rglob("*")} == {
        "manifest.json",
        "records",
        "records/sha256",
        entry.record_path,
        "traces",
        "traces/sha256",
        entry.trace_path,
    }
    assert (destination / entry.record_path).read_bytes() == canonical_bytes(record)
    assert (destination / entry.trace_path).read_bytes() == canonical_bytes(record.trace)
    assert not tuple(tmp_path.glob(".corpus.tmp-*"))


def test_freeze_rejects_second_write_without_changing_first_publication(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    destination = tmp_path / "corpus"
    _freeze(destination, record, parity_results, manifest_metadata)
    before = {
        path.relative_to(destination): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    }
    with pytest.raises(FileExistsError, match="destination already exists"):
        _freeze(destination, record, parity_results, manifest_metadata)
    after = {
        path.relative_to(destination): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not tuple(tmp_path.glob(".corpus.tmp-*"))


def test_verify_rejects_missing_or_changed_payload(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    missing_root = tmp_path / "missing"
    missing = _freeze(missing_root, record, parity_results, manifest_metadata)
    entry = CorpusEntry.from_record(record)
    (missing_root / entry.trace_path).unlink()
    with pytest.raises(ValueError, match="missing corpus payload"):
        missing.verify()

    changed_root = tmp_path / "changed"
    changed = _freeze(changed_root, record, parity_results, manifest_metadata)
    (changed_root / entry.record_path).write_bytes(b"{}")
    with pytest.raises(ValueError, match="payload SHA-256 mismatch"):
        changed.verify()


def test_verify_rejects_unknown_payload_and_duplicate_content_under_second_name(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    unknown_root = tmp_path / "unknown"
    unknown = _freeze(unknown_root, record, parity_results, manifest_metadata)
    extra = unknown_root / "records/sha256" / ("a" * 64 + ".json")
    extra.write_bytes(b"{}")
    with pytest.raises(ValueError, match="unknown corpus payload"):
        unknown.verify()

    duplicate_root = tmp_path / "duplicate"
    duplicate = _freeze(duplicate_root, record, parity_results, manifest_metadata)
    entry = CorpusEntry.from_record(record)
    duplicate_path = duplicate_root / "records/sha256" / ("f" * 64 + ".json")
    duplicate_path.write_bytes((duplicate_root / entry.record_path).read_bytes())
    with pytest.raises(ValueError, match="duplicate corpus payload content"):
        duplicate.verify()


def test_verify_rejects_symlink_or_reparse_payload_without_following_it(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    destination = tmp_path / "corpus"
    repository = _freeze(destination, record, parity_results, manifest_metadata)
    entry = CorpusEntry.from_record(record)
    payload = destination / entry.record_path
    payload.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_bytes(b"must remain")
    _create_directory_link(payload, outside)
    with pytest.raises(ValueError, match="symlink or reparse point"):
        repository.verify()
    assert marker.read_bytes() == b"must remain"


def test_expected_manifest_hash_detects_an_internally_valid_manifest_replacement(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    destination = tmp_path / "corpus"
    repository = _freeze(destination, record, parity_results, manifest_metadata)
    expected = repository.manifest_sha256
    manifest_path = destination / "manifest.json"
    payload = json.loads(manifest_path.read_bytes())
    payload["metadata"]["corpus_id"] = "replacement-corpus"
    manifest_path.write_bytes(canonical_bytes(payload))
    reopened = TraceReplayRepository(destination, expected_manifest_sha256=expected)
    with pytest.raises(ValueError, match="corpus manifest SHA-256 mismatch"):
        reopened.verify()


def test_load_runs_verification_and_returns_hash_equal_immutable_record(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    repository = _freeze(tmp_path / "corpus", record, parity_results, manifest_metadata)
    loaded = repository.load(CorpusEntry.from_record(record).cell)
    assert loaded == record
    assert canonical_sha256(loaded) == canonical_sha256(record)
    with pytest.raises(Exception, match="frozen"):
        loaded.seed = 1


def test_load_rejects_record_whose_embedded_trace_differs_from_trace_payload(
    tmp_path: Path,
    record: ExecutionRecord,
    second_record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    destination = tmp_path / "corpus"
    repository = _freeze(destination, record, parity_results, manifest_metadata)
    old_entry = CorpusEntry.from_record(record)
    changed_record = ExecutionRecord.model_validate(
        {
            **record.model_dump(mode="python"),
            "trace": second_record.trace.model_dump(mode="python"),
            "trace_sha256": canonical_sha256(second_record.trace),
        }
    )
    changed_entry = old_entry.model_copy(
        update={
            "record_sha256": canonical_sha256(changed_record),
            "record_path": f"records/sha256/{canonical_sha256(changed_record)}.json",
        }
    )
    (destination / old_entry.record_path).unlink()
    (destination / changed_entry.record_path).write_bytes(canonical_bytes(changed_record))
    manifest = CorpusManifest.from_entries(
        entries=(changed_entry,), metadata=manifest_metadata
    )
    (destination / "manifest.json").write_bytes(canonical_bytes(manifest))
    repository = TraceReplayRepository(destination)
    with pytest.raises(ValueError, match="record trace does not equal trace payload"):
        repository.load(changed_entry.cell)


def test_freeze_rejects_parity_hash_mismatch_and_cleans_staging(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    destination = tmp_path / "corpus"
    bad = manifest_metadata.model_copy(update={"parity_results_sha256": "a" * 64})
    with pytest.raises(ValueError, match="parity_results_sha256 does not match"):
        _freeze(destination, record, parity_results, bad)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".corpus.tmp-*"))


def test_failed_publish_cleans_only_the_owned_staging_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    destination = tmp_path / "corpus"

    def fail_publish(_source: Path, _destination: Path) -> None:
        raise OSError("publish failed")

    monkeypatch.setattr(
        "spanvouch.evaluation.corpus.repository.publish_directory_no_replace",
        fail_publish,
    )
    with pytest.raises(OSError, match="publish failed"):
        _freeze(destination, record, parity_results, manifest_metadata)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".corpus.tmp-*"))


def test_failed_publish_preserves_foreign_staging_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    destination = tmp_path / "corpus"
    foreign: Path | None = None

    def substitute_then_fail(source: Path, _destination: Path) -> None:
        nonlocal foreign
        owned = source.with_name(source.name + ".owned")
        source.rename(owned)
        source.mkdir()
        foreign = source / "foreign.txt"
        foreign.write_bytes(b"preserve")
        raise OSError("publish failed")

    monkeypatch.setattr(
        "spanvouch.evaluation.corpus.repository.publish_directory_no_replace",
        substitute_then_fail,
    )
    with pytest.raises(OSError, match="publish failed"):
        _freeze(destination, record, parity_results, manifest_metadata)
    assert foreign is not None and foreign.read_bytes() == b"preserve"


def test_formal_repository_is_explicitly_read_only(
    tmp_path: Path,
    record: ExecutionRecord,
    parity_results: tuple[ParityResult, ...],
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    formal_metadata = manifest_metadata.model_copy(update={"mode": "formal"})
    repository = _freeze(tmp_path / "corpus", record, parity_results, formal_metadata)
    assert repository.read_only is True
    before = repository.manifest_sha256
    assert repository.load(CorpusEntry.from_record(record).cell) == record
    assert repository.manifest_sha256 == before
