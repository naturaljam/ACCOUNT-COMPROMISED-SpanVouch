import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from spanvouch.adapters.frameworks.langgraph_review import LangGraphReviewWorkflow
from spanvouch.adapters.storage.sqlite import SQLiteReviewRepository
from spanvouch.api.app import create_app
from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.contracts.review import (
    ReviewStatus,
    WorkflowEventType,
)
from spanvouch.contracts.verification import (
    VerificationInput,
    VerificationMode,
    VerifierKind,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import canonical_json
from spanvouch.diagnosis.engine import DiagnosisEngine
from spanvouch.diagnosis.errors import ProviderProtocolError, ProviderRequestError
from spanvouch.diagnosis.rule_diagnoser import RuleDiagnoser
from spanvouch.labs.supportlab.invariants import supportlab_rules
from spanvouch.review.application import ReviewApplication
from spanvouch.review.commands import ClaimReviewWork, CreateReviewCase
from spanvouch.review.reviser import DiagnosisReviser
from spanvouch.trace.repository import InMemoryTraceRepository
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine
from tests.adapters.frameworks.test_langgraph_review import (
    FakeReviser,
    FakeVerifier,
    _create_case,
    _report,
    _workflow,
)
from tests.review.factories import NOW, make_review_snapshot, make_revision
from tests.trace.test_diagnostic_view import load_trace


@pytest.fixture(autouse=True)
def _disable_live_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def _ingest(client: TestClient, run_id: str = "invalid_argument-01") -> str:
    trace = load_trace(run_id)
    response = client.post("/v1/traces", json=trace.model_dump(mode="json"))
    assert response.status_code == 201
    return trace.trace_id


def _create(client: TestClient, trace_id: str, *, key: str = "review-create-1"):
    return client.post(
        f"/v1/traces/{trace_id}/diagnosis-reviews",
        json={"idempotency_key": key},
    )


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"api-integration-{self.value}"


class RecordingVerifier:
    def __init__(
        self,
        kind: VerifierKind,
        *,
        delegate: Any | None = None,
        error: Exception | None = None,
    ) -> None:
        self.kind = kind
        self.version_fingerprint = f"api-{kind.value}-v1"
        self.delegate = delegate
        self.error = error
        self.inputs: list[VerificationInput] = []

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        self.inputs.append(input_)
        if self.error is not None:
            raise self.error
        assert self.delegate is not None
        return await self.delegate.verify(input_)


def _real_workflow_app(
    database: Path,
    *,
    semantic_error: Exception | None = None,
) -> tuple[
    Any,
    SQLiteReviewRepository,
    RecordingVerifier,
    RecordingVerifier | None,
    MutableClock,
]:
    engine = InvariantEngine(supportlab_rules())
    rule_diagnoser = RuleDiagnoser(engine)
    diagnosers = {DiagnoserKind.RULES: rule_diagnoser}
    diagnosis_service = DiagnosisEngine(diagnosers)
    repository = SQLiteReviewRepository(database)
    deterministic = RecordingVerifier(
        VerifierKind.DETERMINISTIC,
        delegate=DeterministicVerifier(engine, policy_version="supportlab-review-v1"),
    )
    semantic = (
        RecordingVerifier(VerifierKind.SEMANTIC, error=semantic_error)
        if semantic_error is not None
        else None
    )
    clock = MutableClock()
    ids = SequenceIds()
    workflow = LangGraphReviewWorkflow(
        repository=repository,
        deterministic_verifier=deterministic,
        semantic_verifier=semantic,
        reviser=DiagnosisReviser(diagnosers),
        id_factory=ids,
        clock=clock,
        lease_owner="api-integration-worker",
        lease_duration=timedelta(seconds=30),
    )
    review_service = ReviewApplication(
        diagnosis_service=diagnosis_service,
        repository=repository,
        workflow=workflow,
        deterministic_verifier=deterministic,
        id_factory=ids,
        clock=clock,
    )
    application = create_app(
        trace_repository=InMemoryTraceRepository(),
        diagnosis_service=diagnosis_service,
        review_repository=repository,
        review_service=review_service,
    )
    return application, repository, deterministic, semantic, clock


def _seed_active_case(
    repository: SQLiteReviewRepository,
    *,
    case_id: str,
) -> None:
    snapshot = make_review_snapshot()
    revision = make_revision().model_copy(update={"case_id": case_id})
    asyncio.run(
        repository.create_case(
            CreateReviewCase(
                case_id=case_id,
                snapshot=snapshot,
                initial_revision=revision,
                target_status=ReviewStatus.PENDING_VERIFICATION,
                verification_mode=VerificationMode.DETERMINISTIC,
                diagnoser=DiagnoserKind.RULES,
                idempotency_scope="review.create",
                idempotency_key=f"seed-{case_id}",
                request_sha256="a" * 64,
                event_id=f"event-create-{case_id}",
                event_type=WorkflowEventType.CASE_CREATED,
                event_metadata_json=canonical_json({"source": "api-integration"}),
                created_at=NOW,
            )
        )
    )
    asyncio.run(
        repository.claim_work(
            ClaimReviewWork(
                case_id=case_id,
                expected_version=0,
                prior_status=ReviewStatus.PENDING_VERIFICATION,
                target_status=ReviewStatus.VERIFYING,
                lease_owner="crashed-worker",
                lease_expires_at=NOW + timedelta(seconds=30),
                now=NOW,
                event_id=f"event-claim-{case_id}",
                event_type=WorkflowEventType.VERIFICATION_STARTED,
                event_metadata_json=canonical_json({"worker": "crashed-worker"}),
                occurred_at=NOW,
            )
        )
    )


def test_default_review_is_offline_deterministic_and_persists_across_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "review.db"
    with TestClient(create_app(review_database=database)) as first_client:
        trace_id = _ingest(first_client)
        created = _create(first_client, trace_id)

    assert created.status_code == 201
    payload = created.json()
    assert payload["case"]["status"] == "awaiting_human_review"
    assert payload["case"]["verification_mode"] == "deterministic"
    assert payload["resume_requires_live_api"] is False
    assert len(payload["revisions"]) == 1
    assert [report["verifier_kind"] for report in payload["verifier_reports"]] == [
        "deterministic"
    ]
    assert [event["event_sequence"] for event in payload["events"]] == list(
        range(len(payload["events"]))
    )
    assert payload["events"][-1]["event_type"] == "awaiting_human_review"

    serialized = created.text.lower()
    for forbidden in (
        "view_json",
        "provider_body",
        "system_prompt",
        "user_prompt",
        "afc_db_path",
        "select * from",
        "deepseek_api_key",
    ):
        assert forbidden not in serialized

    with TestClient(create_app(review_database=database)) as second_client:
        restored = second_client.get(
            f"/v1/diagnosis-reviews/{payload['case']['case_id']}"
        )

    assert restored.status_code == 200
    assert restored.json() == payload


def test_lifespan_creates_nested_database_parent_but_app_construction_does_not(
    tmp_path: Path,
) -> None:
    database = tmp_path / "nested" / ".data" / "afc.db"
    application = create_app(review_database=database)

    assert not database.parent.exists()
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        assert database.parent.is_dir()
        assert database.is_file()


@pytest.mark.parametrize(
    "database",
    (":memory:", "file:review-memory?mode=memory&cache=shared", "file:review.sqlite3"),
)
def test_app_rejects_unsupported_sqlite_memory_and_uri_paths(database: str) -> None:
    with pytest.raises(ValueError, match="filesystem path"):
        create_app(review_database=database)


def test_create_and_decision_replay_the_original_result(tmp_path: Path) -> None:
    with TestClient(create_app(review_database=tmp_path / "review.db")) as client:
        trace_id = _ingest(client)
        first = _create(client, trace_id)
        replay = _create(client, trace_id)
        assert first.status_code == replay.status_code == 201
        assert replay.json() == first.json()

        case = first.json()["case"]
        body = {
            "action": "confirm",
            "expected_version": case["version"],
            "reviewer_label": "api-reviewer",
            "idempotency_key": "decision-1",
        }
        decided = client.post(
            f"/v1/diagnosis-reviews/{case['case_id']}/decisions", json=body
        )
        decision_replay = client.post(
            f"/v1/diagnosis-reviews/{case['case_id']}/decisions", json=body
        )

    assert decided.status_code == decision_replay.status_code == 200
    assert decided.json() == decision_replay.json()
    assert decided.json()["case"]["status"] == "confirmed"
    assert decided.json()["events"][-1]["event_type"] == "human_confirmed"


def test_resume_awaiting_human_is_conflict_and_does_not_mutate(tmp_path: Path) -> None:
    with TestClient(create_app(review_database=tmp_path / "review.db")) as client:
        trace_id = _ingest(client)
        created = _create(client, trace_id).json()
        case_id = created["case"]["case_id"]

        resumed = client.post(f"/v1/diagnosis-reviews/{case_id}/resume")
        after = client.get(f"/v1/diagnosis-reviews/{case_id}")

    assert resumed.status_code == 409
    assert resumed.json() == {"detail": {"code": "review_conflict"}}
    assert after.json() == created


def test_create_idempotency_conflict_is_409(tmp_path: Path) -> None:
    with TestClient(create_app(review_database=tmp_path / "review.db")) as client:
        trace_id = _ingest(client)
        assert _create(client, trace_id, key="shared-key").status_code == 201
        conflict = client.post(
            f"/v1/traces/{trace_id}/diagnosis-reviews",
            json={"idempotency_key": "shared-key", "verifier": "hybrid"},
        )

    assert conflict.status_code == 409
    assert conflict.json() == {"detail": {"code": "review_conflict"}}


def test_missing_semantic_provider_routes_durably_before_503(tmp_path: Path) -> None:
    with TestClient(create_app(review_database=tmp_path / "review.db")) as client:
        trace_id = _ingest(client, "clean-01")
        failed = client.post(
            f"/v1/traces/{trace_id}/diagnosis-reviews",
            json={"idempotency_key": "hybrid-create", "verifier": "hybrid"},
        )
        case_id = failed.json()["detail"]["case_id"]
        durable = client.get(f"/v1/diagnosis-reviews/{case_id}")

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": {
            "code": "provider_not_configured",
            "case_id": case_id,
            "retryable": False,
        }
    }
    assert durable.status_code == 200
    payload = durable.json()
    assert payload["case"]["status"] == "awaiting_human_review"
    assert payload["resume_requires_live_api"] is False
    assert payload["verifier_reports"][-1]["operational_error"]["code"] == (
        "provider_not_configured"
    )
    assert payload["events"][-2]["event_type"] == "provider_failed"
    assert payload["events"][-1]["event_type"] == "awaiting_human_review"


