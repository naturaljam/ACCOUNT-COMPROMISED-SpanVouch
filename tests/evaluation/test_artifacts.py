from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import spanvouch.evaluation.artifacts as artifacts_module
from spanvouch.contracts.artifacts import ArtifactRef, ModelProvenance, UsageProvenance
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.artifacts import ArtifactBundleWriter, collect_git_provenance


def test_bundle_writer_hashes_every_required_file(
    tmp_path: Path, artifact_manifest: object
) -> None:
    bundle = tmp_path / "bundle"
    writer = ArtifactBundleWriter(bundle)
    written = writer.write(
        manifest=artifact_manifest,
        config={"mode": "deterministic"},
        metrics={"status": "complete"},
        structured_events=(),
        environment="python=3.12\n",
        readme="# Reproduce\n",
    )
    assert set(path.name for path in written) == {
        "manifest.json",
        "config.json",
        "metrics.json",
        "structured-events.jsonl",
        "environment.txt",
        "README.md",
    }
    assert canonical_sha256({"mode": "deterministic"}) == next(
        ref.sha256 for ref in artifact_manifest.inputs if ref.path == "config.json"
    )


def test_bundle_writer_rejects_hash_mismatch_and_cleans_temporary_directory(
    tmp_path: Path, artifact_manifest: object
) -> None:
    bad_manifest = artifact_manifest.model_copy(
        update={
            "configuration": artifact_manifest.configuration.model_copy(
                update={"sha256": "a" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="artifact SHA-256 mismatch"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=bad_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12\n",
            readme="# Reproduce\n",
        )
    assert not list(tmp_path.iterdir())


def test_bundle_writer_refuses_to_overwrite_destination(
    tmp_path: Path, artifact_manifest: object
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(FileExistsError, match="destination already exists"):
        ArtifactBundleWriter(bundle).write(
            manifest=artifact_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12\n",
            readme="# Reproduce\n",
        )


def test_bundle_writer_requires_declared_refs_to_cover_exact_generated_files(
    tmp_path: Path, artifact_manifest: object
) -> None:
    incomplete_manifest = artifact_manifest.model_copy(
        update={"outputs": artifact_manifest.outputs[:-1]}
    )
    with pytest.raises(ValueError, match="declared refs"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=incomplete_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12\n",
            readme="# Reproduce\n",
        )


def test_bundle_writer_rejects_undeclared_external_input(
    tmp_path: Path, artifact_manifest: object
) -> None:
    external_ref = ArtifactRef(
        path="outside.json", sha256="a" * 64, media_type="application/json"
    )
    invalid_manifest = artifact_manifest.model_copy(
        update={"inputs": (artifact_manifest.inputs[0], external_ref)}
    )
    with pytest.raises(ValueError, match="declared refs"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=invalid_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12\n",
            readme="# Reproduce\n",
        )


def test_collect_git_provenance_records_non_secret_local_identity() -> None:
    provenance = collect_git_provenance(Path.cwd())
    assert len(provenance.git_commit) == 40
    assert provenance.repository_identity == "local:phase4-integration"


def test_bundle_writer_permits_provenance_hashes_but_rejects_raw_secrets(
    tmp_path: Path, artifact_manifest: object
) -> None:
    used_manifest = artifact_manifest.model_copy(
        update={
            "provider_status": "used",
            "models": (
                ModelProvenance(
                    provider="deepseek",
                    model="deepseek-chat",
                    endpoint_class="chat.completions",
                    generation_config_sha256="a" * 64,
                    prompt_sha256="b" * 64,
                ),
            ),
            "usage": UsageProvenance(
                requests=1, input_tokens=2, output_tokens=3, total_tokens=5
            ),
        }
    )
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=used_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="token=sk-12345678",
            readme="# Reproduce\n",
        )


@pytest.mark.parametrize(
    ("structured_events", "environment"),
    (
        (({"provider_body": "private response"},), "python=3.12"),
        ((), "DEEPSEEK_API_KEY=artifact-secret-sentinel"),
    ),
)
def test_bundle_writer_rejects_provider_bodies_and_environment_values(
    tmp_path: Path,
    artifact_manifest: object,
    structured_events: tuple[object, ...],
    environment: str,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=structured_events,
            environment=environment,
            readme="# Reproduce\n",
        )


@pytest.mark.parametrize(
    ("config", "metrics", "structured_events", "environment"),
    (
        ({"system_prompt": "do not persist me"}, {"status": "complete"}, (), "python=3.12"),
        ({"mode": "deterministic"}, {"response_raw": "provider body"}, (), "python=3.12"),
        (
            {"mode": "deterministic"},
            {"status": "complete"},
            ({"headers": {"Authorization": "Bearer private"}},),
            "python=3.12",
        ),
        ({"mode": "deterministic"}, {"token": "private"}, (), "python=3.12"),
        ({"mode": "deterministic"}, {"password": "private"}, (), "python=3.12"),
        ({"mode": "deterministic"}, {"hidden reasoning": "private"}, (), "python=3.12"),
        (
            {"mode": "deterministic"},
            {"status": "complete"},
            (),
            "AWS_SECRET_ACCESS_KEY=private",
        ),
    ),
)
def test_bundle_writer_rejects_sensitive_structured_content_before_hashing(
    tmp_path: Path,
    artifact_manifest: object,
    config: object,
    metrics: object,
    structured_events: tuple[object, ...],
    environment: str,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content") as raised:
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config=config,
            metrics=metrics,
            structured_events=structured_events,
            environment=environment,
            readme="# Reproduce\n",
        )
    assert "private" not in str(raised.value)


def test_bundle_writer_rejects_unknown_config_keys_before_hashing(
    tmp_path: Path, artifact_manifest: object
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config={"unreviewed_nested_option": {"value": True}},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12",
            readme="# Reproduce\n",
        )


def test_bundle_writer_accepts_the_task15_reference_config(
    tmp_path: Path, artifact_manifest: object
) -> None:
    reference_config = {
        "schema_version": "1.0",
        "dataset": "evals/datasets/supportlab-review-v1",
        "source_dataset": "evals/datasets/supportlab-v1",
        "verifier": "deterministic",
        "policy_version": "supportlab-review-policy-v1",
        "seed": 20260717,
        "allow_live_api": False,
    }
    reference = ArtifactRef(
        path="config.json",
        sha256=canonical_sha256(reference_config),
        media_type="application/json",
    )
    manifest = artifact_manifest.model_copy(
        update={"configuration": reference, "inputs": (reference,)}
    )
    written = ArtifactBundleWriter(tmp_path / "bundle").write(
        manifest=manifest,
        config=reference_config,
        metrics={"status": "complete"},
        structured_events=(),
        environment="python=3.12",
        readme="# Reproduce\n",
    )
    assert (tmp_path / "bundle" / "config.json") in written


@pytest.mark.parametrize(
    ("config", "environment", "readme"),
    (
        ([], "python=3.12", "# Reproduce"),
        ({"mode": ""}, "python=3.12", "# Reproduce"),
        ({"seed": True}, "python=3.12", "# Reproduce"),
        ({"allow_live_api": "false"}, "python=3.12", "# Reproduce"),
        ({"mode": "deterministic"}, "", "# Reproduce"),
        ({"mode": "deterministic"}, "python=3.12", "Bearer private"),
    ),
)
def test_bundle_writer_rejects_invalid_safe_content_shapes(
    tmp_path: Path,
    artifact_manifest: object,
    config: object,
    environment: str,
    readme: str,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config=config,
            metrics={"status": "complete"},
            structured_events=(),
            environment=environment,
            readme=readme,
        )


def test_bundle_writer_publishes_exactly_once_under_concurrent_writers(
    tmp_path: Path, artifact_manifest: object
) -> None:
    destination = tmp_path / "bundle"

    def write_once() -> str:
        try:
            ArtifactBundleWriter(destination).write(
                manifest=artifact_manifest,
                config={"mode": "deterministic"},
                metrics={"status": "complete"},
                structured_events=(),
                environment="python=3.12",
                readme="# Reproduce\n",
            )
        except FileExistsError:
            return "exists"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: write_once(), range(2)))
    assert sorted(results) == ["exists", "published"]
    assert {path.name for path in destination.iterdir()} == {
        "manifest.json",
        "config.json",
        "metrics.json",
        "structured-events.jsonl",
        "environment.txt",
        "README.md",
    }


def test_publish_fails_closed_when_atomic_no_replace_is_unsupported(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(RuntimeError, match="atomic no-replace"):
        artifacts_module._publish_no_replace(source, tmp_path / "destination", platform="other")
