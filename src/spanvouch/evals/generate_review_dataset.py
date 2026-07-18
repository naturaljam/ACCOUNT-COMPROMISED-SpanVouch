import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spanvouch.diagnosis.evidence import EvidenceCatalog
from spanvouch.diagnosis.models import (
    ClaimStage,
    DiagnoserKind,
    DiagnosisClaim,
    DiagnosisReport,
    DiagnosisStatus,
    EvidenceRef,
    EvidenceSelector,
)
from spanvouch.diagnosis.rule_diagnoser import RuleDiagnoser
from spanvouch.diagnosis.service import DiagnosisService
from spanvouch.diagnosis.trace_view import DiagnosticTraceView
from spanvouch.evals.generate_dataset import DatasetManifest
from spanvouch.invariants.engine import InvariantEngine
from spanvouch.invariants.supportlab import supportlab_rules
from spanvouch.review.models import FindingCode, VerifierVerdict
from spanvouch.trace_ir.models import TraceIR

CANDIDATES_FILENAME = "review-candidates-v1.jsonl"
LABELS_FILENAME = "review-labels-v1.jsonl"
MANIFEST_FILENAME = "manifest.json"
DEFAULT_SOURCE_DATASET = Path("evals/datasets/supportlab-v1")
DEFAULT_OUTPUT_DATASET = Path("evals/datasets/supportlab-review-v1")
DEFAULT_SEED = 20260717
GENERATOR_VERSION = "supportlab-review-generator-v2"


class MutationKind(StrEnum):
    UNMODIFIED = "unmodified"
    INVALID_SELECTOR = "invalid_selector"
    EVIDENCE_VALUE_HASH_MISMATCH = "evidence_value_hash_mismatch"
    CLAIM_NOT_GROUNDED = "claim_not_grounded"
    CRITICAL_SPAN_NOT_GROUNDED = "critical_span_not_grounded"
    DIAGNOSIS_CONFLICT = "diagnosis_conflict"
    UNSUPPORTED_SCOPE = "unsupported_scope"


EXPECTED_MUTATION_COUNTS: Mapping[MutationKind, int] = MappingProxyType(
    {
        MutationKind.UNMODIFIED: 20,
        MutationKind.INVALID_SELECTOR: 2,
        MutationKind.EVIDENCE_VALUE_HASH_MISMATCH: 2,
        MutationKind.CLAIM_NOT_GROUNDED: 2,
        MutationKind.CRITICAL_SPAN_NOT_GROUNDED: 2,
        MutationKind.DIAGNOSIS_CONFLICT: 2,
        MutationKind.UNSUPPORTED_SCOPE: 6,
    }
)
EXPECTED_SOURCE_RUN_IDS = frozenset(
    {
        "clean-01",
        "clean-02",
        "clean-03",
        "clean-04",
        "context_corruption-01",
        "context_corruption-02",
        "ignored_tool_error-01",
        "ignored_tool_error-02",
        "invalid_argument-01",
        "invalid_argument-02",
        "invalid_final_state-01",
        "invalid_final_state-02",
        "loop_or_budget_exhaustion-01",
        "loop_or_budget_exhaustion-02",
        "missing_precondition-01",
        "missing_precondition-02",
        "policy_violation-01",
        "policy_violation-02",
        "wrong_tool-01",
        "wrong_tool-02",
    }
)
EXPECTED_UNSUPPORTED_SOURCE_RUN_IDS = frozenset(
    {
        "context_corruption-01",
        "context_corruption-02",
        "ignored_tool_error-01",
        "ignored_tool_error-02",
        "missing_precondition-01",
        "missing_precondition-02",
    }
)


class ReviewCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    mutation_kind: MutationKind
    report: DiagnosisReport

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = f"{self.source_run_id}--{self.mutation_kind.value}"
        if self.candidate_id != expected:
            raise ValueError("candidate_id must derive from source_run_id and mutation_kind")
        if self.report.run_id != self.source_run_id:
            raise ValueError("candidate report run_id must match source_run_id")
        return self


class ReviewDatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["supportlab-review-v1"] = "supportlab-review-v1"
    schema_version: Literal["1.0"] = "1.0"
    seed: int
    candidate_count: int = Field(ge=1)
    valid_count: int = Field(ge=1)
    mutation_count: int = Field(ge=1)
    candidates_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.candidate_count != self.valid_count + self.mutation_count:
            raise ValueError("candidate count must equal valid plus mutation counts")
        return self


