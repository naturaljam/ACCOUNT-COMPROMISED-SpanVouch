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

_DEFAULT_API_URL = "http://127.0.0.1:8000"
_TIMEOUT = httpx.Timeout(timeout=10.0, connect=5.0)
_NO_BODY = object()
_PUBLIC_ERROR_CODES = frozenset(
    {
        "diagnoser_unavailable",
        "internal_error",
        "missing_response",
        "provider_error",
        "provider_not_configured",
        "provider_protocol_error",
        "provider_request_error",
        "review_conflict",
        "review_invalid",
        "review_not_found",
        "revision_provider_failed",
        "trace_not_found",
        "transport_error",
        "upstream_http_error",
    }
)


class _ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        *,
        case_id: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.case_id = case_id
        self.retryable = retryable


class _TransportError(Exception):
    pass


class _InvalidResponseError(Exception):
    pass


def _add_runtime_options(parser: argparse.ArgumentParser, *, command: bool) -> None:
    suffix = "command" if command else "global"
    parser.add_argument(
        "--api-url",
        dest=f"{suffix}_api_url",
        metavar="URL",
        help="review API base URL (default: AFC_API_URL or http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--allow-live-api",
        dest=f"{suffix}_allow_live_api",
        action="store_true",
        help="explicitly allow create or resume work that can invoke a paid model API",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="afc-review",
        description="HTTP-only client for diagnosis review workflows.",
    )
    _add_runtime_options(parser, command=False)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create and run a diagnosis review")
    _add_runtime_options(create, command=True)
    create.add_argument("--trace-id", required=True)
    create.add_argument("--diagnoser", choices=("rules", "deepseek"), default="rules")
    create.add_argument(
        "--verifier", choices=("deterministic", "hybrid"), default="deterministic"
    )
    create.add_argument("--idempotency-key", required=True)

    show = commands.add_parser("show", help="show a diagnosis review")
    _add_runtime_options(show, command=True)
    show.add_argument("--case-id", required=True)

    resume = commands.add_parser("resume", help="resume recoverable review work")
    _add_runtime_options(resume, command=True)
    resume.add_argument("--case-id", required=True)

    decide = commands.add_parser("decide", help="record a human review decision")
    _add_runtime_options(decide, command=True)
    decide.add_argument("--case-id", required=True)
    decide.add_argument("--action", choices=("confirm", "correct", "reject"), required=True)
    decide.add_argument("--expected-version", type=int, required=True)
    decide.add_argument("--reviewer-label", required=True)
    decide.add_argument("--idempotency-key", required=True)
    decide.add_argument("--reason")
    decide.add_argument("--correction-file", type=Path)
    return parser


def _error_detail(response: httpx.Response) -> tuple[str, str | None, bool | None]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return "api_error", None, None
    if not isinstance(payload, dict):
        return "api_error", None, None
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return "api_error", None, None
    code = detail.get("code")
    if isinstance(code, str) and code in _PUBLIC_ERROR_CODES:
        case_id = detail.get("case_id")
        retryable = detail.get("retryable")
        if isinstance(case_id, str) and case_id and isinstance(retryable, bool):
            return code, case_id, retryable
        return code, None, None
    return "api_error", None, None


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    payload: object = _NO_BODY,
) -> Any:
    try:
        if payload is _NO_BODY:
            response = client.request(method, path)
        else:
            response = client.request(method, path, json=payload)
    except httpx.RequestError as error:
        raise _TransportError from error
    if response.status_code >= 400:
        code, case_id, retryable = _error_detail(response)
        raise _ApiError(
            response.status_code,
            code,
            case_id=case_id,
            retryable=retryable,
        )
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as error:
        raise _InvalidResponseError from error


