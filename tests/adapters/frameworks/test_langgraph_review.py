import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from spanvouch.adapters.frameworks.langgraph_review import LangGraphReviewWorkflow
from spanvouch.adapters.storage.sqlite import SQLiteReviewRepository
from spanvouch.contracts.diagnosis import (
    ClaimStage,
    DiagnoserKind,
    DiagnosisClaim,
    DiagnosisProvenance,
    DiagnosisReport,
    EvidenceSelector,
    TaxonomyRef,
)
from spanvouch.contracts.review import (
    DiagnosisRevision,
    ReviewStatus,
    RevisionOrigin,
)
from spanvouch.contracts.verification import (
    EvidenceGap,
    FindingCode,
    FindingSeverity,
    VerificationFinding,
    VerificationInput,
    VerificationMode,
    VerifierKind,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.review.commands import CreateReviewCase, WorkflowEventType
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine
from tests.review.factories import (
    NOW,
    make_diagnosis_report,
    make_review_snapshot,
    make_revision,
    make_trace_view,
    make_verifier_report,
)


class SequenceIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self) -> str:
        self._next += 1
        return f"workflow-id-{self._next}"


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeVerifier:
    def __init__(
        self,
        kind: VerifierKind,
        outcomes: list[VerifierReport | Exception],
        *,
        before_call: Callable[[], None] | None = None,
    ) -> None:
        self.kind = kind
        self.version_fingerprint = f"fake-{kind.value}-v1"
        self.outcomes = outcomes
        self.inputs: list[VerificationInput] = []
        self.before_call = before_call

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        if self.before_call is not None:
            self.before_call()
        self.inputs.append(input_)
        outcome = self.outcomes[len(self.inputs) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome.model_copy(update={"report_sha256": input_.report_sha256})


class FakeReviser:
    def __init__(
        self,
        *,
        supported: tuple[DiagnoserKind, ...] = (),
        outcomes: list[DiagnosisReport | Exception] | None = None,
        before_call: Callable[[], None] | None = None,
    ) -> None:
        self._supported = supported
        self._outcomes = outcomes or []
        self.calls: list[tuple[object, tuple[EvidenceGap, ...]]] = []
        self.before_call = before_call

    def supports(self, diagnoser_kind: DiagnoserKind) -> bool:
        return diagnoser_kind in self._supported

    async def revise(
        self, runtime_bundle: object, evidence_gaps: tuple[EvidenceGap, ...]
    ) -> DiagnosisReport:
        if self.before_call is not None:
            self.before_call()
        self.calls.append((runtime_bundle, evidence_gaps))
        outcome = self._outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _deepseek_report() -> DiagnosisReport:
    report = make_diagnosis_report()
    provenance = DiagnosisProvenance(
        taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
        diagnoser_version="fake-deepseek-v1",
        prompt_version="fake-prompt-v1",
        prompt_sha256="a" * 64,
        model="fake-model",
        provider="fake-provider",
    )
    return report.model_copy(update={"diagnoser": DiagnoserKind.DEEPSEEK, "provenance": provenance})


def _report(
    kind: VerifierKind,
    verdict: VerifierVerdict,
    *,
    revision_number: int,
    suffix: str,
    revisable: bool = True,
) -> VerifierReport:
    findings: tuple[VerificationFinding, ...] = ()
    gaps: tuple[EvidenceGap, ...] = ()
    if verdict is not VerifierVerdict.VERIFIED:
        findings = (
            VerificationFinding(
                finding_id=f"finding-{suffix}",
                code=FindingCode.CLAIM_NOT_GROUNDED,
                severity=FindingSeverity.HARD,
                message="Evidence is missing.",
                revisable=revisable,
                related_span_ids=("span-tool",),
            ),
        )
        if verdict is VerifierVerdict.NEEDS_EVIDENCE:
            gaps = (
                EvidenceGap(
                    gap_id=f"gap-{suffix}",
                    finding_code=FindingCode.CLAIM_NOT_GROUNDED,
                    claim_index=0,
                    required_evidence_kind="tool error",
                    related_span_ids=("span-tool",),
                    instruction="Ground the cause claim.",
                ),
            )
    return make_verifier_report(kind=kind, verdict=verdict, findings=findings).model_copy(
        update={
            "verifier_run_id": f"run-{suffix}",
            "revision_number": revision_number,
            "evidence_gaps": gaps,
        }
    )


async def _create_case(
    repository: SQLiteReviewRepository,
    *,
    mode: VerificationMode,
    diagnoser: DiagnoserKind,
) -> None:
    if diagnoser is DiagnoserKind.RULES:
        revision = make_revision()
    else:
        report = _deepseek_report()
        revision = DiagnosisRevision(
            revision_id="revision-0",
            case_id="case-review-1",
            revision_number=0,
            origin=RevisionOrigin.INITIAL_DIAGNOSIS,
            report=report,
            report_sha256=canonical_sha256(report),
            provenance=report.provenance,
            created_at=NOW,
        )
    await repository.create_case(
        CreateReviewCase(
            case_id="case-review-1",
            snapshot=make_review_snapshot(),
            initial_revision=revision,
            target_status=ReviewStatus.PENDING_VERIFICATION,
            verification_mode=mode,
            diagnoser=diagnoser,
            idempotency_scope="review.create",
            idempotency_key="create-1",
            request_sha256="b" * 64,
            event_id="event-created",
            event_type=WorkflowEventType.CASE_CREATED,
            event_metadata_json=canonical_json({"source": "workflow-test"}),
            created_at=NOW,
        )
    )


def _assert_active_lease(database: Path, expected_status: ReviewStatus) -> None:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status, lease_owner, lease_expires_at FROM review_cases WHERE case_id = ?",
            ("case-review-1",),
        ).fetchone()
    assert row is not None
    assert row[0] == expected_status.value
    assert row[1] is not None
    assert row[2] is not None


