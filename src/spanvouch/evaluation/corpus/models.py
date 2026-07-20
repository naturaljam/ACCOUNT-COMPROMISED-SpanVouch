from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_sha256
from spanvouch.labs.runtime import (
    ExecutionRecord,
    ExecutionStatus,
    FrameworkId,
    ParityResult,
)

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

    @property
    def pair_identity(self) -> str:
        return ":".join(
            (
                self.domain,
                self.template_id,
                self.scenario_id,
                str(self.repetition),
                str(self.seed),
            )
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


class CorpusParityPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_identity: str = Field(min_length=1)
    reference_cell: CorpusCell
    candidate_cell: CorpusCell
    result: ParityResult

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if self.reference_cell.framework_id is not FrameworkId.LANGGRAPH:
            raise ValueError("parity reference cell must be LangGraph")
        if self.candidate_cell.framework_id is not FrameworkId.AUTOGEN:
            raise ValueError("parity candidate cell must be AutoGen")
        if self.reference_cell.pair_identity != self.candidate_cell.pair_identity:
            raise ValueError("parity cells must identify the same corpus pair")
        if self.pair_identity != self.reference_cell.pair_identity:
            raise ValueError("parity pair identity does not match its cells")
        return self


class CorpusParityEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_identity: str = Field(min_length=1)
    reference_cell: CorpusCell
    candidate_cell: CorpusCell
    result_sha256: str = Field(pattern=SHA256_PATTERN)
    result_path: str = Field(min_length=1)
    status: Literal["matched", "mismatched", "incompatible"]

    @model_validator(mode="after")
    def validate_payload_binding(self) -> Self:
        if self.reference_cell.framework_id is not FrameworkId.LANGGRAPH:
            raise ValueError("parity reference cell must be LangGraph")
        if self.candidate_cell.framework_id is not FrameworkId.AUTOGEN:
            raise ValueError("parity candidate cell must be AutoGen")
        if self.reference_cell.pair_identity != self.candidate_cell.pair_identity:
            raise ValueError("parity cells must identify the same corpus pair")
        if self.pair_identity != self.reference_cell.pair_identity:
            raise ValueError("parity pair identity does not match its cells")
        expected_path = f"parity/sha256/{self.result_sha256}.json"
        if self.result_path != expected_path:
            raise ValueError("parity payload path must match its content hash")
        return self

    @classmethod
    def from_payload(cls, payload: CorpusParityPayload) -> Self:
        result_sha256 = canonical_sha256(payload)
        return cls(
            pair_identity=payload.pair_identity,
            reference_cell=payload.reference_cell,
            candidate_cell=payload.candidate_cell,
            result_sha256=result_sha256,
            result_path=f"parity/sha256/{result_sha256}.json",
            status=payload.result.status,
        )


class Phase5CorpusPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.phase5-corpus-plan"] = (
        "spanvouch.phase5-corpus-plan"
    )
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["pilot", "formal"]
    repetitions: int = Field(ge=3, le=20)
    experiment_config_sha256: str = Field(pattern=SHA256_PATTERN)
    ordered_cells: tuple[CorpusCell, ...] = Field(min_length=1)
    ordered_cells_sha256: str = Field(pattern=SHA256_PATTERN)
    plan_identity_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if len(set(self.ordered_cells)) != len(self.ordered_cells):
            raise ValueError("Phase 5 ordered plan cells must be unique")
        cells_payload = cast(
            JsonValue,
            [cell.model_dump(mode="json") for cell in self.ordered_cells],
        )
        if canonical_sha256(cells_payload) != self.ordered_cells_sha256:
            raise ValueError("ordered_cells_sha256 does not match Phase 5 plan cells")
        identity_payload = {
            "experiment_config_sha256": self.experiment_config_sha256,
            "mode": self.mode,
            "ordered_cells_sha256": self.ordered_cells_sha256,
            "repetitions": self.repetitions,
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
        }
        if canonical_sha256(cast(JsonValue, identity_payload)) != self.plan_identity_sha256:
            raise ValueError("plan_identity_sha256 does not match Phase 5 plan")

        expected_cells = 36 * 2 * self.repetitions
        if self.mode == "pilot" and self.repetitions != 3:
            raise ValueError("Phase 5 pilot plan requires exactly three repetitions")
        if self.mode == "formal" and self.repetitions < 5:
            raise ValueError("Phase 5 formal plan requires at least five repetitions")
        if len(self.ordered_cells) != expected_cells:
            raise ValueError("Phase 5 plan cell count does not match validated repetitions")

        pairs = tuple(zip(self.ordered_cells[::2], self.ordered_cells[1::2], strict=True))
        if any(
            reference.framework_id is not FrameworkId.LANGGRAPH
            or candidate.framework_id is not FrameworkId.AUTOGEN
            or reference.pair_identity != candidate.pair_identity
            for reference, candidate in pairs
        ):
            raise ValueError("Phase 5 ordered plan must contain adjacent framework pairs")
        scenarios: dict[tuple[str, str, str], set[int]] = {}
        for reference, _candidate in pairs:
            key = (reference.domain, reference.template_id, reference.scenario_id)
            scenarios.setdefault(key, set()).add(reference.repetition)
        if len(scenarios) != 36 or any(
            repetitions != set(range(1, self.repetitions + 1))
            for repetitions in scenarios.values()
        ):
            raise ValueError("Phase 5 plan repetition matrix is incomplete")
        return self

    @classmethod
    def from_cells(
        cls,
        *,
        mode: Literal["pilot", "formal"],
        repetitions: int,
        experiment_config_sha256: str,
        ordered_cells: tuple[CorpusCell, ...],
    ) -> Self:
        cells_sha256 = canonical_sha256(
            cast(JsonValue, [cell.model_dump(mode="json") for cell in ordered_cells])
        )
        identity_payload = {
            "experiment_config_sha256": experiment_config_sha256,
            "mode": mode,
            "ordered_cells_sha256": cells_sha256,
            "repetitions": repetitions,
            "schema_name": "spanvouch.phase5-corpus-plan",
            "schema_version": "1.0",
        }
        return cls(
            mode=mode,
            repetitions=repetitions,
            experiment_config_sha256=experiment_config_sha256,
            ordered_cells=ordered_cells,
            ordered_cells_sha256=cells_sha256,
            plan_identity_sha256=canonical_sha256(cast(JsonValue, identity_payload)),
        )


class CorpusManifestMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_id: str = Field(min_length=1)
    mode: Literal["pilot", "formal"]
    experiment_config_sha256: str = Field(pattern=SHA256_PATTERN)
    git_commit: str = Field(pattern=_GIT_COMMIT_PATTERN)
    dependency_lock_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    dirty_worktree: bool
    expected_cell_count: int = Field(ge=1)
    expected_pair_count: int = Field(ge=0)
    phase5_plan: Phase5CorpusPlan | None = None
    created_at_utc: datetime
    parity_results_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("created_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_at_utc must be UTC")
        return value

    @model_validator(mode="after")
    def validate_phase5_plan_binding(self) -> Self:
        is_phase5 = self.corpus_id.startswith("phase5-")
        if is_phase5 and self.phase5_plan is None:
            raise ValueError("Phase 5 corpus metadata requires a canonical plan")
        if not is_phase5 and self.phase5_plan is not None:
            raise ValueError("generic corpus metadata forbids a Phase 5 plan")
        if self.phase5_plan is None:
            return self
        plan = self.phase5_plan
        if self.mode != plan.mode:
            raise ValueError("Phase 5 corpus mode does not match its plan")
        if self.experiment_config_sha256 != plan.experiment_config_sha256:
            raise ValueError("Phase 5 experiment config hash does not match its plan")
        if self.expected_cell_count != len(plan.ordered_cells):
            raise ValueError("Phase 5 expected cell count does not match its plan")
        if self.expected_pair_count != len(plan.ordered_cells) // 2:
            raise ValueError("Phase 5 expected pair count does not match its plan")
        return self


class CorpusManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.trace-corpus"] = "spanvouch.trace-corpus"
    schema_version: Literal["1.0"] = "1.0"
    metadata: CorpusManifestMetadata
    entries: tuple[CorpusEntry, ...] = Field(min_length=1)
    parity_entries: tuple[CorpusParityEntry, ...] = ()
    records_sha256: str = Field(pattern=SHA256_PATTERN)
    traces_sha256: str = Field(pattern=SHA256_PATTERN)
    parity_payloads_sha256: str = Field(pattern=SHA256_PATTERN)
    payloads_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_entries_and_hashes(self) -> Self:
        cells = tuple(entry.cell for entry in self.entries)
        if len(cells) != self.metadata.expected_cell_count:
            raise ValueError("corpus entries do not match expected cell count")
        if len(set(cells)) != len(cells):
            raise ValueError("corpus cells must be unique")
        expected_entries = tuple(sorted(self.entries, key=lambda item: item.cell.sort_key()))
        if self.entries != expected_entries:
            raise ValueError("corpus entries must be sorted")
        if len(self.parity_entries) != self.metadata.expected_pair_count:
            raise ValueError("parity entries must provide complete corpus pair coverage")
        if self.metadata.corpus_id.startswith("phase5-") and (
            self.metadata.expected_cell_count != self.metadata.expected_pair_count * 2
        ):
            raise ValueError("Phase 5 corpus requires two cells per expected pair")
        pair_identities = tuple(entry.pair_identity for entry in self.parity_entries)
        if len(set(pair_identities)) != len(pair_identities):
            raise ValueError("parity pair identities must be unique")
        covered_cells = tuple(
            cell
            for entry in self.parity_entries
            for cell in (entry.reference_cell, entry.candidate_cell)
        )
        if self.parity_entries and (
            len(covered_cells) != len(cells) or set(covered_cells) != set(cells)
        ):
            raise ValueError("parity entries must provide complete corpus pair coverage")
        plan = self.metadata.phase5_plan
        if plan is not None:
            if set(plan.ordered_cells) != set(cells):
                raise ValueError("Phase 5 plan cells do not match corpus entries")
            planned_pairs = tuple(
                (reference, candidate)
                for reference, candidate in zip(
                    plan.ordered_cells[::2],
                    plan.ordered_cells[1::2],
                    strict=True,
                )
            )
            manifest_pairs = tuple(
                (entry.reference_cell, entry.candidate_cell)
                for entry in self.parity_entries
            )
            if manifest_pairs != planned_pairs:
                raise ValueError("Phase 5 parity entries do not match ordered plan pairs")
        (
            records_sha256,
            traces_sha256,
            parity_payloads_sha256,
            payloads_sha256,
        ) = self._derived_hashes(self.entries, self.parity_entries)
        if self.records_sha256 != records_sha256:
            raise ValueError("records_sha256 does not match corpus entries")
        if self.traces_sha256 != traces_sha256:
            raise ValueError("traces_sha256 does not match corpus entries")
        if self.parity_payloads_sha256 != parity_payloads_sha256:
            raise ValueError("parity_payloads_sha256 does not match parity entries")
        if self.payloads_sha256 != payloads_sha256:
            raise ValueError("payloads_sha256 does not match corpus entries")
        return self

    @classmethod
    def from_entries(
        cls,
        *,
        entries: tuple[CorpusEntry, ...],
        metadata: CorpusManifestMetadata,
        parity_entries: tuple[CorpusParityEntry, ...] = (),
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
        validated_parity_entries = tuple(
            CorpusParityEntry.model_validate(entry.model_dump(mode="python"))
            for entry in parity_entries
        )
        (
            records_sha256,
            traces_sha256,
            parity_payloads_sha256,
            payloads_sha256,
        ) = cls._derived_hashes(sorted_entries, validated_parity_entries)
        return cls(
            metadata=validated_metadata,
            entries=sorted_entries,
            parity_entries=validated_parity_entries,
            records_sha256=records_sha256,
            traces_sha256=traces_sha256,
            parity_payloads_sha256=parity_payloads_sha256,
            payloads_sha256=payloads_sha256,
        )

    @staticmethod
    def _derived_hashes(
        entries: tuple[CorpusEntry, ...],
        parity_entries: tuple[CorpusParityEntry, ...],
    ) -> tuple[str, str, str, str]:
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
                *(
                    {"path": entry.result_path, "sha256": entry.result_sha256}
                    for entry in parity_entries
                ),
            ),
            key=lambda item: item["path"],
        )
        return (
            canonical_sha256(cast(JsonValue, records)),
            canonical_sha256(cast(JsonValue, traces)),
            canonical_sha256(
                cast(JsonValue, sorted(entry.result_sha256 for entry in parity_entries))
            ),
            canonical_sha256(cast(JsonValue, payloads)),
        )
