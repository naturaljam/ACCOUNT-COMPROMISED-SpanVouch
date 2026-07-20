import os
import shutil
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import spanvouch.evaluation.provenance as provenance_module
from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    CodeProvenance,
    RuntimeProvenance,
)
from spanvouch.evaluation.provenance import (
    LocalProvenanceCollector,
    dataset_provenance,
    manifest_path_for,
    publish_dataset_and_bundle,
    publish_report_and_bundle,
    write_bound_bundle,
)
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


def _create_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks are unavailable")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        pytest.skip("directory reparse points are unavailable")


def _remove_directory_link(link: Path) -> None:
    if link.is_symlink():
        link.unlink()
    else:
        os.rmdir(link)


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


def test_direct_bundle_refuses_dirty_release_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="clean worktree"):
        write_bound_bundle(
            output=tmp_path / "report.json",
            report={"status": "complete"},
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=True),
        )


def test_publish_removes_just_published_bundle_when_report_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    bundle = tmp_path / "report.json.bundle"

    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish

    def fail_report(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", fail_report)

    with pytest.raises(OSError, match="replace failure"):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert not output.exists()
    assert not bundle.exists()
    assert not tuple(tmp_path.glob(".*.rollback-*"))


def test_owned_quarantine_deletion_never_calls_path_rmtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    real_rmtree = shutil.rmtree
    rollback_rmtree_called = False
    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish

    def fail_primary(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)

    def reject_quarantine_rmtree(path: object, *args: object, **kwargs: object) -> None:
        nonlocal rollback_rmtree_called
        candidate = Path(path)
        if ".rollback-" in candidate.name:
            rollback_rmtree_called = True
            real_rmtree(candidate)
            candidate.mkdir()
            (candidate / "foreign-evidence.txt").write_bytes(b"must survive\n")
            real_rmtree(candidate)
            return
        real_rmtree(candidate, *args, **kwargs)

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", fail_primary)
    monkeypatch.setattr("spanvouch.evaluation.provenance.shutil.rmtree", reject_quarantine_rmtree)

    with pytest.raises(OSError, match="replace failure"):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert rollback_rmtree_called is False
    assert not manifest_path_for(output).parent.exists()
    assert not tuple(tmp_path.glob(".*.rollback-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-sharing probe")
def test_windows_pinned_delete_blocks_quarantine_replacement_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    foreign = tmp_path / "foreign-evidence"
    foreign.mkdir()
    marker = foreign / "marker.txt"
    marker.write_bytes(b"foreign survives\n")
    blocked = False
    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish
    from spanvouch.evaluation.provenance import _pin_windows_tree as real_pin

    def fail_primary(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)

    def pin_then_attack(path: Path, kernel32: object) -> object:
        nonlocal blocked
        root = real_pin(path, kernel32)
        try:
            shutil.rmtree(path)
        except OSError:
            blocked = True
        else:
            path.mkdir()
            (path / "foreign-evidence.txt").write_bytes(b"must survive\n")
        return root

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", fail_primary)
    monkeypatch.setattr(
        "spanvouch.evaluation.provenance._pin_windows_tree", pin_then_attack
    )

    with pytest.raises(OSError, match="replace failure"):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert blocked is True
    assert marker.read_bytes() == b"foreign survives\n"
    assert not tuple(tmp_path.glob(".*.rollback-*"))


def test_child_mutation_before_pinned_delete_is_restored_not_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    bundle = manifest_path_for(output).parent
    mutated = b'{"foreign_mutation":true}\n'
    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish
    from spanvouch.evaluation.provenance import _delete_owned_quarantine as real_delete

    def fail_primary(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)

    def mutate_then_delete(
        quarantine: Path, owner: object, *, platform: str | None = None
    ) -> bool:
        (quarantine / "metrics.json").write_bytes(mutated)
        return real_delete(quarantine, owner, platform=platform)

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", fail_primary)
    monkeypatch.setattr(
        "spanvouch.evaluation.provenance._delete_owned_quarantine", mutate_then_delete
    )

    with pytest.raises(OSError, match="replace failure"):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert (bundle / "metrics.json").read_bytes() == mutated
    assert not tuple(tmp_path.glob(".*.rollback-*"))


def test_identical_byte_child_replacement_mismatches_native_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    bundle = manifest_path_for(output).parent
    replacement = tmp_path / "replacement-config.json"
    replacement_inode = 0
    expected_bytes = b""
    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish
    from spanvouch.evaluation.provenance import (
        _capture_published_bundle_identity as real_capture,
    )

    def fail_primary(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)

    def capture_then_replace_child(destination: Path, identity: object) -> object:
        nonlocal expected_bytes, replacement_inode
        owner = real_capture(destination, identity)
        target = bundle / "config.json"
        expected_bytes = target.read_bytes()
        replacement.write_bytes(expected_bytes)
        replacement_inode = replacement.stat().st_ino
        assert replacement_inode != target.stat().st_ino
        target.unlink()
        replacement.rename(target)
        return owner

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", fail_primary)
    monkeypatch.setattr(
        "spanvouch.evaluation.provenance._capture_published_bundle_identity",
        capture_then_replace_child,
    )

    with pytest.raises(OSError, match="replace failure"):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert (bundle / "config.json").read_bytes() == expected_bytes
    assert (bundle / "config.json").stat().st_ino == replacement_inode
    assert not tuple(tmp_path.glob(".*.rollback-*"))


def test_posix_unsupported_rollback_preserves_recovery_evidence_without_delete_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json.bundle"
    destination.mkdir()
    evidence = destination / "foreign-evidence.bin"
    evidence.write_bytes(b"preserve exactly\x00\x01")
    from spanvouch.evaluation.provenance import (
        _PublishedBundleIdentity,
        _rollback_published_bundle,
        _tree_fingerprint,
    )

    metadata = destination.stat()
    owner = _PublishedBundleIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        tree_fingerprint=_tree_fingerprint(destination),
    )

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("unsupported POSIX rollback must not delete")

    monkeypatch.setattr("spanvouch.evaluation.provenance.os.unlink", forbidden)
    monkeypatch.setattr("spanvouch.evaluation.provenance.os.rmdir", forbidden)
    monkeypatch.setattr("spanvouch.evaluation.provenance.shutil.rmtree", forbidden)

    with pytest.raises(RuntimeError, match="^artifact rollback cleanup conflict$"):
        _rollback_published_bundle(destination, owner, platform="linux")

    assert not destination.exists()
    recoveries = tuple(tmp_path.glob(".*.rollback-*"))
    assert len(recoveries) == 1
    assert (recoveries[0] / evidence.name).read_bytes() == b"preserve exactly\x00\x01"


def test_rollback_restores_foreign_bundle_replacement_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    bundle = manifest_path_for(output).parent
    foreign = {"foreign.txt": b"foreign evidence\n", "nested/item.bin": b"\x00\x01foreign"}
    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish
    from spanvouch.evaluation.provenance import (
        _capture_published_bundle_identity as real_capture,
    )

    def fail_primary(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)

    def capture_then_substitute(destination: Path, identity: object) -> object:
        owner = real_capture(destination, identity)
        shutil.rmtree(bundle)
        for relative, content in foreign.items():
            target = bundle / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return owner

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", fail_primary)
    monkeypatch.setattr(
        "spanvouch.evaluation.provenance._capture_published_bundle_identity",
        capture_then_substitute,
    )

    with pytest.raises(OSError, match="replace failure"):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert not output.exists()
    assert {relative: (bundle / relative).read_bytes() for relative in foreign} == foreign
    assert not tuple(tmp_path.glob(".*.rollback-*"))
    assert not tuple(tmp_path.glob(".*.tmp-*"))


def test_rollback_restores_foreign_non_directory_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    bundle = manifest_path_for(output).parent
    foreign = b"foreign non-directory evidence\n"
    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish
    from spanvouch.evaluation.provenance import (
        _capture_published_bundle_identity as real_capture,
    )

    def fail_primary(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)

    def capture_then_substitute(destination: Path, identity: object) -> object:
        owner = real_capture(destination, identity)
        shutil.rmtree(bundle)
        bundle.write_bytes(foreign)
        return owner

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", fail_primary)
    monkeypatch.setattr(
        "spanvouch.evaluation.provenance._capture_published_bundle_identity",
        capture_then_substitute,
    )

    with pytest.raises(OSError, match="replace failure"):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert bundle.read_bytes() == foreign
    assert not tuple(tmp_path.glob(".*.rollback-*"))
    assert not tuple(tmp_path.glob(".*.tmp-*"))


def test_rollback_preserves_both_foreign_trees_when_destination_is_reoccupied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    bundle = manifest_path_for(output).parent
    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish
    from spanvouch.evaluation.provenance import (
        _capture_published_bundle_identity as real_capture,
    )

    def race(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)
        if source == bundle and ".rollback-" in destination.name:
            bundle.mkdir()
            (bundle / "foreign-two.txt").write_bytes(b"foreign two\n")

    def capture_then_substitute(destination: Path, identity: object) -> object:
        owner = real_capture(destination, identity)
        shutil.rmtree(bundle)
        bundle.mkdir()
        (bundle / "foreign-one.txt").write_bytes(b"foreign one\n")
        return owner

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", race)
    monkeypatch.setattr(
        "spanvouch.evaluation.provenance._capture_published_bundle_identity",
        capture_then_substitute,
    )

    with pytest.raises(RuntimeError, match="artifact rollback cleanup conflict") as raised:
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert "foreign" not in str(raised.value)
    assert (bundle / "foreign-two.txt").read_bytes() == b"foreign two\n"
    quarantines = tuple(tmp_path.glob(".*.rollback-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "foreign-one.txt").read_bytes() == b"foreign one\n"


def test_rollback_restores_foreign_symlink_or_reparse_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    bundle = manifest_path_for(output).parent
    target = tmp_path / "foreign-target"
    target.mkdir()
    marker = target / "evidence.txt"
    marker.write_bytes(b"do not delete\n")
    probe = tmp_path / "symlink-probe"
    _create_directory_link(probe, target)
    _remove_directory_link(probe)
    from spanvouch.evaluation.artifacts import _publish_no_replace as real_publish
    from spanvouch.evaluation.provenance import (
        _capture_published_bundle_identity as real_capture,
    )

    def fail_primary(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("replace failure")
        real_publish(source, destination)

    def capture_then_substitute(destination: Path, identity: object) -> object:
        owner = real_capture(destination, identity)
        shutil.rmtree(bundle)
        _create_directory_link(bundle, target)
        return owner

    monkeypatch.setattr("spanvouch.evaluation.provenance._publish_no_replace", fail_primary)
    monkeypatch.setattr(
        "spanvouch.evaluation.provenance._capture_published_bundle_identity",
        capture_then_substitute,
    )

    with pytest.raises(OSError, match="replace failure"):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_bytes(b'{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert bundle.resolve() == target.resolve()
    assert marker.read_bytes() == b"do not delete\n"
    assert not tuple(tmp_path.glob(".*.rollback-*"))


def test_dataset_and_local_provenance_collect_only_declared_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text("{}\n")
    (dataset / "z.jsonl").write_text("z\n")
    (dataset / "a.jsonl").write_text("a\n")
    provenance = dataset_provenance(
        dataset, dataset_id="fixture", payloads=("z.jsonl", "a.jsonl")
    )
    assert tuple(item.path for item in provenance.payloads) == ("a.jsonl", "z.jsonl")

    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "uv.lock").write_text("lock\n")
    expected = CodeProvenance(
        git_commit="c" * 40, repository_identity="test", dirty_worktree=False
    )
    monkeypatch.setattr(
        "spanvouch.evaluation.provenance.collect_git_provenance", lambda _: expected
    )
    collector = LocalProvenanceCollector(repository)
    assert collector.code() == expected
    assert collector.runtime().dependency_lock_sha256


def test_dataset_generator_commands_publish_bound_bundles(tmp_path: Path) -> None:
    from spanvouch.evaluation.generate_dataset import main as generate_dataset_main
    from spanvouch.evaluation.generate_review_dataset import main as generate_review_main

    source = tmp_path / "supportlab"
    assert generate_dataset_main(
        ["--output", str(source), "--seed", "7"], collector=FixedCollector(dirty=False)
    ) == 0
    assert manifest_path_for(source).is_file()
    assert (source / "manifest.json").read_bytes() == (
        manifest_path_for(source).parent / "metrics.json"
    ).read_bytes()

    review = tmp_path / "review"
    assert generate_review_main(
        [
            "--output",
            str(review),
            "--source-dataset-dir",
            "evals/datasets/supportlab-v1",
        ],
        collector=FixedCollector(dirty=False),
    ) == 0
    assert manifest_path_for(review).is_file()
    assert (review / "manifest.json").read_bytes() == (
        manifest_path_for(review).parent / "metrics.json"
    ).read_bytes()


def test_report_publication_never_overwrites_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    original = b'{"old":true}\n'
    output.write_bytes(original)

    with pytest.raises(FileExistsError):
        publish_report_and_bundle(
            output=output,
            render_report=lambda path: path.write_text('{"status":"complete"}\n'),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch evaluate review",
            artifact_kind="evaluation_bundle",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert output.read_bytes() == original
    assert not manifest_path_for(output).parent.exists()
    assert not tuple(tmp_path.glob(".*.tmp-*"))


def test_dataset_pair_never_overwrites_existing_bundle(tmp_path: Path) -> None:
    output = tmp_path / "dataset"
    bundle = manifest_path_for(output).parent
    bundle.mkdir()
    marker = bundle / "keep.txt"
    marker.write_bytes(b"old bundle\n")

    def build(staged: Path) -> object:
        staged.mkdir()
        (staged / "payload.jsonl").write_bytes(b"{}\n")
        return {"status": "complete"}

    with pytest.raises(FileExistsError):
        publish_dataset_and_bundle(
            output=output,
            build_dataset=build,
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch dataset generate",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert not output.exists()
    assert marker.read_bytes() == b"old bundle\n"
    assert not tuple(tmp_path.glob(".*.tmp-*"))


def test_dataset_pair_race_has_one_complete_winner(tmp_path: Path) -> None:
    output = tmp_path / "dataset"

    def publish(index: int) -> str:
        def build(staged: Path) -> object:
            staged.mkdir()
            (staged / "payload.jsonl").write_bytes(f"{index}\n".encode())
            return {"index": index, "status": "complete"}

        try:
            publish_dataset_and_bundle(
                output=output,
                build_dataset=build,
                config={"schema_version": "1.0", "seed": index, "allow_live_api": False},
                command_name="spanvouch dataset generate",
                seed=index,
                collector=FixedCollector(dirty=False),
            )
        except FileExistsError:
            return "lost"
        return "won"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(publish, (1, 2)))

    assert sorted(results) == ["lost", "won"]
    winner = int((output / "payload.jsonl").read_text(encoding="utf-8"))
    metrics = (manifest_path_for(output).parent / "metrics.json").read_text(encoding="utf-8")
    assert f'"index":{winner}' in metrics
    assert not tuple(tmp_path.glob(".*.tmp-*"))


def test_report_pair_race_has_one_winner_and_never_overwrites(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    def publish(index: int) -> str:
        try:
            publish_report_and_bundle(
                output=output,
                render_report=lambda path: path.write_bytes(
                    f'{{"index":{index},"status":"complete"}}\n'.encode()
                ),
                config={"schema_version": "1.0", "seed": index, "allow_live_api": False},
                command_name="spanvouch evaluate review",
                artifact_kind="evaluation_bundle",
                seed=index,
                collector=FixedCollector(dirty=False),
            )
        except FileExistsError:
            return "lost"
        return "won"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(publish, (1, 2)))

    assert sorted(results) == ["lost", "won"]
    assert output.read_bytes() == (manifest_path_for(output).parent / "metrics.json").read_bytes()
    assert not tuple(tmp_path.glob(".*.tmp-*"))


def test_dataset_pair_rolls_back_bundle_when_output_already_exists(tmp_path: Path) -> None:
    output = tmp_path / "dataset"
    output.mkdir()
    (output / "keep.txt").write_text("old\n")

    with pytest.raises(FileExistsError):
        publish_dataset_and_bundle(
            output=output,
            build_dataset=lambda staged: (
                (staged.mkdir(), {"status": "complete"})[1]
            ),
            config={"schema_version": "1.0", "seed": 1, "allow_live_api": False},
            command_name="spanvouch dataset generate",
            seed=1,
            collector=FixedCollector(dirty=False),
        )

    assert (output / "keep.txt").read_text() == "old\n"
    assert not manifest_path_for(output).parent.exists()
    assert not tuple(tmp_path.glob(".*.tmp-*"))


def test_portable_bundle_identity_covers_nested_content_and_detects_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    nested = bundle / "nested"
    nested.mkdir(parents=True)
    (nested / "payload.json").write_text("{}\n", encoding="utf-8")

    identity = provenance_module._snapshot_portable_bundle_identity(bundle)
    assert identity.tree_fingerprint == provenance_module._tree_fingerprint(bundle)
    monkeypatch.setattr(
        provenance_module, "_snapshot_windows_bundle_identity", lambda _path: identity
    )
    assert provenance_module._capture_published_bundle_identity(bundle, identity) == identity

    (nested / "payload.json").write_text('{"changed":true}\n', encoding="utf-8")
    changed = provenance_module._snapshot_portable_bundle_identity(bundle)
    monkeypatch.setattr(
        provenance_module, "_snapshot_windows_bundle_identity", lambda _path: changed
    )
    with pytest.raises(RuntimeError, match="ownership verification"):
        provenance_module._capture_published_bundle_identity(bundle, identity)


def test_portable_identity_and_fingerprint_reject_unsupported_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    regular_file = tmp_path / "file"
    regular_file.write_text("payload", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not an owned directory"):
        provenance_module._directory_identity(regular_file)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    child = bundle / "payload"
    child.write_text("payload", encoding="utf-8")
    monkeypatch.setattr(
        provenance_module,
        "_is_reparse_point",
        lambda metadata: stat.S_ISREG(metadata.st_mode),
    )
    with pytest.raises(RuntimeError, match="unsupported filesystem entry"):
        provenance_module._tree_fingerprint(bundle)


@pytest.mark.parametrize(
    "signatures",
    (
        ((1,), (2,)),
        ((1,), (1,), (2,), (3,)),
    ),
)
def test_portable_fingerprint_detects_file_and_directory_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signatures: tuple[tuple[int, ...], ...],
) -> None:
    bundle = tmp_path / f"bundle-{len(signatures)}"
    bundle.mkdir()
    (bundle / "payload").write_text("payload", encoding="utf-8")
    values = iter(signatures)
    monkeypatch.setattr(provenance_module, "_stat_signature", lambda _metadata: next(values))
    with pytest.raises(RuntimeError, match="changed while fingerprinting"):
        provenance_module._tree_fingerprint(bundle)