def _events(database: Path) -> list[tuple[str, int]]:
    with sqlite3.connect(database) as connection:
        return [
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT event_type, case_version FROM workflow_events ORDER BY event_sequence"
            ).fetchall()
        ]


def _workflow(
    repository: SQLiteReviewRepository,
    deterministic: FakeVerifier,
    *,
    semantic: FakeVerifier | None = None,
    reviser: FakeReviser | None = None,
    clock: Callable[[], datetime] | None = None,
    id_factory: SequenceIds | None = None,
    lease_owner: str = "workflow-worker",
    lease_token_factory: Callable[[], str] | None = None,
    lease_duration: timedelta = timedelta(seconds=30),
) -> LangGraphReviewWorkflow:
    return LangGraphReviewWorkflow(
        repository=repository,
        deterministic_verifier=deterministic,
        semantic_verifier=semantic,
        reviser=reviser or FakeReviser(),
        id_factory=id_factory or SequenceIds(),
        clock=clock or MutableClock(),
        lease_owner=lease_owner,
        lease_token_factory=lease_token_factory,
        lease_duration=lease_duration,
    )


async def test_deterministic_happy_path_uses_graph_and_routes_every_case_to_human(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workflow.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository, mode=VerificationMode.DETERMINISTIC, diagnoser=DiagnoserKind.RULES
    )
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC, VerifierVerdict.VERIFIED, revision_number=0, suffix="d0"
            )
        ],
        before_call=lambda: _assert_active_lease(database, ReviewStatus.VERIFYING),
    )
    workflow = _workflow(repository, deterministic)

    detail = await workflow.run("case-review-1")

    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert detail.case.composite_verdict is VerifierVerdict.VERIFIED
    assert len(deterministic.inputs) == 1
    assert _events(database) == [
        ("case_created", 0),
        ("verification_started", 1),
        ("verification_completed", 2),
        ("awaiting_human_review", 3),
    ]
    assert {
        "verify_initial",
        "request_revision",
        "revise_once",
        "verify_final",
        "route_to_human",
    }.issubset(workflow.graph.get_graph().nodes)


async def test_hybrid_calls_semantic_only_after_deterministic_pass(tmp_path: Path) -> None:
    database = tmp_path / "hybrid.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(repository, mode=VerificationMode.HYBRID, diagnoser=DiagnoserKind.DEEPSEEK)
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC, VerifierVerdict.VERIFIED, revision_number=0, suffix="d0"
            )
        ],
    )
    semantic = FakeVerifier(
        VerifierKind.SEMANTIC,
        [_report(VerifierKind.SEMANTIC, VerifierVerdict.VERIFIED, revision_number=0, suffix="s0")],
        before_call=lambda: _assert_active_lease(database, ReviewStatus.VERIFYING),
    )

    detail = await _workflow(repository, deterministic, semantic=semantic).run("case-review-1")

    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert detail.case.composite_verdict is VerifierVerdict.VERIFIED
    assert len(semantic.inputs) == 1
    assert [report.verifier_kind for report in detail.verifier_reports] == [
        VerifierKind.DETERMINISTIC,
        VerifierKind.SEMANTIC,
    ]


