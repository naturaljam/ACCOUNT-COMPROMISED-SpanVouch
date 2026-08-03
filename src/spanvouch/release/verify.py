from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_PACKAGE_VERSION_PATTERN = re.compile(
    r"(?m)^\s*__version__\s*=\s*(?:\"(?P<double>[^\"\r\n]+)\"|'(?P<single>[^'\r\n]+)')\s*$"
)
_CITATION_VERSION_PATTERN = re.compile(r"(?m)^version:\s*(?P<version>\S+)\s*$")
_README_RELEASE_PATTERN = re.compile(r"releases/tag/v(?P<version>[^\s\"')]+)")

_REQUIRED_FILES = (
    "LICENSE",
    "README.md",
    "README.zh-CN.md",
    "CITATION.cff",
    "pyproject.toml",
    "uv.lock",
    "src/spanvouch/__init__.py",
)


class ReleaseCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    detail: str


class ReleaseVerificationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.release-verification"]
    expected_version: str
    passed: bool
    checks: tuple[ReleaseCheck, ...]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _check_expected_version(expected_version: str) -> ReleaseCheck:
    passed = _VERSION_PATTERN.fullmatch(expected_version) is not None
    return ReleaseCheck(
        name="expected-version",
        passed=passed,
        detail="expected version format is MAJOR.MINOR.PATCH"
        if passed
        else "expected version must match MAJOR.MINOR.PATCH",
    )


def _check_pyproject(repo_root: Path, expected_version: str) -> ReleaseCheck:
    path = repo_root / "pyproject.toml"
    raw = _read_text(path)
    if raw is None:
        return ReleaseCheck(
            name="pyproject.toml", passed=False, detail="pyproject.toml is unreadable"
        )
    try:
        project = tomllib.loads(raw).get("project")
    except tomllib.TOMLDecodeError:
        return ReleaseCheck(
            name="pyproject.toml", passed=False, detail="pyproject.toml is malformed"
        )
    if not isinstance(project, dict):
        return ReleaseCheck(
            name="pyproject.toml",
            passed=False,
            detail="pyproject.toml project metadata is missing",
        )
    name = project.get("name")
    version = project.get("version")
    passed = name == "spanvouch" and version == expected_version
    if passed:
        detail = "pyproject.toml project name and version match"
    elif name != "spanvouch":
        detail = "pyproject.toml project name does not match"
    else:
        detail = "pyproject.toml version does not match"
    return ReleaseCheck(name="pyproject.toml", passed=passed, detail=detail)


def _check_package_version(repo_root: Path, expected_version: str) -> ReleaseCheck:
    name = "src/spanvouch/__init__.py"
    raw = _read_text(repo_root / name)
    match = _PACKAGE_VERSION_PATTERN.fullmatch(raw.strip()) if raw is not None else None
    version = None if match is None else (match.group("double") or match.group("single"))
    passed = version == expected_version
    return ReleaseCheck(
        name=name,
        passed=passed,
        detail="package __version__ matches" if passed else "package __version__ does not match",
    )


def _check_citation(repo_root: Path, expected_version: str) -> ReleaseCheck:
    name = "CITATION.cff"
    raw = _read_text(repo_root / name)
    match = _CITATION_VERSION_PATTERN.search(raw) if raw is not None else None
    passed = match is not None and match.group("version") == expected_version
    return ReleaseCheck(
        name=name,
        passed=passed,
        detail="CITATION.cff version matches" if passed else "CITATION.cff version does not match",
    )


def _check_readme(repo_root: Path, expected_version: str, name: str) -> ReleaseCheck:
    raw = _read_text(repo_root / name)
    versions = (
        []
        if raw is None
        else [match.group("version") for match in _README_RELEASE_PATTERN.finditer(raw)]
    )
    passed = bool(versions) and all(version == expected_version for version in versions)
    return ReleaseCheck(
        name=name,
        passed=passed,
        detail=f"{name} release links match" if passed else f"{name} release links do not match",
    )


def _check_required_file(repo_root: Path, relative_path: str) -> ReleaseCheck:
    path = repo_root / relative_path
    try:
        passed = path.is_file() and not path.is_symlink()
    except OSError:
        passed = False
    return ReleaseCheck(
        name=f"required:{relative_path}",
        passed=passed,
        detail=(
            f"{relative_path} is present as a regular file"
            if passed
            else f"{relative_path} is missing or not a regular file"
        ),
    )


def verify_release(repo_root: Path, expected_version: str) -> ReleaseVerificationReport:
    """Verify local release metadata and distribution files against one version."""
    checks = [
        _check_expected_version(expected_version),
        _check_pyproject(repo_root, expected_version),
        _check_package_version(repo_root, expected_version),
        _check_citation(repo_root, expected_version),
        _check_readme(repo_root, expected_version, "README.md"),
        _check_readme(repo_root, expected_version, "README.zh-CN.md"),
    ]
    checks.extend(_check_required_file(repo_root, path) for path in _REQUIRED_FILES)
    return ReleaseVerificationReport(
        schema_name="spanvouch.release-verification",
        expected_version=expected_version,
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
    )