def _canonical_line(value: BaseModel | dict[str, object]) -> str:
    row = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _write_jsonl(path: Path, rows: tuple[BaseModel | dict[str, object], ...]) -> str:
    content = "".join(_canonical_line(row) for row in rows)
    path.write_text(content, encoding="utf-8", newline="\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load_traces(path: Path) -> tuple[TraceIR, ...]:
    traces = tuple(
        TraceIR.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )
    run_ids = tuple(trace.run_id for trace in traces)
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("duplicate source trace run_id")
    return tuple(sorted(traces, key=lambda trace: trace.run_id))


def validate_source_dataset(source_dataset: Path) -> DatasetManifest:
    manifest_path = source_dataset / "manifest.json"
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.name,
        manifest.schema_version,
        manifest.seed,
        manifest.trace_count,
    ) != ("supportlab-v1", "1.0", 20260715, 20):
        raise ValueError("unexpected Phase 2 source manifest fields")
    traces_path = source_dataset / "traces.jsonl"
    labels_path = source_dataset / "labels.jsonl"
    if hashlib.sha256(traces_path.read_bytes()).hexdigest() != manifest.traces_sha256:
        raise ValueError("Phase 2 traces SHA-256 does not match source manifest")
    if hashlib.sha256(labels_path.read_bytes()).hexdigest() != manifest.labels_sha256:
        raise ValueError("Phase 2 labels SHA-256 does not match source manifest")
    if len(_load_traces(traces_path)) != manifest.trace_count:
        raise ValueError("Phase 2 trace count does not match source manifest")
    return manifest


def _with_evidence(report: DiagnosisReport, evidence: tuple[EvidenceRef, ...]) -> DiagnosisReport:
    return DiagnosisReport.model_validate(
        {**report.model_dump(mode="python"), "evidence": evidence}
    )


def mutate_invalid_selector(report: DiagnosisReport, trace: TraceIR) -> DiagnosisReport:
    del trace
    first = report.evidence[0]
    changed = first.model_copy(update={"field_path": f"{first.field_path}.missing"})
    return _with_evidence(report, (changed, *report.evidence[1:]))


def mutate_evidence_value_hash_mismatch(
    report: DiagnosisReport, trace: TraceIR
) -> DiagnosisReport:
    del trace
    first = report.evidence[0]
    changed = first.model_copy(
        update={"observed_value": "mutated-value", "value_sha256": "0" * 64}
    )
    return _with_evidence(report, (changed, *report.evidence[1:]))


def mutate_claim_not_grounded(report: DiagnosisReport, trace: TraceIR) -> DiagnosisReport:
    catalog = EvidenceCatalog.from_view(DiagnosticTraceView.from_trace(trace))
    critical_spans = set(report.critical_span_ids)
    decoy_canonical = next(
        selector
        for selector in catalog.selectors
        if selector.split("::", 1)[0] not in critical_spans
    )
    decoy_span_id, decoy_field_path = decoy_canonical.split("::", 1)
    decoy = catalog.resolve(
        EvidenceSelector(span_id=decoy_span_id, field_path=decoy_field_path),
        description="This evidence is outside the diagnosis critical span.",
    )
    claim = report.causal_chain[0]
    changed_claim = DiagnosisClaim(
        stage=claim.stage,
        statement=claim.statement,
        evidence_ids=(decoy.evidence_id,),
    )
    return DiagnosisReport.model_validate(
        {
            **report.model_dump(mode="python"),
            "causal_chain": (changed_claim, *report.causal_chain[1:]),
            "evidence": (*report.evidence, decoy),
        }
    )


def mutate_critical_span_not_grounded(
    report: DiagnosisReport, trace: TraceIR
) -> DiagnosisReport:
    grounded = {item.span_id for item in report.evidence}
    replacement = next(span.span_id for span in trace.spans if span.span_id not in grounded)
    return DiagnosisReport.model_validate(
        {
            **report.model_dump(mode="python"),
            "critical_span_ids": (*report.critical_span_ids, replacement),
        }
    )


