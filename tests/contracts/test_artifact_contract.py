from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    ArtifactRef,
    CodeProvenance,
    CostProvenance,
    ModelProvenance,
    PackageProvenance,
    RuntimeProvenance,
    UsageProvenance,
)
from spanvouch.contracts.versioning import canonical_bytes

ROOT = Path(__file__).resolve().parents[2]


def _manifest(*, dirty: bool = False) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id="phase4-offline-reference",
        artifact_kind="evaluation_bundle",
        created_at_utc=datetime(2026, 7, 18, tzinfo=UTC),
        command_name="spanvouch evaluate review",
        code=CodeProvenance(
            git_commit="a" * 40,
            repository_identity="local:self-agent",
            dirty_worktree=dirty,
        ),
        package=PackageProvenance(name="spanvouch", version="0.2.0"),
        contracts={"spanvouch.verification": "1.0"},
        configuration=ArtifactRef(
            path="config.json", sha256="b" * 64, media_type="application/json"
        ),
        runtime=RuntimeProvenance(
            python="3.12.10",
            os="windows",
            architecture="amd64",
            dependency_lock_sha256="c" * 64,
        ),
        outputs=(
            ArtifactRef(
                path="metrics.json", sha256="d" * 64, media_type="application/json"
            ),
        ),
        provider_status="not_used",
    )


def test_manifest_has_stable_contract_identity() -> None:
    manifest = _manifest()
    assert manifest.schema_name == "spanvouch.artifact-manifest"
    assert manifest.schema_version == "1.0"


def test_release_evidence_rejects_dirty_worktree() -> None:
    with pytest.raises(ValueError, match="release evidence requires a clean worktree"):
        _manifest(dirty=True).require_release_evidence()


def test_not_used_provider_forbids_usage_or_models() -> None:
    payload = _manifest().model_dump(mode="python")
    payload["usage"] = {
        "requests": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
    }
    with pytest.raises(ValueError, match="not_used provider forbids model usage"):
        ArtifactManifest.model_validate(payload)


def test_manifest_requires_utc_and_sorted_unique_reference_identities() -> None:
    payload = _manifest().model_dump(mode="python")
    payload["created_at_utc"] = datetime(2026, 7, 18)
    with pytest.raises(ValueError, match="created_at_utc must be UTC"):
        ArtifactManifest.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    (
        "/absolute.json",
        "../escape.json",
        "folder\\file.json",
        "C:/drive-path.json",
        "//server/share.json",
        "urn:artifact:one",
    ),
)
def test_artifact_reference_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="relative POSIX path"):
        ArtifactRef(path=path, sha256="a" * 64, media_type="application/json")

    payload = _manifest().model_dump(mode="python")
    payload["inputs"] = (
        ArtifactRef(path="z.json", sha256="e" * 64, media_type="application/json"),
        ArtifactRef(path="a.json", sha256="f" * 64, media_type="application/json"),
    )
    with pytest.raises(ValueError, match="inputs must be sorted and unique"):
        ArtifactManifest.model_validate(payload)


def test_used_provider_requires_models_usage_and_consistent_totals() -> None:
    payload = _manifest().model_dump(mode="python")
    payload["provider_status"] = "used"
    with pytest.raises(ValueError, match="used provider requires models and usage"):
        ArtifactManifest.model_validate(payload)

    with pytest.raises(ValueError, match="total_tokens must equal"):
        UsageProvenance(requests=1, input_tokens=2, output_tokens=3, total_tokens=4)

    used_payload = _manifest().model_dump(mode="python")
    used_payload.update(
        provider_status="used",
        models=(
            ModelProvenance(
                provider="deepseek",
                model="deepseek-chat",
                endpoint_class="chat.completions",
                generation_config_sha256="e" * 64,
                prompt_sha256="f" * 64,
            ),
        ),
        usage=UsageProvenance(requests=1, input_tokens=2, output_tokens=3, total_tokens=5),
    )
    manifest = ArtifactManifest.model_validate(used_payload)
    assert manifest.provider_status == "used"


def test_missing_pricing_has_no_fabricated_cost_amount() -> None:
    cost = CostProvenance(currency="USD", basis="estimated")
    assert cost.amount is None
    with pytest.raises(ValueError, match="amount requires a pricing_ref"):
        CostProvenance(currency="USD", basis="estimated", amount=0.0)


def test_checked_in_schema_and_fixture_match_contract() -> None:
    schema = json.dumps(
        ArtifactManifest.model_json_schema(), ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    assert (
        ROOT / "schemas/v1/spanvouch.artifact-manifest-1.0.schema.json"
    ).read_bytes() == schema
    assert (
        ROOT / "tests/contracts/fixtures/v1/artifact-manifest.valid.json"
    ).read_bytes() == canonical_bytes(_manifest()) + b"\n"
