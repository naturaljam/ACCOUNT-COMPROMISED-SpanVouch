from pathlib import Path

import pytest

from spanvouch.evals.diagnosis_labels import load_diagnosis_labels
from spanvouch.evals.run_diagnosis_eval import (
    DEFAULT_DATASET,
    _load_traces,
    _select_run_ids,
    main,
)


def test_rule_cli_writes_byte_exact_artifact(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert main(["--output", str(first)]) == 0
    assert main(["--output", str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")


def test_live_diagnoser_requires_explicit_network_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-be-read")

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--diagnoser",
                "deepseek",
                "--output",
                str(tmp_path / "report.json"),
            ]
        )

    assert raised.value.code == 2
    assert not (tmp_path / "report.json").exists()


def test_live_diagnoser_reports_missing_key_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--diagnoser",
                "deepseek",
                "--allow-live-api",
                "--run-id",
                "clean-01",
                "--output",
                str(tmp_path / "report.json"),
            ]
        )

    assert raised.value.code == 2
    assert not (tmp_path / "report.json").exists()


def test_run_id_allowlist_filters_traces_and_labels_together() -> None:
    traces = _load_traces(DEFAULT_DATASET / "traces.jsonl")
    labels = load_diagnosis_labels(DEFAULT_DATASET / "diagnosis-labels-v1.jsonl")

    selected_traces, selected_labels = _select_run_ids(
        traces,
        labels,
        ("invalid_argument-01", "clean-01"),
    )

    assert tuple(trace.run_id for trace in selected_traces) == (
        "invalid_argument-01",
        "clean-01",
    )
    assert tuple(label.run_id for label in selected_labels) == (
        "invalid_argument-01",
        "clean-01",
    )


def test_run_id_allowlist_rejects_unknown_ids() -> None:
    traces = _load_traces(DEFAULT_DATASET / "traces.jsonl")
    labels = load_diagnosis_labels(DEFAULT_DATASET / "diagnosis-labels-v1.jsonl")

    with pytest.raises(ValueError, match="unknown run IDs: missing-run"):
        _select_run_ids(traces, labels, ("missing-run",))
