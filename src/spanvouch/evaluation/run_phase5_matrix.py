"""Provider-phase CLI boundary for the Phase 5 verification matrix."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProviderRunRequest:
    config: Path
    corpus_dir: Path
    candidate_dir: Path
    output_dir: Path
    allow_live_provider: bool
    formal_run: bool


ProviderRunCommand = Callable[[ProviderRunRequest], None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch experiments run")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--corpus-dir", required=True, type=Path)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-live-provider", action="store_true")
    parser.add_argument("--formal-run", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    command: ProviderRunCommand | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
    request = ProviderRunRequest(
        config=arguments.config,
        corpus_dir=arguments.corpus_dir,
        candidate_dir=arguments.candidate_dir,
        output_dir=arguments.output_dir,
        allow_live_provider=arguments.allow_live_provider,
        formal_run=arguments.formal_run,
    )
    if command is None:
        raise RuntimeError("provider runner composition is not installed")
    command(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
