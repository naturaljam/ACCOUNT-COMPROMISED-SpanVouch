from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx
import pytest

from afc.cli.review import main


def _transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _success(payload: object) -> httpx.MockTransport:
    return _transport(lambda request: httpx.Response(200, json=payload, request=request))


@pytest.mark.parametrize(
    ("argv", "method", "path", "body"),
    [
        (
            [
                "create",
                "--trace-id",
                "trace-1",
                "--diagnoser",
                "rules",
                "--verifier",
                "deterministic",
                "--idempotency-key",
                "create-1",
            ],
            "POST",
            "/v1/traces/trace-1/diagnosis-reviews",
            {
                "diagnoser": "rules",
                "verifier": "deterministic",
                "idempotency_key": "create-1",
            },
        ),
        (
            ["show", "--case-id", "case-1"],
            "GET",
            "/v1/diagnosis-reviews/case-1",
            None,
        ),
        (
            [
                "decide",
                "--case-id",
                "case-1",
                "--action",
                "confirm",
                "--expected-version",
                "7",
                "--reviewer-label",
                "operator",
                "--idempotency-key",
                "decision-1",
                "--reason",
                "evidence checked",
            ],
            "POST",
            "/v1/diagnosis-reviews/case-1/decisions",
            {
                "action": "confirm",
                "expected_version": 7,
                "reviewer_label": "operator",
                "idempotency_key": "decision-1",
                "reason": "evidence checked",
            },
        ),
    ],
)
def test_commands_send_exact_http_request_and_canonical_json(
    argv: list[str],
    method: str,
    path: str,
    body: dict[str, object] | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"z": 1, "message": "审计完成", "a": {"b": True}},
            request=request,
        )

    exit_code = main(argv, transport=_transport(handler), environ={})

    assert exit_code == 0
    assert len(seen) == 1
    request = seen[0]
    assert request.method == method
    assert request.url == httpx.URL(f"http://127.0.0.1:8000{path}")
    assert (json.loads(request.content) if request.content else None) == body
    captured = capsys.readouterr()
    assert captured.out == '{"a":{"b":true},"message":"审计完成","z":1}\n'
    assert captured.err == ""


def test_offline_resume_preflights_case_then_posts_explicit_false_consent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "resume_requires_live_api": False,
                    "case": {
                        "status": "verifying",
                        "verification_mode": "hybrid",
                        "diagnoser": "rules",
                    }
                },
                request=request,
            )
        return httpx.Response(200, json={"ok": True}, request=request)

    assert main(
        ["resume", "--case-id", "case-1"],
        transport=_transport(handler),
        environ={},
    ) == 0

    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/v1/diagnosis-reviews/case-1"),
        ("POST", "/v1/diagnosis-reviews/case-1/resume"),
    ]
    assert json.loads(seen[1].content) == {"allow_live_api": False}
    assert capsys.readouterr().err == ""


def test_authoritative_resume_requirement_blocks_status_mode_false_negative(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "resume_requires_live_api": True,
                "case": {
                    "status": "pending_verification",
                    "verification_mode": "deterministic",
                    "diagnoser": "deepseek",
                },
            },
            request=request,
        )

    assert main(
        ["resume", "--case-id", "case-1"],
        transport=_transport(handler),
        environ={},
    ) == 2
    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/v1/diagnosis-reviews/case-1")
    ]
    assert capsys.readouterr().err == (
        "afc-review: live API use requires --allow-live-api\n"
    )


@pytest.mark.parametrize(
    "case",
    (
        {
            "status": "pending_verification",
            "verification_mode": "hybrid",
            "diagnoser": "rules",
        },
        {
            "status": "verifying",
            "verification_mode": "hybrid",
            "diagnoser": "rules",
        },
        {
            "status": "revising",
            "verification_mode": "deterministic",
            "diagnoser": "deepseek",
        },
    ),
)
def test_paid_capable_resume_without_flag_stops_after_safe_get(
    case: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"case": case, "resume_requires_live_api": True},
            request=request,
        )

    assert main(
        ["resume", "--case-id", "case-1"],
        transport=_transport(handler),
        environ={},
    ) == 2
    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/v1/diagnosis-reviews/case-1")
    ]
    assert capsys.readouterr().err == (
        "afc-review: live API use requires --allow-live-api\n"
    )


