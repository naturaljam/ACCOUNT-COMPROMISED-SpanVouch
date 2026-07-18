import ast
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "spanvouch"
REVIEW_ROOT = SOURCE_ROOT / "review"
FORBIDDEN_REVIEW_PREFIXES = (
    "langgraph",
    "sqlite3",
    "spanvouch.adapters.frameworks",
    "spanvouch.adapters.storage",
)
ALLOWED_FRAMEWORK_ADAPTER_IMPORTERS = {"api/app.py"}


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


def _matches_prefix(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_REVIEW_PREFIXES
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
                if _matches_prefix(module)
            )
        )
        if forbidden:
            violations[path.relative_to(root).as_posix()] = forbidden
    return violations


def test_review_core_has_no_framework_or_storage_imports() -> None:
    assert _forbidden_imports(REVIEW_ROOT) == {}


def test_langgraph_adapter_has_canonical_location() -> None:
    assert (SOURCE_ROOT / "adapters" / "frameworks" / "langgraph_review.py").is_file()
    assert not (REVIEW_ROOT / "workflow.py").exists()


def test_review_application_uses_ports_not_concrete_engine() -> None:
    application = REVIEW_ROOT / "application.py"
    assert "spanvouch.diagnosis.engine" not in _imports(application)


def test_review_application_create_accepts_extensible_diagnoser_identifier() -> None:
    from spanvouch.review.application import ReviewApplication

    signature = inspect.signature(ReviewApplication.create)
    assert get_type_hints(ReviewApplication.create)["diagnoser"] is str
    assert signature.parameters["diagnoser"].kind is inspect.Parameter.KEYWORD_ONLY


def test_only_composition_root_imports_langgraph_adapter() -> None:
    importers = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if any(
            module == "spanvouch.adapters.frameworks.langgraph_review"
            or module.startswith("spanvouch.adapters.frameworks.langgraph_review.")
            for module in _imports(path)
        )
    }
    assert importers == ALLOWED_FRAMEWORK_ADAPTER_IMPORTERS


def test_boundary_scanner_recurses_and_ignores_strings_and_comments(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "spanvouch"
    review_root = package_root / "review"
    nested = review_root / "nested"
    nested.mkdir(parents=True)
    (review_root / "safe.py").write_text(
        'TEXT = "import langgraph; import sqlite3"\n'
        "# from spanvouch.adapters.frameworks import langgraph_review\n",
        encoding="utf-8",
    )
    (nested / "unsafe.py").write_text(
        "import langgraph.graph as graph\n"
        "from spanvouch.adapters.storage import sqlite as storage\n",
        encoding="utf-8",
    )

    assert _forbidden_imports(review_root, source_root=package_root) == {
        "nested/unsafe.py": (
            "langgraph.graph",
            "spanvouch.adapters.storage",
            "spanvouch.adapters.storage.sqlite",
        )
    }


@pytest.mark.parametrize(
    "statement",
    (
        "from ..adapters.frameworks import langgraph_review as workflow",
        "from spanvouch.adapters import frameworks as infrastructure",
    ),
)
def test_boundary_scanner_resolves_relative_and_aliased_imports(
    tmp_path: Path,
    statement: str,
) -> None:
    package_root = tmp_path / "spanvouch"
    review_root = package_root / "review"
    review_root.mkdir(parents=True)
    (review_root / "probe.py").write_text(f"{statement}\n", encoding="utf-8")

    assert _forbidden_imports(review_root, source_root=package_root)