def test_restart_without_revision_provider_routes_durably_before_sanitized_503(
    tmp_path: Path,
) -> None:
    database = tmp_path / "restart-missing-reviser.db"

    class StopAfterRevisionRequest(SQLiteReviewRepository):
        async def append_verifier_run(self, command):  # type: ignore[no-untyped-def]
            result = await super().append_verifier_run(command)
            if result.status is ReviewStatus.REVISION_REQUESTED:
                raise RuntimeError("simulated restart after revision request")
            return result

    repository = StopAfterRevisionRequest(database)
    asyncio.run(repository.initialize())
    asyncio.run(
        _create_case(
            repository,
            mode=VerificationMode.DETERMINISTIC,
            diagnoser=DiagnoserKind.DEEPSEEK,
        )
    )
    verifier = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                verdict=VerifierVerdict.NEEDS_EVIDENCE,
                revision_number=0,
                suffix="restart-missing-reviser",
            )
        ],
    )
    with pytest.raises(RuntimeError, match="simulated restart"):
        asyncio.run(
            _workflow(
                repository,
                verifier,
                reviser=FakeReviser(supported=(DiagnoserKind.DEEPSEEK,)),
            ).run("case-review-1")
        )
    persisted = asyncio.run(repository.get_detail("case-review-1"))
    assert persisted.case.status is ReviewStatus.REVISION_REQUESTED

    with TestClient(create_app(review_database=database)) as client:
        failed = client.post(
            "/v1/diagnosis-reviews/case-review-1/resume",
            json={"allow_live_api": True},
        )
        durable = client.get("/v1/diagnosis-reviews/case-review-1")
        case = durable.json()["case"]
        decided = client.post(
            "/v1/diagnosis-reviews/case-review-1/decisions",
            json={
                "action": "confirm",
                "expected_version": case["version"],
                "reviewer_label": "restart-reviewer",
                "idempotency_key": "restart-human-decision",
                "reason": "Confirm after the revision provider became unavailable.",
            },
        )

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": {
            "code": "provider_not_configured",
            "case_id": "case-review-1",
            "retryable": False,
        }
    }
    assert durable.status_code == 200
    payload = durable.json()
    assert payload["case"]["status"] == "awaiting_human_review"
    assert payload["events"][-1]["event_type"] == "revision_provider_failed"
    with sqlite3.connect(database) as connection:
        failure_event = connection.execute(
            "SELECT metadata_json FROM workflow_events "
            "WHERE event_type = 'revision_provider_failed' "
            "ORDER BY event_sequence DESC LIMIT 1"
        ).fetchone()
    assert failure_event is not None
    assert failure_event[0] == canonical_json(
        {"code": "provider_not_configured", "retryable": False}
    )
    assert decided.status_code == 200
    assert decided.json()["case"]["status"] == "confirmed"


