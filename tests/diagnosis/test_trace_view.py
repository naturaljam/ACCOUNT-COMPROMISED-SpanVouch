import json
from itertools import product
from pathlib import Path

import pytest

from afc.diagnosis import trace_view as trace_view_module
from afc.diagnosis.trace_view import (
    ALLOWED_ATTRIBUTES,
    SECRET_REDACTION,
    DiagnosticTraceView,
    sanitize_diagnostic_trace_view,
    sanitize_diagnostic_value,
)
from afc.review.models import canonical_json
from afc.trace_ir.models import TraceIR

DATASET = Path("evals/datasets/supportlab-v1/traces.jsonl")
FORBIDDEN_PARTS = (
    "scenario",
    "idempotency_key",
    "ignore_error",
    "calculated_amount",
)
VALUE_SECRET = "value-level-sentinel-credential"


def load_trace(run_id: str) -> TraceIR:
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        trace = TraceIR.model_validate_json(line)
        if trace.run_id == run_id:
            return trace
    raise AssertionError(f"missing fixture: {run_id}")


def test_trace_view_removes_identity_and_fault_injection_fields() -> None:
    trace = load_trace("invalid_argument-01")
    original = trace.model_dump()

    view = DiagnosticTraceView.from_trace(trace)

    assert not hasattr(view, "trace_id")
    assert not hasattr(view, "run_id")
    assert trace.model_dump() == original
    assert tuple(span.span_id for span in view.spans) == tuple(
        span.span_id
        for span in sorted(
            trace.spans,
            key=lambda span: (span.started_at, span.ended_at, span.span_id),
        )
    )
    for span in view.spans:
        assert set(span.attributes) <= ALLOWED_ATTRIBUTES
        assert not any(part in key for key in span.attributes for part in FORBIDDEN_PARTS)


def test_trace_view_keeps_only_diagnostic_business_attributes() -> None:
    view = DiagnosticTraceView.from_trace(load_trace("policy_violation-01"))
    submit = next(span for span in view.spans if span.name == "submit_refund")

    assert submit.attributes["tool.arguments.approval"] == "none"
    assert submit.attributes["tool.error.type"] == "RefundRejected"
    assert submit.attributes["tool.error.message"] == "missing_approval"


def test_trace_view_recursively_redacts_credentials_inside_allowed_values() -> None:
    trace = load_trace("clean-01")
    root = trace.spans[0]
    root = root.model_copy(
        update={
            "attributes": {
                **root.attributes,
                "tool.result": {
                    "api_key": VALUE_SECRET,
                    "nested": [
                        f"DEEPSEEK_API_KEY={VALUE_SECRET}",
                        {"password": VALUE_SECRET, "safe": "useful context"},
                    ],
                },
                "tool.error.message": (
                    f"request rejected; Authorization: Bearer {VALUE_SECRET}; retry later"
                ),
                "run.final_message": (
                    f"upstream https://agent:{VALUE_SECRET}@example.test/path failed"
                ),
            }
        }
    )
    trace = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(trace)
    serialized = canonical_json(view)

    assert VALUE_SECRET not in serialized
    assert "useful context" in serialized
    assert "request rejected" in serialized
    assert "retry later" not in serialized
    assert "example.test/path failed" in serialized
    assert f"{SECRET_REDACTION}]" not in serialized
    assert DiagnosticTraceView.model_validate(view.model_dump()) == view


def test_trace_view_redacts_every_authorization_scheme_inside_allowed_strings() -> None:
    trace = load_trace("clean-01")
    schemes = (
        f"Token {VALUE_SECRET}",
        f'Digest username="agent", response="{VALUE_SECRET}"',
        (
            "AWS4-HMAC-SHA256 "
            f"Credential={VALUE_SECRET}/20260718/cn-north-1/service/aws4_request, "
            f"SignedHeaders=host;x-date, Signature={VALUE_SECRET}"
        ),
        f"Custom-Scheme {VALUE_SECRET}",
    )

    for scheme in schemes:
        root = trace.spans[0].model_copy(
            update={
                "attributes": {
                    **trace.spans[0].attributes,
                    "tool.error.message": (
                        f"request failed; Authorization: {scheme}; safe retry context"
                    ),
                }
            }
        )
        candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

        serialized = canonical_json(DiagnosticTraceView.from_trace(candidate))

        assert VALUE_SECRET not in serialized
        assert "safe retry context" not in serialized


def test_trace_view_sanitizes_escaped_and_double_encoded_json_idempotently() -> None:
    trace = load_trace("clean-01")
    encoded = json.dumps(
        {
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={VALUE_SECRET}/20260718/region/service/aws4_request"
            ),
            "nested": {"token": VALUE_SECRET},
            "safe": "保留安全上下文",
        },
        ensure_ascii=False,
    )
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": [encoded, json.dumps(encoded, ensure_ascii=False)],
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)
    serialized = canonical_json(view)

    assert VALUE_SECRET not in serialized
    assert "保留安全上下文" in serialized
    assert sanitize_diagnostic_trace_view(view) == view
    assert sanitize_diagnostic_trace_view(sanitize_diagnostic_trace_view(view)) == view


def test_trace_view_preserves_safe_bearer_prose_and_redacts_token_shaped_bearer() -> None:
    trace = load_trace("clean-01")
    safe_message = "Bearer of good news crossed the bridge. 熊猫依旧安全。"
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "run.final_message": safe_message,
                "tool.error.message": f"Bearer {VALUE_SECRET}; safe tail",
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)

    assert view.spans[0].attributes["run.final_message"] == safe_message
    assert VALUE_SECRET not in canonical_json(view)
    assert "safe tail" in canonical_json(view)


def test_trace_view_redacts_opaque_bearer_tokens_only_at_value_boundaries() -> None:
    trace = load_trace("clean-01")
    opaque_letters = "qwertyuiopasdfghjklzxcvbnm"
    short_token = "zzq"
    safe_prose = "Bearer of good news remains ordinary prose. 熊猫仍然安全。"
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": [
                    f"opaque=Bearer {opaque_letters}",
                    f"short=Bearer {short_token},next=retry",
                    json.dumps(
                        {"credential": f"Bearer {short_token}"},
                        ensure_ascii=False,
                    ),
                ],
                "tool.error.message": (
                    f"Authorization: Custom {short_token}; auth context survives"
                ),
                "run.final_message": safe_prose,
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)
    serialized = canonical_json(view)

    assert f"Bearer {opaque_letters}" not in serialized
    assert f"Bearer {short_token}" not in serialized
    assert "next=retry" in serialized
    assert "auth context survives" not in serialized
    assert view.spans[0].attributes["run.final_message"] == safe_prose
    assert sanitize_diagnostic_trace_view(view) == view
    assert sanitize_diagnostic_trace_view(sanitize_diagnostic_trace_view(view)) == view


def test_trace_view_uses_one_classifier_for_common_nested_credential_keys() -> None:
    trace = load_trace("clean-01")
    credential_keys = (
        "Authorization",
        "proxy-authorization",
        "X API KEY",
        "XAPIKEY",
        "api_key",
        "clientSecret",
        "private-key",
        "password",
        "passwd",
        "pwd",
        "db.password",
        "token",
        "accessToken",
        "refresh-token",
        "session_token",
        "id.token",
        "Cookie",
        "cookies",
        "Set-Cookie",
        "session-cookie",
        "account_password_value",
        "provider_secret_value",
        "auth_token_value",
        "session_credential",
        "secretkey",
        "passwordhash",
        "tokenstring",
        "userpasswordhash",
        "sessiontokenvalue",
        "clientsecretstring",
    )
    nested = {key: VALUE_SECRET for key in credential_keys}
    nested.update(
        {
            "token_count": 7,
            "password_policy_name": "rotate-quarterly",
            "secret_rotation_duration": 30,
            "tokenizer_name": "sentencepiece",
            "tokenizerType": "bpe",
            "password_hash_algorithm": "argon2id",
            "token_value_length": 128,
            "safe": "useful context",
        }
    )
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": {"nested": [nested]},
                "tool.error.message": {"details": nested},
            }
        }
    )

    view = DiagnosticTraceView.from_trace(
        trace.model_copy(update={"spans": [root, *trace.spans[1:]]})
    )
    serialized = canonical_json(view)

    assert VALUE_SECRET not in serialized
    assert '"token_count":7' in serialized
    assert '"password_policy_name":"rotate-quarterly"' in serialized
    assert '"secret_rotation_duration":30' in serialized
    assert '"tokenizer_name":"sentencepiece"' in serialized
    assert '"tokenizerType":"bpe"' in serialized
    assert '"password_hash_algorithm":"argon2id"' in serialized
    assert '"token_value_length":128' in serialized
    assert "useful context" in serialized


def test_trace_view_classifies_mapping_labels_after_stripping_delimiters_and_paths() -> None:
    trace = load_trace("clean-01")
    credential_labels = (
        " api_key: ",
        "'authorization='",
        '"headers.authorization:"',
        "userpassword",
        "sessiontokenvalue",
        "request.headers.Authorization",
    )
    safe_metadata = {
        "token_count": 7,
        "password_policy": "rotate-quarterly",
        "session_duration": 30,
    }
    root = trace.spans[0].model_copy(
        update={
            "name": f"worker userpassword={VALUE_SECRET}",
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": {
                    **{label: VALUE_SECRET for label in credential_labels},
                    **safe_metadata,
                },
                "tool.error.message": (
                    f"headers.authorization: Token {VALUE_SECRET}; retry remains safe"
                ),
            },
        }
    )

    view = DiagnosticTraceView.from_trace(
        trace.model_copy(update={"spans": [root, *trace.spans[1:]]})
    )
    serialized = canonical_json(view)

    assert VALUE_SECRET not in serialized
    assert "retry remains safe" not in serialized
    for key, value in safe_metadata.items():
        assert view.spans[0].attributes["tool.result"][key] == value
    assert sanitize_diagnostic_trace_view(view) == view


def test_trace_view_sanitizes_span_names_mapping_keys_and_final_messages() -> None:
    trace = load_trace("clean-01")
    root = trace.spans[0].model_copy(
        update={
            "name": f"request api_key={VALUE_SECRET}",
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": {f"token={VALUE_SECRET}": "safe value"},
                "run.final_message": f"client_secret: {VALUE_SECRET}",
            },
        }
    )

    view = DiagnosticTraceView.from_trace(
        trace.model_copy(update={"spans": [root, *trace.spans[1:]]})
    )

    assert VALUE_SECRET not in canonical_json(view)
    assert view.spans[0].name == f"request api_key={SECRET_REDACTION}"
    assert view.spans[0].attributes["tool.result"] == {
        f"token={SECRET_REDACTION}": SECRET_REDACTION
    }


