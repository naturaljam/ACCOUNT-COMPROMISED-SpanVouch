from hashlib import sha256
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spanvouch.diagnosis.evidence import EvidenceCatalog
from spanvouch.diagnosis.models import DiagnosisStatus, EvidenceSelector
from spanvouch.diagnosis.trace_view import DiagnosticTraceView
from spanvouch.failure_types import SUPPORTED_DIAGNOSIS_FAILURE_TYPES, FailureType
from spanvouch.trace_ir.models import TraceIR


class DiagnosisGoldLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    failure_type: FailureType
    expected_status: DiagnosisStatus
    acceptable_critical_span_ids: tuple[str, ...] = ()
    acceptable_evidence: tuple[EvidenceSelector, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if self.expected_status is DiagnosisStatus.DIAGNOSED:
            if self.failure_type not in SUPPORTED_DIAGNOSIS_FAILURE_TYPES:
                raise ValueError("diagnosed gold label requires supported failure type")
            if not self.acceptable_critical_span_ids or not self.acceptable_evidence:
                raise ValueError("diagnosed gold label requires critical spans and evidence")
        elif self.expected_status is DiagnosisStatus.NO_FAILURE:
            if self.failure_type is not FailureType.NO_FAILURE:
                raise ValueError("no_failure gold label requires no_failure type")
            if self.acceptable_critical_span_ids or self.acceptable_evidence:
                raise ValueError("no_failure gold label forbids failure evidence")
        else:
            if self.failure_type in SUPPORTED_DIAGNOSIS_FAILURE_TYPES or (
                self.failure_type is FailureType.NO_FAILURE
            ):
                raise ValueError("abstained gold label requires unsupported failure type")
            if self.acceptable_critical_span_ids or self.acceptable_evidence:
                raise ValueError("abstained gold label forbids supported evidence targets")
        return self


class DiagnosisDatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["supportlab-v1-diagnosis-labels"] = "supportlab-v1-diagnosis-labels"
    schema_version: Literal["1.0"] = "1.0"
    label_count: int = Field(ge=1)
    labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def load_diagnosis_labels(path: Path) -> tuple[DiagnosisGoldLabel, ...]:
    labels = tuple(
        DiagnosisGoldLabel.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )
    run_ids = [label.run_id for label in labels]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("duplicate diagnosis label run_id")
    return labels


def validate_dataset_join(
    traces: tuple[TraceIR, ...], labels: tuple[DiagnosisGoldLabel, ...]
) -> None:
    traces_by_run = {trace.run_id: trace for trace in traces}
    labels_by_run = {label.run_id: label for label in labels}
    if set(traces_by_run) != set(labels_by_run):
        missing = sorted(set(traces_by_run) - set(labels_by_run))
        extra = sorted(set(labels_by_run) - set(traces_by_run))
        raise ValueError(f"diagnosis label join mismatch: missing={missing}, extra={extra}")
    for run_id, label in labels_by_run.items():
        trace = traces_by_run[run_id]
        span_ids = {span.span_id for span in trace.spans}
        if not set(label.acceptable_critical_span_ids) <= span_ids:
            raise ValueError(f"unknown critical span in label: {run_id}")
        selectors = set(
            EvidenceCatalog.from_view(DiagnosticTraceView.from_trace(trace)).selectors
        )
        if not {item.canonical for item in label.acceptable_evidence} <= selectors:
            raise ValueError(f"unknown evidence selector in label: {run_id}")


def build_diagnosis_manifest(path: Path) -> DiagnosisDatasetManifest:
    content = path.read_bytes()
    return DiagnosisDatasetManifest(
        label_count=len(load_diagnosis_labels(path)),
        labels_sha256=sha256(content).hexdigest(),
    )
