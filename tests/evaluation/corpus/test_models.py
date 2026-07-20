from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.corpus import (
    CorpusCell,
    CorpusEntry,
    CorpusManifest,
    CorpusManifestMetadata,
    CorpusParityEntry,
    CorpusParityPayload,
    Phase5CorpusPlan,
)
from spanvouch.evaluation.corpus.generate import build_corpus_plan
from spanvouch.evaluation.experiments import load_experiment_config
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


@pytest.mark.parametrize(
    ("target", "message"),
    (
        ("reference_framework", "reference cell must be LangGraph"),
        ("candidate_framework", "candidate cell must be AutoGen"),
        ("candidate_identity", "same corpus pair"),
        ("pair_identity", "pair identity does not match"),
    ),
)
def test_parity_payload_rejects_misbound_pair_components(
    record: ExecutionRecord,
    target: str,
    message: str,
) -> None:
    reference = CorpusEntry.from_record(record).cell
    candidate = reference.model_copy(update={"framework_id": FrameworkId.AUTOGEN})
    payload = {
        "pair_identity": reference.pair_identity,
        "reference_cell": reference.model_dump(mode="json"),
        "candidate_cell": candidate.model_dump(mode="json"),
        "reference_record_sha256": "1" * 64,
        "candidate_record_sha256": "2" * 64,
        "result": {"status": "matched"},
    }
    if target == "reference_framework":
        payload["reference_cell"]["framework_id"] = FrameworkId.AUTOGEN
    elif target == "candidate_framework":
        payload["candidate_cell"]["framework_id"] = FrameworkId.LANGGRAPH
    elif target == "candidate_identity":
        payload["candidate_cell"]["seed"] += 1
    else:
        payload["pair_identity"] = "supportlab:wrong:wrong:1:1"

    with pytest.raises(ValidationError, match=message):
        CorpusParityPayload.model_validate(payload)


@pytest.mark.parametrize(
    ("target", "message"),
    (
        ("reference_framework", "reference cell must be LangGraph"),
        ("candidate_framework", "candidate cell must be AutoGen"),
        ("candidate_identity", "same corpus pair"),
        ("pair_identity", "pair identity does not match"),
        ("result_path", "payload path must match"),
    ),
)
def test_parity_entry_rejects_misbound_pair_components(
    record: ExecutionRecord,
    target: str,
    message: str,
) -> None:
    reference = CorpusEntry.from_record(record).cell
    candidate = reference.model_copy(update={"framework_id": FrameworkId.AUTOGEN})
    payload = CorpusParityPayload(
        pair_identity=reference.pair_identity,
        reference_cell=reference,
        candidate_cell=candidate,
        reference_record_sha256="1" * 64,
        candidate_record_sha256="2" * 64,
        result=ParityResult(status="matched"),
    )
    entry = CorpusParityEntry.from_payload(payload).model_dump(mode="json")
    if target == "reference_framework":
        entry["reference_cell"]["framework_id"] = FrameworkId.AUTOGEN
    elif target == "candidate_framework":
        entry["candidate_cell"]["framework_id"] = FrameworkId.LANGGRAPH
    elif target == "candidate_identity":
        entry["candidate_cell"]["seed"] += 1
    elif target == "pair_identity":
        entry["pair_identity"] = "supportlab:wrong:wrong:1:1"
    else:
        entry["result_path"] = f"parity/sha256/{'f' * 64}.json"

    with pytest.raises(ValidationError, match=message):
        CorpusParityEntry.model_validate(entry)


@pytest.fixture
def phase5_plan() -> Phase5CorpusPlan:
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    cells = tuple(
        CorpusCell(
            domain=cell.scenario.domain,
            template_id=cell.scenario.template_id,
            scenario_id=cell.scenario.scenario_id,
            framework_id=cell.framework_id,
            repetition=cell.repetition,
            seed=cell.seed,
        )
        for cell in build_corpus_plan(config)
    )
    return Phase5CorpusPlan.from_cells(
        mode="pilot",
        repetitions=config.repetitions,
        seed=config.seed,
        experiment_config_sha256=canonical_sha256(config.model_dump(mode="json")),
        ordered_cells=cells,
    )


