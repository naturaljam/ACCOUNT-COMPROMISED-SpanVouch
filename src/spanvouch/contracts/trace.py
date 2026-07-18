from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from spanvouch.contracts.versioning import ContractModel, ContractRoot


class SpanKind(StrEnum):
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    APPROVAL = "approval"
    WORKFLOW = "workflow"


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    UNSET = "unset"


class TraceSpan(ContractModel):
    trace_id: str = Field(min_length=1)
    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    name: str = Field(min_length=1)
    kind: SpanKind
    status: SpanStatus = SpanStatus.UNSET
    started_at: datetime
    ended_at: datetime
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("started_at and ended_at must be timezone-aware")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        return self


class TraceIR(ContractRoot):
    schema_name: Literal["spanvouch.trace"] = "spanvouch.trace"
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    spans: list[TraceSpan] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_span_tree(self) -> Self:
        ids = [span.span_id for span in self.spans]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate span_id in trace")
        known_ids = set(ids)
        parent_by_id: dict[str, str | None] = {}
        for span in self.spans:
            if span.trace_id != self.trace_id:
                raise ValueError("span trace_id does not match trace")
            if span.parent_span_id is not None and span.parent_span_id not in known_ids:
                raise ValueError(f"missing parent span: {span.parent_span_id}")
            if span.parent_span_id == span.span_id:
                raise ValueError("span cannot parent itself")
            parent_by_id[span.span_id] = span.parent_span_id

        colors = {span_id: 0 for span_id in known_ids}
        for start_id in known_ids:
            if colors[start_id] == 2:
                continue
            path: list[str] = []
            current_id: str | None = start_id
            while current_id is not None and colors[current_id] != 2:
                if colors[current_id] == 1:
                    raise ValueError("span parent cycle detected")
                colors[current_id] = 1
                path.append(current_id)
                current_id = parent_by_id[current_id]
            for span_id in path:
                colors[span_id] = 2

        root_ids = [span_id for span_id, parent_id in parent_by_id.items() if parent_id is None]
        if len(root_ids) != 1:
            raise ValueError("trace must contain exactly one root span")

        children_by_id: dict[str, list[str]] = {span_id: [] for span_id in known_ids}
        for span_id, parent_id in parent_by_id.items():
            if parent_id is not None:
                children_by_id[parent_id].append(span_id)
        reachable_ids: set[str] = set()
        pending_ids = [root_ids[0]]
        while pending_ids:
            span_id = pending_ids.pop()
            if span_id in reachable_ids:
                continue
            reachable_ids.add(span_id)
            pending_ids.extend(children_by_id[span_id])
        if reachable_ids != known_ids:
            raise ValueError("all spans must be reachable from root")
        return self

    def span_by_id(self, span_id: str) -> TraceSpan:
        for span in self.spans:
            if span.span_id == span_id:
                return span
        raise KeyError(span_id)


class DiagnosticSpan(ContractModel):
    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    name: str = Field(min_length=1)
    kind: SpanKind
    status: SpanStatus
    started_at: datetime
    ended_at: datetime
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class DiagnosticTraceView(ContractModel):
    spans: tuple[DiagnosticSpan, ...] = Field(min_length=1)


class DiagnosticContext(ContractRoot):
    schema_name: Literal["spanvouch.diagnostic-context"] = (
        "spanvouch.diagnostic-context"
    )
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    view: DiagnosticTraceView
