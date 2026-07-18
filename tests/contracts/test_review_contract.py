import ast
import importlib
import json
from pathlib import Path

import pytest

from spanvouch.contracts.review import DiagnosisReviewCase, DiagnosisReviewDetail
from spanvouch.contracts.versioning import canonical_bytes
from tests.review.factories import make_review_detail

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def review_detail() -> DiagnosisReviewDetail:
    return make_review_detail()


def test_review_detail_is_the_versioned_public_root(
    review_detail: DiagnosisReviewDetail,
) -> None:
    detail = DiagnosisReviewDetail.model_validate(
        review_detail.model_dump(mode="python")
    )
    assert detail.schema_name == "spanvouch.review"
    assert detail.schema_version == "1.0"


def test_review_case_accepts_an_extensible_diagnoser_identifier() -> None:
    payload = make_review_detail().case.model_dump(mode="python")
    payload["diagnoser"] = "third-party.diagnoser"
    assert DiagnosisReviewCase.model_validate(payload).diagnoser == "third-party.diagnoser"


def test_runtime_bundle_is_not_exported_as_a_contract() -> None:
    module = importlib.import_module("spanvouch.contracts.review")
    assert not hasattr(module, "ReviewRuntimeBundle")


def test_review_schema_excludes_private_runtime_state() -> None:
    schema = json.dumps(DiagnosisReviewDetail.model_json_schema())
    for private_name in (
        "ReviewRuntimeBundle",
        "ReviewInputSnapshot",
        "lease_owner",
        "lease_expires_at",
    ):
        assert private_name not in schema


def test_checked_in_schema_and_fixture_match_contract(
    review_detail: DiagnosisReviewDetail,
) -> None:
    schema = json.dumps(
        DiagnosisReviewDetail.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"
    assert (ROOT / "schemas/v1/spanvouch.review-1.0.schema.json").read_bytes() == schema
    assert (
        ROOT / "tests/contracts/fixtures/v1/review.valid.json"
    ).read_bytes() == canonical_bytes(review_detail) + b"\n"


def test_public_review_contract_does_not_import_runtime_or_commands() -> None:
    module = ROOT / "src/spanvouch/contracts/review.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(
        name.startswith(("spanvouch.review.runtime", "spanvouch.review.commands"))
        for name in imported
    )
