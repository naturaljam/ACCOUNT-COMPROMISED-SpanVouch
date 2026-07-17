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
