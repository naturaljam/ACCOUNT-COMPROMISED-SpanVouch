from __future__ import annotations

import asyncio
import io
import json
import re
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from afc.api.app import create_app
from afc.cli import review as review_cli
from afc.diagnosis.errors import ProviderProtocolError
from afc.diagnosis.evidence import EvidenceCatalog
from afc.diagnosis.models import DiagnoserKind, EvidenceSelector
from afc.diagnosis.rule_diagnoser import RuleDiagnoser
from afc.diagnosis.service import DiagnosisService
from afc.diagnosis.trace_view import SECRET_REDACTION, DiagnosticTraceView
from afc.invariants.engine import InvariantEngine
from afc.invariants.supportlab import supportlab_rules
from afc.review.evidence_verifier import EvidenceVerifier
from afc.review.models import VerificationInput, VerificationMode, VerifierKind, VerifierReport
from afc.review.reviser import DiagnosisReviser
from afc.review.semantic_verifier import SemanticVerifier
from afc.review.service import ReviewService
from afc.review.sqlite_repository import SQLiteReviewRepository
from afc.review.workflow import ReviewWorkflow, ReviewWorkflowProviderError
from afc.trace_ir.models import TraceIR
from afc.trace_ir.repository import InMemoryTraceRepository

ROOT = Path(__file__).resolve().parents[2]
SENTINEL_KEY = "sentinel" + "-private-deepseek-key"
RAW_PROVIDER_BODY = "raw" + "-provider-body-must-not-escape"
_KEY_NAME = "DEEPSEEK" + "_API_KEY"
_ASSIGNMENT_PATTERNS = (
    re.compile(
        rf"(?m)^[ \t]*(?:export[ \t]+)?{re.escape(_KEY_NAME)}[ \t]*="
        r"[ \t]*(?P<value>[^\r\n#]*)"
    ),
    re.compile(
        rf"(?m)^[ \t]*\$env:{re.escape(_KEY_NAME)}[ \t]*="
        r"[ \t]*(?P<value>[^\r\n#]*)"
    ),
    re.compile(
        rf"(?m)^[ \t]*(?:[{{,][ \t]*)?[\"']?{re.escape(_KEY_NAME)}[\"']?[ \t]*:"
        r"[ \t]*(?P<value>[^\r\n#]*)"
    ),
)
_KEY_LITERAL_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{24,}(?![A-Za-z0-9])")
_DOCUMENTATION_PLACEHOLDERS = frozenset({"", "<set-locally>"})


class FailingSemanticVerifier:
    kind = VerifierKind.SEMANTIC
    version_fingerprint = "secret-hygiene-semantic-v1"

    def __init__(self) -> None:
        self.inputs: list[VerificationInput] = []

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        self.inputs.append(input_)
        raise ProviderProtocolError(f"{SENTINEL_KEY}:{RAW_PROVIDER_BODY}")


def _clean_trace() -> TraceIR:
    dataset = ROOT / "evals" / "datasets" / "supportlab-v1" / "traces.jsonl"
    return next(
        trace
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if (trace := TraceIR.model_validate_json(line)).run_id == "clean-01"
    )


