from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.corpus import (
    CorpusCell,
    CorpusEntry,
    CorpusManifest,
    CorpusManifestMetadata,
    CorpusParityPayload,
)
from spanvouch.labs.runtime import ExecutionRecord, FrameworkId, ParityResult


def test_corpus_entry_binds_record_and_trace_hashes(record: ExecutionRecord) -> None:
    entry = CorpusEntry.from_record(record)
    assert entry.record_sha256 == canonical_sha256(record)
    assert entry.trace_sha256 == canonical_sha256(record.trace)
    assert entry.record_path == f"records/sha256/{entry.record_sha256}.json"
    assert entry.trace_path == f"traces/sha256/{entry.trace_sha256}.json"
    assert entry.status == record.status
    assert entry.cell == CorpusCell(
        domain=record.domain,
        template_id=record.template_id,
        scenario_id=record.scenario_id,
        framework_id=record.framework_id,
        repetition=record.repetition,
        seed=record.seed,
    )


def test_corpus_models_are_frozen_and_reject_evaluator_only_fields(
    entry: CorpusEntry,
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        entry.cell.seed = 1
    with pytest.raises(ValidationError, match="frozen"):
        manifest_metadata.created_at_utc = datetime.now()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CorpusManifestMetadata.model_validate(
            {**manifest_metadata.model_dump(), "gold_label": "sealed"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CorpusCell.model_validate({**entry.cell.model_dump(), "split_identity": "formal"})


def test_metadata_requires_utc_creation_timestamp(
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    with pytest.raises(ValidationError, match="created_at_utc must be UTC"):
        CorpusManifestMetadata.model_validate(
            {**manifest_metadata.model_dump(), "created_at_utc": datetime(2026, 7, 19)}
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("record_path", "records/sha256/../payload.json", "record payload path"),
        ("record_path", "other/sha256/" + "a" * 64 + ".json", "record payload path"),
        ("trace_path", "traces\\sha256\\" + "a" * 64 + ".json", "trace payload path"),
        ("trace_path", "traces/sha256/../../secret.json", "trace payload path"),
    ),
)
def test_entry_rejects_unknown_or_traversing_payload_paths(
    entry: CorpusEntry, field: str, value: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        CorpusEntry.model_validate({**entry.model_dump(), field: value})


def test_entry_rejects_path_hash_mismatch(entry: CorpusEntry) -> None:
    with pytest.raises(ValidationError, match="record payload path"):
        CorpusEntry.model_validate(
            {
                **entry.model_dump(),
                "record_sha256": "a" * 64,
            }
        )


def test_manifest_sorts_entries_and_derives_payload_hashes(
    record: ExecutionRecord,
    second_record: ExecutionRecord,
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    entries = (CorpusEntry.from_record(second_record), CorpusEntry.from_record(record))
    metadata = manifest_metadata.model_copy(update={"expected_cell_count": 2})
    manifest = CorpusManifest.from_entries(entries=entries, metadata=metadata)
    assert manifest.entries == tuple(sorted(entries, key=lambda item: item.cell.sort_key()))
    assert manifest.records_sha256 == canonical_sha256(
        sorted({item.record_sha256 for item in entries})
    )
    assert manifest.traces_sha256 == canonical_sha256(
        sorted({item.trace_sha256 for item in entries})
    )
    assert manifest.payloads_sha256 == canonical_sha256(
        sorted(
            (
                *(
                    {"path": item.record_path, "sha256": item.record_sha256}
                    for item in entries
                ),
                *(
                    {"path": item.trace_path, "sha256": item.trace_sha256}
                    for item in entries
                ),
            ),
            key=lambda item: item["path"],
        )
    )


def test_manifest_rejects_duplicate_cells(
    entry: CorpusEntry,
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    metadata = manifest_metadata.model_copy(update={"expected_cell_count": 2})
    with pytest.raises(ValueError, match="corpus cells must be unique"):
        CorpusManifest.from_entries(entries=(entry, entry), metadata=metadata)


def test_parity_payload_binds_both_execution_record_hashes(
    record: ExecutionRecord,
) -> None:
    candidate = ExecutionRecord.model_validate(
        {
            **record.model_dump(mode="python"),
            "framework_id": FrameworkId.AUTOGEN,
        }
    )
    reference_entry = CorpusEntry.from_record(record)
    candidate_entry = CorpusEntry.from_record(candidate)

    payload = CorpusParityPayload(
        pair_identity=reference_entry.cell.pair_identity,
        reference_cell=reference_entry.cell,
        candidate_cell=candidate_entry.cell,
        reference_record_sha256=reference_entry.record_sha256,
        candidate_record_sha256=candidate_entry.record_sha256,
        result=ParityResult(status="matched"),
    )

    assert payload.reference_record_sha256 == reference_entry.record_sha256
    assert payload.candidate_record_sha256 == candidate_entry.record_sha256


def test_manifest_direct_validation_rejects_unsorted_entries_and_derived_hash_mismatch(
    record: ExecutionRecord,
    second_record: ExecutionRecord,
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    entries = (CorpusEntry.from_record(second_record), CorpusEntry.from_record(record))
    metadata = manifest_metadata.model_copy(update={"expected_cell_count": 2})
    valid = CorpusManifest.from_entries(entries=entries, metadata=metadata)
    with pytest.raises(ValidationError, match="corpus entries must be sorted"):
        CorpusManifest.model_validate({**valid.model_dump(), "entries": entries})
    with pytest.raises(ValidationError, match="records_sha256 does not match"):
        CorpusManifest.model_validate({**valid.model_dump(), "records_sha256": "a" * 64})


def test_manifest_factory_revalidates_forged_model_copy_instances(
    entry: CorpusEntry,
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    forged_entry = entry.model_copy(update={"record_path": "../foreign.json"})
    with pytest.raises(ValidationError, match="record payload path"):
        CorpusManifest.from_entries(
            entries=(forged_entry,), metadata=manifest_metadata
        )

    forged_metadata = manifest_metadata.model_copy(
        update={"created_at_utc": datetime(2026, 7, 19)}
    )
    with pytest.raises(ValidationError, match="created_at_utc must be UTC"):
        CorpusManifest.from_entries(entries=(entry,), metadata=forged_metadata)