def test_trace_view_sanitizes_deep_encoding_and_fails_closed_at_budget() -> None:
    trace = load_trace("clean-01")
    six_layers = json.dumps({"client_secret": VALUE_SECRET})
    for _ in range(6):
        six_layers = json.dumps(six_layers)
    exhausted = json.dumps({"password": VALUE_SECRET})
    for _ in range(12):
        exhausted = json.dumps(exhausted)
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": [six_layers, exhausted],
            }
        }
    )

    view = DiagnosticTraceView.from_trace(
        trace.model_copy(update={"spans": [root, *trace.spans[1:]]})
    )
    serialized = canonical_json(view)

    assert VALUE_SECRET not in serialized
    assert SECRET_REDACTION in serialized
    assert sanitize_diagnostic_trace_view(view) == view


def test_sanitizer_bounds_large_and_cyclic_values_without_corrupting_safe_data() -> None:
    safe = [f"safe-{index}" for index in range(2_000)]
    assert sanitize_diagnostic_value(safe) == safe

    cyclic: list[object] = ["safe prefix"]
    cyclic.append(cyclic)
    assert sanitize_diagnostic_value(cyclic) == ["safe prefix", SECRET_REDACTION]


def test_sanitizer_stops_iterating_large_lists_when_node_budget_is_exhausted() -> None:
    class GuardedList(list[str]):
        visited = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            for index in range(len(self)):
                self.visited += 1
                if self.visited > 10_000:
                    raise AssertionError("sanitizer traversed beyond its node budget")
                yield self[index]

    value = GuardedList(f"safe-{index}" for index in range(20_000))

    sanitized = sanitize_diagnostic_value(value)

    assert sanitized == SECRET_REDACTION
    assert value.visited <= 10_000
    assert sanitize_diagnostic_value(sanitized) == SECRET_REDACTION


def test_sanitizer_charges_mapping_keys_and_values_then_stops_immediately() -> None:
    class GuardedDict(dict[str, str]):
        visited = 0

        def items(self):  # type: ignore[no-untyped-def]
            for item in super().items():
                self.visited += 1
                if self.visited > 5_000:
                    raise AssertionError("sanitizer traversed uncharged mapping entries")
                yield item

    value = GuardedDict((f"safe-{index}", "context") for index in range(12_000))

    sanitized = sanitize_diagnostic_value(value)

    assert sanitized == SECRET_REDACTION
    assert value.visited <= 5_000
    assert sanitize_diagnostic_value(sanitized) == SECRET_REDACTION


def test_sanitizer_fails_closed_before_regex_for_oversized_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regex_inputs: list[str] = []
    original = trace_view_module._sanitize_plain_string

    def record_regex_input(value: str) -> str:
        regex_inputs.append(value)
        return original(value)

    monkeypatch.setattr(trace_view_module, "_sanitize_plain_string", record_regex_input)
    oversized = "ordinary prose " + "x" * 262_144

    sanitized = sanitize_diagnostic_value(oversized)

    assert sanitized == SECRET_REDACTION
    assert regex_inputs == []
    assert sanitize_diagnostic_value(sanitized) == SECRET_REDACTION


def test_sanitizer_rejects_oversized_mapping_key_before_normalization_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GuardedDict(dict[str, str]):
        visited = 0

        def items(self):  # type: ignore[no-untyped-def]
            for item in super().items():
                self.visited += 1
                if self.visited > 1:
                    raise AssertionError("sanitizer scanned past oversized mapping key")
                yield item

    normalized: list[str] = []
    original_normalize = trace_view_module._normalize_credential_label

    def record_normalization(value: str) -> tuple[str, ...]:
        normalized.append(value)
        return original_normalize(value)

    monkeypatch.setattr(
        trace_view_module,
        "_normalize_credential_label",
        record_normalization,
    )
    oversized_key = "k" * 262_145
    value = GuardedDict(((oversized_key, "opaque"), ("safe", "unvisited")))

    sanitized = sanitize_diagnostic_value(value)

    assert sanitized == SECRET_REDACTION
    assert value.visited == 1
    assert oversized_key not in normalized
    assert sanitize_diagnostic_value(sanitized) == SECRET_REDACTION


@pytest.mark.parametrize(
    "safe_prose",
    (
        "Bearer news",
        "Bearer of good news",
        "The bearer bond.",
        "Bearer news remains safe. 熊猫依旧安全。",
    ),
)
def test_trace_view_preserves_ordinary_bearer_prose_byte_for_byte(
    safe_prose: str,
) -> None:
    trace = load_trace("clean-01")
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "run.final_message": safe_prose,
            }
        }
    )

    view = DiagnosticTraceView.from_trace(
        trace.model_copy(update={"spans": [root, *trace.spans[1:]]})
    )

    assert view.spans[0].attributes["run.final_message"] == safe_prose


def test_trace_view_redacts_long_and_short_credential_shaped_bearer_values() -> None:
    trace = load_trace("clean-01")
    long_letters = "abcdefghijklmnopqrstuvwxyzabcdef"
    short_shaped = "ab1cd"
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": [
                    f"Bearer {long_letters}",
                    f"Bearer {long_letters}.",
                    f"Bearer {short_shaped}; safe tail",
                    "Bearer ab_cd, safe tail",
                ],
            }
        }
    )

    view = DiagnosticTraceView.from_trace(
        trace.model_copy(update={"spans": [root, *trace.spans[1:]]})
    )
    serialized = canonical_json(view)

    assert long_letters not in serialized
    assert short_shaped not in serialized
    assert "ab_cd" not in serialized
    assert "safe tail" in serialized


def test_trace_view_fails_closed_for_delimited_keys_and_complete_auth_headers() -> None:
    trace = load_trace("clean-01")
    short_bearer = "Qaz"
    safe_metadata = {
        "auth_timeout": 30,
        "access_key_id": "public-key-id",
        "access_key_rotation_status": "scheduled",
    }
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": {
                    "headers:authorization": VALUE_SECRET,
                    "proxy=auth": VALUE_SECRET,
                    "access_key": VALUE_SECRET,
                    "serviceAuth": VALUE_SECRET,
                    **safe_metadata,
                },
                "tool.error.message": (
                    f"Cookie: session=first; csrf={VALUE_SECRET}\n"
                    f"Set-Cookie: sid=first; refresh={VALUE_SECRET}\n"
                    f"Bearer {short_bearer}; HTTP 401"
                ),
                "run.final_message": "Bearer of good news remains harmless prose.",
            }
        }
    )

    view = DiagnosticTraceView.from_trace(
        trace.model_copy(update={"spans": [root, *trace.spans[1:]]})
    )
    serialized = canonical_json(view)

    assert VALUE_SECRET not in serialized
    assert short_bearer not in serialized
    assert "csrf=" not in serialized
    assert "refresh=" not in serialized
    assert view.spans[0].attributes["run.final_message"] == (
        "Bearer of good news remains harmless prose."
    )
    for key, value in safe_metadata.items():
        assert view.spans[0].attributes["tool.result"][key] == value


@pytest.mark.parametrize(
    "assignment",
    (
        f"api_key={SECRET_REDACTION}topsecret",
        f"api_key='{SECRET_REDACTION}topsecret'",
        f'api_key="{SECRET_REDACTION}topsecret"',
    ),
)
def test_sanitizer_rejects_partial_redaction_assignment_values(
    assignment: str,
) -> None:
    sanitized = sanitize_diagnostic_value(assignment)

    assert "topsecret" not in canonical_json(sanitized)
    assert sanitize_diagnostic_value(sanitized) == sanitized


@pytest.mark.parametrize(
    "header",
    (
        f"Authorization:{SECRET_REDACTION}topsecret",
        f"Proxy-Authorization={SECRET_REDACTION}topsecret",
        "Authorization:abc; arbitrary semicolon tail=topsecret",
        "Proxy-Authorization=abc; refresh=topsecret",
        'Authorization:"abc"; tail=topsecret',
        "Proxy-Authorization = 'abc'; tail=topsecret",
    ),
)
def test_sanitizer_redacts_complete_authorization_values(
    header: str,
) -> None:
    sanitized = sanitize_diagnostic_value(header)

    assert "topsecret" not in canonical_json(sanitized)
    assert ";" not in sanitized
    assert sanitize_diagnostic_value(sanitized) == sanitized


@pytest.mark.parametrize(
    "header",
    (
        f"Cookie:{SECRET_REDACTION}; csrf=topsecret",
        f"Set-Cookie: {SECRET_REDACTION}; refresh=topsecret",
        "Cookie=session=first; csrf=topsecret",
        "Set-Cookie=sid=first; refresh=topsecret",
        "Request Cookie: session=first; csrf=topsecret",
        "headers.cookie: session=first; csrf=topsecret",
        "HTTP Cookie=session=first; csrf=topsecret",
        "Response Set-Cookie: sid=first; refresh=topsecret",
    ),
)
def test_sanitizer_redacts_complete_context_qualified_cookie_values(
    header: str,
) -> None:
    sanitized = sanitize_diagnostic_value(header)

    assert "topsecret" not in canonical_json(sanitized)
    assert ";" not in sanitized
    assert sanitize_diagnostic_value(sanitized) == sanitized


@pytest.mark.parametrize(
    "header",
    (
        "Cookie: qwertyuiopasdfghjklzxcvbnm",
        "Browser cookie: qwertyuiopasdfghjklzxcvbnm",
    ),
)
def test_sanitizer_redacts_opaque_cookie_credential_values(header: str) -> None:
    assert sanitize_diagnostic_value(header).endswith(SECRET_REDACTION)


@pytest.mark.parametrize(
    "header",
    (
        f"Authorization:{SECRET_REDACTION}",
        f"Proxy-Authorization: {SECRET_REDACTION}",
        f"Cookie:{SECRET_REDACTION}",
        f"Set-Cookie: {SECRET_REDACTION}",
        f"Cookie={SECRET_REDACTION}",
        f"Set-Cookie={SECRET_REDACTION}",
        f"api_key={SECRET_REDACTION}",
        f"api_key='{SECRET_REDACTION}'",
        f'api_key="{SECRET_REDACTION}"',
        f'Authorization="{SECRET_REDACTION}"',
        f"Proxy-Authorization='{SECRET_REDACTION}'",
    ),
)
def test_sanitizer_preserves_only_complete_redacted_header_values(header: str) -> None:
    assert sanitize_diagnostic_value(header) == header


@pytest.mark.parametrize(
    "safe_prose",
    (
        "A cookie: recipe; instructions remain safe",
        "cookie: recipe; instructions remain safe",
        "Browser cookie: recipe; instructions remain safe",
        "Bearer of; good news",
    ),
)
def test_sanitizer_preserves_cookie_and_bearer_prose_with_punctuation(
    safe_prose: str,
) -> None:
    assert sanitize_diagnostic_value(safe_prose) == safe_prose