def _trace_with_value_secrets() -> TraceIR:
    trace = _clean_trace()
    root = trace.spans[0]
    encoded = json.dumps({"client_secret": SENTINEL_KEY})
    for _ in range(6):
        encoded = json.dumps(encoded)
    root = root.model_copy(
        update={
            "name": f"root secretkey={SENTINEL_KEY}",
            "attributes": {
                **root.attributes,
                "tool.result": {
                    "api_key": SENTINEL_KEY,
                    "api_key:": SENTINEL_KEY,
                    "headers:authorization": SENTINEL_KEY,
                    "proxy=auth": SENTINEL_KEY,
                    "access_key": SENTINEL_KEY,
                    "serviceAuth": SENTINEL_KEY,
                    "authorization=": SENTINEL_KEY,
                    "headers.authorization": SENTINEL_KEY,
                    "userpassword": SENTINEL_KEY,
                    "sessiontokenvalue": SENTINEL_KEY,
                    "secretkey": SENTINEL_KEY,
                    "passwordhash": SENTINEL_KEY,
                    "tokenstring": SENTINEL_KEY,
                    "userpasswordhash": SENTINEL_KEY,
                    "clientsecretstring": SENTINEL_KEY,
                    "X-API-Key": SENTINEL_KEY,
                    "clientSecret": SENTINEL_KEY,
                    "private-key": SENTINEL_KEY,
                    "session_token": SENTINEL_KEY,
                    "Set-Cookie": SENTINEL_KEY,
                    "deep": encoded,
                    "token_count": 7,
                    "password_policy": "rotate-quarterly",
                    "session_duration": 30,
                    "tokenizer_name": "sentencepiece",
                    "password_hash_algorithm": "argon2id",
                    "token_value_length": 128,
                    "auth_timeout": 15,
                    "access_key_id": "public-key-id",
                    "safe": "diagnostic context survives",
                },
                "tool.error.message": {
                    "proxy authorization": SENTINEL_KEY,
                    "db.password": SENTINEL_KEY,
                    "message": "provider rejected request",
                },
                "run.final_message": (
                    f"passwordhash: {SENTINEL_KEY}; final context survives\n"
                    f"Cookie: session=first; csrf={SENTINEL_KEY}\n"
                    f"Set-Cookie: sid=first; refresh={SENTINEL_KEY}\n"
                    f"Authorization:{SECRET_REDACTION}; Bearer {SENTINEL_KEY}\n"
                    f"Proxy-Authorization: {SECRET_REDACTION}; tail={SENTINEL_KEY}\n"
                    f"Cookie:{SECRET_REDACTION}; csrf={SENTINEL_KEY}\n"
                    f"Set-Cookie: {SECRET_REDACTION}; refresh={SENTINEL_KEY}\n"
                    f"Cookie=session=first; csrf={SENTINEL_KEY}\n"
                    f"Set-Cookie=sid=first; refresh={SENTINEL_KEY}\n"
                    f"api_key={SECRET_REDACTION}{SENTINEL_KEY}\n"
                    f"client_secret='{SECRET_REDACTION}{SENTINEL_KEY}'\n"
                    f"Authorization:{SECRET_REDACTION}{SENTINEL_KEY}\n"
                    f"Proxy-Authorization={SECRET_REDACTION}{SENTINEL_KEY}\n"
                    f"Authorization:abc; arbitrary={SENTINEL_KEY}\n"
                    f"Proxy-Authorization=abc; refresh={SENTINEL_KEY}\n"
                    f"Request Cookie: session=first; csrf={SENTINEL_KEY}\n"
                    f"headers.cookie: session=first; csrf={SENTINEL_KEY}\n"
                    f"HTTP Cookie=session=first; csrf={SENTINEL_KEY}\n"
                    f"Response Set-Cookie: sid=first; refresh={SENTINEL_KEY}\n"
                    f'api_key="{SECRET_REDACTION}top {SENTINEL_KEY}"\n'
                    f"client_secret='{SECRET_REDACTION}top;{SENTINEL_KEY}'\n"
                    f"request.headers.Cookie=session=first; csrf={SENTINEL_KEY}\n"
                    f"http_request_headers_Set-Cookie=sid=first; refresh={SENTINEL_KEY}\n"
                    rf'api_key=\"{SECRET_REDACTION}top {SENTINEL_KEY}\"' "\n"
                    rf"Cookie:\'{SECRET_REDACTION}top;{SENTINEL_KEY}\'" "\n"
                    f"http.request.headers.response.http.request.headers.Cookie="
                    f"session=first; csrf={SENTINEL_KEY}\n"
                    f"Set Cookie: session=first; csrf={SENTINEL_KEY}\n"
                    f"Session Cookie=a_1\n"
                    "session_cookie_count=3; metadata remains safe\n"
                    rf'{{\"api_key\":\"{SENTINEL_KEY}\"}}' "\n"
                    rf'\"api_key\":\"{SECRET_REDACTION}top {SENTINEL_KEY}\"' "\n"
                    f"api_key.{('x' * 96)}={SENTINEL_KEY}\n"
                    f"{('x' * 96)}.token_count=7; long metadata remains safe\n"
                    f"headers[api_key]={SENTINEL_KEY}\n"
                    rf'credentials[\"api_key\"]={SENTINEL_KEY}' "\n"
                    f"api$key={SENTINEL_KEY}\n"
                    "https://auth.example.com:443/path?status=ok\n"
                    "https://token.example.com:8443/health\n"
                    f"https://agent:{SENTINEL_KEY}@cookie.internal:8080/health\n"
                    rf"https:\/\/agent:{SENTINEL_KEY}@auth.example.com:443/path"
                    "\n"
                    r"https:\/\/auth.example.com:443/path escaped-url-safe"
                    "\n"
                    "https://token.example.com:8443/path#auth:section "
                    "colon-tag-safe\n"
                    "safe=token_count\N{FULLWIDTH SEMICOLON}field=ok "
                    "unicode-boundary-safe\n"
                    f"https://auth.example.com:443\N{IDEOGRAPHIC COMMA}"
                    f"api_key={SENTINEL_KEY}\n"
                    f"headers→api_key={SENTINEL_KEY}\n"
                    f"api_key\N{FULLWIDTH EQUALS SIGN}{SENTINEL_KEY}\n"
                    f"Authorization\N{PRESENTATION FORM FOR VERTICAL COLON}"
                    f"{SENTINEL_KEY}\n"
                    "Cookie\N{FULLWIDTH EQUALS SIGN}a_1\n"
                    f"https://auth.example/path?api_key"
                    f"\N{SUPERSCRIPT EQUALS SIGN}{SENTINEL_KEY}\n"
                    "ratio\N{FULLWIDTH EQUALS SIGN}1 compatible-ratio-safe\n"
                    f"api\N{NO-BREAK SPACE}key={SENTINEL_KEY}\n"
                    f"access\N{EM SPACE}key={SENTINEL_KEY}\n"
                    "safe=token_count\N{NO-BREAK SPACE}field=ok "
                    "unicode-space-safe\n"
                    rf"https:/\/agent:{SENTINEL_KEY}@mixed.example.com:443/path"
                    "\n"
                    rf"https:\//agent:{SENTINEL_KEY}@mixed-two.example.com:443/path"
                    "\n"
                    r"https:/\/auth.example.com:443/path mixed-url-safe"
                    "\n"
                    f"authorization {'x' * 40}={SENTINEL_KEY}\n"
                    "safe=custom_token_count\N{NO-BREAK SPACE}field=ok "
                    "arbitrary-metadata-safe\n"
                    f"safe=custom_token_count\N{EM SPACE}api key={SENTINEL_KEY}\n"
                    f"https\N{FULLWIDTH COLON}/\\/agent:{SENTINEL_KEY}"
                    "@compatible.example.com:443/path\n"
                    "https\N{SMALL COLON}//auth.example.com:443/path "
                    "compatible-colon-url-safe\n"
                    f"token count payload={SENTINEL_KEY}\n"
                    f"tenant custom authorization\N{NO-BREAK SPACE}status"
                    f"\N{NO-BREAK SPACE}header={SENTINEL_KEY}\n"
                    "token count status=visible full-label-parity-safe\n"
                    f"safe=ok token count payload={SENTINEL_KEY}\n"
                    f"safe=custom_token_count\N{EM SPACE}authorization"
                    f"\N{EM SPACE}status\N{EM SPACE}header={SENTINEL_KEY}\n"
                    "safe=custom_password_policy\N{NO-BREAK SPACE}field=ok "
                    "previous-value-safe\n"
                    "Bearer Qaz; HTTP 401\n"
                    "A cookie: recipe; instructions remain safe\n"
                    "cookie: recipe; instructions remain safe\n"
                    "Browser cookie: recipe; instructions remain safe\n"
                    "Cookie=recipe; instructions remain safe\n"
                    "request.headers.Cookie=recipe; instructions remain safe\n"
                    "Bearer of; good news\n"
                    "Bearer of good news remains harmless prose."
                ),
            },
        }
    )
    return trace.model_copy(update={"spans": [root, *trace.spans[1:]]})


