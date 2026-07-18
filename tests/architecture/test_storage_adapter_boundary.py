import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "spanvouch"
REVIEW_ROOT = SOURCE_ROOT / "review"
FORBIDDEN_REVIEW_PREFIXES = (
    "spanvouch.adapters.storage",
    "sqlite3",
)
ALLOWED_STORAGE_ADAPTER_IMPORTERS = {"api/app.py"}


def _resolve_from_import(
    path: Path,
    node: ast.ImportFrom,
    *,
    source_root: Path = SOURCE_ROOT,
) -> str:
    if node.level == 0:
        return node.module or ""
    package = ("spanvouch", *path.relative_to(source_root).parent.parts)
    retained = package[: len(package) - (node.level - 1)]
    suffix = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join((*retained, *suffix))


def _imports(path: Path, *, source_root: Path = SOURCE_ROOT) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from_import(path, node, source_root=source_root)
            if module:
                modules.add(module)
                modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def _matches_prefix(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes
    )


def _forbidden_imports(
    root: Path,
    *,
    source_root: Path = SOURCE_ROOT,
) -> dict[str, tuple[str, ...]]:
    violations: dict[str, tuple[str, ...]] = {}
    for path in sorted(root.rglob("*.py")):
        forbidden = tuple(
            sorted(
                module
                for module in _imports(path, source_root=source_root)
                if _matches_prefix(module, FORBIDDEN_REVIEW_PREFIXES)
            )
        )
        if forbidden:
            violations[path.relative_to(root).as_posix()] = forbidden
    return violations


def test_sqlite_adapter_has_canonical_location() -> None:
    assert (SOURCE_ROOT / "adapters" / "storage" / "sqlite.py").is_file()
    assert (SOURCE_ROOT / "adapters" / "storage" / "sqlite_schema.py").is_file()
    assert not (REVIEW_ROOT / "sqlite_repository.py").exists()
    assert not (REVIEW_ROOT / "schema.py").exists()


def test_review_core_does_not_import_sqlite_or_storage_adapter() -> None:
    assert _forbidden_imports(REVIEW_ROOT) == {}


def test_only_composition_roots_import_storage_adapter() -> None:
    importers = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if any(
            module == "spanvouch.adapters.storage.sqlite"
            or module.startswith("spanvouch.adapters.storage.sqlite.")
            for module in _imports(path)
        )
    }
    assert importers == ALLOWED_STORAGE_ADAPTER_IMPORTERS


def test_boundary_scanner_recurses_and_ignores_strings_and_comments(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "spanvouch"
    review_root = package_root / "review"
    nested = review_root / "nested"
    nested.mkdir(parents=True)
    (review_root / "safe.py").write_text(
        'TEXT = "import sqlite3"\n'
        "# from spanvouch.adapters.storage import sqlite\n",
        encoding="utf-8",
    )
    (nested / "unsafe.py").write_text(
        "import sqlite3 as database\n"
        "from spanvouch.adapters.storage import sqlite as storage\n",
        encoding="utf-8",
    )

    assert _forbidden_imports(review_root, source_root=package_root) == {
        "nested/unsafe.py": (
            "spanvouch.adapters.storage",
            "spanvouch.adapters.storage.sqlite",
            "sqlite3",
        )
    }


@pytest.mark.parametrize(
    "statement",
    (
        "from ..adapters.storage import sqlite as storage",
        "from spanvouch.adapters import storage as infrastructure",
    ),
)
def test_boundary_scanner_resolves_relative_and_aliased_imports(
    tmp_path: Path,
    statement: str,
) -> None:
    package_root = tmp_path / "spanvouch"
    review_root = package_root / "review"
    review_root.mkdir(parents=True)
    source = review_root / "probe.py"
    source.write_text(f"{statement}\n", encoding="utf-8")

    assert _forbidden_imports(review_root, source_root=package_root)