_UNICODE_ZS_SPACES = tuple(
    chr(codepoint)
    for codepoint in (
        0x0020,
        0x00A0,
        0x1680,
        0x2000,
        0x2001,
        0x2002,
        0x2003,
        0x2004,
        0x2005,
        0x2006,
        0x2007,
        0x2008,
        0x2009,
        0x200A,
        0x202F,
        0x205F,
        0x3000,
    )
)
_METADATA_QUALIFIED_CREDENTIAL_LABELS = (
    "token count payload",
    "authorization status header",
    "password policy value",
    "api key count material",
    "access key id material",
    "private key algorithm payload",
    "client secret length material",
    "cookie count payload",
)
_STRUCTURAL_CREDENTIAL_CORE_LABELS = (
    "auth",
    "authorization",
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "credential",
    "cookie",
    "cookies",
    "api key",
    "access key",
    "private key",
    "client secret",
)
_EMBEDDED_STRUCTURAL_CREDENTIAL_LABELS = (
    *_STRUCTURAL_CREDENTIAL_CORE_LABELS,
    *_METADATA_QUALIFIED_CREDENTIAL_LABELS,
)
_QUOTED_STRUCTURAL_WRAPPERS = ('"', "'", '\\"', "\\'")
_ESCAPED_INNER_QUOTE_CASES = (
    ('"', r'\"'),
    ("'", r"\'"),
    (r'\"', r'\\\"'),
    (r"\'", r"\\\'"),
)
_COMPACT_VALUE_PREFIX_CORES = (
    "apikey",
    "accesskey",
    "privatekey",
    "userpassword",
    "clientsecret",
    "sessiontoken",
)
_COMPACT_SENSITIVE_VALUE_SUFFIXES = (
    "credential",
    "credentials",
    "hash",
    "key",
    "string",
    "value",
)
_NORMALIZED_COMPACT_BASE_PARTS = (
    ("api", "key"),
    ("access", "key"),
    ("private", "key"),
    ("user", "password"),
    ("client", "secret"),
    ("session", "token"),
)
_NORMALIZED_COMPACT_SENSITIVE_SUFFIXES = (
    "credential",
    "hash",
    "key",
    "string",
    "value",
)
_ADDITIONAL_SENSITIVE_LABELS = (
    "session_id",
    "session-id",
    "sessionId",
    "sessionid",
    "jwt",
    "JWT",
    "jwt_token",
    "access_jwt",
    "passphrase",
    "pass_phrase",
    "pass-phrase",
)
_PUNCTUATED_SENSITIVE_LABELS = (
    "api::key",
    "api,key",
    "api;key",
    "api|key",
    "deepseek::api::key",
)


def _normalized_compact_spellings(parts: tuple[str, str]) -> tuple[str, ...]:
    first, second = parts
    return (
        f"{first}{second}",
        f"{first}_{second}",
        f"{first}-{second}",
        f"{first}.{second}",
        f"{first}/{second}",
        f"{first}${second}",
        f"{first}@{second}",
        f"{first}{second.title()}",
        f"{first.title()}{second.title()}",
        f"{first}[{second}]",
    )


def _assert_sanitizer_fixed_point(source: str, expected: str) -> None:
    sanitized = sanitize_diagnostic_value(source)

    assert sanitized == expected
    for _ in range(4):
        sanitized = sanitize_diagnostic_value(sanitized)
        assert sanitized == expected


def test_assignment_marker_value_matrix_consumes_complete_quoted_values() -> None:
    labels = ("api_key", "client_secret", "access_token")
    separators = ("=", ":")
    left_whitespace = ("", " ")
    right_whitespace = ("", " ", "\t")
    quotes = ('"', "'")
    suffixes = (
        "top secret",
        "top;secret",
        "top,secret",
        "top]secret",
        "top\tsecret",
        "top=secret",
    )

    for label, separator, left, right, quote, suffix in product(
        labels,
        separators,
        left_whitespace,
        right_whitespace,
        quotes,
        suffixes,
    ):
        prefix = f"{label}{left}{separator}{right}"
        source = f"{prefix}{quote}{SECRET_REDACTION}{suffix}{quote}"
        expected = f"{prefix}{quote}{SECRET_REDACTION}{quote}"

        _assert_sanitizer_fixed_point(source, expected)


def test_assignment_exact_marker_matrix_is_byte_idempotent() -> None:
    for label, separator, left, right, quote in product(
        ("api_key", "client_secret", "access_token"),
        ("=", ":"),
        ("", " "),
        ("", " ", "\t"),
        ("", '"', "'"),
    ):
        source = f"{label}{left}{separator}{right}{quote}{SECRET_REDACTION}{quote}"

        _assert_sanitizer_fixed_point(source, source)


def test_cookie_header_context_matrix_redacts_complete_pair_values() -> None:
    context_paths = (
        ("request", "headers"),
        ("response", "headers"),
        ("http", "request", "headers"),
        ("http", "response", "headers"),
    )

    for context, joiner, header, separator, whitespace in product(
        context_paths,
        (".", "_", "-", " "),
        ("Cookie", "Set-Cookie"),
        (":", "="),
        ("", " ", "\t"),
    ):
        prefix = f"{joiner.join(context)}{joiner}{header}{separator}{whitespace}"
        source = f"{prefix}session=first; csrf=topsecret"

        _assert_sanitizer_fixed_point(source, f"{prefix}{SECRET_REDACTION}")


def test_cookie_marker_matrix_is_atomic_and_idempotent() -> None:
    context_prefixes = (
        "",
        "Browser ",
        "request.headers.",
        "response_headers_",
        "http-request-headers-",
        "http request headers ",
    )

    for context, header, separator, whitespace, quote, partial in product(
        context_prefixes,
        ("Cookie", "Set-Cookie"),
        (":", "="),
        ("", " "),
        ("", '"', "'"),
        (False, True),
    ):
        prefix = f"{context}{header}{separator}{whitespace}"
        suffix = "top;secret" if partial else ""
        source = f"{prefix}{quote}{SECRET_REDACTION}{suffix}{quote}"
        expected = (
            f"{prefix}{quote}{SECRET_REDACTION}{quote}" if partial else source
        )

        _assert_sanitizer_fixed_point(source, expected)


def test_cookie_safe_prose_matrix_is_byte_idempotent() -> None:
    for context, header, separator, whitespace in product(
        (
            "",
            "Browser ",
            "request.headers.",
            "response_headers_",
            "http-request-headers-",
            "http request headers ",
        ),
        ("Cookie", "Set-Cookie"),
        (":", "="),
        ("", " ", "\t"),
    ):
        source = (
            f"{context}{header}{separator}{whitespace}"
            "recipe; instructions remain safe"
        )

        _assert_sanitizer_fixed_point(source, source)


def test_structural_credential_lines_handle_escaped_quote_wrappers() -> None:
    for label, separator, wrapper, partial in product(
        ("api_key", "Cookie"),
        (":", "="),
        ('\\"', "\\'"),
        (False, True),
    ):
        suffix = "top secret;tail" if partial else ""
        source = f"{label}{separator}{wrapper}{SECRET_REDACTION}{suffix}{wrapper}"
        expected = (
            f"{label}{separator}{wrapper}{SECRET_REDACTION}{wrapper}"
            if partial
            else source
        )

        _assert_sanitizer_fixed_point(source, expected)


def test_structural_cookie_labels_have_no_component_depth_cap() -> None:
    context_parts = (
        "http",
        "request",
        "headers",
        "response",
        "http",
        "request",
        "headers",
        "response",
    )

    for depth, joiner, header, separator in product(
        range(9),
        (".", "_", "-", " "),
        ("Cookie", "Set-Cookie"),
        (":", "="),
    ):
        context = joiner.join(context_parts[:depth])
        label = f"{context}{joiner if context else ''}{header}"
        source = f"{label}{separator}session=first; csrf=topsecret"

        _assert_sanitizer_fixed_point(
            source,
            f"{label}{separator}{SECRET_REDACTION}",
        )


def test_shared_cookie_label_spellings_redact_complete_values() -> None:
    for label, separator in product(
        (
            "Set Cookie",
            "set_cookie",
            "set.cookie",
            "Session Cookie",
            "request.session.cookie",
        ),
        (":", "="),
    ):
        source = f"{label}{separator}session=first; csrf=topsecret"

        _assert_sanitizer_fixed_point(
            source,
            f"{label}{separator}{SECRET_REDACTION}",
        )


def test_cookie_short_token_shapes_redact_without_hiding_recipe_prose() -> None:
    for value, suffix in product(
        ("a1", "a_b", "a~b", "a+b", "a/b", "a=b", "a-b", "a.b"),
        ("", "; instructions remain safe"),
    ):
        _assert_sanitizer_fixed_point(
            f"Session Cookie:{value}{suffix}",
            f"Session Cookie:{SECRET_REDACTION}",
        )

    for value in (
        "recipe",
        "recipe instructions",
        "recipe; instructions remain safe",
    ):
        source = f"Session Cookie:{value}"
        _assert_sanitizer_fixed_point(source, source)


def test_cookie_tokens_stop_before_the_following_safe_field() -> None:
    labels = (
        "Cookie",
        "Cookies",
        "Set-Cookie",
        "set_cookie",
        "set.cookie",
        "Session Cookie",
        "request.session.cookie",
        "headers.cookie",
    )
    for label, separator, wrapper in product(
        labels,
        (":", "="),
        ("", '"', "'", r'\"', r"\'"),
    ):
        source = f"{label}{separator}{wrapper}secret-one{wrapper} field=ok"
        expected = source.replace("secret-one", SECRET_REDACTION)
        _assert_sanitizer_fixed_point(source, expected)


def test_unclosed_cookie_values_fail_closed_without_inventing_a_closer() -> None:
    labels = ("Cookie", "Set-Cookie", "Session Cookie", "headers.cookie")
    for label, separator, wrapper in product(
        labels,
        (":", "="),
        ('"', "'", r'\"', r"\'"),
    ):
        source = f"{label}{separator}{wrapper}secret-one field=ok"
        expected = f"{label}{separator}{SECRET_REDACTION}"
        _assert_sanitizer_fixed_point(source, expected)


def test_cookie_prose_still_preserves_a_following_safe_field() -> None:
    for label, separator, wrapper in product(
        ("Cookie", "Set-Cookie", "Session Cookie", "headers.cookie"),
        (":", "="),
        ("", '"', "'", r'\"', r"\'"),
    ):
        source = f"{label}{separator}{wrapper}recipe{wrapper} field=ok"
        _assert_sanitizer_fixed_point(source, source)


def test_unquoted_safe_cookie_values_stop_at_physical_line_boundaries() -> None:
    for label, separator, newline, safe_value, next_field in product(
        (
            "Cookie",
            "Set-Cookie",
            "Session Cookie",
            "headers.cookie",
            "request.headers.Cookie",
            "http request headers Set-Cookie",
        ),
        (":", "="),
        ("\n", "\r\n"),
        ("token count", "api key", "private key"),
        ("note", "message", "result", "header"),
    ):
        source = f"{label}{separator} {safe_value}{newline}{next_field}=ok"
        _assert_sanitizer_fixed_point(source, source)


