import json
import re
from dataclasses import dataclass, field
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
_MAX_ENCODED_JSON_DEPTH = 8
_MAX_STRUCTURE_DEPTH = 64
_MAX_VALUE_NODES = 10_000
_MAX_ENCODED_STRING_BYTES = 262_144
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATOR = re.compile(r"[^a-z0-9]+")
_LABEL_DELIMITERS = " \t\r\n:=\"'"
_NON_SECRET_METADATA_PARTS = frozenset(
    {
        "age",
        "count",
        "counts",
        "duration",
        "enabled",
        "expires",
        "expiry",
        "id",
        "length",
        "name",
        "policy",
        "required",
        "rotation",
        "status",
        "timeout",
        "ttl",
        "type",
        "version",
    }
)
_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>[\"']?(?P<key>[a-z][a-z0-9_. -]{0,80})[\"']?"
    r"\s*(?:=|:)\s*)"
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
    r"[A-Za-z0-9._~+/=-]+)(?=$|[\"',;|)}\]])"
)
_PROVIDER_KEY = re.compile(
    r"(?<![A-Za-z0-9])(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{20,}(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:ghp|github_pat|xox[baprs])_[A-Za-z0-9_-]{16,}"
    r"(?![A-Za-z0-9])"
)
_URL_USERINFO = re.compile(r"(?i)(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@")


@dataclass
class _SanitizationBudget:
    """Fail-closed budget: 8 encodings, depth 64, 10k nodes, 256 KiB strings."""

    remaining_nodes: int = _MAX_VALUE_NODES
    active_container_ids: set[int] = field(default_factory=set)

    def claim_node(self) -> None:
        if self.remaining_nodes <= 0:
            raise _SanitizationBudgetExhausted
        self.remaining_nodes -= 1


class _SanitizationBudgetExhausted(Exception):
    """Abort the whole sanitation boundary as soon as its budget is spent."""


def _normalize_credential_label(label: str) -> tuple[str, ...]:
    stripped = label.strip(_LABEL_DELIMITERS)
    separated = _CAMEL_BOUNDARY.sub("_", stripped).lower()
    return tuple(part for part in _KEY_SEPARATOR.split(separated) if part)


def _has_only_metadata_suffix(parts: tuple[str, ...], index: int) -> bool:
    suffix = parts[index + 1 :]
    return bool(suffix) and all(part in _NON_SECRET_METADATA_PARTS for part in suffix)


def _is_compact_credential_label(compact: str) -> bool:
    credential_endings = (
        "authorization",
        "apikey",
        "privatekey",
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "credential",
        "cookie",
        "cookies",
    )
    return any(
        compact.endswith(ending) or compact.endswith(f"{ending}value")
        for ending in credential_endings
    )


def _is_credential_label(label: str) -> bool:
    """Classify a mapping/assignment label after syntax normalization."""

    parts = _normalize_credential_label(label)
    if not parts:
        return False
    compact = "".join(parts)

    credential_cores = {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "credential",
    }
    for index, part in enumerate(parts):
        if part in credential_cores:
            return not _has_only_metadata_suffix(parts, index)

    for first, second in (("api", "key"), ("private", "key")):
        for index in range(len(parts) - 1):
            if parts[index : index + 2] == (first, second):
                return not _has_only_metadata_suffix(parts, index + 1)

    cookie_indexes = tuple(
        index for index, part in enumerate(parts) if part in {"cookie", "cookies"}
    )
    if any(not _has_only_metadata_suffix(parts, index) for index in cookie_indexes):
        return True
    return _is_compact_credential_label(compact)


def _is_credential_mapping_key(key: str) -> bool:
    """Classify a structural key, excluding prose assignments stored as keys."""

    stripped = key.strip().strip("\"'").strip()
    without_trailing_delimiter = stripped.rstrip(":=").rstrip()
    if ":" in without_trailing_delimiter or "=" in without_trailing_delimiter:
        return False
    return _is_credential_label(stripped)


def _redact_match(match: re.Match[str]) -> str:
    value = match.group("value")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return f"{match.group('prefix')}{value[0]}{SECRET_REDACTION}{value[0]}"
    return f"{match.group('prefix')}{SECRET_REDACTION}"


def _redact_assignment(match: re.Match[str]) -> str:
    if not _is_credential_label(match.group("key")):
        return match.group(0)
    return _redact_match(match)


def _redact_bearer(match: re.Match[str]) -> str:
    token = match.group("value")
    sentence_terminal = token.endswith(".") and token[:-1].isalpha()
    token_body = token[:-1] if sentence_terminal else token
    credential_punctuation = any(character in "_~+/=-" for character in token_body)
    internal_period = "." in token_body
    digit_shaped = any(character.isdigit() for character in token_body)
    opaque_letters = token_body.isalpha() and len(token_body) >= 16
    prefix = match.string[: match.start()].rstrip()
    assignment_context = prefix.endswith(("=", ":"))
    if (
        opaque_letters
        or credential_punctuation
        or internal_period
        or digit_shaped
        or assignment_context
    ):
        return _redact_match(match)
    return match.group(0)


