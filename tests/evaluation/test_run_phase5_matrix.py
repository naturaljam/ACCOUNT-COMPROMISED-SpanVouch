import asyncio
from pathlib import Path

import pytest

from spanvouch.contracts.versioning import canonical_bytes, canonical_sha256
from spanvouch.evaluation import evaluate_phase5_matrix, run_phase5_matrix
from spanvouch.evaluation.corpus import TraceReplayRepository
from spanvouch.evaluation.corpus.labels import GoldLabel, GoldLabelManifest
from spanvouch.evaluation.experiments.runner import ProviderPhaseRepository
from tests.evaluation.experiments.test_planner import _candidate_pair


def test_run_cli_has_no_label_argument_and_supports_live_formal_flags() -> None:
    parser = run_phase5_matrix.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert {"--config", "--corpus-dir", "--candidate-dir", "--output-dir"} <= option_strings
    assert {"--allow-live-provider", "--formal-run"} <= option_strings
    assert all("label" not in option for option in option_strings)


def test_evaluate_cli_has_only_offline_join_arguments() -> None:
    parser = evaluate_phase5_matrix.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert {"--provider-results", "--sealed-labels", "--output-dir"} <= option_strings
    for forbidden in ("endpoint", "api-key", "allow-live", "formal-run"):
        assert all(forbidden not in option for option in option_strings)


def test_cli_modules_accept_injected_offline_commands() -> None:
    called: list[object] = []
    assert run_phase5_matrix.main(
        ["--config", "config.json", "--corpus-dir", "corpus",
         "--candidate-dir", "candidates", "--output-dir", "out"],
        command=lambda request: called.append(request),
    ) == 0
    assert evaluate_phase5_matrix.main(
        ["--provider-results", "provider", "--sealed-labels", "labels",
         "--output-dir", "evaluated"],
        command=lambda request: called.append(request),
    ) == 0
    assert len(called) == 2


def _identity(label_cell: object) -> str:
    from spanvouch.evaluation.corpus import CorpusCell

    cell = CorpusCell.model_validate(label_cell)
    return ":".join(
        (
            cell.domain,
            cell.template_id,
            cell.scenario_id,
            cell.framework_id.value,
            str(cell.repetition),
            str(cell.seed),
        )
    )


def test_default_clis_run_verified_cache_only_matrix_and_offline_join(
    tmp_path: Path,
) -> None:
    asyncio.run(_candidate_pair(tmp_path))
    provider_dir = tmp_path / "provider"
    assert run_phase5_matrix.main(
        [
            "--config", "evals/configs/phase5-pilot.json",
            "--corpus-dir", str(tmp_path / "corpus"),
            "--candidate-dir", str(tmp_path / "candidates"),
            "--output-dir", str(provider_dir),
        ]
    ) == 0
    provider = ProviderPhaseRepository(provider_dir)
    provider_manifest = provider.verify(
        expected_manifest_sha256=provider.manifest_sha256
    )
    assert provider_manifest.provider_phase_complete
    assert len(provider_manifest.entries) == 12

    corpus = TraceReplayRepository(tmp_path / "corpus")
    corpus_manifest = corpus.verify()
    labels = tuple(
        GoldLabel(
            cell_identity=_identity(entry.cell),
            scenario_id=entry.cell.scenario_id,
            expected_failure_type="no_failure",
            causal_chain_expectations=(),
            evidence_expectations=(),
            control=True,
            split="pilot",
            record_sha256=entry.record_sha256,
            trace_sha256=entry.trace_sha256,
        )
        for entry in corpus_manifest.entries
    )
    sealed = GoldLabelManifest(
        corpus_manifest_sha256=corpus.manifest_sha256,
        labels=labels,
        labels_sha256=canonical_sha256(
            [label.model_dump(mode="json") for label in labels]
        ),
    )
    label_dir = tmp_path / "labels"
    label_dir.mkdir()
    (label_dir / "manifest.json").write_bytes(canonical_bytes(sealed))
    assert evaluate_phase5_matrix.main(
        [
            "--provider-results", str(provider_dir),
            "--sealed-labels", str(label_dir),
            "--output-dir", str(tmp_path / "evaluated"),
        ]
    ) == 0


def test_default_run_rejects_tampered_candidate_parent(tmp_path: Path) -> None:
    asyncio.run(_candidate_pair(tmp_path))
    candidate = next((tmp_path / "candidates/cells").rglob("*.json"))
    candidate.write_bytes(candidate.read_bytes() + b" ")
    with pytest.raises(ValueError):
        run_phase5_matrix.main(
            [
                "--config", "evals/configs/phase5-pilot.json",
                "--corpus-dir", str(tmp_path / "corpus"),
                "--candidate-dir", str(tmp_path / "candidates"),
                "--output-dir", str(tmp_path / "provider"),
            ]
        )
