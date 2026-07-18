from __future__ import annotations

from collections.abc import Sequence

import pytest

from spanvouch.cli import main as cli_main


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
        (("review", "show", "--case-id", "c1"), "review", ("show", "--case-id", "c1")),
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

    monkeypatch.setattr(cli_main, handler_name, record(handler_name))
    assert cli_main.main(list(argv)) == 0
    assert calls == [(handler_name, forwarded)]


@pytest.mark.parametrize("argv", [("dataset",), ("evaluate",)])
def test_main_rejects_a_missing_subcommand(argv: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli_main.main(argv)


@pytest.mark.parametrize(
    "argv",
    [("dataset", "unknown"), ("evaluate", "unknown")],
)
def test_main_rejects_an_unknown_subcommand(argv: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli_main.main(argv)
