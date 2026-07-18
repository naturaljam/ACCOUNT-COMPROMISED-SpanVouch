import json

import pytest
from pydantic import ValidationError

from spanvouch.review.models import (
    DiagnosisCorrectionDraft,
    DiagnosisRevision,
    ReviewInputSnapshot,
    RevisionOrigin,
    canonical_sha256,
)
from tests.review.factories import (
    make_correction_draft,
    make_review_snapshot,
    make_revision,
)


def test_snapshot_hash_is_stable_for_canonical_json() -> None:
    first = make_review_snapshot()
    second = first.model_copy(update={"view_json": first.view_json})
    assert first.input_sha256 == second.input_sha256


def test_snapshot_rejects_invalid_and_noncanonical_view_json() -> None:
    snapshot = make_review_snapshot()
    payload = snapshot.model_dump(mode="python")

    with pytest.raises(ValidationError, match="valid JSON"):
        ReviewInputSnapshot.model_validate({**payload, "view_json": "{"})

    noncanonical = json.dumps(json.loads(snapshot.view_json), ensure_ascii=False)
    with pytest.raises(ValidationError, match="canonical JSON"):
        ReviewInputSnapshot.model_validate({**payload, "view_json": noncanonical})


def test_snapshot_rejects_hash_that_is_not_bound_to_sanitized_view() -> None:
    snapshot = make_review_snapshot()

    with pytest.raises(ValidationError, match="input_sha256"):
        ReviewInputSnapshot.model_validate(
            {**snapshot.model_dump(mode="python"), "input_sha256": "0" * 64}
        )


def test_revision_zero_has_no_previous_hash() -> None:
    revision = make_revision(revision_number=0, previous_report_sha256=None)
    assert revision.report_sha256 == canonical_sha256(revision.report)


def test_revision_one_requires_previous_hash_and_gap_ids() -> None:
    with pytest.raises(ValidationError):
        make_revision(
            revision_number=1,
            previous_report_sha256=None,
            triggering_gap_ids=(),
        )


def test_revision_requires_explicit_diagnoser_provenance() -> None:
    payload = make_revision().model_dump(mode="json")
    payload.pop("provenance")
    with pytest.raises(ValidationError):
        DiagnosisRevision.model_validate(payload)


def test_evidence_revision_is_permanently_capped_at_one() -> None:
    with pytest.raises(ValidationError, match="evidence revision"):
        make_revision(
            revision_number=2,
            origin=RevisionOrigin.EVIDENCE_REVISION,
            previous_report_sha256="a" * 64,
            triggering_gap_ids=("gap-1",),
        )


@pytest.mark.parametrize("revision_number", (1, 2))
def test_human_correction_remains_valid_after_either_review_round(
    revision_number: int,
) -> None:
    revision = make_revision(
        revision_number=revision_number,
        origin=RevisionOrigin.HUMAN_CORRECTION,
        previous_report_sha256="a" * 64,
    )
    assert revision.origin is RevisionOrigin.HUMAN_CORRECTION


def test_correction_draft_rejects_forged_observed_value() -> None:
    payload = make_correction_draft().model_dump(mode="json")
    payload["observed_value"] = "forged"
    with pytest.raises(ValidationError):
        DiagnosisCorrectionDraft.model_validate(payload)
