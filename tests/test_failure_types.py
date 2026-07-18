from afc.failure_types import SUPPORTED_DIAGNOSIS_FAILURE_TYPES, FailureType
from afc.supportlab.scenarios import FailureType as LegacyFailureType


def test_failure_type_has_one_shared_definition() -> None:
    assert LegacyFailureType is FailureType
    assert FailureType.MISSING_PRECONDITION not in SUPPORTED_DIAGNOSIS_FAILURE_TYPES
    assert FailureType.INVALID_FINAL_STATE in SUPPORTED_DIAGNOSIS_FAILURE_TYPES
