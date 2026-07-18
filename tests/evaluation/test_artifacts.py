from __future__ import annotations

from pathlib import Path

import pytest

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
    with pytest.raises(ValueError, match="sensitive value"):
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
    with pytest.raises(ValueError, match="sensitive"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=structured_events,
            environment=environment,
            readme="# Reproduce\n",
        )
