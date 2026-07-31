from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote

import httpx

from spanvouch.audit.export import verify_audit_export

_DEFAULT_API_URL = "http://127.0.0.1:8000"
_DEFAULT_API_KEY_ENV = "SPANVOUCH_API_KEY"
_TIMEOUT = httpx.Timeout(timeout=10.0, connect=5.0)
_NO_BODY = object()
_PUBLIC_ERROR_CODES = frozenset(
    {
        "api_key_conflict",
        "api_key_invalid",
        "api_key_not_found",
        "audit_export_conflict",
        "audit_export_not_found",
        "audit_signing_key_invalid",
        "audit_signing_key_required",
        "authentication_failed",
        "authentication_required",
        "authorization_failed",
        "project_conflict",
        "project_not_found",
    }
)


class _ApiError(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code


class _TransportError(Exception):
    pass


class _InvalidResponseError(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spanvouch admin",
        description="HTTP client for SpanVouch project, key, and audit administration.",
    )
    parser.add_argument(
        "--api-url",
        help="admin API base URL (default: SPANVOUCH_API_URL or http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--api-key-env",
        default=_DEFAULT_API_KEY_ENV,
        help="environment variable containing the API key",
    )
    parser.add_argument(
        "--api-key-fd",
        type=int,
        help="file descriptor containing the API key",
    )
    resources = parser.add_subparsers(dest="resource", required=True)

    project = resources.add_parser("project", help="manage projects")
    project_commands = project.add_subparsers(dest="action", required=True)
    project_create = project_commands.add_parser("create", help="create a project")
    project_create.add_argument("--name", required=True)
    project_commands.add_parser("list", help="list projects")

    key = resources.add_parser("key", help="manage API keys")
    key_commands = key.add_subparsers(dest="action", required=True)
    key_create = key_commands.add_parser("create", help="create a project API key")
    key_create.add_argument("--project-id", required=True)
    key_create.add_argument("--roles", required=True)
    key_create.add_argument("--expires-at")
    key_rotate = key_commands.add_parser("rotate", help="rotate an API key")
    key_rotate.add_argument("--key-id", required=True)
    key_revoke = key_commands.add_parser("revoke", help="revoke an API key")
    key_revoke.add_argument("--key-id", required=True)

    audit = resources.add_parser("audit", help="manage audit exports")
    audit_commands = audit.add_subparsers(dest="action", required=True)
    audit_export = audit_commands.add_parser("export", help="create an audit export")
    audit_export.add_argument("--project-id", required=True)
    audit_verify = audit_commands.add_parser("verify", help="verify an audit export offline")
    audit_verify.add_argument("--bundle", type=Path, required=True)
    return parser


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _effective_api_url(args: argparse.Namespace, environ: Mapping[str, str]) -> str:
    candidate = (
        args.api_url or environ.get("SPANVOUCH_API_URL") or _DEFAULT_API_URL
    ).rstrip("/")
    try:
        parsed = httpx.URL(candidate)
    except (httpx.InvalidURL, ValueError) as error:
        raise ValueError from error
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise ValueError
    return candidate


def _read_api_key(args: argparse.Namespace, environ: Mapping[str, str]) -> str:
    if args.api_key_fd is not None:
        try:
            with os.fdopen(args.api_key_fd, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError as error:
            raise ValueError from error
    else:
        value = environ.get(args.api_key_env, "").strip()
    if not value:
        raise ValueError
    return value


def _roles(value: str) -> list[str]:
    roles = [item.strip() for item in value.split(",") if item.strip()]
    if not roles or len(set(roles)) != len(roles):
        raise ValueError
    return roles


def _command_request(args: argparse.Namespace) -> tuple[str, str, object]:
    if args.resource == "project" and args.action == "create":
        return "POST", "/v1/admin/projects", {"name": args.name}
    if args.resource == "project" and args.action == "list":
        return "GET", "/v1/admin/projects", _NO_BODY
    if args.resource == "key" and args.action == "create":
        body: dict[str, object] = {"roles": _roles(args.roles)}
        if args.expires_at is not None:
            body["expires_at"] = args.expires_at
        return (
            "POST",
            f"/v1/admin/projects/{quote(args.project_id, safe='')}/api-keys",
            body,
        )
    if args.resource == "key" and args.action == "rotate":
        return "POST", f"/v1/admin/api-keys/{quote(args.key_id, safe='')}/rotate", _NO_BODY
    if args.resource == "key" and args.action == "revoke":
        return "POST", f"/v1/admin/api-keys/{quote(args.key_id, safe='')}/revoke", _NO_BODY
    if args.resource == "audit" and args.action == "export":
        return (
            "POST",
            f"/v1/admin/projects/{quote(args.project_id, safe='')}/audit-exports",
            _NO_BODY,
        )
    raise ValueError


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return "api_error"
    if not isinstance(payload, dict):
        return "api_error"
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return "api_error"
    code = detail.get("code")
    if isinstance(code, str) and code in _PUBLIC_ERROR_CODES:
        return code
    return "api_error"


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    payload: object,
) -> Any:
    try:
        if payload is _NO_BODY:
            response = client.request(method, path)
        else:
            response = client.request(method, path, json=payload)
    except httpx.RequestError as error:
        raise _TransportError from error
    if response.status_code >= 400:
        raise _ApiError(response.status_code, _error_detail(response))
    if response.status_code == 204:
        return _NO_BODY
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as error:
        raise _InvalidResponseError from error


def _verify_payload(bundle: Path) -> dict[str, object]:
    verified = verify_audit_export(bundle)
    return {
        "project_id": verified.project_id,
        "first_event_sequence": verified.first_event_sequence,
        "last_event_sequence": verified.last_event_sequence,
        "terminal_event_sha256": verified.terminal_event_sha256,
        "manifest_sha256": verified.manifest_sha256,
        "event_count": verified.event_count,
        "checkpoint_count": verified.checkpoint_count,
        "signing_key_fingerprint": verified.signing_key_fingerprint,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr

    if args.resource == "audit" and args.action == "verify":
        try:
            print(_canonical_json(_verify_payload(args.bundle)), file=output)
            return 0
        except ValueError:
            print("spanvouch admin: audit export verification failed", file=errors)
            return 4

    try:
        method, path, body = _command_request(args)
    except ValueError:
        print("spanvouch admin: invalid command arguments", file=errors)
        return 2

    try:
        api_key = _read_api_key(args, environment)
    except ValueError:
        print(
            f"spanvouch admin: API key required in {args.api_key_env}",
            file=errors,
        )
        return 2

    try:
        base_url = _effective_api_url(args, environment)
    except ValueError:
        print("spanvouch admin: invalid API URL", file=errors)
        return 2

    try:
        with httpx.Client(
            base_url=f"{base_url}/",
            timeout=_TIMEOUT,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        ) as client:
            response_payload = _request(client, method, path.lstrip("/"), body)
    except httpx.InvalidURL:
        print("spanvouch admin: invalid API URL", file=errors)
        return 2
    except _ApiError as error:
        print(
            "spanvouch admin: API request failed "
            f"(status={error.status_code}, code={error.code})",
            file=errors,
        )
        return 3 if 400 <= error.status_code < 500 else 4
    except _TransportError:
        print("spanvouch admin: API transport failed", file=errors)
        return 4
    except _InvalidResponseError:
        print("spanvouch admin: API returned an invalid JSON response", file=errors)
        return 4

    if response_payload is _NO_BODY:
        response_payload = {"key_id": args.key_id, "revoked": True}
    print(_canonical_json(response_payload), file=output)
    return 0
