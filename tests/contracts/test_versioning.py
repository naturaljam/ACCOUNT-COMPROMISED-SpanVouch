from datetime import UTC, datetime, timedelta, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from spanvouch.contracts import (
    ContractError,
    ContractIntegrityError,
    ContractModel,
    ContractRoot,
    UnknownSchemaError,
    UnsupportedSchemaVersionError,
    canonical_bytes,
    canonical_json,
    canonical_sha256,
    require_schema,
)


class ExampleContract(ContractRoot):
    schema_name: Literal["spanvouch.example"] = "spanvouch.example"
    schema_version: Literal["1.0"] = "1.0"
    happened_at: datetime
    value: str


def test_canonical_bytes_are_utf8_sorted_compact_and_utc_z() -> None:
    model = ExampleContract(
        happened_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        value="证据",
    )

    assert canonical_bytes(model) == (
        b'{"happened_at":"2026-07-18T12:00:00Z",'
        b'"schema_name":"spanvouch.example",'
        b'"schema_version":"1.0","value":"\xe8\xaf\x81\xe6\x8d\xae"}'
    )
    assert canonical_json(model) == canonical_bytes(model).decode("utf-8")


def test_non_utc_aware_datetime_is_normalized_to_utc_z() -> None:
    local = timezone(timedelta(hours=8))
    model = ExampleContract(
        happened_at=datetime(2026, 7, 18, 20, 0, tzinfo=local),
        value="x",
    )

    assert '"happened_at":"2026-07-18T12:00:00Z"' in canonical_json(model)


def test_naive_datetime_is_rejected() -> None:
    model = ExampleContract(
        happened_at=datetime(2026, 7, 18, 12, 0),
        value="x",
    )

    with pytest.raises(ContractError, match="timezone-aware"):
        canonical_bytes(model)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected_when_nested(value: float) -> None:
    with pytest.raises(ContractError, match="NaN and Infinity"):
        canonical_bytes({"outer": [{"value": value}]})


@pytest.mark.parametrize("key", [1, True, None])
def test_non_string_mapping_keys_are_rejected_without_coercion(key: object) -> None:
    with pytest.raises(ContractError, match="string keys"):
        canonical_bytes({key: "value"})  # type: ignore[dict-item]


def test_unknown_fields_are_rejected_and_contracts_are_frozen() -> None:
    model = ExampleContract(
        happened_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        value="x",
    )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExampleContract.model_validate(
            {
                "schema_name": "spanvouch.example",
                "schema_version": "1.0",
                "happened_at": "2026-07-18T12:00:00Z",
                "value": "x",
                "unknown": True,
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        model.value = "changed"


def test_contract_model_is_strict_without_requiring_schema_metadata() -> None:
    class NestedContract(ContractModel):
        name: str

    assert NestedContract(name="nested").name == "nested"


def test_unknown_schema_and_unsupported_version_are_typed_separately() -> None:
    supported = {"spanvouch.example": {"1.0"}}

    with pytest.raises(UnknownSchemaError, match="unknown schema"):
        require_schema("spanvouch.missing", "1.0", supported=supported)
    with pytest.raises(UnsupportedSchemaVersionError, match="unsupported schema version"):
        require_schema("spanvouch.example", "2.0", supported=supported)
    assert require_schema("spanvouch.example", "1.0", supported=supported) is None


def test_hash_is_lowercase_sha256_and_hash_mismatch_is_typed() -> None:
    digest = canonical_sha256({"a": 1})

    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(character in "0123456789abcdef" for character in digest)
    with pytest.raises(ContractIntegrityError, match="SHA-256 mismatch"):
        canonical_sha256({"a": 1}, expected_sha256="0" * 64)


def test_json_document_string_retains_phase3_review_hash_semantics() -> None:
    document = '{"b":2,"a":1}'

    assert canonical_json(document) == '{"a":1,"b":2}'
    assert canonical_sha256(document) == canonical_sha256({"a": 1, "b": 2})


def test_plain_string_is_encoded_as_a_json_string() -> None:
    assert canonical_json("RefundRejected") == '"RefundRejected"'


def test_json_like_scalar_string_retains_phase3_evidence_hash_semantics() -> None:
    assert canonical_sha256("200.00") == (
        "2722d098b83d496e226a50ebd1ba44a47714943aff35a8d94b12df4dc504467c"
    )


def test_existing_phase3_diagnosis_fixture_hash_is_unchanged() -> None:
    from tests.review.factories import make_diagnosis_report

    assert canonical_sha256(make_diagnosis_report()) == (
        "21683b3b792dff3dce0570a54c694577be27d010b59062480c6dc3a40e4d3c87"
    )
