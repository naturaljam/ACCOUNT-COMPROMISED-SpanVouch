from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

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
