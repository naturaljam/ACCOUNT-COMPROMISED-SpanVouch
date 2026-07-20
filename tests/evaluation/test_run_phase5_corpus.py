from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from spanvouch.evaluation import generate_phase5_labels as label_command
from spanvouch.evaluation import run_phase5_corpus
from spanvouch.evaluation.experiments import load_experiment_config
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
        lambda config, repository_root: ({"offline": object()}, object()),
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
