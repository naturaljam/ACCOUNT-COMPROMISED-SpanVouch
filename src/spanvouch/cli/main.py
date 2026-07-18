from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from spanvouch.cli.review import main as review
from spanvouch.evaluation.generate_dataset import main as generate_dataset
from spanvouch.evaluation.generate_review_dataset import main as generate_review
from spanvouch.evaluation.run_diagnosis_eval import main as evaluate_diagnosis
from spanvouch.evaluation.run_review_eval import main as evaluate_review

Handler = Callable[[Sequence[str] | None], int]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch")
    parser.add_argument("command", choices=("dataset", "evaluate", "review"))
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    rest = tuple(arguments.rest)
    if arguments.command == "review":
        return review(rest)
    if not rest:
        _parser().error(f"{arguments.command} requires a subcommand")
    subcommand, forwarded = rest[0], rest[1:]
    handlers: dict[tuple[str, str], Handler] = {
        ("dataset", "generate"): generate_dataset,
        ("dataset", "generate-review"): generate_review,
        ("evaluate", "diagnosis"): evaluate_diagnosis,
        ("evaluate", "review"): evaluate_review,
    }
    handler = handlers.get((arguments.command, subcommand))
    if handler is None:
        _parser().error(f"unknown {arguments.command} subcommand: {subcommand}")
    return handler(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
