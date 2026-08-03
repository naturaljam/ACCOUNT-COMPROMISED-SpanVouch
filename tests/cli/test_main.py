from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from spanvouch.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("argv", "handler_name", "forwarded"),
    [
        (("dataset", "generate", "--output", "x"), "generate_dataset", ("--output", "x")),
        (
            ("dataset", "generate-review", "--output", "x"),
            "generate_review",
            ("--output", "x"),
        ),
        (
            ("evaluate", "diagnosis", "--output", "x"),
            "evaluate_diagnosis",
            ("--output", "x"),
        ),
        (("evaluate", "review", "--output", "x"), "evaluate_review", ("--output", "x")),
        (("labs", "corpus", "--output-dir", "x"), "generate_corpus", ("--output-dir", "x")),
        (("labs", "labels", "--output-dir", "x"), "generate_labels", ("--output-dir", "x")),
        (("experiments", "run", "--help"), "experiments_run", ("--help",)),
        (("experiments", "evaluate", "--help"), "experiments_evaluate", ("--help",)),
        (
            ("experiments", "analyze", "--output-dir", "x"),
            "analyze_experiment",
            ("--output-dir", "x"),
        ),
        (
            ("experiments", "prepare-analysis", "--output-dir", "x"),
            "prepare_analysis",
            ("--output-dir", "x"),
        ),
        (("review", "show", "--case-id", "c1"), "review", ("show", "--case-id", "c1")),
        (("admin", "project", "list"), "admin", ("project", "list")),
    ],
)
def test_main_routes_to_one_public_command_tree(
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    handler_name: str,
    forwarded: tuple[str, ...],
) -> None:
    calls: list[tuple[str, Sequence[str]]] = []

    def record(name: str):
        def handler(args: Sequence[str] | None = None) -> int:
            calls.append((name, tuple(args or ())))
            return 0

        return handler

    monkeypatch.setattr(
        cli_main,
        "_load_handler",
        lambda command, subcommand: record(handler_name),
    )
    assert cli_main.main(list(argv)) == 0
    assert calls == [(handler_name, forwarded)]


def test_real_console_entrypoint_loads_only_the_selected_corpus_branch() -> None:
    script = """
import importlib.metadata
import json
import sys

entrypoint = next(
    item
    for item in importlib.metadata.distribution("spanvouch").entry_points
    if item.group == "console_scripts" and item.name == "spanvouch"
)
main = entrypoint.load()
try:
    main(["labs", "corpus", "--help"])
except SystemExit as error:
    if error.code != 0:
        raise
forbidden = sorted(
    name
    for name in sys.modules
    if name.startswith(
        (
            "spanvouch.evaluation.corpus.gold_specs",
            "spanvouch.evaluation.corpus.labels",
            "spanvouch.evaluation.generate_phase5_labels",
            "spanvouch.evaluation.provider_view",
            "spanvouch.evaluation.run_diagnosis_eval",
            "spanvouch.evaluation.run_review_eval",
        )
    )
)
print("MODULES=" + json.dumps(forbidden))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    marker = next(line for line in result.stdout.splitlines() if line.startswith("MODULES="))
    assert json.loads(marker.removeprefix("MODULES=")) == []


@pytest.mark.parametrize(
    ("argv", "failure"),
    (
        (("labs", "corpus"), FileNotFoundError("GOLD_SENTINEL missing config")),
        (("labs", "corpus"), FileExistsError("GOLD_SENTINEL destination conflict")),
        (("labs", "corpus"), RuntimeError("GOLD_SENTINEL generation failed")),
        (("labs", "labels"), ValueError("GOLD_SENTINEL tampered label input")),
    ),
)
def test_expected_command_failures_are_concise_and_do_not_leak_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, str],
    failure: Exception,
) -> None:
    def fail(_arguments: Sequence[str] | None = None) -> int:
        raise failure

    monkeypatch.setattr(cli_main, "_load_handler", lambda command, subcommand: fail)

    assert cli_main.main(argv) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"spanvouch: {argv[0]} {argv[1]} failed\n"
    assert "Traceback" not in captured.err
    assert "GOLD_SENTINEL" not in captured.err


def test_handler_import_failure_is_a_concise_nonzero_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_import(command: str, subcommand: str) -> object:
        raise ImportError("GOLD_SENTINEL branch dependency unavailable")

    monkeypatch.setattr(cli_main, "_load_handler", fail_import)

    assert cli_main.main(("labs", "corpus")) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "spanvouch: labs corpus failed\n"
    assert "Traceback" not in captured.err
    assert "GOLD_SENTINEL" not in captured.err


@pytest.mark.parametrize(
    "argv", [("admin",), ("dataset",), ("evaluate",), ("labs",), ("experiments",)]
)
def test_main_rejects_a_missing_subcommand(argv: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli_main.main(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ("admin", "unknown"),
        ("dataset", "unknown"),
        ("evaluate", "unknown"),
        ("labs", "unknown"),
        ("experiments", "unknown"),
    ],
)
def test_main_rejects_an_unknown_subcommand(argv: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli_main.main(argv)


def test_candidate_generation_cli_is_routed_lazily() -> None:
    assert cli_main._HANDLER_IMPORTS[("experiments", "candidates")] == (
        "spanvouch.evaluation.run_phase5_candidates",
        "main",
    )