def _review_runtime(
    database: Path,
) -> tuple[ReviewService, SQLiteReviewRepository, FailingSemanticVerifier]:
    engine = InvariantEngine(supportlab_rules())
    diagnoser = RuleDiagnoser(engine)
    diagnosis_service = DiagnosisService({DiagnoserKind.RULES: diagnoser})
    repository = SQLiteReviewRepository(database)
    deterministic = EvidenceVerifier(engine, policy_version="supportlab-review-v1")
    semantic = FailingSemanticVerifier()
    workflow = ReviewWorkflow(
        repository=repository,
        deterministic_verifier=deterministic,
        semantic_verifier=semantic,
        reviser=DiagnosisReviser({DiagnoserKind.RULES: diagnoser}),
        id_factory=lambda: str(uuid4()),
        clock=lambda: datetime.now(UTC),
        lease_owner="secret-hygiene-worker",
        lease_duration=timedelta(seconds=30),
    )
    service = ReviewService(
        diagnosis_service=diagnosis_service,
        repository=repository,
        workflow=workflow,
        deterministic_verifier=deterministic,
        id_factory=lambda: str(uuid4()),
        clock=lambda: datetime.now(UTC),
    )
    return service, repository, semantic


def _assert_sanitized(value: object) -> None:
    serialized = value if isinstance(value, str) else json.dumps(value, default=str)
    assert SENTINEL_KEY not in serialized
    assert RAW_PROVIDER_BODY not in serialized