def mutate_diagnosis_conflict(report: DiagnosisReport, trace: TraceIR) -> DiagnosisReport:
    del trace
    assert report.failure_type is not None
    replacement = (
        "invalid_argument" if report.failure_type.value != "invalid_argument" else "wrong_tool"
    )
    return DiagnosisReport.model_validate(
        {**report.model_dump(mode="python"), "failure_type": replacement}
    )


def mutate_unsupported_scope(report: DiagnosisReport, trace: TraceIR) -> DiagnosisReport:
    del trace
    evidence_ids = tuple(item.evidence_id for item in report.evidence)
    claim = DiagnosisClaim(
        stage=ClaimStage.CAUSE,
        statement="The selected tool caused the request to fail.",
        evidence_ids=evidence_ids,
    )
    return DiagnosisReport.model_validate(
        {
            **report.model_dump(mode="python"),
            "status": DiagnosisStatus.DIAGNOSED,
            "failure_type": "wrong_tool",
            "critical_span_ids": tuple(dict.fromkeys(item.span_id for item in report.evidence)),
            "causal_chain": (claim,),
            "confidence": 1.0,
            "abstain_reason": None,
        }
    )


_MUTATIONS: dict[
    str, tuple[MutationKind, Callable[[DiagnosisReport, TraceIR], DiagnosisReport]]
] = {
    "invalid_argument-01": (MutationKind.INVALID_SELECTOR, mutate_invalid_selector),
    "invalid_argument-02": (MutationKind.INVALID_SELECTOR, mutate_invalid_selector),
    "invalid_final_state-01": (
        MutationKind.EVIDENCE_VALUE_HASH_MISMATCH,
        mutate_evidence_value_hash_mismatch,
    ),
    "invalid_final_state-02": (
        MutationKind.EVIDENCE_VALUE_HASH_MISMATCH,
        mutate_evidence_value_hash_mismatch,
    ),
    "loop_or_budget_exhaustion-01": (
        MutationKind.CLAIM_NOT_GROUNDED,
        mutate_claim_not_grounded,
    ),
    "loop_or_budget_exhaustion-02": (
        MutationKind.CLAIM_NOT_GROUNDED,
        mutate_claim_not_grounded,
    ),
    "policy_violation-01": (
        MutationKind.CRITICAL_SPAN_NOT_GROUNDED,
        mutate_critical_span_not_grounded,
    ),
    "policy_violation-02": (
        MutationKind.CRITICAL_SPAN_NOT_GROUNDED,
        mutate_critical_span_not_grounded,
    ),
    "wrong_tool-01": (MutationKind.DIAGNOSIS_CONFLICT, mutate_diagnosis_conflict),
    "wrong_tool-02": (MutationKind.DIAGNOSIS_CONFLICT, mutate_diagnosis_conflict),
    "context_corruption-01": (MutationKind.UNSUPPORTED_SCOPE, mutate_unsupported_scope),
    "context_corruption-02": (MutationKind.UNSUPPORTED_SCOPE, mutate_unsupported_scope),
    "ignored_tool_error-01": (MutationKind.UNSUPPORTED_SCOPE, mutate_unsupported_scope),
    "ignored_tool_error-02": (MutationKind.UNSUPPORTED_SCOPE, mutate_unsupported_scope),
    "missing_precondition-01": (MutationKind.UNSUPPORTED_SCOPE, mutate_unsupported_scope),
    "missing_precondition-02": (MutationKind.UNSUPPORTED_SCOPE, mutate_unsupported_scope),
}
EXPECTED_MUTATION_KIND_BY_SOURCE: Mapping[str, MutationKind] = MappingProxyType(
    {run_id: mutation_kind for run_id, (mutation_kind, _) in _MUTATIONS.items()}
)


