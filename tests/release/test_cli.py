from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from spanvouch.release import cli
from spanvouch.release.verify import ReleaseCheck, ReleaseVerificationReport


def _report(passed: bool) -> ReleaseVerificationReport:
    return ReleaseVerificationReport(
        schema_name="spanvouch.release-verification",
        expected_version="0.6.0",
        passed=passed,
        checks=(
            ReleaseCheck(
                name="pyproject.toml",
                passed=passed,
                detail=(
                    "pyproject.toml version matches"
                    if passed
                    else "pyproject.toml version does not match"
                ),
            ),
        ),
    )


def test_release_cli_prints_stable_human_report_and_status(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "verify_release", lambda root, version: _report(False))

    assert cli.main(("--repo-root", "C:/private/repo", "--expected-version", "0.6.0")) == 1

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "pyproject.toml" in captured.out
    assert "does not match" in captured.out
    assert "C:/private/repo" not in captured.out


def test_release_cli_json_is_one_canonical_object(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "verify_release", lambda root, version: _report(True))

    assert cli.main(
        (
            "--repo-root",
            str(tmp_path),
            "--expected-version",
            "0.6.0",
            "--json",
        )
    ) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out) == {
        "checks": [
            {
                "detail": "pyproject.toml version matches",
                "name": "pyproject.toml",
                "passed": True,
            }
        ],
        "expected_version": "0.6.0",
        "passed": True,
        "schema_name": "spanvouch.release-verification",
    }


def test_release_cli_can_write_to_explicit_streams(monkeypatch) -> None:
    monkeypatch.setattr(cli, "verify_release", lambda root, version: _report(True))
    output = StringIO()
    errors = StringIO()

    assert cli.main(
        ("--repo-root", ".", "--expected-version", "0.6.0"),
        stdout=output,
        stderr=errors,
    ) == 0
    assert errors.getvalue() == ""
    assert output.getvalue().startswith("pyproject.toml: pass")