def test_provider_failure_is_sanitized_across_error_sqlite_api_cli_and_report(
    tmp_path: Path,
) -> None:
    database = tmp_path / "review.db"
    service, repository, semantic = _review_runtime(database)
    asyncio.run(repository.initialize())

    with pytest.raises(ReviewWorkflowProviderError) as raised:
        asyncio.run(
            service.create(
                _clean_trace(),
                diagnoser=DiagnoserKind.RULES,
                verification_mode=VerificationMode.HYBRID,
                idempotency_key="secret-hygiene-create",
            )
        )

    case_id = raised.value.case_id
    _assert_sanitized(str(raised.value))
    detail = asyncio.run(repository.get_detail(case_id))
    assert semantic.inputs
    _assert_sanitized(semantic.inputs[0].model_dump(mode="json"))
    _assert_sanitized(detail.model_dump(mode="json"))
    assert detail.verifier_reports[-1].operational_error is not None
    assert detail.verifier_reports[-1].operational_error.code == "provider_protocol_error"
    assert detail.events[-2].event_type.value == "provider_failed"
    assert detail.events[-1].event_type.value == "awaiting_human_review"

    with sqlite3.connect(database) as connection:
        sqlite_text = "\n".join(
            str(value)
            for table in (
                "review_cases",
                "verifier_runs",
                "workflow_events",
                "idempotency_keys",
            )
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            for value in row
        )
    _assert_sanitized(sqlite_text)

    trace_repository = InMemoryTraceRepository()
    application = create_app(
        trace_repository=trace_repository,
        review_repository=repository,
        review_service=service,
    )
    with TestClient(application) as client:
        response = client.get(f"/v1/diagnosis-reviews/{case_id}")
    assert response.status_code == 200
    _assert_sanitized(response.text)

    payload = response.json()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/diagnosis-reviews/{case_id}"
        return httpx.Response(200, json=payload, request=request)

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = review_cli.main(
        ["show", "--case-id", case_id],
        transport=httpx.MockTransport(handler),
        environ={},
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == 0
    assert stderr.getvalue() == ""
    _assert_sanitized(stdout.getvalue())


def test_allowed_trace_value_secrets_never_reach_sqlite_or_public_aggregate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "trace-value-secrets.db"
    service, repository, semantic = _review_runtime(database)
    asyncio.run(repository.initialize())

    try:
        created = asyncio.run(
            service.create(
                _trace_with_value_secrets(),
                diagnoser=DiagnoserKind.RULES,
                verification_mode=VerificationMode.HYBRID,
                idempotency_key="trace-value-secret-create",
            )
        )
    except ReviewWorkflowProviderError as error:
        case_id = error.case_id
    else:
        case_id = created.case.case_id

    detail = asyncio.run(repository.get_detail(case_id))
    trace_view = DiagnosticTraceView.from_trace(_trace_with_value_secrets())
    catalog = EvidenceCatalog.from_view(trace_view)
    catalog_refs = tuple(
        catalog.resolve(
            EvidenceSelector(
                span_id=selector.split("::", 1)[0],
                field_path=selector.split("::", 1)[1],
            ),
            description="sanitizer boundary check",
        )
        for selector in catalog.selectors
    )
    _assert_sanitized(trace_view.model_dump(mode="json"))
    _assert_sanitized([ref.model_dump(mode="json") for ref in catalog_refs])
    assert semantic.inputs
    _assert_sanitized(semantic.inputs[0].model_dump(mode="json"))
    _assert_sanitized(detail.model_dump(mode="json"))
    with sqlite3.connect(database) as connection:
        view_json = connection.execute(
            "SELECT view_json FROM review_inputs WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        aggregate = "\n".join(
            str(value)
            for table in (
                "review_inputs",
                "diagnosis_revisions",
                "verifier_runs",
                "workflow_events",
            )
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            for value in row
        )
    assert view_json is not None
    _assert_sanitized(view_json[0])
    _assert_sanitized(aggregate)
    assert "diagnostic context survives" in view_json[0]
    assert "final context survives" not in view_json[0]
    assert '"token_count":7' in view_json[0]
    assert '"password_policy":"rotate-quarterly"' in view_json[0]
    assert '"session_duration":30' in view_json[0]
    assert '"tokenizer_name":"sentencepiece"' in view_json[0]
    assert '"password_hash_algorithm":"argon2id"' in view_json[0]
    assert '"token_value_length":128' in view_json[0]
    assert '"auth_timeout":15' in view_json[0]
    assert '"access_key_id":"public-key-id"' in view_json[0]
    assert "csrf=" not in view_json[0]
    assert "refresh=" not in view_json[0]
    assert "Qaz" not in view_json[0]
    assert "Bearer of good news remains harmless prose." in view_json[0]
    assert "A cookie: recipe; instructions remain safe" in view_json[0]
    assert "cookie: recipe; instructions remain safe" in view_json[0]
    assert "Browser cookie: recipe; instructions remain safe" in view_json[0]
    assert "Cookie=recipe; instructions remain safe" in view_json[0]
    assert "request.headers.Cookie=recipe; instructions remain safe" in view_json[0]
    assert "a_1" not in view_json[0]
    assert "session_cookie_count=3; metadata remains safe" in view_json[0]
    assert "long metadata remains safe" in view_json[0]
    assert "https://auth.example.com:443/path?status=ok" in view_json[0]
    assert "https://token.example.com:8443/health" in view_json[0]
    assert f"https://{SECRET_REDACTION}@cookie.internal:8080/health" in view_json[0]
    assert "escaped-url-safe" in view_json[0]
    assert "colon-tag-safe" in view_json[0]
    assert "unicode-boundary-safe" in view_json[0]
    assert "compatible-ratio-safe" in view_json[0]
    assert "unicode-space-safe" in view_json[0]
    assert "mixed-url-safe" in view_json[0]
    assert "arbitrary-metadata-safe" in view_json[0]
    assert "compatible-colon-url-safe" in view_json[0]
    assert "full-label-parity-safe" in view_json[0]
    assert "previous-value-safe" in view_json[0]
    assert "Bearer of; good news" in view_json[0]


@pytest.mark.asyncio
async def test_invalid_semantic_provider_body_is_not_copied_into_verifier_report() -> None:
    from afc.diagnosis.models import ProviderUsage
    from afc.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
    from afc.review.models import VerificationInput, canonical_sha256
    from tests.review.factories import make_diagnosis_report, make_review_snapshot

    class InvalidBodyProvider:
        async def complete(
            self,
            messages: tuple[ChatMessage, ...],
            config: GenerationConfig,
        ) -> ProviderResponse:
            del messages
            return ProviderResponse(
                content=f"{SENTINEL_KEY}:{RAW_PROVIDER_BODY}",
                model=config.model,
                response_id="secret-hygiene-response",
                finish_reason="stop",
                usage=ProviderUsage(
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    latency_ms=1.0,
                    request_id="secret-hygiene-response",
                ),
            )

    diagnosis = make_diagnosis_report()
    report = await SemanticVerifier(InvalidBodyProvider()).verify(
        VerificationInput(
            snapshot=make_review_snapshot(),
            report=diagnosis,
            report_sha256=canonical_sha256(diagnosis),
        )
    )

    _assert_sanitized(report.model_dump(mode="json"))


def _credential_findings(relative: str, text: str) -> tuple[str, ...]:
    findings: list[str] = []
    is_template = relative.replace("\\", "/") == ".env.example"
    for pattern in _ASSIGNMENT_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group("value").strip().removesuffix(",").strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1].strip()
            if is_template and value in _DOCUMENTATION_PLACEHOLDERS:
                continue
            findings.append("credential_assignment")
    if _KEY_LITERAL_PATTERN.search(text):
        findings.append("key_shaped_literal")
    return tuple(findings)


