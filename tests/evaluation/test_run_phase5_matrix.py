import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spanvouch.contracts.versioning import canonical_bytes, canonical_sha256
from spanvouch.evaluation import evaluate_phase5_matrix, run_phase5_matrix
from spanvouch.evaluation.corpus import TraceReplayRepository
from spanvouch.evaluation.corpus.labels import GoldLabel, GoldLabelManifest
from spanvouch.evaluation.experiments.config import (
    FormalFreezePolicy,
    freeze_formal_config,
    load_experiment_config,
)
from spanvouch.evaluation.experiments.models import ExperimentFailureCategory, IneligibleCell
from spanvouch.evaluation.experiments.provider import ProviderConfigurationError
from spanvouch.evaluation.experiments.runner import ProviderPhaseRepository
from spanvouch.evaluation.run_phase5_candidates import CandidateIneligibleManifest
from tests.evaluation.experiments.test_planner import _candidate_pair


def _request(**updates: object) -> run_phase5_matrix.ProviderRunRequest:
    payload: dict[str, object] = {
        "config": Path("config.json"),
        "corpus_dir": Path("corpus"),
        "candidate_dir": Path("candidates"),
        "output_dir": Path("out"),
        "allow_live_provider": True,
        "formal_run": True,
        "approved_manifest_sha256": "a" * 64,
    }
    payload.update(updates)
    return run_phase5_matrix.ProviderRunRequest(**payload)  # type: ignore[arg-type]


def test_run_cli_has_no_label_argument_and_supports_live_formal_flags() -> None:
    parser = run_phase5_matrix.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert {"--config", "--corpus-dir", "--candidate-dir", "--output-dir"} <= option_strings
    assert {
        "--allow-live-provider",
        "--formal-run",
        "--approved-manifest-sha256",
    } <= option_strings
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


def test_default_run_reuses_verified_corpus_without_reloading_each_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_candidate_pair(tmp_path))

    def fail_load(*args: object, **kwargs: object) -> object:
        raise AssertionError("default matrix run must not reverify each corpus entry")

    monkeypatch.setattr(TraceReplayRepository, "load", fail_load)
    assert run_phase5_matrix.main(
        [
            "--config", "evals/configs/phase5-pilot.json",
            "--corpus-dir", str(tmp_path / "corpus"),
            "--candidate-dir", str(tmp_path / "candidates"),
            "--output-dir", str(tmp_path / "provider"),
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


def test_every_live_run_requires_the_preapproved_exact_matrix_identity() -> None:
    with pytest.raises(ProviderConfigurationError, match="live run requires"):
        run_phase5_matrix._require_approved_manifest(
            _request(formal_run=False, approved_manifest_sha256=None),
            matrix_manifest_sha256="a" * 64,
        )
    with pytest.raises(ProviderConfigurationError, match="requires"):
        run_phase5_matrix._require_approved_manifest(
            _request(approved_manifest_sha256=None),
            matrix_manifest_sha256="a" * 64,
        )
    with pytest.raises(ProviderConfigurationError, match="does not match"):
        run_phase5_matrix._require_approved_manifest(
            _request(approved_manifest_sha256="b" * 64),
            matrix_manifest_sha256="a" * 64,
        )
    assert run_phase5_matrix._require_approved_manifest(
        _request(), matrix_manifest_sha256="a" * 64
    ) == "a" * 64


def test_pilot_records_and_checks_an_explicit_approved_identity_when_supplied() -> None:
    request = _request(formal_run=False, approved_manifest_sha256="b" * 64)
    with pytest.raises(ProviderConfigurationError, match="does not match"):
        run_phase5_matrix._require_approved_manifest(
            request, matrix_manifest_sha256="a" * 64
        )


def test_deepseek_only_is_rejected_for_formal_runs() -> None:
    request = _request(
        config=Path("evals/configs/phase5-pilot.json"),
        formal_run=True,
        deepseek_only=True,
    )
    with pytest.raises(ProviderConfigurationError, match="configuration mode"):
        run_phase5_matrix._default_command(request)


def test_deepseek_only_is_allowed_for_explicit_formal_config() -> None:
    pilot = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    policy = FormalFreezePolicy.model_validate_json(
        Path("evals/configs/phase5-formal-policy.json").read_text(encoding="utf-8")
    )
    formal = freeze_formal_config(
        pilot,
        policy,
        repetitions=5,
        coverage_loss_tolerance=0.05,
        frozen_at_utc=datetime(2026, 8, 3, tzinfo=UTC),
    )

    run_phase5_matrix._require_deepseek_only_scope(
        formal,
        formal_run=True,
        deepseek_only=True,
    )


def test_matrix_loads_candidate_ineligible_sidecar_without_treating_it_as_candidate(
    tmp_path: Path,
) -> None:
    asyncio.run(_candidate_pair(tmp_path))
    corpus = TraceReplayRepository(tmp_path / "corpus")
    entries = corpus.verify().entries
    sidecar = CandidateIneligibleManifest(
        corpus_manifest_sha256=corpus.manifest_sha256,
        entries=(
            IneligibleCell(
                cell=entries[0].cell,
                category=ExperimentFailureCategory.DIAGNOSIS,
                reason_code="unsafe-artifact-content",
            ),
        ),
    )
    ineligible_identity = canonical_sha256(entries[0].cell)[:16]
    next((tmp_path / "candidates" / "cells" / ineligible_identity).glob("*.json")).unlink()
    (tmp_path / "candidates" / "ineligible.json").write_bytes(canonical_bytes(sidecar))

    candidates = run_phase5_matrix._load_candidates(
        tmp_path / "candidates",
        entries,
        corpus.manifest_sha256,
    )
    ineligible = run_phase5_matrix._load_ineligible(
        tmp_path / "candidates",
        entries,
        corpus.manifest_sha256,
    )
    assert len(candidates) == 1
    assert ineligible == sidecar.entries