def _rehash_plan_payload(payload: dict[str, object]) -> None:
    payload["ordered_cells_sha256"] = canonical_sha256(payload["ordered_cells"])
    payload["plan_identity_sha256"] = canonical_sha256(
        {
            key: payload[key]
            for key in (
                "experiment_config_sha256",
                "mode",
                "ordered_cells_sha256",
                "repetitions",
                "seed",
                "schema_name",
                "schema_version",
            )
        }
    )


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("duplicate", "ordered plan cells must be unique"),
        ("pilot_repetitions", "pilot plan requires exactly three"),
        ("formal_repetitions", "formal plan requires at least five"),
        ("cell_count", "cell count does not match"),
    ),
)
def test_phase5_plan_rejects_self_consistent_invalid_shapes(
    phase5_plan: Phase5CorpusPlan,
    case: str,
    message: str,
) -> None:
    payload = phase5_plan.model_dump(mode="json")
    if case == "duplicate":
        payload["ordered_cells"][-1] = payload["ordered_cells"][0]
    elif case == "pilot_repetitions":
        payload["repetitions"] = 4
    elif case == "formal_repetitions":
        payload["mode"] = "formal"
    else:
        payload["mode"] = "formal"
        payload["repetitions"] = 5
    _rehash_plan_payload(payload)

    with pytest.raises(ValidationError, match=message):
        Phase5CorpusPlan.model_validate(payload)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("missing_plan", "requires a canonical plan"),
        ("generic_plan", "generic corpus metadata forbids"),
        ("missing_frameworks", "full provenance per framework"),
        ("generic_frameworks", "generic corpus metadata forbids framework"),
        ("mode", "mode does not match"),
        ("config", "config hash does not match"),
        ("pairs", "pair count does not match"),
    ),
)
def test_phase5_metadata_rejects_inconsistent_plan_bindings(
    phase5_plan: Phase5CorpusPlan,
    record: ExecutionRecord,
    case: str,
    message: str,
) -> None:
    frameworks = {
        FrameworkId.LANGGRAPH: record.provenance,
        FrameworkId.AUTOGEN: record.provenance,
    }
    payload = {
        "corpus_id": "phase5-pilot",
        "mode": "pilot",
        "experiment_config_sha256": phase5_plan.experiment_config_sha256,
        "git_commit": record.provenance.git_commit,
        "dependency_lock_sha256": record.provenance.dependency_lock_sha256,
        "dataset_manifest_sha256": record.provenance.dataset_manifest_sha256,
        "dirty_worktree": False,
        "expected_cell_count": len(phase5_plan.ordered_cells),
        "expected_pair_count": len(phase5_plan.ordered_cells) // 2,
        "phase5_plan": phase5_plan.model_dump(mode="json"),
        "framework_provenance": {
            key.value: value.model_dump(mode="json") for key, value in frameworks.items()
        },
        "created_at_utc": datetime(2026, 7, 20, tzinfo=UTC),
        "parity_results_sha256": "3" * 64,
    }
    if case == "missing_plan":
        payload["phase5_plan"] = None
    elif case == "generic_plan":
        payload["corpus_id"] = "generic-pilot"
    elif case == "missing_frameworks":
        payload["framework_provenance"] = None
    elif case == "generic_frameworks":
        payload["corpus_id"] = "generic-pilot"
        payload["phase5_plan"] = None
    elif case == "mode":
        payload["mode"] = "formal"
    elif case == "config":
        payload["experiment_config_sha256"] = "4" * 64
    else:
        payload["expected_pair_count"] += 1

    with pytest.raises(ValidationError, match=message):
        CorpusManifestMetadata.model_validate(payload)


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
