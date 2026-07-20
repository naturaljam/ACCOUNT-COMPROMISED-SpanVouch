from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_sha256
from spanvouch.labs.runtime import ExecutionRecord, ExecutionStatus, FrameworkId

_GIT_COMMIT_PATTERN = r"^[0-9a-f]{40}$"


class CorpusCell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: Literal["supportlab", "opslab"]
    template_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    framework_id: FrameworkId
    repetition: int = Field(ge=1)
    seed: int

    def sort_key(self) -> tuple[str, str, str, str, int, int]:
        return (
            self.domain,
            self.template_id,
            self.scenario_id,
            self.framework_id.value,
            self.repetition,
            self.seed,
        )


class CorpusEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cell: CorpusCell
    record_sha256: str = Field(pattern=SHA256_PATTERN)
    record_path: str = Field(min_length=1)
    trace_sha256: str = Field(pattern=SHA256_PATTERN)
    trace_path: str = Field(min_length=1)
    status: ExecutionStatus

    @model_validator(mode="after")
    def validate_payload_paths(self) -> Self:
        expected_record = f"records/sha256/{self.record_sha256}.json"
        expected_trace = f"traces/sha256/{self.trace_sha256}.json"
        if self.record_path != expected_record:
            raise ValueError("record payload path must match its content hash")
        if self.trace_path != expected_trace:
            raise ValueError("trace payload path must match its content hash")
        return self

    @classmethod
    def from_record(cls, record: ExecutionRecord) -> Self:
        record_sha256 = canonical_sha256(record)
        trace_sha256 = canonical_sha256(record.trace)
        return cls(
            cell=CorpusCell(
                domain=record.domain,
                template_id=record.template_id,
                scenario_id=record.scenario_id,
                framework_id=record.framework_id,
                repetition=record.repetition,
                seed=record.seed,
            ),
            record_sha256=record_sha256,
            record_path=f"records/sha256/{record_sha256}.json",
            trace_sha256=trace_sha256,
            trace_path=f"traces/sha256/{trace_sha256}.json",
            status=record.status,
        )


class CorpusManifestMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_id: str = Field(min_length=1)
    mode: Literal["pilot", "formal"]
    experiment_config_sha256: str = Field(pattern=SHA256_PATTERN)
    git_commit: str = Field(pattern=_GIT_COMMIT_PATTERN)
    dependency_lock_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    created_at_utc: datetime
    parity_results_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("created_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_at_utc must be UTC")
        return value


class CorpusManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.trace-corpus"] = "spanvouch.trace-corpus"
    schema_version: Literal["1.0"] = "1.0"
    metadata: CorpusManifestMetadata
    entries: tuple[CorpusEntry, ...] = Field(min_length=1)
    records_sha256: str = Field(pattern=SHA256_PATTERN)
    traces_sha256: str = Field(pattern=SHA256_PATTERN)
    payloads_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_entries_and_hashes(self) -> Self:
        cells = tuple(entry.cell for entry in self.entries)
        if len(set(cells)) != len(cells):
            raise ValueError("corpus cells must be unique")
        expected_entries = tuple(sorted(self.entries, key=lambda item: item.cell.sort_key()))
        if self.entries != expected_entries:
            raise ValueError("corpus entries must be sorted")
        records_sha256, traces_sha256, payloads_sha256 = self._derived_hashes(self.entries)
        if self.records_sha256 != records_sha256:
            raise ValueError("records_sha256 does not match corpus entries")
        if self.traces_sha256 != traces_sha256:
            raise ValueError("traces_sha256 does not match corpus entries")
        if self.payloads_sha256 != payloads_sha256:
            raise ValueError("payloads_sha256 does not match corpus entries")
        return self

    @classmethod
    def from_entries(
        cls,
        *,
        entries: tuple[CorpusEntry, ...],
        metadata: CorpusManifestMetadata,
    ) -> Self:
        validated_metadata = CorpusManifestMetadata.model_validate(
            metadata.model_dump(mode="python")
        )
        validated_entries = tuple(
            CorpusEntry.model_validate(entry.model_dump(mode="python"))
            for entry in entries
        )
        sorted_entries = tuple(
            sorted(validated_entries, key=lambda item: item.cell.sort_key())
        )
        records_sha256, traces_sha256, payloads_sha256 = cls._derived_hashes(sorted_entries)
        return cls(
            metadata=validated_metadata,
            entries=sorted_entries,
            records_sha256=records_sha256,
            traces_sha256=traces_sha256,
            payloads_sha256=payloads_sha256,
        )

    @staticmethod
    def _derived_hashes(entries: tuple[CorpusEntry, ...]) -> tuple[str, str, str]:
        records = sorted({entry.record_sha256 for entry in entries})
        traces = sorted({entry.trace_sha256 for entry in entries})
        payloads = sorted(
            (
                *(
                    {"path": entry.record_path, "sha256": entry.record_sha256}
                    for entry in entries
                ),
                *(
                    {"path": entry.trace_path, "sha256": entry.trace_sha256}
                    for entry in entries
                ),
            ),
            key=lambda item: item["path"],
        )
        return (
            canonical_sha256(cast(JsonValue, records)),
            canonical_sha256(cast(JsonValue, traces)),
            canonical_sha256(cast(JsonValue, payloads)),
        )