@pytest.mark.parametrize(
    ("relative", "text"),
    (
        ("config.env", _KEY_NAME + "=live-value"),
        ("profile.sh", "export " + _KEY_NAME + "='live-value'"),
        ("profile.ps1", "$env:" + _KEY_NAME + ' = "live-value"'),
        ("config.yaml", _KEY_NAME + ": live-value"),
        ("config.json", '{"' + _KEY_NAME + '": "live-value"}'),
        ("settings.py", 'provider_key = "' + "sk-" + "A" * 32 + '"'),
        ("config.env", _KEY_NAME + "="),
        (".env.example", _KEY_NAME + "=live-value"),
    ),
)
def test_credential_scanner_detects_assignment_syntaxes_and_key_literals(
    relative: str,
    text: str,
) -> None:
    assert _credential_findings(relative, text)


@pytest.mark.parametrize(
    ("relative", "text"),
    (
        (".env.example", _KEY_NAME + "="),
        (".env.example", _KEY_NAME + "=<set-locally>"),
        ("README.md", "Set `" + _KEY_NAME + "` in the local environment."),
        ("test_client.py", 'sentinel = "sk-private-provider-response"'),
        ("config.yaml", "# " + _KEY_NAME + ": documented-only"),
    ),
)
def test_credential_scanner_allows_only_non_secret_documentation_forms(
    relative: str,
    text: str,
) -> None:
    assert _credential_findings(relative, text) == ()


def test_tracked_files_do_not_contain_deepseek_credentials() -> None:
    encoded_paths = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    tracked = tuple(encoded_path.decode("utf-8") for encoded_path in encoded_paths if encoded_path)
    assert ".env" not in tracked, "ignored local environment file must never be scanned"

    offenders: dict[str, tuple[str, ...]] = {}
    for relative in tracked:
        content = (ROOT / relative).read_bytes()
        if b"\0" in content:
            continue
        findings = _credential_findings(relative, content.decode("utf-8", errors="replace"))
        if findings:
            offenders[relative] = findings
    assert not offenders, f"tracked files contain populated DeepSeek credentials: {offenders}"
