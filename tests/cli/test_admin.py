from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from spanvouch.audit.export import create_audit_export
from spanvouch.cli.admin import main
from tests.audit.test_export import _bootstrap_audit_events, _write_signing_key


def _transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    ("argv", "method", "path", "body", "status"),
    [
        (
            ["project", "create", "--name", "Alpha"],
            "POST",
            "/v1/admin/projects",
            {"name": "Alpha"},
            201,
        ),
        (["project", "list"], "GET", "/v1/admin/projects", None, 200),
        (
            ["key", "create", "--project-id", "project-1", "--roles", "operator,reviewer"],
            "POST",
            "/v1/admin/projects/project-1/api-keys",
            {"roles": ["operator", "reviewer"]},
            201,
        ),
        (
            ["key", "rotate", "--key-id", "key-1"],
            "POST",
            "/v1/admin/api-keys/key-1/rotate",
            None,
            200,
        ),
        (
            ["audit", "export", "--project-id", "project-1"],
            "POST",
            "/v1/admin/projects/project-1/audit-exports",
            None,
            201,
        ),
    ],
)
def test_admin_commands_send_auth_header_and_canonical_json(
    argv: list[str],
    method: str,
    path: str,
    body: dict[str, object] | None,
    status: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            status,
            json={"z": 1, "a": {"ok": True}},
            request=request,
        )

    assert (
        main(
            argv,
            transport=_transport(handler),
            environ={"SPANVOUCH_API_KEY": "svk_admin_secret"},
        )
        == 0
    )

    request = seen[0]
    assert request.method == method
    assert request.url == httpx.URL(f"http://127.0.0.1:8000{path}")
    assert request.headers["authorization"] == "Bearer svk_admin_secret"
    assert (json.loads(request.content) if request.content else None) == body
    assert capsys.readouterr().out == '{"a":{"ok":true},"z":1}\n'


def test_revoke_prints_deterministic_success_for_204(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, request=request)

    assert (
        main(
            ["key", "revoke", "--key-id", "key-1"],
            transport=_transport(handler),
            environ={"SPANVOUCH_API_KEY": "svk_admin_secret"},
        )
        == 0
    )

    assert capsys.readouterr().out == '{"key_id":"key-1","revoked":true}\n'


def test_missing_api_key_stops_before_http(capsys: pytest.CaptureFixture[str]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={}, request=request)

    assert main(["project", "list"], transport=_transport(handler), environ={}) == 2
    captured = capsys.readouterr()
    assert calls == 0
    assert captured.out == ""
    assert captured.err == "spanvouch admin: API key required in SPANVOUCH_API_KEY\n"


def test_api_error_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    secret = "private-provider-response"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=secret, request=request)

    assert (
        main(
            ["project", "list"],
            transport=_transport(handler),
            environ={"SPANVOUCH_API_KEY": "svk_admin_secret"},
        )
        == 4
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "spanvouch admin: API request failed (status=503, code=api_error)\n"
    assert secret not in captured.err


def test_verify_audit_export_is_offline_and_does_not_require_api_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_id, events = _bootstrap_audit_events(tmp_path / "audit.db")
    signing_key_path = tmp_path / "audit-signing-key.pem"
    _write_signing_key(signing_key_path)
    bundle = create_audit_export(
        project_id,
        tmp_path / "bundle",
        events=events,
        checkpoints=(),
        signing_key_path=signing_key_path,
    )

    assert main(["audit", "verify", "--bundle", str(bundle)], environ={}) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_id"] == project_id
    assert payload["event_count"] == 2
    assert payload["checkpoint_count"] == 1