def _label_for(candidate: ReviewCandidate) -> dict[str, object]:
    expectations: dict[MutationKind, tuple[VerifierVerdict, tuple[FindingCode, ...]]] = {
        MutationKind.UNMODIFIED: (VerifierVerdict.VERIFIED, ()),
        MutationKind.INVALID_SELECTOR: (
            VerifierVerdict.NEEDS_EVIDENCE,
            (FindingCode.INVALID_SELECTOR,),
        ),
        MutationKind.EVIDENCE_VALUE_HASH_MISMATCH: (
            VerifierVerdict.REVIEW_REQUIRED,
            (FindingCode.EVIDENCE_VALUE_MISMATCH, FindingCode.EVIDENCE_HASH_MISMATCH),
        ),
        MutationKind.CLAIM_NOT_GROUNDED: (
            VerifierVerdict.NEEDS_EVIDENCE,
            (FindingCode.CLAIM_NOT_GROUNDED,),
        ),
        MutationKind.CRITICAL_SPAN_NOT_GROUNDED: (
            VerifierVerdict.NEEDS_EVIDENCE,
            (FindingCode.CRITICAL_SPAN_NOT_GROUNDED,),
        ),
        MutationKind.DIAGNOSIS_CONFLICT: (
            VerifierVerdict.REVIEW_REQUIRED,
            (FindingCode.DIAGNOSIS_CONFLICT,),
        ),
        MutationKind.UNSUPPORTED_SCOPE: (
            VerifierVerdict.REVIEW_REQUIRED,
            (FindingCode.UNSUPPORTED_SCOPE,),
        ),
    }
    verdict, codes = expectations[candidate.mutation_kind]
    if (
        candidate.mutation_kind is MutationKind.UNSUPPORTED_SCOPE
        and candidate.source_run_id.startswith("ignored_tool_error-")
    ):
        codes = (*codes, FindingCode.DIAGNOSIS_CONFLICT)
    return {
        "candidate_id": candidate.candidate_id,
        "expected_verdict": verdict.value,
        "expected_finding_codes": [code.value for code in codes],
    }


async def generate_review_dataset(
    output_dir: Path,
    seed: int = DEFAULT_SEED,
    *,
    source_dataset: Path = DEFAULT_SOURCE_DATASET,
) -> ReviewDatasetManifest:
    validate_source_dataset(source_dataset)
    traces = _load_traces(source_dataset / "traces.jsonl")
    service = DiagnosisService(
        {DiagnoserKind.RULES: RuleDiagnoser(InvariantEngine(supportlab_rules()))}
    )
    candidates: list[ReviewCandidate] = []
    for trace in traces:
        report = await service.diagnose(trace, DiagnoserKind.RULES)
        candidates.append(
            ReviewCandidate(
                candidate_id=f"{trace.run_id}--{MutationKind.UNMODIFIED.value}",
                source_run_id=trace.run_id,
                mutation_kind=MutationKind.UNMODIFIED,
                report=report,
            )
        )
        mutation = _MUTATIONS.get(trace.run_id)
        if mutation is not None:
            mutation_kind, apply_mutation = mutation
            mutated_report = apply_mutation(report, trace)
            candidate = ReviewCandidate(
                candidate_id=f"{trace.run_id}--{mutation_kind.value}",
                source_run_id=trace.run_id,
                mutation_kind=mutation_kind,
                report=mutated_report,
            )
            candidates.append(candidate)
    ordered = tuple(
        sorted(candidates, key=lambda item: (item.source_run_id, item.mutation_kind.value))
    )
    if Counter(item.mutation_kind for item in ordered) != Counter(
        EXPECTED_MUTATION_COUNTS
    ):
        raise ValueError("review cohort mutation family counts do not match contract")

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_hash = _write_jsonl(output_dir / CANDIDATES_FILENAME, ordered)
    labels_hash = _write_jsonl(
        output_dir / LABELS_FILENAME, tuple(_label_for(candidate) for candidate in ordered)
    )
    manifest = ReviewDatasetManifest(
        seed=seed,
        candidate_count=len(ordered),
        valid_count=sum(item.mutation_kind is MutationKind.UNMODIFIED for item in ordered),
        mutation_count=sum(item.mutation_kind is not MutationKind.UNMODIFIED for item in ordered),
        candidates_sha256=candidates_hash,
        labels_sha256=labels_hash,
        source_manifest_sha256=hashlib.sha256(
            (source_dataset / "manifest.json").read_bytes()
        ).hexdigest(),
        generator_version=GENERATOR_VERSION,
    )
    (output_dir / MANIFEST_FILENAME).write_text(
        _canonical_line(manifest), encoding="utf-8", newline="\n"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DATASET)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    arguments = parser.parse_args(argv)
    import asyncio

    asyncio.run(generate_review_dataset(arguments.output, arguments.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