async def test_hybrid_hard_deterministic_finding_skips_semantic(tmp_path: Path) -> None:
    repository = SQLiteReviewRepository(tmp_path / "hard.sqlite3")
    await repository.initialize()
    await _create_case(repository, mode=VerificationMode.HYBRID, diagnoser=DiagnoserKind.DEEPSEEK)
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.REVIEW_REQUIRED,
                revision_number=0,
                suffix="hard",
                revisable=False,
            )
        ],
    )
    semantic = FakeVerifier(VerifierKind.SEMANTIC, [])

    detail = await _workflow(repository, deterministic, semantic=semantic).run("case-review-1")

    assert detail.case.composite_verdict is VerifierVerdict.REVIEW_REQUIRED
    assert not semantic.inputs


async def test_one_evidence_revision_is_append_only_and_fully_reverified(
    tmp_path: Path,
) -> None:
    database = tmp_path / "revision.sqlite3"
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.DEEPSEEK,
    )
    revised_report = _deepseek_report().model_copy(
        update={
            "provenance": _deepseek_report().provenance.model_copy(
                update={"diagnoser_version": "fake-deepseek-revision-v1"}
            )
        }
    )
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.NEEDS_EVIDENCE,
                revision_number=0,
                suffix="d0",
            ),
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=1,
                suffix="d1",
            ),
        ],
    )
    reviser = FakeReviser(supported=(DiagnoserKind.DEEPSEEK,), outcomes=[revised_report])

    detail = await _workflow(repository, deterministic, reviser=reviser).run("case-review-1")

    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert detail.case.current_revision_number == 1
    assert detail.case.evidence_revision_count == 1
    assert len(detail.revisions) == 2
    assert detail.revisions[1].origin is RevisionOrigin.EVIDENCE_REVISION
    assert detail.revisions[1].previous_report_sha256 == detail.revisions[0].report_sha256
    assert detail.revisions[1].triggering_gap_ids == ("gap-d0",)
    assert [report.revision_number for report in detail.verifier_reports] == [0, 1]
    assert len(reviser.calls) == 1


async def test_identical_report_bytes_reverify_with_revision_safe_run_identity(
    tmp_path: Path,
) -> None:
    repository = SQLiteReviewRepository(tmp_path / "same-report-revision.sqlite3")
    await repository.initialize()
    source = _deepseek_report()
    decoy = EvidenceCatalog.from_view(make_trace_view()).resolve(
        EvidenceSelector(span_id="span-root", field_path="status"),
        description="The root span ended in an error state.",
    )
    same_report = DiagnosisReport(
        **{
            **source.model_dump(exclude={"causal_chain", "evidence"}),
            "causal_chain": (
                DiagnosisClaim(
                    stage=ClaimStage.CAUSE,
                    statement=source.causal_chain[0].statement,
                    evidence_ids=(decoy.evidence_id,),
                ),
            ),
            "evidence": (*source.evidence, decoy),
        }
    )
    initial_revision = DiagnosisRevision(
        revision_id="revision-0",
        case_id="case-review-1",
        revision_number=0,
        origin=RevisionOrigin.INITIAL_DIAGNOSIS,
        report=same_report,
        report_sha256=canonical_sha256(same_report),
        provenance=same_report.provenance,
        created_at=NOW,
    )
    await repository.create_case(
        CreateReviewCase(
            case_id="case-review-1",
            snapshot=make_review_snapshot(),
            initial_revision=initial_revision,
            target_status=ReviewStatus.PENDING_VERIFICATION,
            verification_mode=VerificationMode.DETERMINISTIC,
            diagnoser=DiagnoserKind.DEEPSEEK,
            idempotency_scope="review.create",
            idempotency_key="same-report-create",
            request_sha256="c" * 64,
            event_id="same-report-created",
            event_type=WorkflowEventType.CASE_CREATED,
            event_metadata_json=canonical_json({"source": "revision-identity-test"}),
            created_at=NOW,
        )
    )
    verifier = DeterministicVerifier(InvariantEngine(()), policy_version="review-policy-v1")
    reviser = FakeReviser(
        supported=(DiagnoserKind.DEEPSEEK,), outcomes=[same_report]
    )
    workflow = LangGraphReviewWorkflow(
        repository=repository,
        deterministic_verifier=verifier,
        semantic_verifier=None,
        reviser=reviser,
        id_factory=SequenceIds(),
        clock=MutableClock(),
        lease_owner="workflow-worker",
        lease_duration=timedelta(seconds=30),
    )

    detail = await workflow.run("case-review-1")

    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert [revision.report_sha256 for revision in detail.revisions] == [
        canonical_sha256(same_report),
        canonical_sha256(same_report),
    ]
    assert [report.revision_number for report in detail.verifier_reports] == [0, 1]
    assert len({report.verifier_run_id for report in detail.verifier_reports}) == 2


