from pathlib import Path

import pytest

from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    CodeProvenance,
    RuntimeProvenance,
)
from spanvouch.evaluation.provenance import manifest_path_for
from spanvouch.evaluation.run_review_eval import main


class FixedCollector:
    def __init__(self, *, dirty: bool) -> None:
        self._code = CodeProvenance(
            git_commit="a" * 40, repository_identity="test", dirty_worktree=dirty
        )

    def code(self) -> CodeProvenance:
        return self._code

    def runtime(self) -> RuntimeProvenance:
        return RuntimeProvenance(
            python="3.12.7",
            os="windows",
            architecture="amd64",
            dependency_lock_sha256="b" * 64,
        )


def test_evaluation_output_always_has_a_bound_manifest(tmp_path: Path) -> None:
    output = tmp_path / "review.json"

    assert main(["--output", str(output)], collector=FixedCollector(dirty=False)) == 0

    bundle = manifest_path_for(output).parent
    manifest = ArtifactManifest.model_validate_json(
        (bundle / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.provider_status == "not_used"
    assert manifest.usage is None
    assert any(ref.sha256 for ref in manifest.outputs if ref.path == "metrics.json")
    assert manifest.code.dirty_worktree is False
    assert (bundle / "metrics.json").read_bytes() == output.read_bytes()


def test_dirty_artifact_is_exploratory_but_not_release_evidence(tmp_path: Path) -> None:
    output = tmp_path / "review.json"

    assert (
        main(
            ["--output", str(output), "--allow-dirty-artifact"],
            collector=FixedCollector(dirty=True),
        )
        == 0
    )

    manifest = ArtifactManifest.model_validate_json(
        manifest_path_for(output).read_text(encoding="utf-8")
    )
    assert manifest.code.dirty_worktree is True
    with pytest.raises(ValueError, match="clean worktree"):
        manifest.require_release_evidence()


def test_bundle_failure_leaves_no_new_report_or_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "review.json"

    def fail_bundle(**_: object) -> Path:
        raise ValueError("bundle failure")

    monkeypatch.setattr("spanvouch.evaluation.provenance.write_bound_bundle", fail_bundle)

    with pytest.raises(SystemExit):
        main(["--output", str(output)], collector=FixedCollector(dirty=False))

    assert not output.exists()
    assert not manifest_path_for(output).parent.exists()
