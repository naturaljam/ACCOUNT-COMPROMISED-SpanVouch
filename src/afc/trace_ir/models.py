from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


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


class TraceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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


class TraceIR(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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
        for span in self.spans:
            if span.trace_id != self.trace_id:
                raise ValueError("span trace_id does not match trace")
            if span.parent_span_id is not None and span.parent_span_id not in known_ids:
                raise ValueError(f"missing parent span: {span.parent_span_id}")
            if span.parent_span_id == span.span_id:
                raise ValueError("span cannot parent itself")
        return self

    def span_by_id(self, span_id: str) -> TraceSpan:
        for span in self.spans:
            if span.span_id == span_id:
                return span
        raise KeyError(span_id)
