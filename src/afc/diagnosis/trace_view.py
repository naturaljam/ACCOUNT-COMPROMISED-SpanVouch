import re
from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from afc.trace_ir.models import SpanKind, SpanStatus, TraceIR

ALLOWED_ATTRIBUTES = frozenset(
    {
        "run.outcome",
        "run.final_message",
        "tool.name",
        "tool.arguments.customer_id",
        "tool.arguments.order_id",
        "tool.arguments.item_skus",
        "tool.arguments.amount",
        "tool.arguments.approval",
        "tool.arguments.reason",
        "tool.result",
        "tool.error.type",
        "tool.error.message",
    }
)

SECRET_REDACTION = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?i)^(?:authorization|proxy[_-]?authorization|password|passwd|pwd|secret|"
    r"token|(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token))$"
)
_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|token|password|passwd|pwd|secret)[\"']?\s*(?:=|:)\s*)"
    r"(?P<value>(?![\"']?\[REDACTED\][\"']?)"
    r"(?:[\"'][^\"'\r\n]+[\"']|[^\s,;}\]]+))"
)
_AUTHORIZATION = re.compile(
    r"(?i)(?P<prefix>\b(?:proxy-)?authorization\s*(?:=|:)\s*"
    r"(?:bearer|basic)\s+)(?P<value>(?!\[REDACTED\])[^\s,;}\]]+)"
)
_BEARER = re.compile(
    r"(?i)(?P<prefix>\bbearer\s+)(?P<value>(?!\[REDACTED\])[^\s,;}\]]+)"
)
_PROVIDER_KEY = re.compile(
    r"(?<![A-Za-z0-9])(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{20,}(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:ghp|github_pat|xox[baprs])_[A-Za-z0-9_-]{16,}"
    r"(?![A-Za-z0-9])"
)
_URL_USERINFO = re.compile(
    r"(?i)(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@"
)


def _redact_match(match: re.Match[str]) -> str:
    value = match.group("value")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return f"{match.group('prefix')}{value[0]}{SECRET_REDACTION}{value[0]}"
    return f"{match.group('prefix')}{SECRET_REDACTION}"


def _sanitize_string(value: str) -> str:
    sanitized = _URL_USERINFO.sub(
        lambda match: f"{match.group('scheme')}{SECRET_REDACTION}@",
        value,
    )
    sanitized = _AUTHORIZATION.sub(_redact_match, sanitized)
    sanitized = _BEARER.sub(_redact_match, sanitized)
    sanitized = _ASSIGNMENT.sub(_redact_match, sanitized)
    return _PROVIDER_KEY.sub(SECRET_REDACTION, sanitized)


def _is_populated(value: JsonValue) -> bool:
    return value not in (None, "", [], {})


def sanitize_diagnostic_value(value: JsonValue) -> JsonValue:
    """Remove credential fragments from one JSON value without changing its shape."""

    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, list):
        return [sanitize_diagnostic_value(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, JsonValue] = {}
        for key, item in value.items():
            sanitized[key] = (
                SECRET_REDACTION
                if _SENSITIVE_KEY.fullmatch(key) and _is_populated(item)
                else sanitize_diagnostic_value(item)
            )
        return sanitized
    return value


class DiagnosticSpan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    name: str = Field(min_length=1)
    kind: SpanKind
    status: SpanStatus
    started_at: datetime
    ended_at: datetime
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("attributes", mode="before")
    @classmethod
    def sanitize_attributes(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return sanitize_diagnostic_value(cast(dict[str, JsonValue], value))


class DiagnosticTraceView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    spans: tuple[DiagnosticSpan, ...] = Field(min_length=1)

    @classmethod
    def from_trace(cls, trace: TraceIR) -> "DiagnosticTraceView":
        spans = tuple(
            DiagnosticSpan(
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                name=span.name,
                kind=span.kind,
                status=span.status,
                started_at=span.started_at,
                ended_at=span.ended_at,
                attributes={
                    key: value
                    for key, value in span.attributes.items()
                    if key in ALLOWED_ATTRIBUTES
                },
            )
            for span in sorted(
                trace.spans,
                key=lambda item: (item.started_at, item.ended_at, item.span_id),
            )
        )
        return cls(spans=spans)


def sanitize_diagnostic_trace_view(view: DiagnosticTraceView) -> DiagnosticTraceView:
    """Revalidate a diagnostic view through the central recursive sanitizer."""

    return DiagnosticTraceView.model_validate(view.model_dump(mode="python"))