def test_invalid_correction_is_review_422_without_mutation(tmp_path: Path) -> None:
    with TestClient(create_app(review_database=tmp_path / "review.db")) as client:
        trace_id = _ingest(client)
        before = _create(client, trace_id).json()
        case = before["case"]
        response = client.post(
            f"/v1/diagnosis-reviews/{case['case_id']}/decisions",
            json={
                "action": "correct",
                "expected_version": case["version"],
                "reviewer_label": "api-reviewer",
                "idempotency_key": "invalid-correction",
                "correction": {
                    "status": "diagnosed",
                    "failure_type": "invalid_argument",
                    "critical_span_ids": ["missing-span"],
                    "causal_chain": [
                        {
                            "stage": "cause",
                            "statement": "Forged correction",
                            "selectors": [
                                {
                                    "span_id": "missing-span",
                                    "field_path": "attributes.tool.error.type",
                                }
                            ],
                        }
                    ],
                    "confidence": 1.0,
                },
            },
        )
        after = client.get(f"/v1/diagnosis-reviews/{case['case_id']}")

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "review_invalid"}}
    assert after.json() == before


def test_review_request_validation_remains_fastapi_422(tmp_path: Path) -> None:
    with TestClient(create_app(review_database=tmp_path / "review.db")) as client:
        response = client.post(
            "/v1/traces/missing/diagnosis-reviews",
            json={"idempotency_key": "", "verifier": "unknown"},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"]


def test_real_endpoint_rejects_active_lease_then_resumes_expired_work(
    tmp_path: Path,
) -> None:
    application, repository, deterministic, _, clock = _real_workflow_app(
        tmp_path / "review.db"
    )
    with TestClient(application) as client:
        _seed_active_case(repository, case_id="case-expired-resume")

        active = client.post("/v1/diagnosis-reviews/case-expired-resume/resume")
        assert active.status_code == 409
        assert active.json() == {"detail": {"code": "review_conflict"}}
        assert deterministic.inputs == []

        clock.now = NOW + timedelta(seconds=31)
        resumed = client.post("/v1/diagnosis-reviews/case-expired-resume/resume")

    assert resumed.status_code == 200
    assert resumed.json()["case"]["status"] == "awaiting_human_review"
    assert len(deterministic.inputs) == 1


def test_real_endpoint_human_and_terminal_resume_invoke_no_verifier(
    tmp_path: Path,
) -> None:
    application, _, deterministic, semantic, _ = _real_workflow_app(
        tmp_path / "review.db",
        semantic_error=ProviderProtocolError("must not be called"),
    )
    assert semantic is not None
    with TestClient(application) as client:
        trace_id = _ingest(client)
        created = _create(client, trace_id).json()
        case = created["case"]
        calls_after_create = len(deterministic.inputs)

        awaiting = client.post(f"/v1/diagnosis-reviews/{case['case_id']}/resume")
        assert awaiting.status_code == 409
        assert len(deterministic.inputs) == calls_after_create
        assert semantic.inputs == []

        decided = client.post(
            f"/v1/diagnosis-reviews/{case['case_id']}/decisions",
            json={
                "action": "confirm",
                "expected_version": case["version"],
                "reviewer_label": "api-integration-reviewer",
                "idempotency_key": "terminal-decision",
            },
        )
        assert decided.status_code == 200
        terminal = client.post(f"/v1/diagnosis-reviews/{case['case_id']}/resume")

    assert terminal.status_code == 409
    assert len(deterministic.inputs) == calls_after_create
    assert semantic.inputs == []


def test_real_endpoint_stale_human_version_is_409(tmp_path: Path) -> None:
    application, _, _, _, _ = _real_workflow_app(tmp_path / "review.db")
    with TestClient(application) as client:
        trace_id = _ingest(client)
        created = _create(client, trace_id).json()
        case = created["case"]
        stale = client.post(
            f"/v1/diagnosis-reviews/{case['case_id']}/decisions",
            json={
                "action": "confirm",
                "expected_version": case["version"] - 1,
                "reviewer_label": "stale-reviewer",
                "idempotency_key": "stale-decision",
            },
        )

    assert stale.status_code == 409
    assert stale.json() == {"detail": {"code": "review_conflict"}}


@pytest.mark.parametrize(
    ("provider_error", "expected_status", "expected_code", "expected_retryable"),
    [
        (
            ProviderProtocolError("private provider response"),
            502,
            "provider_protocol_error",
            False,
        ),
        (
            ProviderRequestError("upstream_http_error", retryable=False),
            502,
            "upstream_http_error",
            False,
        ),
        (
            ProviderRequestError("transport_error", retryable=True),
            503,
            "transport_error",
            True,
        ),
    ],
)
def test_real_provider_failure_is_durable_before_api_error(
    provider_error: Exception,
    expected_status: int,
    expected_code: str,
    expected_retryable: bool,
    tmp_path: Path,
) -> None:
    application, _, _, semantic, _ = _real_workflow_app(
        tmp_path / "review.db", semantic_error=provider_error
    )
    assert semantic is not None
    with TestClient(application) as client:
        trace_id = _ingest(client, "clean-01")
        failed = client.post(
            f"/v1/traces/{trace_id}/diagnosis-reviews",
            json={"idempotency_key": "provider-failure", "verifier": "hybrid"},
        )
        detail = failed.json()["detail"]
        durable = client.get(f"/v1/diagnosis-reviews/{detail['case_id']}")

    assert failed.status_code == expected_status
    assert detail == {
        "code": expected_code,
        "case_id": detail["case_id"],
        "retryable": expected_retryable,
    }
    assert durable.status_code == 200
    payload = durable.json()
    assert payload["case"]["status"] == "awaiting_human_review"
    assert payload["verifier_reports"][-1]["operational_error"]["code"] == expected_code
    assert payload["events"][-2]["event_type"] == "provider_failed"
    assert payload["events"][-1]["event_type"] == "awaiting_human_review"
    assert len(semantic.inputs) == 1