def test_safe_credential_metadata_labels_remain_visible() -> None:
    for label, value in (
        ("cookie_count", "7"),
        ("cookie_policy", "rotate-quarterly"),
        ("session_cookie_count", "3"),
        ("token_count", "11"),
        ("password_policy", "rotate-quarterly"),
    ):
        source = f"{label}={value}; metadata remains safe"
        _assert_sanitizer_fixed_point(source, source)


def test_field_terminal_uses_shared_safe_metadata_semantics() -> None:
    compact_labels = (
        "token_count_field",
        "custom_token_count_field",
        "tenant_custom_token_count_field",
    )
    for label in compact_labels:
        assert sanitize_diagnostic_value({label: "ok"}) == {label: "ok"}
        _assert_sanitizer_fixed_point(f"{label}=ok", f"{label}=ok")

    for space, prefix, boundary in product(
        _UNICODE_ZS_SPACES,
        ("", "tenant ", "arbitrary tenant custom "),
        ("", "status=ok; ", "status=ok | ", "status=ok\N{IDEOGRAPHIC COMMA}"),
    ):
        label = space.join((*prefix.split(), "token", "count", "field"))
        source = f"{boundary}{label}=ok"
        assert sanitize_diagnostic_value({label: "ok"}) == {label: "ok"}
        _assert_sanitizer_fixed_point(source, source)

        previous_value_source = f"safe={label}=ok"
        _assert_sanitizer_fixed_point(
            previous_value_source,
            previous_value_source,
        )


def test_note_terminal_uses_shared_safe_metadata_semantics() -> None:
    compact_labels = (
        "token_count_note",
        "custom_token_count_note",
        "tenant_custom_token_count_note",
    )
    for label in compact_labels:
        assert sanitize_diagnostic_value({label: "ok"}) == {label: "ok"}
        _assert_sanitizer_fixed_point(f"{label}=ok", f"{label}=ok")

    for space, prefix, boundary in product(
        _UNICODE_ZS_SPACES,
        ("", "tenant ", "arbitrary tenant custom "),
        ("", "status=ok; ", "status=ok | ", "status=ok\N{IDEOGRAPHIC COMMA}"),
    ):
        label = space.join((*prefix.split(), "token", "count", "note"))
        source = f"{boundary}{label}=ok"
        assert sanitize_diagnostic_value({label: "ok"}) == {label: "ok"}
        _assert_sanitizer_fixed_point(source, source)

        previous_value_source = f"safe={label}=ok"
        _assert_sanitizer_fixed_point(
            previous_value_source,
            previous_value_source,
        )


def test_note_terminal_does_not_weaken_qualified_credential_labels() -> None:
    for space, prefix, boundary, qualified_label in product(
        _UNICODE_ZS_SPACES,
        ("", "tenant custom "),
        ("", "status=ok; ", "status=ok\N{IDEOGRAPHIC COMMA}"),
        _METADATA_QUALIFIED_CREDENTIAL_LABELS,
    ):
        label = space.join((*prefix.split(), *qualified_label.split()))
        source = f"{boundary}{label}=secret-one note=ok"
        expected = source.replace("secret-one", SECRET_REDACTION)
        assert sanitize_diagnostic_value({label: "secret-one"}) == {
            label: SECRET_REDACTION
        }
        _assert_sanitizer_fixed_point(source, expected)


def test_field_terminal_does_not_weaken_qualified_credential_labels() -> None:
    for space, prefix, boundary, qualified_label in product(
        _UNICODE_ZS_SPACES,
        ("", "tenant custom "),
        ("", "status=ok; ", "status=ok\N{IDEOGRAPHIC COMMA}"),
        _METADATA_QUALIFIED_CREDENTIAL_LABELS,
    ):
        label = space.join((*prefix.split(), *qualified_label.split()))
        source = f"{boundary}{label}=secret-one field=ok"
        expected = source.replace("secret-one", SECRET_REDACTION)
        assert sanitize_diagnostic_value({label: "secret-one"}) == {
            label: SECRET_REDACTION
        }
        _assert_sanitizer_fixed_point(source, expected)


def test_escaped_structural_label_and_value_wrappers_are_independent() -> None:
    for label_wrapper, value_wrapper, separator in product(
        ('"', "'", '\\"', "\\'"),
        ("", '"', "'", '\\"', "\\'"),
        (":", "="),
    ):
        source = (
            f"{label_wrapper}api_key{label_wrapper}{separator}"
            f"{value_wrapper}topsecret{value_wrapper}"
        )
        expected = (
            f"{label_wrapper}api_key{label_wrapper}{separator}"
            f"{value_wrapper}{SECRET_REDACTION}{value_wrapper}"
        )
        _assert_sanitizer_fixed_point(source, expected)

    cases = (
        (
            r'\"api_key\":\"topsecret\"',
            rf'\"api_key\":\"{SECRET_REDACTION}\"',
        ),
        (
            r'{\"api_key\":\"topsecret\"}',
            rf'{{\"api_key\":\"{SECRET_REDACTION}\"}}',
        ),
        (
            r"\'api_key\':\'topsecret\'",
            rf"\'api_key\':\'{SECRET_REDACTION}\'",
        ),
        (
            r"{\'Cookie\':\'a_1\'}",
            rf"{{\'Cookie\':\'{SECRET_REDACTION}\'}}",
        ),
        (
            rf'\"api_key\":\"{SECRET_REDACTION}tail\"',
            rf'\"api_key\":\"{SECRET_REDACTION}\"',
        ),
        (
            rf'{{\"api_key\":\"{SECRET_REDACTION}\"}}',
            rf'{{\"api_key\":\"{SECRET_REDACTION}\"}}',
        ),
    )

    for source, expected in cases:
        _assert_sanitizer_fixed_point(source, expected)


def test_structural_label_scanning_has_no_fail_open_length_threshold() -> None:
    for label_length in (79, 80, 81, 256, 200_000):
        sensitive_prefix = "api_key."
        sensitive_label = sensitive_prefix + "x" * (
            label_length - len(sensitive_prefix)
        )
        _assert_sanitizer_fixed_point(
            f"{sensitive_label}=topsecret",
            f"{sensitive_label}={SECRET_REDACTION}",
        )

        metadata_suffix = ".token_count"
        safe_label = "x" * (label_length - len(metadata_suffix)) + metadata_suffix
        safe_source = f"{safe_label}=7; long metadata remains safe"
        _assert_sanitizer_fixed_point(safe_source, safe_source)

    for suffix_length in (80, 81, 256):
        label = f"api_key.{('x' * suffix_length)}"
        _assert_sanitizer_fixed_point(
            f"{label}=topsecret",
            f"{label}={SECRET_REDACTION}",
        )


def test_structural_punctuation_labels_share_mapping_key_normalization() -> None:
    label_pairs = (
        ("headers[api_key]", "headers[token_count]"),
        (r'credentials[\"api_key\"]', r'credentials[\"token_count\"]'),
        ('credentials["api_key"]', 'credentials["token_count"]'),
        ("[api_key]", "[token_count]"),
        ("api$key", "token$count"),
        ("headers(api_key)", "headers(token_count)"),
        ("headers{api_key}", "headers{token_count}"),
        ("headers@api-key", "headers@token-count"),
        ("headers/api/key", "headers/token/count"),
        ("headers→api_key", "headers→token_count"),
        ("headers【api_key】", "headers【token_count】"),
    )

    for sensitive_label, safe_label in label_pairs:
        for prefix in ("", "status=ok; ", "status=ok | "):
            _assert_sanitizer_fixed_point(
                f"{prefix}{sensitive_label}=topsecret",
                f"{prefix}{sensitive_label}={SECRET_REDACTION}",
            )

        safe_source = f"{safe_label}=7; punctuation metadata remains safe"
        _assert_sanitizer_fixed_point(safe_source, safe_source)


def test_additional_sensitive_families_share_mapping_and_structural_parity() -> None:
    for label in _ADDITIONAL_SENSITIVE_LABELS:
        assert sanitize_diagnostic_value({label: "secret-one"}) == {
            label: SECRET_REDACTION
        }
        _assert_sanitizer_fixed_point(
            f"{label}=secret-one",
            f"{label}={SECRET_REDACTION}",
        )
        for wrapper in _QUOTED_STRUCTURAL_WRAPPERS:
            source = (
                f"message={wrapper}{label}=secret-one{wrapper} field=ok"
            )
            _assert_sanitizer_fixed_point(
                source,
                source.replace("secret-one", SECRET_REDACTION),
            )


def test_additional_sensitive_family_metadata_remains_visible() -> None:
    for label in (
        "session_duration",
        "session_state",
        "jwt_algorithm",
        "jwt_header",
        "access_jwt_header",
        "passphrase_policy",
        "passphrase_hint",
        "user_passphrase_hint",
    ):
        assert sanitize_diagnostic_value({label: "visible"}) == {
            label: "visible"
        }
        _assert_sanitizer_fixed_point(f"{label}=visible", f"{label}=visible")


def test_punctuated_sensitive_labels_share_mapping_structural_parity() -> None:
    for label, prefix in product(
        _PUNCTUATED_SENSITIVE_LABELS,
        ("", "safe=ok; ", "safe=ok | "),
    ):
        assert sanitize_diagnostic_value({label: "secret-one"}) == {
            label: SECRET_REDACTION
        }
        source = f"{prefix}{label}=secret-one"
        expected = f"{prefix}{label}={SECRET_REDACTION}"
        _assert_sanitizer_fixed_point(source, expected)

        for wrapper in _QUOTED_STRUCTURAL_WRAPPERS:
            quoted = (
                f"message={wrapper}{label}=secret-one{wrapper} field=ok"
            )
            _assert_sanitizer_fixed_point(
                quoted,
                quoted.replace("secret-one", SECRET_REDACTION),
            )


def test_punctuation_field_boundaries_remain_safe() -> None:
    for source in (
        "safe=token_count;field=ok",
        "safe=token_count|note=ok",
        "ratio=1,field=ok",
        "message=api,key field=ok",
        "message=api|key note=ok",
        "status=ok; ordinary=value",
    ):
        _assert_sanitizer_fixed_point(source, source)


def test_multiline_sensitive_assignments_keep_independent_label_boundaries() -> None:
    line_boundaries = ("\n", "\r\n", "\r", "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}")
    first_labels = (
        "api::key",
        "session_id",
        "jwt",
        "passphrase",
        "deepseek::api::key",
    )
    second_labels = ("api,key", "api;key", "api|key")

    for first_label, second_label, boundary in product(
        first_labels,
        second_labels,
        line_boundaries,
    ):
        source = (
            f"{first_label}=secret-one{boundary}"
            f"{second_label}=secret-two"
        )
        expected = (
            f"{first_label}={SECRET_REDACTION}{boundary}"
            f"{second_label}={SECRET_REDACTION}"
        )
        _assert_sanitizer_fixed_point(source, expected)