def _read_correction(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError from error
    if not isinstance(payload, dict):
        raise ValueError
    return payload


def _effective_api_url(args: argparse.Namespace, environ: Mapping[str, str]) -> str:
    candidate = (
        args.command_api_url
        or args.global_api_url
        or environ.get("AFC_API_URL")
        or _DEFAULT_API_URL
    ).rstrip("/")
    try:
        parsed = httpx.URL(candidate)
    except (httpx.InvalidURL, ValueError) as error:
        raise ValueError from error
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise ValueError
    return candidate


def _allows_live(args: argparse.Namespace) -> bool:
    return bool(args.command_allow_live_api or args.global_allow_live_api)


def _command_request(
    args: argparse.Namespace,
) -> tuple[str, str, object]:
    if args.command == "create":
        path = f"/v1/traces/{quote(args.trace_id, safe='')}/diagnosis-reviews"
        return (
            "POST",
            path,
            {
                "diagnoser": args.diagnoser,
                "verifier": args.verifier,
                "idempotency_key": args.idempotency_key,
            },
        )
    if args.command == "show":
        return "GET", f"/v1/diagnosis-reviews/{quote(args.case_id, safe='')}", _NO_BODY
    if args.command == "resume":
        path = f"/v1/diagnosis-reviews/{quote(args.case_id, safe='')}/resume"
        return "POST", path, {"allow_live_api": _allows_live(args)}

    body: dict[str, object] = {
        "action": args.action,
        "expected_version": args.expected_version,
        "reviewer_label": args.reviewer_label,
        "idempotency_key": args.idempotency_key,
    }
    if args.reason is not None:
        body["reason"] = args.reason
    if args.correction_file is not None:
        body["correction"] = _read_correction(args.correction_file)
    path = f"/v1/diagnosis-reviews/{quote(args.case_id, safe='')}/decisions"
    return "POST", path, body


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _resume_can_call_live(payload: object) -> bool:
    if not isinstance(payload, dict):
        raise _InvalidResponseError
    requires_live_api = payload.get("resume_requires_live_api")
    if not isinstance(requires_live_api, bool):
        raise _InvalidResponseError
    return requires_live_api


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

    if args.command == "create":
        can_call_live = args.diagnoser == "deepseek" or args.verifier == "hybrid"
        if can_call_live and not _allows_live(args):
            print("afc-review: live API use requires --allow-live-api", file=errors)
            return 2

    try:
        method, path, payload = _command_request(args)
    except ValueError:
        print("afc-review: correction file must contain one JSON object", file=errors)
        return 2

    try:
        base_url = _effective_api_url(args, environment)
    except ValueError:
        print("afc-review: invalid API URL", file=errors)
        return 2
    try:
        with httpx.Client(
            base_url=f"{base_url}/",
            timeout=_TIMEOUT,
            transport=transport,
        ) as client:
            if args.command == "resume" and not _allows_live(args):
                show_path = f"v1/diagnosis-reviews/{quote(args.case_id, safe='')}"
                case_payload = _request(client, "GET", show_path)
                if _resume_can_call_live(case_payload):
                    print(
                        "afc-review: live API use requires --allow-live-api",
                        file=errors,
                    )
                    return 2
            response_payload = _request(client, method, path.lstrip("/"), payload)
    except httpx.InvalidURL:
        print("afc-review: invalid API URL", file=errors)
        return 2
    except _ApiError as error:
        if error.case_id is not None and error.retryable is not None:
            print(
                "afc-review: API request failed "
                f"(code={error.code}, case_id={error.case_id}, "
                f"retryable={str(error.retryable).lower()})",
                file=errors,
            )
        else:
            print(
                "afc-review: API request failed "
                f"(status={error.status_code}, code={error.code})",
                file=errors,
            )
        return 3 if 400 <= error.status_code < 500 else 4
    except _TransportError:
        print("afc-review: API transport failed", file=errors)
        return 4
    except _InvalidResponseError:
        print("afc-review: API returned an invalid JSON response", file=errors)
        return 4

    print(_canonical_json(response_payload), file=output)
    return 0
