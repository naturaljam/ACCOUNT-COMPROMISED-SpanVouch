import json
import re
import unicodedata
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
_SAFE_METADATA_TERMINALS = frozenset(
    {
        "age",
        "algorithm",
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
_SAFE_METADATA_CHAIN_PARTS = _SAFE_METADATA_TERMINALS | {
    "credential",
    "credentials",
    "hash",
    "key",
    "string",
    "value",
}
_COMPACT_CREDENTIAL_CORES = frozenset(
    {
        "accesskey",
        "apikey",
        "auth",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "password",
        "passwd",
        "privatekey",
        "pwd",
        "secret",
        "sessioncookie",
        "sessioncredential",
        "sessiontoken",
        "token",
    }
)
_COMPACT_SENSITIVE_PREFIXES = frozenset(
    {
        "",
        "access",
        "account",
        "app",
        "application",
        "auth",
        "client",
        "database",
        "db",
        "deepseek",
        "header",
        "headers",
        "http",
        "id",
        "model",
        "openai",
        "provider",
        "proxy",
        "refresh",
        "request",
        "service",
        "session",
        "set",
        "user",
        "x",
    }
)
_COMPACT_SENSITIVE_SUFFIXES = frozenset(
    {"", "credential", "credentials", "hash", "key", "string", "value"}
)
_CREDENTIAL_PART_CORES = frozenset(
    {
        "auth",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
    }
)
_CREDENTIAL_PAIR_CORES = frozenset(
    {("access", "key"), ("api", "key"), ("private", "key")}
)
_COMPACT_CREDENTIAL_LABELS = frozenset(
    f"{prefix}{core}{suffix}"
    for prefix in _COMPACT_SENSITIVE_PREFIXES
    for core in _COMPACT_CREDENTIAL_CORES
    for suffix in _COMPACT_SENSITIVE_SUFFIXES
)
_MAX_COMPACT_CREDENTIAL_LABEL_LENGTH = max(
    len(label) for label in _COMPACT_CREDENTIAL_LABELS
)
_COMPACT_SAFE_METADATA_BASES = _COMPACT_CREDENTIAL_CORES | {
    "session",
    "tokenization",
    "tokenizer",
}
_COMPACT_SAFE_METADATA_QUALIFIERS = frozenset(
    {"", "credential", "hash", "key", "rotation", "string", "value"}
)
_COMPACT_SAFE_METADATA_LABELS = frozenset(
    f"{prefix}{base}{qualifier}{terminal}"
    for prefix in _COMPACT_SENSITIVE_PREFIXES
    for base in _COMPACT_SAFE_METADATA_BASES
    for qualifier in _COMPACT_SAFE_METADATA_QUALIFIERS
    for terminal in _SAFE_METADATA_TERMINALS
)
_NONEMPTY_LINE = re.compile(r"[^\r\n]+")
_COOKIE_PAIR_VALUE = re.compile(
    r"(?i)(?:^|;\s*)[!#$%&'*+\-.^_`|~0-9A-Za-z]+\s*="
)
_COOKIE_TOKEN_VALUE = re.compile(r"(?i)^[A-Za-z0-9._~+/=-]+$")
_REDACTED_VALUE_PREFIX = re.compile(r"^[\"']?\[REDACTED\]")
_BEARER = re.compile(
    r"(?i)(?P<prefix>\bbearer\s+)(?P<value>(?!\[REDACTED\])"
    r"[A-Za-z0-9._~+/=-]+)(?=$|[\"',;|)}\]])"
)
_BEARER_STATUS_CONTEXT = re.compile(
    r"(?i)^\s*[;,|]?\s*(?:HTTP(?:/\d(?:\.\d)?)?\s+|status\s*(?:=|:)\s*)"
    r"[45]\d\d\b"
)
_PROVIDER_KEY = re.compile(
    r"(?<![A-Za-z0-9])(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{20,}(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:ghp|github_pat|xox[baprs])_[A-Za-z0-9_-]{16,}"
    r"(?![A-Za-z0-9])"
)
_URL_SCHEME_COLONS = ":\N{PRESENTATION FORM FOR VERTICAL COLON}\N{SMALL COLON}\N{FULLWIDTH COLON}"
_URL_SCHEME_PATTERN = (
    rf"\b[a-z][a-z0-9+.-]*[{_URL_SCHEME_COLONS}](?:(?:\\)?/){{2}}"
)
_URL_SCHEME = re.compile(rf"(?i){_URL_SCHEME_PATTERN}")
_URL_USERINFO = re.compile(
    rf"(?i)(?P<scheme>{_URL_SCHEME_PATTERN})"
    r"(?P<userinfo>[^/@\s]+)@"
)
_STRUCTURAL_FIELD_BOUNDARIES = frozenset(";,|")
_URL_AUTHORITY_TERMINATORS = frozenset("/?#")
_URL_LABEL_BOUNDARIES = frozenset("/?#&")


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


def _is_safe_metadata_tail(
    length: int,
    last_part: str | None,
    all_parts_are_metadata: bool,
) -> bool:
    return (
        length > 0
        and last_part in _SAFE_METADATA_TERMINALS
        and all_parts_are_metadata
    )


def _has_only_metadata_suffix(parts: tuple[str, ...], index: int) -> bool:
    suffix = parts[index + 1 :]
    return _is_safe_metadata_tail(
        len(suffix),
        suffix[-1] if suffix else None,
        all(part in _SAFE_METADATA_CHAIN_PARTS for part in suffix),
    )


def _is_compact_credential_label(compact: str) -> bool:
    if compact in _COMPACT_SAFE_METADATA_LABELS:
        return False
    return compact in _COMPACT_CREDENTIAL_LABELS


def _is_credential_core_end(previous_part: str | None, part: str) -> bool:
    return part in _CREDENTIAL_PART_CORES or (
        previous_part is not None
        and (previous_part, part) in _CREDENTIAL_PAIR_CORES
    )


def _credential_core_ends_at(parts: tuple[str, ...], index: int) -> bool:
    previous_part = parts[index - 1] if index > 0 else None
    return _is_credential_core_end(previous_part, parts[index])


def _first_credential_core_end(parts: tuple[str, ...]) -> int | None:
    return next(
        (
            index
            for index in range(len(parts))
            if _credential_core_ends_at(parts, index)
        ),
        None,
    )


def _is_credential_label_parts(parts: tuple[str, ...]) -> bool:
    if not parts:
        return False
    core_end = _first_credential_core_end(parts)
    if core_end is not None:
        return not _has_only_metadata_suffix(parts, core_end)

    compact_length = sum(len(part) for part in parts)
    if compact_length > _MAX_COMPACT_CREDENTIAL_LABEL_LENGTH:
        return False
    return _is_compact_credential_label("".join(parts))


def _is_credential_label(label: str) -> bool:
    """Classify a mapping/assignment label after syntax normalization."""

    return _is_credential_label_parts(_normalize_credential_label(label))


def _is_credential_mapping_key(key: str) -> bool:
    """Classify every structural key after fail-closed syntax normalization."""

    return _is_credential_label(key)


def _redact_match(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.strip("\"' ") == SECRET_REDACTION:
        return match.group(0)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return f"{match.group('prefix')}{value[0]}{SECRET_REDACTION}{value[0]}"
    return f"{match.group('prefix')}{SECRET_REDACTION}"


def _unwrap_structural_value(value: str) -> tuple[str, str | None, str]:
    candidate = value.strip()
    structural_suffix = ""
    while (
        candidate
        and candidate != SECRET_REDACTION
        and candidate[-1] in "}])"
    ):
        structural_suffix = f"{candidate[-1]}{structural_suffix}"
        candidate = candidate[:-1].rstrip()
    for wrapper in ('\\"', "\\'", '"', "'"):
        if len(candidate) >= 2 * len(wrapper) and candidate.startswith(
            wrapper
        ) and candidate.endswith(wrapper):
            return (
                candidate[len(wrapper) : -len(wrapper)].strip(),
                wrapper,
                structural_suffix,
            )
    return candidate, None, structural_suffix


def _is_credential_shaped_cookie_value(value: str) -> bool:
    candidate, _, _ = _unwrap_structural_value(value)
    primary_value = candidate.split(";", 1)[0].strip()
    shaped_token = bool(_COOKIE_TOKEN_VALUE.fullmatch(primary_value)) and (
        len(primary_value) >= 16
        or any(
            character.isdigit() or character in "._~+/=-"
            for character in primary_value
        )
    )
    return bool(
        _REDACTED_VALUE_PREFIX.match(candidate)
        or _COOKIE_PAIR_VALUE.search(candidate)
        or shaped_token
    )


def _compact_credential_suffix_parts(
    chunks: tuple[tuple[str, ...], ...],
    boundary: int,
) -> tuple[str, ...] | None:
    compact_suffix = ""
    suffix_parts: tuple[str, ...] = ()
    for chunk in reversed(chunks[boundary:]):
        compact_suffix = f"{''.join(chunk)}{compact_suffix}"
        if len(compact_suffix) > _MAX_COMPACT_CREDENTIAL_LABEL_LENGTH:
            return None
        suffix_parts = (*chunk, *suffix_parts)
        if _is_compact_credential_label(compact_suffix):
            return suffix_parts
    return None


def _structural_credential_label_parts(
    label: str,
) -> tuple[str, ...] | None:
    chunks = tuple(
        parts
        for chunk in label.split()
        if (parts := _normalize_credential_label(chunk))
    )
    if not chunks:
        return None
    full_parts = tuple(part for chunk in chunks for part in chunk)
    if _is_credential_label_parts(full_parts):
        return full_parts
    return _compact_credential_suffix_parts(chunks, 0)


def _redact_structural_value(prefix: str, value: str) -> str:
    candidate, wrapper, structural_suffix = _unwrap_structural_value(value)
    if candidate.strip() == SECRET_REDACTION:
        return f"{prefix}{value}"
    if wrapper is not None:
        return (
            f"{prefix}{wrapper}{SECRET_REDACTION}{wrapper}{structural_suffix}"
        )
    return f"{prefix}{SECRET_REDACTION}{structural_suffix}"


def _is_structural_field_boundary(character: str) -> bool:
    if character in _STRUCTURAL_FIELD_BOUNDARIES:
        return True
    if character in " \t":
        return False
    if character.isspace() or unicodedata.category(character).startswith("Z"):
        return True
    if unicodedata.normalize("NFKC", character) in _STRUCTURAL_FIELD_BOUNDARIES:
        return True
    name = unicodedata.name(character, "")
    return unicodedata.category(character) == "Po" and (
        "COMMA" in name or "SEMICOLON" in name
    )


def _is_structural_label_whitespace(character: str) -> bool:
    return character.isspace() or unicodedata.category(character).startswith("Z")


def _normalize_structural_delimiter(character: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", character)
    return normalized if normalized in {":", "="} else None


def _structural_url_contexts(line: str) -> tuple[tuple[int, int, int], ...]:
    contexts: list[tuple[int, int, int]] = []
    token_end = 0
    for match in _URL_SCHEME.finditer(line):
        authority_end = match.end()
        while (
            authority_end < len(line)
            and not line[authority_end].isspace()
            and line[authority_end] not in _URL_AUTHORITY_TERMINATORS
            and not _is_structural_field_boundary(line[authority_end])
        ):
            authority_end += 1

        if match.start() >= token_end:
            token_end = authority_end
            while (
                token_end < len(line)
                and not line[token_end].isspace()
                and not _is_structural_field_boundary(line[token_end])
            ):
                token_end += 1

        contexts.append((match.start(), authority_end, token_end))
    return tuple(contexts)


def _extract_structural_label(
    line: str,
    candidate_start: int,
    delimiter_index: int,
    url_context: tuple[int, int, int] | None,
) -> str:
    label_start = delimiter_index
    is_url_path_or_query = (
        url_context is not None and delimiter_index >= url_context[1]
    )
    while label_start > candidate_start:
        previous = line[label_start - 1]
        if (
            _is_structural_field_boundary(previous)
            and not _is_structural_label_whitespace(previous)
        ) or (
            is_url_path_or_query and previous in _URL_LABEL_BOUNDARIES
        ):
            break
        label_start -= 1
    return line[label_start:delimiter_index].strip()


def _sanitize_structural_credential_line(line: str) -> str:
    url_contexts = _structural_url_contexts(line)
    url_context_index = 0
    url_context: tuple[int, int, int] | None = None
    candidate_start = 0
    candidate_starts_with_previous_value = False
    index = 0
    while index < len(line):
        if url_context is not None and index >= url_context[2]:
            candidate_start = max(
                candidate_start,
                url_context[2],
            )
            url_context = None
        while (
            url_context_index < len(url_contexts)
            and url_contexts[url_context_index][0] <= index
        ):
            url_context = url_contexts[url_context_index]
            url_context_index += 1

        character = line[index]
        if candidate_starts_with_previous_value:
            if _is_structural_label_whitespace(character):
                while (
                    index < len(line)
                    and _is_structural_label_whitespace(line[index])
                ):
                    index += 1
                candidate_start = index
                candidate_starts_with_previous_value = False
                continue
            if _is_structural_field_boundary(character):
                candidate_start = index + 1
                candidate_starts_with_previous_value = False
                index += 1
                continue

        delimiter = _normalize_structural_delimiter(character)
        if delimiter is None:
            index += 1
            continue
        if url_context is not None and (
            index < url_context[1] or delimiter == ":"
        ):
            index += 1
            continue

        label = _extract_structural_label(
            line,
            candidate_start,
            index,
            url_context,
        )
        value_start = index + 1
        while value_start < len(line) and line[value_start].isspace():
            value_start += 1
        label_parts = _structural_credential_label_parts(label)
        if label_parts is not None:
            value = line[value_start:]
            is_cookie_label = any(
                part in {"cookie", "cookies"} for part in label_parts
            )
            if not is_cookie_label or _is_credential_shaped_cookie_value(value):
                return _redact_structural_value(line[:value_start], value)

        candidate_start = value_start
        candidate_starts_with_previous_value = True
        index = value_start
    return line


def _sanitize_structural_credential_lines(value: str) -> str:
    return _NONEMPTY_LINE.sub(
        lambda match: _sanitize_structural_credential_line(match.group(0)),
        value,
    )


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
    status_context = bool(_BEARER_STATUS_CONTEXT.match(match.string[match.end() :]))
    if (
        opaque_letters
        or credential_punctuation
        or internal_period
        or digit_shaped
        or assignment_context
        or status_context
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

    sanitized = _sanitize_structural_credential_lines(value)
    sanitized = _URL_USERINFO.sub(
        lambda match: f"{match.group('scheme')}{SECRET_REDACTION}@",
        sanitized,
    )
    sanitized = _BEARER.sub(_redact_bearer, sanitized)
    return _PROVIDER_KEY.sub(SECRET_REDACTION, sanitized)


def _sanitize_string(
    value: str,
    *,
    budget: _SanitizationBudget,
    depth: int,
    encoded_json_depth: int,
) -> str:
    if _string_exceeds_budget(value):
        return SECRET_REDACTION
    if (
        encoded_json_depth >= _MAX_ENCODED_JSON_DEPTH
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


def _string_exceeds_budget(value: str) -> bool:
    if len(value) > _MAX_ENCODED_STRING_BYTES:
        return True
    return len(value.encode("utf-8", errors="replace")) > _MAX_ENCODED_STRING_BYTES


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
                if _string_exceeds_budget(key):
                    raise _SanitizationBudgetExhausted
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
