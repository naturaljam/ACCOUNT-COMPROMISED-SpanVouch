from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from importlib import import_module

Handler = Callable[[Sequence[str] | None], int]
_HANDLER_IMPORTS = {
    ("admin", ""): ("spanvouch.cli.admin", "main"),
    ("dataset", "generate"): ("spanvouch.evaluation.generate_dataset", "main"),
    ("dataset", "generate-review"): (
        "spanvouch.evaluation.generate_review_dataset",
        "main",
    ),
    ("evaluate", "diagnosis"): (
        "spanvouch.evaluation.run_diagnosis_eval",
        "main",
    ),
    ("evaluate", "review"): ("spanvouch.evaluation.run_review_eval", "main"),
    ("labs", "corpus"): ("spanvouch.evaluation.run_phase5_corpus", "main"),
    ("labs", "labels"): ("spanvouch.evaluation.generate_phase5_labels", "main"),
    ("experiments", "run"): ("spanvouch.evaluation.run_phase5_matrix", "main"),
    ("experiments", "candidates"): (
        "spanvouch.evaluation.run_phase5_candidates",
        "main",
    ),
    ("experiments", "evaluate"): (
        "spanvouch.evaluation.evaluate_phase5_matrix",
        "main",
    ),
    ("experiments", "analyze"): (
        "spanvouch.evaluation.run_phase5_analysis",
        "main",
    ),
    ("experiments", "prepare-analysis"): (
        "spanvouch.evaluation.prepare_phase5_analysis",
        "main",
    ),
    ("review", ""): ("spanvouch.cli.review", "main"),
    ("release", "verify"): ("spanvouch.release.cli", "main"),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch")
    parser.add_argument(
        "command",
        choices=(
            "admin",
            "dataset",
            "evaluate",
            "experiments",
            "labs",
            "release",
            "review",
        ),
    )
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def _load_handler(command: str, subcommand: str) -> Handler:
    module_name, attribute = _HANDLER_IMPORTS[(command, subcommand)]
    handler = getattr(import_module(module_name), attribute)
    return handler  # type: ignore[no-any-return]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    rest = tuple(arguments.rest)
    if arguments.command in {"admin", "review"}:
        subcommand = ""
        forwarded = rest
    else:
        if not rest:
            _parser().error(f"{arguments.command} requires a subcommand")
        subcommand, forwarded = rest[0], rest[1:]
    if (arguments.command, subcommand) not in _HANDLER_IMPORTS:
        _parser().error(f"unknown {arguments.command} subcommand: {subcommand}")
    try:
        handler = _load_handler(arguments.command, subcommand)
        return handler(forwarded)
    except Exception:
        label = arguments.command
        if subcommand:
            label = f"{label} {subcommand}"
        print(f"spanvouch: {label} failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