def _canonical_json_value(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _looks_structurally_encoded(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 2:
        return False
    return (stripped[0], stripped[-1]) in {("{", "}"), ("[", "]"), ('"', '"')}


def _sanitize_plain_string(value: str) -> str:
    """Sanitize credential text without attempting structured decoding."""

    sanitized = _URL_USERINFO.sub(
        lambda match: f"{match.group('scheme')}{SECRET_REDACTION}@",
        value,
    )
    sanitized = _AUTHORIZATION.sub(_redact_match, sanitized)
    sanitized = _BEARER.sub(_redact_bearer, sanitized)
    sanitized = _ASSIGNMENT.sub(_redact_assignment, sanitized)
    return _PROVIDER_KEY.sub(SECRET_REDACTION, sanitized)


def _sanitize_string(
    value: str,
    *,
    budget: _SanitizationBudget,
    depth: int,
    encoded_json_depth: int,
) -> str:
    if len(value) > _MAX_ENCODED_STRING_BYTES:
        return SECRET_REDACTION
    encoded_size = len(value.encode("utf-8", errors="replace"))
    if (
        encoded_json_depth >= _MAX_ENCODED_JSON_DEPTH
        or encoded_size > _MAX_ENCODED_STRING_BYTES
        or depth >= _MAX_STRUCTURE_DEPTH
    ):
        return SECRET_REDACTION

    if _looks_structurally_encoded(value):
        budget.claim_node()
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, RecursionError, ValueError):
            pass
        else:
            if isinstance(decoded, (str, list, dict)):
                return _canonical_json_value(
                    _sanitize_diagnostic_value(
                        cast(JsonValue, decoded),
                        budget=budget,
                        depth=depth + 1,
                        encoded_json_depth=encoded_json_depth + 1,
                    )
                )
    return _sanitize_plain_string(value)


def _is_populated(value: JsonValue) -> bool:
    return value not in (None, "", [], {})


def _sanitize_diagnostic_value(
    value: JsonValue,
    *,
    budget: _SanitizationBudget,
    depth: int,
    encoded_json_depth: int,
) -> JsonValue:
    budget.claim_node()
    if isinstance(value, str):
        return _sanitize_string(
            value,
            budget=budget,
            depth=depth,
            encoded_json_depth=encoded_json_depth,
        )
    if isinstance(value, list):
        identity = id(value)
        if identity in budget.active_container_ids or depth >= _MAX_STRUCTURE_DEPTH:
            return SECRET_REDACTION
        budget.active_container_ids.add(identity)
        try:
            sanitized_items: list[JsonValue] = []
            for item in value:
                sanitized_items.append(
                    _sanitize_diagnostic_value(
                        item,
                        budget=budget,
                        depth=depth + 1,
                        encoded_json_depth=encoded_json_depth,
                    )
                )
            return sanitized_items
        finally:
            budget.active_container_ids.remove(identity)
    if isinstance(value, dict):
        identity = id(value)
        if identity in budget.active_container_ids or depth >= _MAX_STRUCTURE_DEPTH:
            return SECRET_REDACTION
        budget.active_container_ids.add(identity)
        sanitized: dict[str, JsonValue] = {}
        try:
            for key, item in value.items():
                budget.claim_node()
                sanitized_key = _sanitize_string(
                    key,
                    budget=budget,
                    depth=depth + 1,
                    encoded_json_depth=encoded_json_depth,
                )
                if _is_credential_mapping_key(key) and _is_populated(item):
                    budget.claim_node()
                    sanitized_item: JsonValue = SECRET_REDACTION
                else:
                    sanitized_item = _sanitize_diagnostic_value(
                        item,
                        budget=budget,
                        depth=depth + 1,
                        encoded_json_depth=encoded_json_depth,
                    )
                if sanitized_key in sanitized:
                    sanitized[sanitized_key] = SECRET_REDACTION
                else:
                    sanitized[sanitized_key] = sanitized_item
            return sanitized
        finally:
            budget.active_container_ids.remove(identity)
    return value


def sanitize_diagnostic_value(value: JsonValue) -> JsonValue:
    """Sanitize one JSON value under bounded recursive, encoded inspection."""

    try:
        return _sanitize_diagnostic_value(
            value,
            budget=_SanitizationBudget(),
            depth=0,
            encoded_json_depth=0,
        )
    except _SanitizationBudgetExhausted:
        return SECRET_REDACTION


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

    @field_validator("span_id", "parent_span_id", "name", mode="before")
    @classmethod
    def sanitize_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return sanitize_diagnostic_value(value)
        return value

    @field_validator("attributes", mode="before")
    @classmethod
    def sanitize_attributes(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        sanitized = sanitize_diagnostic_value(cast(dict[str, JsonValue], value))
        if isinstance(sanitized, dict):
            return sanitized
        return {"sanitization": SECRET_REDACTION}


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
