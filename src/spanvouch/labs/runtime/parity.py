from __future__ import annotations

from pydantic import JsonValue

from spanvouch.contracts.trace import SpanKind, TraceSpan
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.labs.runtime.models import (
    ExecutionRecord,
    ParityDimension,
    ParityMismatch,
    ParityResult,
    RuntimeFailure,
    RuntimeFailureCategory,
)

_DIMENSION_ORDER = tuple(ParityDimension)


class ScenarioParityValidator:
    def validate(
        self,
        reference: ExecutionRecord,
        candidate: ExecutionRecord,
    ) -> ParityResult:
        incompatibility = _typed_incompatibility(reference, candidate)
        if incompatibility is not None:
            return ParityResult(
                status="incompatible",
                framework_incompatibility=incompatibility,
                incompatibility_code=incompatibility.code,
            )

        reference_payloads = _dimension_payloads(reference)
        candidate_payloads = _dimension_payloads(candidate)
        mismatches = tuple(
            ParityMismatch(
                dimension=dimension,
                reference_sha256=canonical_sha256(reference_payloads[dimension]),
                candidate_sha256=canonical_sha256(candidate_payloads[dimension]),
            )
            for dimension in _DIMENSION_ORDER
            if reference_payloads[dimension] != candidate_payloads[dimension]
        )
        if mismatches:
            return ParityResult(status="mismatched", mismatches=mismatches)
        return ParityResult(status="matched")


def _typed_incompatibility(
    reference: ExecutionRecord,
    candidate: ExecutionRecord,
) -> RuntimeFailure | None:
    for record in (reference, candidate):
        failure = record.failure
        if (
            failure is not None
            and failure.category
            is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
        ):
            return failure
    return None


def _dimension_payloads(
    record: ExecutionRecord,
) -> dict[ParityDimension, JsonValue]:
    tools = tuple(
        span for span in record.trace.spans if span.kind is SpanKind.TOOL
    )
    return {
        ParityDimension.SCENARIO_INPUT: {
            "scenario_id": record.scenario_id,
            "template_id": record.template_id,
            "domain": record.domain,
            "failure_family": record.failure_family,
            "scenario_input_sha256": record.scenario_input_sha256,
        },
        ParityDimension.TOOL_SEQUENCE: [
            {
                "span_name": span.name,
                "tool_name": span.attributes.get("tool.name"),
            }
            for span in tools
        ],
        ParityDimension.TOOL_ARGUMENTS: [
            _attributes_with_prefix(span, "tool.arguments.") for span in tools
        ],
        ParityDimension.TOOL_RESULTS: [
            {
                "status": span.status.value,
                "result": span.attributes.get("tool.result"),
                "error_type": span.attributes.get("tool.error.type"),
                "error_message": span.attributes.get("tool.error.message"),
            }
            for span in tools
        ],
        ParityDimension.INJECTION_TRIGGER: {
            "injection_trigger_sha256": record.injection_trigger_sha256,
            "trace_markers": [
                markers
                for span in record.trace.spans
                if (markers := _attributes_with_prefix(span, "injection."))
            ],
        },
        ParityDimension.RUNTIME_LIMIT: {
            "seed": record.seed,
            "repetition": record.repetition,
            "runtime_config": record.runtime_config.model_dump(mode="json"),
            "runtime_config_sha256": record.runtime_config_sha256,
        },
        ParityDimension.TERMINAL_PREDICATE: record.terminal_predicate_sha256,
        ParityDimension.OUTCOME: {
            "status": record.status.value,
            "failure": (
                record.failure.model_dump(mode="json")
                if record.failure is not None
                else None
            ),
            "final_message": record.final_message,
            "semantic_spans": [
                _normalized_semantic_span(span)
                for span in record.trace.spans
                if span.kind is not SpanKind.TOOL
                and not _is_framework_only_workflow(span)
            ],
        },
        ParityDimension.EVIDENCE_SELECTOR: record.evidence_selector_sha256,
    }


def _attributes_with_prefix(span: TraceSpan, prefix: str) -> dict[str, JsonValue]:
    return {
        key: value
        for key, value in sorted(span.attributes.items())
        if key.startswith(prefix)
    }


def _normalized_semantic_span(span: TraceSpan) -> dict[str, JsonValue]:
    return {
        "name": span.name,
        "kind": span.kind.value,
        "status": span.status.value,
        "attributes": {
            key: value for key, value in sorted(span.attributes.items())
        },
    }


def _is_framework_only_workflow(span: TraceSpan) -> bool:
    return span.kind is SpanKind.WORKFLOW and not span.attributes
