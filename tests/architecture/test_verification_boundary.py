import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFICATION_ROOT = REPOSITORY_ROOT / "src" / "spanvouch" / "verification"
FORBIDDEN_IMPORT_PREFIXES = (
    "aiohttp",
    "aiosqlite",
    "fastapi",
    "httpcore",
    "httpx",
    "langgraph",
    "openai",
    "requests",
    "sqlalchemy",
    "spanvouch.adapters.models",
    "spanvouch.adapters.storage",
    "spanvouch.api",
    "spanvouch.diagnosis.deepseek",
    "spanvouch.evaluation",
    "spanvouch.labs",
    "spanvouch.persistence",
    "spanvouch.review",
    "spanvouch.storage",
    "sqlite3",
    "urllib3",
)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def _resolve_from_import(path: Path, root: Path, node: ast.ImportFrom) -> set[str]:
    if node.level == 0:
        base = node.module or ""
    else:
        package = ("spanvouch", "verification", *path.relative_to(root).parent.parts)
        retained = package[: len(package) - (node.level - 1)]
        suffix = tuple((node.module or "").split(".")) if node.module else ()
        base = ".".join((*retained, *suffix))
    modules = {base} if base else set()
    if base and not _is_forbidden(base):
        modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def _forbidden_imports(root: Path) -> dict[str, tuple[str, ...]]:
    violations: dict[str, tuple[str, ...]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.update(_resolve_from_import(path, root, node))
        forbidden = tuple(sorted(module for module in modules if _is_forbidden(module)))
        if forbidden:
            violations[path.relative_to(root).as_posix()] = forbidden
    return violations


def test_verification_module_is_populated_with_deterministic_core() -> None:
    expected = {
        "deterministic.py",
        "invariants.py",
        "invariant_engine.py",
        "verdicts.py",
    }
    assert expected <= {path.name for path in VERIFICATION_ROOT.glob("*.py")}


def test_verification_module_does_not_import_review_or_infrastructure() -> None:
    assert _forbidden_imports(VERIFICATION_ROOT) == {}


def test_boundary_scanner_recurses_and_ignores_strings_and_comments(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "safe.py").write_text(
        'TEXT = "import spanvouch.review"\n# import sqlite3\n', encoding="utf-8"
    )
    (nested / "forbidden.py").write_text(
        "import sqlite3\n"
        "import spanvouch.api.app\n"
        "from langgraph.graph import StateGraph\n"
        "from spanvouch.adapters.models import deepseek\n",
        encoding="utf-8",
    )

    assert _forbidden_imports(tmp_path) == {
        "nested/forbidden.py": (
            "langgraph.graph",
            "spanvouch.adapters.models",
            "spanvouch.api.app",
            "sqlite3",
        )
    }


@pytest.mark.parametrize(
    "statement",
    (
        "from ..review import workflow",
        "from spanvouch import review",
    ),
)
def test_boundary_scanner_resolves_relative_and_aliased_imports(
    tmp_path: Path,
    statement: str,
) -> None:
    source = tmp_path / "boundary_probe.py"
    source.write_text(f"{statement}\n", encoding="utf-8")

    assert _forbidden_imports(tmp_path)
