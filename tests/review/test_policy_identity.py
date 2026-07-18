from pathlib import Path

from spanvouch.api.app import _deterministic_runtime
from spanvouch.evals.run_review_eval import DEFAULT_POLICY_VERSION
from spanvouch.review.models import VerificationInput, canonical_sha256
from spanvouch.review.policy import DEFAULT_REVIEW_POLICY_VERSION
from tests.review.factories import make_diagnosis_report, make_review_snapshot


async def test_production_and_evaluator_share_one_default_policy_identity() -> None:
    _, verifier = _deterministic_runtime()
    report = make_diagnosis_report()

    verified = await verifier.verify(
        VerificationInput(
            snapshot=make_review_snapshot(),
            report=report,
            report_sha256=canonical_sha256(report),
        )
    )

    assert DEFAULT_POLICY_VERSION == DEFAULT_REVIEW_POLICY_VERSION
    assert verified.provenance.policy_version == DEFAULT_REVIEW_POLICY_VERSION
    source_root = Path(__file__).parents[2] / "src" / "spanvouch"
    occurrences = sum(
        path.read_text(encoding="utf-8").count(DEFAULT_REVIEW_POLICY_VERSION)
        for path in source_root.rglob("*.py")
    )
    assert occurrences == 1