def test_multiline_header_and_unfinished_value_controls() -> None:
    for boundary in (
        "\n",
        "\r\n",
        "\r",
        "\N{LINE SEPARATOR}",
        "\N{PARAGRAPH SEPARATOR}",
    ):
        safe_header = f"Cookie: token count{boundary}note=ok"
        _assert_sanitizer_fixed_point(safe_header, safe_header)

        unfinished = f"api_key={boundary}secret-one field=ok"
        _assert_sanitizer_fixed_point(
            unfinished,
            unfinished.replace("secret-one", SECRET_REDACTION),
        )

        cookie_continuation = f"Cookie:{boundary}a_1 field=ok"
        _assert_sanitizer_fixed_point(
            cookie_continuation,
            cookie_continuation.replace("a_1", SECRET_REDACTION),
        )


def test_url_authorities_are_not_structural_assignment_labels() -> None:
    safe_urls = (
        "https://auth.example.com:443/path",
        "https://token.example.com:8443/health",
        "http://cookie.internal:8080/health",
        "https://api-key.example:8443/v1",
        "https://auth.example.com:443/path?status=ok&token_count=7#healthy",
        (
            "https://example.test/path?redirect="
            "https://auth.example.com:443/health"
        ),
    )

    for safe_url in safe_urls:
        _assert_sanitizer_fixed_point(safe_url, safe_url)

    _assert_sanitizer_fixed_point(
        "https://agent:topsecret@auth.example.com:443/path?status=ok",
        f"https://{SECRET_REDACTION}@auth.example.com:443/path?status=ok",
    )
    _assert_sanitizer_fixed_point(
        "https://auth.example.com:443/path?api_key=topsecret",
        f"https://auth.example.com:443/path?api_key={SECRET_REDACTION}",
    )
    _assert_sanitizer_fixed_point(
        "https://example.test/path/api$key=topsecret",
        f"https://example.test/path/api$key={SECRET_REDACTION}",
    )
    _assert_sanitizer_fixed_point(
        "https://auth.example.com:443\N{NO-BREAK SPACE}api_key=topsecret",
        (
            "https://auth.example.com:443\N{NO-BREAK SPACE}"
            f"api_key={SECRET_REDACTION}"
        ),
    )


def test_escaped_url_schemes_preserve_spelling_and_redact_userinfo() -> None:
    for safe_url in (
        r"https:\/\/auth.example.com:443/path",
        r"https:\/\/token.example.com:8443\/health?status=ok",
    ):
        _assert_sanitizer_fixed_point(safe_url, safe_url)

    _assert_sanitizer_fixed_point(
        r"https:\/\/agent:topsecret@auth.example.com:443/path",
        rf"https:\/\/{SECRET_REDACTION}@auth.example.com:443/path",
    )
    _assert_sanitizer_fixed_point(
        (
            r"https://outer.example/path?redirect="
            r"https:\/\/agent:topsecret@auth.example.com:443/path"
        ),
        (
            r"https://outer.example/path?redirect="
            rf"https:\/\/{SECRET_REDACTION}@auth.example.com:443/path"
        ),
    )
    _assert_sanitizer_fixed_point(
        (
            r"https:\/\/outer.example/path?redirect="
            r"https:\/\/agent:topsecret@auth.example.com:443/path"
        ),
        (
            r"https:\/\/outer.example/path?redirect="
            rf"https:\/\/{SECRET_REDACTION}@auth.example.com:443/path"
        ),
    )


def test_mixed_escaped_url_slashes_preserve_spelling_and_redact_userinfo() -> None:
    schemes = (r"https:/\/", r"https:\//")
    for scheme in schemes:
        safe_url = f"{scheme}auth.example.com:443/path"
        _assert_sanitizer_fixed_point(safe_url, safe_url)

        _assert_sanitizer_fixed_point(
            f"{scheme}agent:topsecret@auth.example.com:443/path",
            f"{scheme}{SECRET_REDACTION}@auth.example.com:443/path",
        )

    _assert_sanitizer_fixed_point(
        (
            r"https:/\/outer.example/path?redirect="
            r"https:\//agent:topsecret@auth.example.com:443/path "
            r"https:/\/user:second-secret@token.example.com:8443/health"
        ),
        (
            r"https:/\/outer.example/path?redirect="
            rf"https:\//{SECRET_REDACTION}@auth.example.com:443/path "
            rf"https:/\/{SECRET_REDACTION}@token.example.com:8443/health"
        ),
    )


def test_nfkc_structural_delimiters_preserve_their_original_glyphs() -> None:
    colon_delimiters = (
        "\N{PRESENTATION FORM FOR VERTICAL COLON}",
        "\N{SMALL COLON}",
        "\N{FULLWIDTH COLON}",
    )
    equals_delimiters = (
        "\N{SUPERSCRIPT EQUALS SIGN}",
        "\N{SUBSCRIPT EQUALS SIGN}",
        "\N{SMALL EQUALS SIGN}",
        "\N{FULLWIDTH EQUALS SIGN}",
    )

    for delimiter in (*colon_delimiters, *equals_delimiters):
        for label, value in (
            ("api_key", "topsecret"),
            ("Authorization", "topsecret"),
            ("Cookie", "a_1"),
        ):
            _assert_sanitizer_fixed_point(
                f"{label}{delimiter}{value}",
                f"{label}{delimiter}{SECRET_REDACTION}",
            )
        safe_ratio = f"ratio{delimiter}1"
        _assert_sanitizer_fixed_point(safe_ratio, safe_ratio)

    for delimiter in equals_delimiters:
        _assert_sanitizer_fixed_point(
            f"https://auth.example/path?api_key{delimiter}topsecret",
            (
                f"https://auth.example/path?api_key{delimiter}"
                f"{SECRET_REDACTION}"
            ),
        )

    for delimiter in colon_delimiters:
        safe_tag = f"https://auth.example/path#auth{delimiter}section"
        _assert_sanitizer_fixed_point(safe_tag, safe_tag)


def test_unicode_spaces_support_labels_without_cross_field_contamination() -> None:
    spaces = tuple(
        chr(codepoint)
        for codepoint in (
            0x0020,
            0x00A0,
            0x1680,
            0x2000,
            0x2001,
            0x2002,
            0x2003,
            0x2004,
            0x2005,
            0x2006,
            0x2007,
            0x2008,
            0x2009,
            0x200A,
            0x202F,
            0x205F,
            0x3000,
        )
    )

    for space in spaces:
        for first, second in (
            ("api", "key"),
            ("access", "key"),
            ("private", "key"),
            ("client", "secret"),
        ):
            label = f"{first}{space}{second}"
            _assert_sanitizer_fixed_point(
                f"{label}=topsecret",
                f"{label}={SECRET_REDACTION}",
            )

        for parts in (
            ("Authorization", "Header"),
            ("token", "payload"),
            ("api", "key", "material"),
        ):
            label = space.join(parts)
            _assert_sanitizer_fixed_point(
                f"{label}=topsecret",
                f"{label}={SECRET_REDACTION}",
            )

        cookie_label = f"session{space}cookie"
        _assert_sanitizer_fixed_point(
            f"{cookie_label}=a_1",
            f"{cookie_label}={SECRET_REDACTION}",
        )

        for safe_source in (
            f"safe=token_count{space}field=ok",
            f"ratio{space}field=ok",
            f"password{space}policy=rotate-quarterly",
        ):
            _assert_sanitizer_fixed_point(safe_source, safe_source)


@pytest.mark.parametrize(
    ("credential_core", "former_boundary"),
    (
        ("token", 35),
        ("authorization", 27),
        ("api key", 34),
        ("private key", 30),
    ),
)
def test_structural_credential_cores_ignore_compact_alias_length_limits(
    credential_core: str,
    former_boundary: int,
) -> None:
    for suffix_length in (
        former_boundary - 3,
        former_boundary - 1,
        former_boundary,
        former_boundary + 1,
        former_boundary + 3,
        256,
    ):
        label = f"{credential_core} {'x' * suffix_length}"
        source = f"{label}=secret-one"
        expected = f"{label}={SECRET_REDACTION}"

        _assert_sanitizer_fixed_point(source, expected)
        assert sanitize_diagnostic_value({label: "secret-one"}) == {
            label: SECRET_REDACTION
        }


def test_structural_credential_core_scan_is_linear_for_huge_suffixes() -> None:
    long_single_chunk = f"token {'x' * 200_000}"
    many_chunks = f"authorization {' '.join('x' for _ in range(50_000))}"

    for label in (long_single_chunk, many_chunks):
        sanitized = sanitize_diagnostic_value(f"{label}=secret-one")

        assert sanitized == f"{label}={SECRET_REDACTION}"
        assert sanitize_diagnostic_value(sanitized) == sanitized


def test_arbitrary_prefix_metadata_stops_cross_field_label_contamination() -> None:
    metadata_labels = (
        "custom_token_count",
        "custom_password_policy",
        "tenant_refresh_token_rotation_status",
    )

    for space in _UNICODE_ZS_SPACES:
        for metadata_label in metadata_labels:
            safe_source = f"safe={metadata_label}{space}field=ok"
            _assert_sanitizer_fixed_point(safe_source, safe_source)
            assert sanitize_diagnostic_value({metadata_label: "visible"}) == {
                metadata_label: "visible"
            }

        for credential_label in ("api key", "token payload"):
            source = f"safe=custom_token_count{space}{credential_label}=secret-one"
            expected = (
                f"safe=custom_token_count{space}{credential_label}="
                f"{SECRET_REDACTION}"
            )
            _assert_sanitizer_fixed_point(source, expected)


@pytest.mark.parametrize(
    "label",
    _METADATA_QUALIFIED_CREDENTIAL_LABELS,
)
def test_structural_labels_preserve_mapping_parity_after_metadata_terminals(
    label: str,
) -> None:
    assert sanitize_diagnostic_value({label: "secret-one"}) == {
        label: SECRET_REDACTION
    }
    for prefix in ("", "status=ok; ", "status=ok | "):
        _assert_sanitizer_fixed_point(
            f"{prefix}{label}=secret-one",
            f"{prefix}{label}={SECRET_REDACTION}",
        )


@pytest.mark.parametrize("label", _METADATA_QUALIFIED_CREDENTIAL_LABELS)
def test_previous_assignment_values_do_not_hide_full_credential_labels(
    label: str,
) -> None:
    boundaries = (
        *_UNICODE_ZS_SPACES,
        "\t",
        ";",
        "; ",
        "|",
        " | ",
        "\N{FULLWIDTH SEMICOLON}",
        "\N{IDEOGRAPHIC COMMA}",
    )

    assert sanitize_diagnostic_value({label: "secret-one"}) == {
        label: SECRET_REDACTION
    }
    for previous_value, boundary in product(
        ("ok", "custom_token_count"),
        boundaries,
    ):
        prefix = f"safe={previous_value}{boundary}"
        _assert_sanitizer_fixed_point(
            f"{prefix}{label}=secret-one",
            f"{prefix}{label}={SECRET_REDACTION}",
        )


