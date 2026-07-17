import json
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
_MAX_ENCODED_JSON_DEPTH = 4
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
    r"(?i)(?P<prefix>\b(?:proxy[-_ ]?)?authorization(?:\\?[\"'])?\s*"
    r"(?:=|:)\s*)(?P<value>(?!\s*[\"']?\[REDACTED\][\"']?)"
    r"(?:[\"'][^\"'\r\n]*[\"']|(?:(?!;\s)[^\r\n])+))"
)
_BEARER = re.compile(
    r"(?i)(?P<prefix>\bbearer\s+)(?P<value>(?!\[REDACTED\])"
    r"[A-Za-z0-9._~+/=-]{20,})(?=$|[\s,;}\]])"
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


def _redact_token_shaped_bearer(match: re.Match[str]) -> str:
    value = match.group("value")
    shape_evidence = sum(
        character.isdigit() or character in "._~+/=-" for character in value
    )
    if shape_evidence < 2:
        return match.group(0)
    return f"{match.group('prefix')}{SECRET_REDACTION}"


def _canonical_json_value(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sanitize_string(value: str, *, encoded_json_depth: int) -> str:
    if encoded_json_depth < _MAX_ENCODED_JSON_DEPTH:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(decoded, (str, list, dict)):
                return _canonical_json_value(
                    _sanitize_diagnostic_value(
                        cast(JsonValue, decoded),
                        encoded_json_depth=encoded_json_depth + 1,
                    )
                )
    sanitized = _URL_USERINFO.sub(
        lambda match: f"{match.group('scheme')}{SECRET_REDACTION}@",
        value,
    )
    sanitized = _AUTHORIZATION.sub(_redact_match, sanitized)
    sanitized = _BEARER.sub(_redact_token_shaped_bearer, sanitized)
    sanitized = _ASSIGNMENT.sub(_redact_match, sanitized)
    return _PROVIDER_KEY.sub(SECRET_REDACTION, sanitized)


def _is_populated(value: JsonValue) -> bool:
    return value not in (None, "", [], {})


def _sanitize_diagnostic_value(
    value: JsonValue, *, encoded_json_depth: int
) -> JsonValue:
    if isinstance(value, str):
        return _sanitize_string(value, encoded_json_depth=encoded_json_depth)
    if isinstance(value, list):
        return [
            _sanitize_diagnostic_value(
                item, encoded_json_depth=encoded_json_depth
            )
            for item in value
        ]
    if isinstance(value, dict):
        sanitized: dict[str, JsonValue] = {}
        for key, item in value.items():
            sanitized[key] = (
                SECRET_REDACTION
                if _SENSITIVE_KEY.fullmatch(key) and _is_populated(item)
                else _sanitize_diagnostic_value(
                    item, encoded_json_depth=encoded_json_depth
                )
            )
        return sanitized
    return value


def sanitize_diagnostic_value(value: JsonValue) -> JsonValue:
    """Remove credential fragments from one JSON value without changing its shape."""

    return _sanitize_diagnostic_value(value, encoded_json_depth=0)


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