def test_live_resume_flag_posts_explicit_consent_without_preflight(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    assert main(
        ["resume", "--case-id", "case-1", "--allow-live-api"],
        transport=_transport(handler),
        environ={},
    ) == 0
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {"allow_live_api": True}
    assert capsys.readouterr().err == ""


def test_api_url_flag_overrides_environment(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    exit_code = main(
        [
            "create",
            "--api-url",
            "https://clinic.example/base/",
            "--trace-id",
            "trace-1",
            "--idempotency-key",
            "key-1",
        ],
        transport=_transport(handler),
        environ={"AFC_API_URL": "https://ignored.example"},
    )

    assert exit_code == 0
    assert seen[0].url == httpx.URL(
        "https://clinic.example/base/v1/traces/trace-1/diagnosis-reviews"
    )
    assert capsys.readouterr().err == ""


def test_api_url_uses_environment_when_flag_is_absent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    assert (
        main(
            ["show", "--case-id", "case-1"],
            transport=_transport(handler),
            environ={"AFC_API_URL": "https://clinic.example"},
        )
        == 0
    )
    assert seen[0].url == httpx.URL("https://clinic.example/v1/diagnosis-reviews/case-1")
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("argv", "environ"),
    [
        (
            [
                "show",
                "--case-id",
                "case-1",
                "--api-url",
                "not-a-valid-api-url-private-host",
            ],
            {},
        ),
        (
            ["show", "--case-id", "case-1"],
            {"AFC_API_URL": "ftp://private-user:private-password@secret.example"},
        ),
        (
            ["show", "--case-id", "case-1"],
            {"AFC_API_URL": "https://private-user:private-password@[]/secret"},
        ),
    ],
)
def test_invalid_api_url_is_redacted_before_client_construction(
    argv: list[str],
    environ: Mapping[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("HTTP client constructed for an invalid API URL")

    monkeypatch.setattr(httpx, "Client", unexpected_client)

    exit_code = main(argv, environ=environ)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "afc-review: invalid API URL\n"
    assert all(private not in captured.err for private in ("private", "secret", "example"))


def test_client_invalid_url_exception_is_translated_without_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "private-user:private-password@secret.example"

    def invalid_client(*args: object, **kwargs: object) -> None:
        raise httpx.InvalidURL(secret)

    monkeypatch.setattr(httpx, "Client", invalid_client)

    exit_code = main(["show", "--case-id", "case-1"], environ={})

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "afc-review: invalid API URL\n"
    assert secret not in captured.err


def test_correction_file_is_validated_json_and_passed_through_unchanged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    correction = {
        "status": "diagnosed",
        "failure_type": "invalid_argument",
        "critical_span_ids": ["span-2"],
        "causal_chain": [
            {
                "stage": "cause",
                "statement": "参数类型错误",
                "selectors": [
                    {
                        "span_id": "span-2",
                        "field_path": "attributes.tool.error.type",
                    }
                ],
            }
        ],
        "confidence": 0.9,
    }
    correction_path = tmp_path / "correction.json"
    correction_path.write_text(json.dumps(correction, ensure_ascii=False), encoding="utf-8")
    seen_body: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_body.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True}, request=request)

    exit_code = main(
        [
            "decide",
            "--case-id",
            "case-1",
            "--action",
            "correct",
            "--expected-version",
            "3",
            "--reviewer-label",
            "operator",
            "--idempotency-key",
            "decision-2",
            "--correction-file",
            str(correction_path),
        ],
        transport=_transport(handler),
        environ={},
    )

    assert exit_code == 0
    assert seen_body == [
        {
            "action": "correct",
            "expected_version": 3,
            "reviewer_label": "operator",
            "idempotency_key": "decision-2",
            "correction": correction,
        }
    ]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("contents", ["not-json", "[]", '"text"', "null"])
def test_invalid_correction_stops_before_http_without_echoing_file(
    contents: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    correction_path = tmp_path / "private-correction.json"
    correction_path.write_text(contents, encoding="utf-8")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={}, request=request)

    exit_code = main(
        [
            "decide",
            "--case-id",
            "case-1",
            "--action",
            "correct",
            "--expected-version",
            "1",
            "--reviewer-label",
            "operator",
            "--idempotency-key",
            "decision-1",
            "--correction-file",
            str(correction_path),
        ],
        transport=_transport(handler),
        environ={},
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert calls == 0
    assert captured.out == ""
    assert captured.err == "afc-review: correction file must contain one JSON object\n"
    assert contents not in captured.err
    assert str(correction_path) not in captured.err


@pytest.mark.parametrize(
    "live_args",
    [
        ["--diagnoser", "deepseek", "--verifier", "deterministic"],
        ["--diagnoser", "rules", "--verifier", "hybrid"],
        ["--diagnoser", "deepseek", "--verifier", "hybrid"],
    ],
)
def test_live_create_requires_explicit_flag_before_http(
    live_args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={}, request=request)

    exit_code = main(
        [
            "create",
            "--trace-id",
            "trace-1",
            "--idempotency-key",
            "create-1",
            *live_args,
        ],
        transport=_transport(handler),
        environ={"DEEPSEEK_API_KEY": "must-never-be-read"},
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert calls == 0
    assert captured.out == ""
    assert captured.err == "afc-review: live API use requires --allow-live-api\n"
    assert "must-never-be-read" not in captured.err


def test_explicit_live_flag_allows_http_without_reading_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class KeyGuard(Mapping[str, str]):
        def __getitem__(self, key: str) -> str:
            if key == "DEEPSEEK_API_KEY":
                raise AssertionError("CLI attempted to read provider credential")
            raise KeyError(key)

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(())

        def __len__(self) -> int:
            return 0

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(201, json={"ok": True}, request=request)

    exit_code = main(
        [
            "create",
            "--trace-id",
            "trace-1",
            "--diagnoser",
            "deepseek",
            "--verifier",
            "hybrid",
            "--idempotency-key",
            "create-1",
            "--allow-live-api",
        ],
        transport=_transport(handler),
        environ=KeyGuard(),
    )

    assert exit_code == 0
    assert calls == 1
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "argv",
    [
        [
            "create",
            "--trace-id",
            "trace-1",
            "--idempotency-key",
            "key-1",
        ],
        ["show", "--case-id", "case-1"],
        [
            "decide",
            "--case-id",
            "case-1",
            "--action",
            "reject",
            "--expected-version",
            "1",
            "--reviewer-label",
            "operator",
            "--idempotency-key",
            "decision-1",
            "--reason",
            "not supported",
        ],
    ],
)
def test_offline_commands_do_not_require_live_flag(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv, transport=_success({"ok": True}), environ={}) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_api_4xx_has_stable_exit_and_redacted_diagnostic(
    status: int, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "provider-private-body-should-never-appear"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"detail": {"code": "review_invalid", "private": secret}},
            request=request,
        )

    exit_code = main(
        ["show", "--case-id", "case-1"],
        transport=_transport(handler),
        environ={},
    )

    captured = capsys.readouterr()
    assert exit_code == 3
    assert captured.out == ""
    assert captured.err == (
        f"afc-review: API request failed (status={status}, code=review_invalid)\n"
    )
    assert secret not in captured.err


@pytest.mark.parametrize("status", [500, 502, 503, 599])
def test_api_5xx_has_stable_exit_and_never_echoes_provider_body(
    status: int, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "sk-private-provider-response"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=secret,
            headers={"content-type": "text/plain"},
            request=request,
        )

    exit_code = main(
        ["show", "--case-id", "case-1"],
        transport=_transport(handler),
        environ={},
    )

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.out == ""
    assert captured.err == f"afc-review: API request failed (status={status}, code=api_error)\n"
    assert secret not in captured.err


def test_operational_api_error_retains_only_recovery_handle_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "private-provider-body"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "detail": {
                    "code": "revision_provider_failed",
                    "case_id": "case-durable-7",
                    "retryable": True,
                    "provider_body": secret,
                }
            },
            request=request,
        )

    exit_code = main(
        ["show", "--case-id", "case-durable-7"],
        transport=_transport(handler),
        environ={},
    )

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.out == ""
    assert captured.err == (
        "afc-review: API request failed "
        "(code=revision_provider_failed, case_id=case-durable-7, retryable=true)\n"
    )
    assert secret not in captured.err