def test_previous_safe_metadata_values_do_not_contaminate_next_safe_field() -> None:
    boundaries = (
        *_UNICODE_ZS_SPACES,
        "\t",
        ";",
        " | ",
        "\N{FULLWIDTH SEMICOLON}",
    )
    for metadata_label, boundary in product(
        (
            "custom_token_count",
            "custom_password_policy",
            "tenant_refresh_token_rotation_status",
        ),
        boundaries,
    ):
        source = f"safe={metadata_label}{boundary}field=ok"
        _assert_sanitizer_fixed_point(source, source)


def test_previous_values_keep_compact_alias_fallback_for_the_next_label() -> None:
    for space, compact_alias in product(
        _UNICODE_ZS_SPACES,
        ("userpassword", "clientsecretstring", "sessiontokenvalue"),
    ):
        label = f"worker{space}{compact_alias}"
        _assert_sanitizer_fixed_point(
            f"safe=ok{space}{label}=secret-one",
            f"safe=ok{space}{label}={SECRET_REDACTION}",
        )


def test_compact_credential_value_prefixes_share_mapping_parity() -> None:
    for core, suffix, space in product(
        _COMPACT_VALUE_PREFIX_CORES,
        _COMPACT_SENSITIVE_VALUE_SUFFIXES,
        (*_UNICODE_ZS_SPACES, "\t"),
    ):
        label = f"{core}{space}{suffix}"
        source = f"message={label}=secret-one"
        expected = f"message={label}={SECRET_REDACTION}"

        assert sanitize_diagnostic_value({label: "secret-one"}) == {
            label: SECRET_REDACTION
        }
        _assert_sanitizer_fixed_point(source, expected)


def test_compact_credential_value_prefixes_follow_prior_boundaries() -> None:
    boundaries = (
        *_UNICODE_ZS_SPACES,
        "\t",
        ";",
        "; ",
        "|",
        " | ",
        "\N{FULLWIDTH SEMICOLON}",
        "\N{IDEOGRAPHIC COMMA}",
    )
    for core, suffix, boundary in product(
        _COMPACT_VALUE_PREFIX_CORES,
        _COMPACT_SENSITIVE_VALUE_SUFFIXES,
        boundaries,
    ):
        source = f"safe=ok{boundary}{core} {suffix}=secret-one"
        expected = source.replace("secret-one", SECRET_REDACTION)
        _assert_sanitizer_fixed_point(source, expected)


def test_compact_credential_value_prefixes_preserve_punctuation_forms() -> None:
    for core, suffix, punctuation in product(
        _COMPACT_VALUE_PREFIX_CORES,
        _COMPACT_SENSITIVE_VALUE_SUFFIXES,
        ("_", "-", ".", "$", "/", "@", "[", "]", "(", ")", "{", "}"),
    ):
        label = f"{core}{punctuation}{suffix}"
        source = f"message={label}=secret-one"
        expected = f"message={label}={SECRET_REDACTION}"
        assert sanitize_diagnostic_value({label: "secret-one"}) == {
            label: SECRET_REDACTION
        }
        _assert_sanitizer_fixed_point(source, expected)


def test_quoted_compact_credential_value_prefixes_redact_inner_values() -> None:
    for core, suffix, wrapper in product(
        _COMPACT_VALUE_PREFIX_CORES,
        _COMPACT_SENSITIVE_VALUE_SUFFIXES,
        _QUOTED_STRUCTURAL_WRAPPERS,
    ):
        source = (
            f"message={wrapper}{core} {suffix}=secret-one{wrapper} field=ok"
        )
        expected = source.replace("secret-one", SECRET_REDACTION)
        _assert_sanitizer_fixed_point(source, expected)


def test_token_shaped_safe_values_remain_field_isolated() -> None:
    for safe_value in ("secret-one", "token-shaped", "api-key"):
        source = f"message={safe_value} field=ok"
        _assert_sanitizer_fixed_point(source, source)


def test_many_compact_credential_value_prefixes_remain_linear() -> None:
    labels = tuple(
        f"{core} {suffix}"
        for core, suffix in product(
            _COMPACT_VALUE_PREFIX_CORES,
            _COMPACT_SENSITIVE_VALUE_SUFFIXES,
        )
    )
    sources: list[str] = []
    expected: list[str] = []
    for index in range(2_000):
        label = labels[index % len(labels)]
        sources.append(f"message={label}=secret-{index} field=ok")
        expected.append(
            f"message={label}={SECRET_REDACTION} field=ok"
        )

    source = "; ".join(sources)
    sanitized = sanitize_diagnostic_value(source)

    assert sanitized == "; ".join(expected)
    assert sanitize_diagnostic_value(sanitized) == sanitized


def test_normalized_compact_prefixes_share_structural_mapping_parity() -> None:
    separators = (*_UNICODE_ZS_SPACES, "\t")
    case_count = 0
    for parts, suffix, separator in product(
        _NORMALIZED_COMPACT_BASE_PARTS,
        _NORMALIZED_COMPACT_SENSITIVE_SUFFIXES,
        separators,
    ):
        for spelling in _normalized_compact_spellings(parts):
            label = f"{spelling}{separator}{suffix}"
            source = f"message={label}=secret-one"
            expected = f"message={label}={SECRET_REDACTION}"

            assert sanitize_diagnostic_value({label: "secret-one"}) == {
                label: SECRET_REDACTION
            }
            assert sanitize_diagnostic_value(source) == expected
            assert sanitize_diagnostic_value(expected) == expected
            case_count += 1

    assert case_count == 5_400


def test_normalized_compact_prefixes_work_top_level_and_after_assignments() -> None:
    boundaries = (
        " ",
        "\t",
        ";",
        " | ",
        "\N{FULLWIDTH SEMICOLON}",
        "\N{IDEOGRAPHIC COMMA}",
    )
    for parts, suffix, boundary in product(
        _NORMALIZED_COMPACT_BASE_PARTS,
        _NORMALIZED_COMPACT_SENSITIVE_SUFFIXES,
        boundaries,
    ):
        for spelling in _normalized_compact_spellings(parts):
            label = f"{spelling} {suffix}"
            top_level = f"{label}=secret-one"
            prior_assignment = f"safe=ok{boundary}{label}=secret-one"
            _assert_sanitizer_fixed_point(
                top_level,
                f"{label}={SECRET_REDACTION}",
            )
            _assert_sanitizer_fixed_point(
                prior_assignment,
                prior_assignment.replace("secret-one", SECRET_REDACTION),
            )


def test_camel_case_compact_prefixes_redact_nested_quoted_values() -> None:
    case_count = 0
    for parts, suffix, wrapper in product(
        _NORMALIZED_COMPACT_BASE_PARTS,
        _NORMALIZED_COMPACT_SENSITIVE_SUFFIXES,
        _QUOTED_STRUCTURAL_WRAPPERS,
    ):
        first, second = parts
        camel_case = f"{first}{second.title()}"
        source = (
            f"message={wrapper}{camel_case} {suffix}=secret-one"
            f"{wrapper} field=ok"
        )
        expected = source.replace("secret-one", SECRET_REDACTION)
        _assert_sanitizer_fixed_point(source, expected)
        case_count += 1

    assert case_count == 120


def test_normalized_compact_prefixes_keep_token_shaped_values_safe() -> None:
    for safe_value, following_field in product(
        ("secret-one", "token-shaped", "api-key"),
        ("field", "note"),
    ):
        source = f"message={safe_value} {following_field}=ok"
        _assert_sanitizer_fixed_point(source, source)


def test_many_normalized_compact_prefixes_remain_idempotent() -> None:
    labels = tuple(
        f"{spelling} value"
        for parts in _NORMALIZED_COMPACT_BASE_PARTS
        for spelling in _normalized_compact_spellings(parts)
    )
    sources: list[str] = []
    expected: list[str] = []
    for index in range(2_000):
        label = labels[index % len(labels)]
        sources.append(f"message={label}=secret-{index} field=ok")
        expected.append(
            f"message={label}={SECRET_REDACTION} field=ok"
        )

    source = "; ".join(sources)
    sanitized = sanitize_diagnostic_value(source)

    assert sanitized == "; ".join(expected)
    assert sanitize_diagnostic_value(sanitized) == sanitized


def test_quoted_safe_assignment_values_sanitize_embedded_credentials() -> None:
    separators = (*_UNICODE_ZS_SPACES, "\t")
    for wrapper, separator, label, later_token in product(
        _QUOTED_STRUCTURAL_WRAPPERS,
        separators,
        _EMBEDDED_STRUCTURAL_CREDENTIAL_LABELS,
        (False, True),
    ):
        spaced_label = separator.join(label.split())
        prefix = f"note{separator}" if later_token else ""
        interior = f"{prefix}{spaced_label}=secret-one"
        expected_interior = f"{prefix}{spaced_label}={SECRET_REDACTION}"
        source = f"message={wrapper}{interior}{wrapper} field=ok"
        expected = f"message={wrapper}{expected_interior}{wrapper} field=ok"

        assert sanitize_diagnostic_value({label: "secret-one"}) == {
            label: SECRET_REDACTION
        }
        sanitized = sanitize_diagnostic_value(source)
        assert sanitized == expected
        assert sanitize_diagnostic_value(sanitized) == expected


def test_quoted_safe_prose_preserves_closers_and_following_field_boundaries() -> None:
    boundaries = (
        *_UNICODE_ZS_SPACES,
        "\t",
        ";",
        "; ",
        "|",
        " | ",
        "\N{FULLWIDTH SEMICOLON}",
        "\N{IDEOGRAPHIC COMMA}",
    )
    for wrapper, boundary, safe_interior in product(
        _QUOTED_STRUCTURAL_WRAPPERS,
        boundaries,
        ("custom token count", "custom password policy", "ordinary prose"),
    ):
        safe_source = (
            f"message={wrapper}{safe_interior}{wrapper}{boundary}field=ok"
        )
        _assert_sanitizer_fixed_point(safe_source, safe_source)

        credential_source = (
            f"message={wrapper}{safe_interior}{wrapper}{boundary}"
            "api key=secret-one"
        )
        credential_expected = (
            f"message={wrapper}{safe_interior}{wrapper}{boundary}"
            f"api key={SECRET_REDACTION}"
        )
        _assert_sanitizer_fixed_point(
            credential_source,
            credential_expected,
        )


def test_quoted_inner_escapes_preserve_the_true_outer_closer() -> None:
    for outer_wrapper, inner_quote in _ESCAPED_INNER_QUOTE_CASES:
        other_quote = "'" if outer_wrapper.endswith('"') else '"'
        safe_interior = (
            f"note {inner_quote}quoted{inner_quote} and "
            f"{other_quote}other{other_quote} custom token count"
        )
        safe_source = (
            f"message={outer_wrapper}{safe_interior}{outer_wrapper} field=ok"
        )
        _assert_sanitizer_fixed_point(safe_source, safe_source)

        credential_interior = (
            f"note {inner_quote}quoted{inner_quote} "
            "api key=secret-one"
        )
        credential_source = (
            f"message={outer_wrapper}{credential_interior}{outer_wrapper} "
            "field=ok"
        )
        credential_expected = credential_source.replace(
            "secret-one",
            SECRET_REDACTION,
        )
        _assert_sanitizer_fixed_point(
            credential_source,
            credential_expected,
        )

        marker_source = credential_source.replace(
            "secret-one",
            SECRET_REDACTION,
        )
        _assert_sanitizer_fixed_point(marker_source, marker_source)


