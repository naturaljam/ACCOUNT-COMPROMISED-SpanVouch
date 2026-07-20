"""Offline command entry point for sealed Phase 5 label generation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from spanvouch.evaluation.corpus.labels import generate_phase5_labels


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spanvouch labs labels",
        allow_abbrev=False,
    )
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = generate_phase5_labels(
        corpus_dir=arguments.corpus_dir,
        output_dir=arguments.output_dir,
    )
    print(
        json.dumps(
            {
                "corpus_manifest_sha256": result.manifest.corpus_manifest_sha256,
                "labels_manifest": str(result.output_dir / "manifest.json"),
                "labels_manifest_sha256": result.manifest_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
