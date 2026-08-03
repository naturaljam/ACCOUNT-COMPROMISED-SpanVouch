from __future__ import annotations

from pathlib import Path

import pytest

from spanvouch.release.verify import verify_release


def _write_checkout(root: Path, *, version: str = "0.6.0") -> None:
    (root / "src" / "spanvouch").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "spanvouch"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src" / "spanvouch" / "__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (root / "CITATION.cff").write_text(
        f"cff-version: 1.2.0\nversion: {version}\n",
        encoding="utf-8",
    )
    for name in ("README.md", "README.zh-CN.md"):
        (root / name).write_text(
            f"Download https://github.com/example/project/releases/tag/v{version}\n",
            encoding="utf-8",
        )
    for name in ("LICENSE", "uv.lock"):
        (root / name).write_text("fixture\n", encoding="utf-8")


def test_verify_release_returns_deterministic_passing_report(tmp_path: Path) -> None:
    _write_checkout(tmp_path)

    report = verify_release(tmp_path, "0.6.0")

    assert report.schema_name == "spanvouch.release-verification"
    assert report.expected_version == "0.6.0"
    assert report.passed is True
    assert all(check.passed for check in report.checks)
    assert [check.name for check in report.checks] == [
        "expected-version",
        "pyproject.toml",
        "src/spanvouch/__init__.py",
        "CITATION.cff",
        "README.md",
        "README.zh-CN.md",
        "required:LICENSE",
        "required:README.md",
        "required:README.zh-CN.md",
        "required:CITATION.cff",
        "required:pyproject.toml",
        "required:uv.lock",
        "required:src/spanvouch/__init__.py",
    ]


@pytest.mark.parametrize(
    ("relative_path", "replacement"),
    (
        ("pyproject.toml", '[project]\nname = "spanvouch"\nversion = "0.5.0"\n'),
        ("src/spanvouch/__init__.py", '__version__ = "0.5.0"\n'),
        ("CITATION.cff", "cff-version: 1.2.0\nversion: 0.5.0\n"),
    ),
)
def test_verify_release_reports_version_source_mismatches(
    tmp_path: Path, relative_path: str, replacement: str
) -> None:
    _write_checkout(tmp_path)
    (tmp_path / relative_path).write_text(replacement, encoding="utf-8")

    report = verify_release(tmp_path, "0.6.0")

    assert report.passed is False
    assert any(not check.passed for check in report.checks if check.name == relative_path)


@pytest.mark.parametrize("readme_name", ("README.md", "README.zh-CN.md"))
def test_verify_release_reports_mismatched_or_missing_readme_release_links(
    tmp_path: Path, readme_name: str
) -> None:
    _write_checkout(tmp_path)
    (tmp_path / readme_name).write_text("no release link\n", encoding="utf-8")

    report = verify_release(tmp_path, "0.6.0")

    assert report.passed is False
    assert next(check for check in report.checks if check.name == readme_name).passed is False


def test_verify_release_reports_missing_required_file(tmp_path: Path) -> None:
    _write_checkout(tmp_path)
    (tmp_path / "uv.lock").unlink()

    report = verify_release(tmp_path, "0.6.0")

    assert report.passed is False
    check = next(check for check in report.checks if check.name == "required:uv.lock")
    assert check.passed is False
    assert "uv.lock" in check.detail
    assert str(tmp_path) not in check.detail


def test_verify_release_rejects_invalid_expected_version(tmp_path: Path) -> None:
    _write_checkout(tmp_path)

    report = verify_release(tmp_path, "v0.6.0")

    assert report.passed is False
    check = next(check for check in report.checks if check.name == "expected-version")
    assert check.passed is False
