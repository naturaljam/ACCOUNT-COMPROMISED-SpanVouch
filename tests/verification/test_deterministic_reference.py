import json
from hashlib import sha256
from pathlib import Path

from spanvouch.contracts.verification import VerificationInput, VerifierReport
from spanvouch.contracts.versioning import canonical_bytes, canonical_json, canonical_sha256
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine

REFERENCE = Path(__file__).with_name("fixtures") / "deterministic-reference-v1.json"
REFERENCE_FIXTURE_SHA256 = "f5e047e53b9d7961ebd9531e8a8aae451db8467a4e6ca5dc6636f54bc00d8447"
REFERENCE_INPUT_SHA256 = "04f2f8d0f9f9aa8b0bb815eb66a5e0ccfba12cbbb5723b2d16be92a39f55ef93"
REFERENCE_REPORT_SHA256 = "fa03e25ace6b1be93e6cf9f3d6e2f7e0ae9b83562509e04688a3f6e6f59de5dc"
TASK9_BASE_COMMIT = "9cd6b40875666f3158604eb45f0e11f0d477c8ad"
TASK9_COMMIT = "7ef13b0a870848037461a03fe735e2c30278aa2e"


async def test_deterministic_verifier_matches_frozen_canonical_reference() -> None:
    raw = REFERENCE.read_bytes()
    payload = json.loads(raw)

    assert raw == f"{canonical_json(payload)}\n".encode()
    assert sha256(raw).hexdigest() == REFERENCE_FIXTURE_SHA256
    assert payload["provenance"] == {
        "policy_version": "review-policy-v1",
        "task9_base_commit": TASK9_BASE_COMMIT,
        "task9_commit": TASK9_COMMIT,
        "verifier_version_source": "evidence-verifier-v1",
    }

    request = VerificationInput.model_validate(payload["input"])
    expected = VerifierReport.model_validate(payload["report"])
    assert canonical_sha256(request) == REFERENCE_INPUT_SHA256
    assert canonical_sha256(expected) == REFERENCE_REPORT_SHA256

    actual = await DeterministicVerifier(
        InvariantEngine(()), policy_version=payload["provenance"]["policy_version"]
    ).verify(request)

    assert canonical_bytes(actual) == canonical_bytes(expected)
    assert canonical_sha256(actual) == REFERENCE_REPORT_SHA256
