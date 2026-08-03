from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from spanvouch.contracts.versioning import canonical_json
from spanvouch.release.verify import verify_release


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spanvouch release verify",
        description="Verify local release metadata and distribution files.",
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    del stderr
    report = verify_release(args.repo_root, args.expected_version)

    if args.as_json:
        print(canonical_json(report), file=output)
    else:
        for check in report.checks:
            status = "pass" if check.passed else "fail"
            print(f"{check.name}: {status} - {check.detail}", file=output)
    return int(not report.passed)