async def test_hybrid_revision_reruns_both_verifiers_against_revision_one(
    tmp_path: Path,
) -> None:
    repository = SQLiteReviewRepository(tmp_path / "hybrid-revision.sqlite3")
    await repository.initialize()
    await _create_case(
        repository,
        mode=VerificationMode.HYBRID,
        diagnoser=DiagnoserKind.DEEPSEEK,
    )
    revised_report = _deepseek_report().model_copy(
        update={
            "provenance": _deepseek_report().provenance.model_copy(
                update={"diagnoser_version": "fake-deepseek-revision-v1"}
            )
        }
    )
    deterministic = FakeVerifier(
        VerifierKind.DETERMINISTIC,
        [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=0,
                suffix="hybrid-d0",
            ),
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.VERIFIED,
                revision_number=1,
                suffix="hybrid-d1",
            ),
        ],
    )
    semantic = FakeVerifier(
        VerifierKind.SEMANTIC,
        [
            _report(
                VerifierKind.SEMANTIC,
                VerifierVerdict.NEEDS_EVIDENCE,
                revision_number=0,
                suffix="hybrid-s0",
            ),
            _report(
                VerifierKind.SEMANTIC,
                VerifierVerdict.VERIFIED,
                revision_number=1,
                suffix="hybrid-s1",
            ),
        ],
    )
    reviser = FakeReviser(
        supported=(DiagnoserKind.DEEPSEEK,), outcomes=[revised_report]
    )

    detail = await _workflow(
        repository,
        deterministic,
        semantic=semantic,
        reviser=reviser,
    ).run("case-review-1")

    assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
    assert detail.case.composite_verdict is VerifierVerdict.VERIFIED
    assert [report.revision_number for report in detail.verifier_reports] == [0, 0, 1, 1]
    assert len(deterministic.inputs) == 2
    assert len(semantic.inputs) == 2
    assert semantic.inputs[1].report.provenance.diagnoser_version == (
        "fake-deepseek-revision-v1"
    )


async def test_second_needs_evidence_routes_human_and_rules_never_revise(
    tmp_path: Path,
) -> None:
    for name, diagnoser, supports, expected_calls in (
        ("bounded", DiagnoserKind.DEEPSEEK, (DiagnoserKind.DEEPSEEK,), 1),
        ("rules", DiagnoserKind.RULES, (), 0),
    ):
        repository = SQLiteReviewRepository(tmp_path / f"{name}.sqlite3")
        await repository.initialize()
        await _create_case(
            repository,
            mode=VerificationMode.DETERMINISTIC,
            diagnoser=diagnoser,
        )
        outcomes = [
            _report(
                VerifierKind.DETERMINISTIC,
                VerifierVerdict.NEEDS_EVIDENCE,
                revision_number=0,
                suffix=f"{name}-0",
            )
        ]
        if expected_calls:
            outcomes.append(
                _report(
                    VerifierKind.DETERMINISTIC,
                    VerifierVerdict.NEEDS_EVIDENCE,
                    revision_number=1,
                    suffix=f"{name}-1",
                )
            )
        deterministic = FakeVerifier(VerifierKind.DETERMINISTIC, outcomes)
        reviser = FakeReviser(
            supported=supports,
            outcomes=[_deepseek_report()] if expected_calls else [],
        )

        detail = await _workflow(repository, deterministic, reviser=reviser).run("case-review-1")

        assert detail.case.status is ReviewStatus.AWAITING_HUMAN_REVIEW
        assert detail.case.current_revision_number == expected_calls
        assert detail.case.evidence_revision_count == expected_calls
        assert len(reviser.calls) == expected_calls
