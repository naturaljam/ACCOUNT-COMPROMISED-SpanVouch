from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from spanvouch.evaluation import generate_phase5_labels as label_command
from spanvouch.evaluation import run_phase5_corpus
from spanvouch.evaluation.experiments import (
    FormalFreezePolicy,
    freeze_formal_config,
    load_experiment_config,
)
from spanvouch.labs.frameworks.autogen import AutoGenRuntimeAdapter
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.runtime import FrameworkId, ParityMismatch, ParityResult


def _mismatch() -> ParityResult:
    return ParityResult(
        status="mismatched",
        mismatches=(
            ParityMismatch(
                dimension="outcome",
                reference_sha256="a" * 64,
                candidate_sha256="b" * 64,
            ),
        ),
    )


def test_corpus_command_is_offline_and_returns_nonzero_for_parity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    async def fake_generate(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(
            repository=SimpleNamespace(manifest_sha256="c" * 64),
            manifest=SimpleNamespace(entries=(1, 2)),
            parity_results=(_mismatch(),),
            has_unapproved_parity_mismatches=True,
            logical_payload_sha256="d" * 64,
        )

    monkeypatch.setattr(run_phase5_corpus, "generate_phase5_corpus", fake_generate)
    monkeypatch.setattr(
        run_phase5_corpus,
        "build_stage_a_inputs",
        lambda config, repository_root, **kwargs: ({"offline": object()}, object()),
    )
    output = tmp_path / "corpus"

    status = run_phase5_corpus.main(
        (
            "--config",
            "evals/configs/phase5-pilot.json",
            "--output-dir",
            str(output),
        )
    )

    printed = capsys.readouterr().out
    assert status != 0
    assert captured["destination"] == output
    assert "provider" not in captured
    assert "allow_live" not in captured
    assert "c" * 64 in printed
    assert "d" * 64 in printed
    assert "GOLD_SENTINEL" not in printed


@pytest.mark.parametrize("flag", ("--labels", "--provider", "--allow-live-provider"))
def test_corpus_command_rejects_label_and_provider_flags(flag: str) -> None:
    with pytest.raises(SystemExit, match="2"):
        run_phase5_corpus.main(
            (
                "--config",
                "evals/configs/phase5-pilot.json",
                "--output-dir",
                "unused",
                flag,
                "forbidden",
            )
        )


def test_corpus_command_rejects_mode_config_mismatch(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        run_phase5_corpus.main(
            (
                "--mode",
                "formal",
                "--config",
                "evals/configs/phase5-pilot.json",
                "--output-dir",
                str(tmp_path / "corpus"),
            )
        )


def test_stage_a_inputs_bind_only_local_frameworks_and_inventory() -> None:
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))

    adapters, provenance = run_phase5_corpus.build_stage_a_inputs(
        config,
        Path.cwd(),
    )

    assert set(adapters) == {FrameworkId.LANGGRAPH, FrameworkId.AUTOGEN}
    assert isinstance(adapters[FrameworkId.LANGGRAPH], LangGraphRuntimeAdapter)
    assert isinstance(adapters[FrameworkId.AUTOGEN], AutoGenRuntimeAdapter)
    assert len(provenance.dataset_manifest_sha256) == 64
    assert provenance.tool_versions == {"opslab": "1.0", "supportlab": "1.0"}


def test_labels_command_prints_only_paths_and_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "sealed"
    fake = SimpleNamespace(
        output_dir=output,
        manifest=SimpleNamespace(corpus_manifest_sha256="a" * 64),
        manifest_sha256="b" * 64,
    )
    monkeypatch.setattr(label_command, "generate_phase5_labels", lambda **kwargs: fake)

    status = label_command.main(
        ("--corpus-dir", str(tmp_path / "corpus"), "--output-dir", str(output))
    )

    printed = json.loads(capsys.readouterr().out)
    assert status == 0
    assert printed == {
        "corpus_manifest_sha256": "a" * 64,
        "labels_manifest": str(output / "manifest.json"),
        "labels_manifest_sha256": "b" * 64,
    }


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _formal_config_bytes() -> bytes:
    pilot = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    policy = FormalFreezePolicy.model_validate_json(
        Path("evals/configs/phase5-formal-policy.json").read_text(encoding="utf-8")
    )
    formal = freeze_formal_config(
        pilot,
        policy,
        repetitions=policy.minimum_repetitions,
        coverage_loss_tolerance=0.05,
        frozen_at_utc=datetime(2026, 7, 19, tzinfo=UTC),
    )
    return formal.model_dump_json(indent=2).encode("utf-8") + b"\n"


@pytest.mark.parametrize(
    "source_state",
    ("external", "untracked", "working-tree-different", "index-different"),
)
def test_formal_command_rejects_non_committed_config_before_building_adapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_state: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "task9@example.invalid")
    _git(repository, "config", "user.name", "Task 9")
    tracked = repository / "formal.json"
    tracked.write_bytes(_formal_config_bytes())
    _git(repository, "add", "formal.json")
    _git(repository, "commit", "-m", "freeze formal config")

    config = tracked
    if source_state == "external":
        config = tmp_path / "external-formal.json"
        config.write_bytes(tracked.read_bytes())
    elif source_state == "untracked":
        config = repository / "untracked-formal.json"
        config.write_bytes(tracked.read_bytes())
    elif source_state == "working-tree-different":
        tracked.write_bytes(tracked.read_bytes() + b" \n")
    elif source_state == "index-different":
        committed = tracked.read_bytes()
        tracked.write_bytes(committed + b" \n")
        _git(repository, "add", "formal.json")
        tracked.write_bytes(committed)

    monkeypatch.chdir(repository)

    def adapters_must_not_be_built(*args: object, **kwargs: object) -> object:
        raise AssertionError("adapters were built before formal config verification")

    monkeypatch.setattr(run_phase5_corpus, "build_stage_a_inputs", adapters_must_not_be_built)

    with pytest.raises(ValueError, match="formal configuration"):
        run_phase5_corpus.main(
            (
                "--mode",
                "formal",
                "--config",
                str(config),
                "--output-dir",
                str(tmp_path / "corpus"),
            )
        )


def test_formal_command_accepts_a_tracked_byte_identical_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "task9@example.invalid")
    _git(repository, "config", "user.name", "Task 9")
    config = repository / "formal.json"
    config.write_bytes(_formal_config_bytes())
    _git(repository, "add", "formal.json")
    _git(repository, "commit", "-m", "freeze formal config")
    monkeypatch.chdir(repository)
    built: list[Path] = []

    def build(config: object, repository_root: Path, **kwargs: object) -> tuple[object, object]:
        built.append(repository_root)
        return ({"offline": object()}, object())

    async def generate(**kwargs: object) -> object:
        return SimpleNamespace(
            repository=SimpleNamespace(manifest_sha256="a" * 64),
            manifest=SimpleNamespace(entries=(1, 2)),
            parity_results=(),
            has_unapproved_parity_mismatches=False,
            logical_payload_sha256="b" * 64,
        )

    monkeypatch.setattr(run_phase5_corpus, "build_stage_a_inputs", build)
    monkeypatch.setattr(run_phase5_corpus, "generate_phase5_corpus", generate)

    assert run_phase5_corpus.main(
        (
            "--mode",
            "formal",
            "--config",
            str(config),
            "--output-dir",
            str(tmp_path / "corpus"),
        )
    ) == 0
    assert built == [repository.resolve()]