def test_unrecognized_error_code_is_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "private_secret_from_provider"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"detail": {"code": secret, "retryable": False}},
            request=request,
        )

    exit_code = main(
        ["show", "--case-id", "case-1"],
        transport=_transport(handler),
        environ={},
    )

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.err == "afc-review: API request failed (status=503, code=api_error)\n"
    assert secret not in captured.err


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("https://user:secret@example.invalid/private"),
        httpx.ReadTimeout("private provider body"),
    ],
)
def test_transport_and_timeout_failures_are_redacted(
    error: httpx.RequestError,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    exit_code = main(
        ["show", "--case-id", "case-1"],
        transport=_transport(handler),
        environ={},
    )

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.out == ""
    assert captured.err == "afc-review: API transport failed\n"
    assert str(error) not in captured.err


def test_non_json_success_is_rejected_without_echoing_body(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "private-success-body"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=secret, request=request)

    exit_code = main(
        ["show", "--case-id", "case-1"],
        transport=_transport(handler),
        environ={},
    )

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.out == ""
    assert captured.err == "afc-review: API returned an invalid JSON response\n"
    assert secret not in captured.err


def test_cli_source_is_http_only_and_never_names_provider_key() -> None:
    source = Path("src/afc/cli/review.py").read_text(encoding="utf-8")
    forbidden = (
        "afc.api",
        "afc.review",
        "afc.diagnosis",
        "afc.trace_ir",
        "sqlite",
        "DEEPSEEK_API_KEY",
    )
    assert all(name not in source for name in forbidden)