def test_quoted_inner_escapes_preserve_crlf_and_malformed_input() -> None:
    for outer_wrapper, inner_quote in _ESCAPED_INNER_QUOTE_CASES:
        safe_line = (
            f"message={outer_wrapper}note {inner_quote}quoted{inner_quote} "
            f"custom token count{outer_wrapper} field=ok"
        )
        credential_line = (
            f"message={outer_wrapper}note {inner_quote}quoted{inner_quote} "
            f"api key=secret-one{outer_wrapper} field=ok"
        )
        source = f"{safe_line}\r\n{credential_line}\r\n"
        expected = source.replace("secret-one", SECRET_REDACTION)
        _assert_sanitizer_fixed_point(source, expected)

        malformed = (
            f"message={outer_wrapper}note {inner_quote}quoted{inner_quote} "
            "api key=secret-one"
        )
        malformed_expected = malformed.replace(
            "secret-one",
            SECRET_REDACTION,
        )
        _assert_sanitizer_fixed_point(malformed, malformed_expected)


def test_sensitive_assignments_continue_across_lf_and_crlf() -> None:
    for newline, label in product(
        ("\n", "\r\n"),
        ("api_key", "client_secret", "token"),
    ):
        source = f"{label}={newline}secret-one field=ok"
        expected = source.replace("secret-one", SECRET_REDACTION)
        _assert_sanitizer_fixed_point(source, expected)


def test_quoted_sensitive_assignments_continue_across_lf_and_crlf() -> None:
    for newline, outer_wrapper in product(
        ("\n", "\r\n"),
        _QUOTED_STRUCTURAL_WRAPPERS,
    ):
        source = (
            f"message={outer_wrapper}api key={newline}secret-one"
            f"{outer_wrapper} field=ok"
        )
        expected = source.replace("secret-one", SECRET_REDACTION)
        _assert_sanitizer_fixed_point(source, expected)

        malformed = (
            f"message={outer_wrapper}api key={newline}secret-one"
        )
        malformed_expected = malformed.replace(
            "secret-one",
            SECRET_REDACTION,
        )
        _assert_sanitizer_fixed_point(malformed, malformed_expected)


def test_unquoted_safe_assignment_values_keep_credential_first_tokens() -> None:
    separators = (*_UNICODE_ZS_SPACES, "\t")
    for separator, label, later_token in product(
        separators,
        _EMBEDDED_STRUCTURAL_CREDENTIAL_LABELS,
        (False, True),
    ):
        spaced_label = separator.join(label.split())
        prefix = f"note{separator}" if later_token else ""
        source = f"message={prefix}{spaced_label}=secret-one"
        expected = f"message={prefix}{spaced_label}={SECRET_REDACTION}"
        _assert_sanitizer_fixed_point(source, expected)


def test_quoted_embedded_exact_markers_are_byte_idempotent() -> None:
    for wrapper, label in product(
        _QUOTED_STRUCTURAL_WRAPPERS,
        _EMBEDDED_STRUCTURAL_CREDENTIAL_LABELS,
    ):
        source = (
            f"message={wrapper}{label}={SECRET_REDACTION}{wrapper} field=ok"
        )
        _assert_sanitizer_fixed_point(source, source)


def test_many_quoted_assignments_are_sanitized_without_recursive_depth() -> None:
    sources: list[str] = []
    expected: list[str] = []
    for index in range(2_000):
        wrapper = _QUOTED_STRUCTURAL_WRAPPERS[index % len(_QUOTED_STRUCTURAL_WRAPPERS)]
        label = _EMBEDDED_STRUCTURAL_CREDENTIAL_LABELS[
            index % len(_EMBEDDED_STRUCTURAL_CREDENTIAL_LABELS)
        ]
        sources.append(f"message={wrapper}{label}=secret-{index}{wrapper}")
        expected.append(f"message={wrapper}{label}={SECRET_REDACTION}{wrapper}")

    source = " ".join(sources)
    sanitized = sanitize_diagnostic_value(source)

    assert sanitized == " ".join(expected)
    assert sanitize_diagnostic_value(sanitized) == sanitized


def test_many_inner_escaped_quotes_keep_outer_assignment_boundaries() -> None:
    sources: list[str] = []
    expected: list[str] = []
    for index in range(2_000):
        outer_wrapper, inner_quote = _ESCAPED_INNER_QUOTE_CASES[
            index % len(_ESCAPED_INNER_QUOTE_CASES)
        ]
        interior = (
            f"note {inner_quote}quoted-{index}{inner_quote} "
            f"api key=secret-{index}"
        )
        sources.append(
            f"message={outer_wrapper}{interior}{outer_wrapper} field=ok"
        )
        expected.append(
            f"message={outer_wrapper}note "
            f"{inner_quote}quoted-{index}{inner_quote} "
            f"api key={SECRET_REDACTION}{outer_wrapper} field=ok"
        )

    source = "; ".join(sources)
    sanitized = sanitize_diagnostic_value(source)

    assert sanitized == "; ".join(expected)
    assert sanitize_diagnostic_value(sanitized) == sanitized


def test_mapping_and_structural_label_matrix_share_full_candidate_semantics() -> None:
    credential_cores = (
        ("token",),
        ("authorization",),
        ("password",),
        ("api", "key"),
        ("access", "key"),
        ("private", "key"),
        ("client", "secret"),
        ("cookie",),
    )
    metadata_terminals = ("count", "status", "policy", "id", "algorithm", "length")
    qualifier_cases = (
        ("status", False),
        ("payload", True),
        ("value", True),
    )
    arbitrary_prefixes = ((), ("tenant", "custom"))

    for space, prefix, core, terminal, qualifier_case in product(
        _UNICODE_ZS_SPACES,
        arbitrary_prefixes,
        credential_cores,
        metadata_terminals,
        qualifier_cases,
    ):
        qualifier, should_redact = qualifier_case
        label = space.join((*prefix, *core, terminal, qualifier))
        expected_value = SECRET_REDACTION if should_redact else "secret-one"

        mapping = sanitize_diagnostic_value({label: "secret-one"})
        structural = sanitize_diagnostic_value(f"{label}=secret-one")

        assert mapping == {label: expected_value}
        assert structural == f"{label}={expected_value}"


def test_safe_qualifier_values_do_not_contaminate_a_following_field() -> None:
    separators = (*_UNICODE_ZS_SPACES, "\t")
    credential_cores = (
        ("token",),
        ("authorization",),
        ("password",),
        ("api", "key"),
        ("access", "key"),
        ("private", "key"),
        ("client", "secret"),
        ("cookie",),
    )
    metadata_qualifiers = (
        "count",
        "status",
        "policy",
        "id",
        "algorithm",
        "length",
        "field",
    )
    token_shaped_safe_values = ("secret-one", "token-shaped", "api-key")

    case_count = 0
    for space, prefix, core, qualifier, safe_value in product(
        separators,
        ((), ("tenant", "custom")),
        credential_cores,
        metadata_qualifiers,
        token_shaped_safe_values,
    ):
        label = space.join((*prefix, *core, qualifier, "status"))
        source = f"{label}={safe_value}{space}field=ok"
        sanitized = sanitize_diagnostic_value(source)

        assert sanitized == source
        assert sanitize_diagnostic_value(sanitized) == source
        case_count += 1

    assert case_count == 6_048


def test_compact_structural_alias_suffixes_work_with_every_zs_space() -> None:
    for space, compact_alias in product(
        _UNICODE_ZS_SPACES,
        ("userpassword", "clientsecretstring", "sessiontokenvalue"),
    ):
        label = f"worker{space}{compact_alias}"
        _assert_sanitizer_fixed_point(
            f"{label}=secret-one",
            f"{label}={SECRET_REDACTION}",
        )


def test_full_structural_candidate_scan_is_linear_with_many_chunks() -> None:
    label = " ".join((*(("context",) * 25_000), "token", "count", "payload"))
    sanitized = sanitize_diagnostic_value(f"{label}=secret-one")

    assert sanitized == f"{label}={SECRET_REDACTION}"
    assert sanitize_diagnostic_value(sanitized) == sanitized


def test_previous_assignment_full_candidate_scan_is_linear_with_many_chunks() -> None:
    label = " ".join((*(("context",) * 25_000), "token", "count", "payload"))
    source = f"safe=ok {label}=secret-one"
    expected = f"safe=ok {label}={SECRET_REDACTION}"

    assert sanitize_diagnostic_value(source) == expected
    assert sanitize_diagnostic_value(expected) == expected


def test_nfkc_url_scheme_colons_preserve_spelling_and_url_boundaries() -> None:
    colon_delimiters = (
        "\N{PRESENTATION FORM FOR VERTICAL COLON}",
        "\N{SMALL COLON}",
        "\N{FULLWIDTH COLON}",
    )
    slash_spellings = ("//", r"\/\/", r"/\/", r"\//")

    for colon, slashes in product(colon_delimiters, slash_spellings):
        scheme = f"https{colon}{slashes}"
        safe_url = f"{scheme}auth.example:443/path?status=ok"
        _assert_sanitizer_fixed_point(safe_url, safe_url)
        _assert_sanitizer_fixed_point(
            f"{scheme}agent:secret-one@auth.example:443/path",
            f"{scheme}{SECRET_REDACTION}@auth.example:443/path",
        )

    first, second, third = colon_delimiters
    source = (
        f"https{first}//outer.example/path?redirect="
        f"https{second}/\\/agent:secret-one@auth.example:443/path "
        f"https{third}\\//user:secret-two@token.example:8443/health"
    )
    expected = (
        f"https{first}//outer.example/path?redirect="
        f"https{second}/\\/{SECRET_REDACTION}@auth.example:443/path "
        f"https{third}\\//{SECRET_REDACTION}@token.example:8443/health"
    )
    _assert_sanitizer_fixed_point(source, expected)


def test_url_path_query_and_fragment_colons_are_safe_tags() -> None:
    safe_urls = (
        "https://auth.example.com:443/path/token:latest",
        "https://token.example.com:8443/path#auth:section",
        "https://cookie.internal:8080/path?tag=token:latest",
        "https://api-key.example:8443/path/api_key:secret-one",
        r"https:\/\/auth.example.com:443\/path#token:latest",
    )
    for safe_url in safe_urls:
        _assert_sanitizer_fixed_point(safe_url, safe_url)

    _assert_sanitizer_fixed_point(
        "https://auth.example.com:443/path?tag=token:latest&api_key=topsecret",
        (
            "https://auth.example.com:443/path?tag=token:latest&"
            f"api_key={SECRET_REDACTION}"
        ),
    )


def test_unicode_field_boundaries_do_not_contaminate_later_labels() -> None:
    boundaries = (
        "\N{FULLWIDTH SEMICOLON}",
        "\N{FULLWIDTH COMMA}",
        "\N{ARABIC SEMICOLON}",
        "\N{IDEOGRAPHIC COMMA}",
        "\N{FULLWIDTH VERTICAL LINE}",
        "\N{NO-BREAK SPACE}",
        "\N{EM SPACE}",
    )
    sensitive_labels = (
        "headers[api_key]",
        "api$key",
        "headers→api_key",
        "headers【api_key】",
    )

    for boundary in boundaries:
        safe_source = f"safe=token_count{boundary}field=ok"
        _assert_sanitizer_fixed_point(safe_source, safe_source)

        for label in sensitive_labels:
            _assert_sanitizer_fixed_point(
                f"safe=token_count{boundary}{label}=topsecret",
                f"safe=token_count{boundary}{label}={SECRET_REDACTION}",
            )

        _assert_sanitizer_fixed_point(
            f"https://auth.example.com:443{boundary}api_key=topsecret",
            (
                f"https://auth.example.com:443{boundary}"
                f"api_key={SECRET_REDACTION}"
            ),
        )


def test_structural_scanner_stays_stable_for_many_segments_and_urls() -> None:
    safe_url = "https://auth.example.com:443/path?status=ok"
    repeated_urls = " ".join(safe_url for _ in range(3_000))
    _assert_sanitizer_fixed_point(repeated_urls, repeated_urls)

    safe_assignments = ";".join(f"field_{index}=ok" for index in range(10_000))
    source = f"{safe_assignments};headers[api_key]=topsecret"
    expected = f"{safe_assignments};headers[api_key]={SECRET_REDACTION}"
    _assert_sanitizer_fixed_point(source, expected)

    escaped_url = r"https:\/\/auth.example.com:443/path/token:latest"
    repeated_escaped_urls = " ".join(escaped_url for _ in range(3_000))
    _assert_sanitizer_fixed_point(repeated_escaped_urls, repeated_escaped_urls)

    unicode_assignments = "\N{FULLWIDTH SEMICOLON}".join(
        f"field_{index}=ok" for index in range(10_000)
    )
    _assert_sanitizer_fixed_point(unicode_assignments, unicode_assignments)

    mixed_url = r"https:/\/auth.example.com:443/path/token:latest"
    repeated_mixed_urls = " ".join(mixed_url for _ in range(3_000))
    _assert_sanitizer_fixed_point(repeated_mixed_urls, repeated_mixed_urls)

    compatible_assignments = ";".join(
        f"field_{index}\N{FULLWIDTH EQUALS SIGN}ok" for index in range(10_000)
    )
    _assert_sanitizer_fixed_point(compatible_assignments, compatible_assignments)


def test_trace_view_preserves_safe_urls_and_redacts_punctuation_labels() -> None:
    trace = load_trace("clean-01")
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.error.message": (
                    f"headers[api_key]={VALUE_SECRET}\n"
                    rf'credentials[\"api_key\"]={VALUE_SECRET}' "\n"
                    f"api$key={VALUE_SECRET}\n"
                    "https://auth.example.com:443/path?status=ok\n"
                    f"https://agent:{VALUE_SECRET}@token.example.com:8443/health\n"
                    rf"https:\/\/agent:{VALUE_SECRET}@auth.example.com:443/path"
                    "\n"
                    r"https:\/\/auth.example.com:443/path escaped-url-safe"
                    "\n"
                    "https://token.example.com:8443/path#auth:section "
                    "colon-tag-safe\n"
                    "safe=token_count\N{FULLWIDTH SEMICOLON}field=ok "
                    "unicode-boundary-safe\n"
                    f"https://auth.example.com:443\N{IDEOGRAPHIC COMMA}"
                    f"api_key={VALUE_SECRET}\n"
                    f"api_key\N{FULLWIDTH EQUALS SIGN}{VALUE_SECRET}\n"
                    f"Authorization\N{PRESENTATION FORM FOR VERTICAL COLON}"
                    f"{VALUE_SECRET}\n"
                    "Cookie\N{FULLWIDTH EQUALS SIGN}a_1\n"
                    f"https://auth.example/path?api_key"
                    f"\N{SUPERSCRIPT EQUALS SIGN}{VALUE_SECRET}\n"
                    "ratio\N{FULLWIDTH EQUALS SIGN}1 compatible-ratio-safe\n"
                    f"api\N{NO-BREAK SPACE}key={VALUE_SECRET}\n"
                    f"access\N{EM SPACE}key={VALUE_SECRET}\n"
                    "safe=token_count\N{NO-BREAK SPACE}field=ok "
                    "unicode-space-safe\n"
                    rf"https:/\/agent:{VALUE_SECRET}@mixed.example.com:443/path"
                    "\n"
                    rf"https:\//agent:{VALUE_SECRET}@mixed-two.example.com:443/path"
                    "\n"
                    r"https:/\/auth.example.com:443/path mixed-url-safe"
                    "\n"
                    f"authorization {'x' * 40}={VALUE_SECRET}\n"
                    "safe=custom_token_count\N{NO-BREAK SPACE}field=ok "
                    "arbitrary-metadata-safe\n"
                    f"safe=custom_token_count\N{EM SPACE}api key={VALUE_SECRET}\n"
                    f"https\N{FULLWIDTH COLON}/\\/agent:{VALUE_SECRET}"
                    "@compatible.example.com:443/path\n"
                    "https\N{SMALL COLON}//auth.example.com:443/path "
                    "compatible-colon-url-safe\n"
                    f"token count payload={VALUE_SECRET}\n"
                    f"tenant custom authorization\N{NO-BREAK SPACE}status"
                    f"\N{NO-BREAK SPACE}header={VALUE_SECRET}\n"
                    "token count status=visible full-label-parity-safe\n"
                    f"safe=ok token count payload={VALUE_SECRET}\n"
                    f"safe=custom_token_count\N{EM SPACE}authorization"
                    f"\N{EM SPACE}status\N{EM SPACE}header={VALUE_SECRET}\n"
                    "safe=custom_password_policy\N{NO-BREAK SPACE}field=ok "
                    "previous-value-safe\n"
                    f'message="api key={VALUE_SECRET}"\n'
                    rf'message=\"token count payload={VALUE_SECRET}\" field=ok'
                    "\n"
                    f"message=private key={VALUE_SECRET}\n"
                    'message="custom token count" field=ok quoted-value-safe\n'
                    "safe=custom token count field=ok field-terminal-safe\n"
                    "cookie=secret-one field=cookie-field-safe\n"
                    f"api_key=\r\n{VALUE_SECRET} field=newline-field-safe\r\n"
                    f'message="api key=\n{VALUE_SECRET}" '
                    "field=quoted-newline-safe\n"
                    rf'message=\"note \\\"quoted\\\" api key={VALUE_SECRET}'
                    r'\" field=escaped-inner-safe'
                    "\n"
                    "safe=custom token count note=ok note-terminal-safe\n"
                    "Cookie: token count\r\n"
                    "message=cookie-newline-boundary-safe\r\n"
                    "headers.cookie=api key\n"
                    "result=header-newline-boundary-safe\n"
                    f"message=sessiontoken value={VALUE_SECRET} "
                    "field=compact-prefix-e2e\n"
                    f"message=ApiKey value={VALUE_SECRET} "
                    "field=normalized-prefix-e2e\n"
                    f"sessionId={VALUE_SECRET} field=session-id-e2e\n"
                    f"pass_phrase={VALUE_SECRET} field=passphrase-e2e\n"
                    f"api::key={VALUE_SECRET} field=punctuated-label-e2e"
                ),
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)
    message = view.spans[0].attributes["tool.error.message"]

    assert isinstance(message, str)
    assert VALUE_SECRET not in message
    assert "https://auth.example.com:443/path?status=ok" in message
    assert (
        f"https://{SECRET_REDACTION}@token.example.com:8443/health" in message
    )
    assert (
        rf"https:\/\/{SECRET_REDACTION}@auth.example.com:443/path" in message
    )
    assert "escaped-url-safe" in message
    assert "colon-tag-safe" in message
    assert "unicode-boundary-safe" in message
    assert "compatible-ratio-safe" in message
    assert "unicode-space-safe" in message
    assert "mixed-url-safe" in message
    assert "arbitrary-metadata-safe" in message
    assert "compatible-colon-url-safe" in message
    assert "full-label-parity-safe" in message
    assert "previous-value-safe" in message
    assert "quoted-value-safe" in message
    assert "field-terminal-safe" in message
    assert "cookie-field-safe" in message
    assert "newline-field-safe" in message
    assert "quoted-newline-safe" in message
    assert "escaped-inner-safe" in message
    assert "note-terminal-safe" in message
    assert "cookie-newline-boundary-safe" in message
    assert "header-newline-boundary-safe" in message
    assert "compact-prefix-e2e" in message
    assert "normalized-prefix-e2e" in message
    assert "session-id-e2e" in message
    assert "passphrase-e2e" in message
    assert "punctuated-label-e2e" in message
    assert "secret-one" not in message
    assert f"api_key=\r\n{SECRET_REDACTION} field=newline-field-safe" in message
    assert sanitize_diagnostic_trace_view(view) == view


def test_trace_view_redacts_additional_sensitive_mapping_families() -> None:
    trace = load_trace("clean-01")
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": {
                    "session_id": VALUE_SECRET,
                    "jwt": VALUE_SECRET,
                    "passphrase": VALUE_SECRET,
                    "api::key": VALUE_SECRET,
                    "jwt_header": "visible",
                    "passphrase_hint": "visible",
                },
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)
    result = view.spans[0].attributes["tool.result"]

    assert result == {
        "session_id": SECRET_REDACTION,
        "jwt": SECRET_REDACTION,
        "passphrase": SECRET_REDACTION,
        "api::key": SECRET_REDACTION,
        "jwt_header": "visible",
        "passphrase_hint": "visible",
    }
    assert sanitize_diagnostic_trace_view(view) == view


def test_trace_view_redacts_composed_multiline_tool_result() -> None:
    trace = load_trace("clean-01")
    source = (
        f"api::key={VALUE_SECRET}\r\n"
        f"api,key={VALUE_SECRET}\N{LINE SEPARATOR}"
        f"passphrase={VALUE_SECRET}\N{PARAGRAPH SEPARATOR}"
        f"api|key={VALUE_SECRET}"
    )
    root = trace.spans[0].model_copy(
        update={
            "attributes": {
                **trace.spans[0].attributes,
                "tool.result": source,
            }
        }
    )
    candidate = trace.model_copy(update={"spans": [root, *trace.spans[1:]]})

    view = DiagnosticTraceView.from_trace(candidate)
    result = view.spans[0].attributes["tool.result"]

    assert isinstance(result, str)
    assert VALUE_SECRET not in result
    assert result == source.replace(VALUE_SECRET, SECRET_REDACTION)
    assert sanitize_diagnostic_trace_view(view) == view
