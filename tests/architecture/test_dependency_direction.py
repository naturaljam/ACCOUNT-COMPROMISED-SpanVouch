from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "spanvouch"
CORE_ROOTS = ("contracts", "trace", "diagnosis", "verification", "review")
PHASE5_EXPERIMENTAL_PREFIXES = (
    "spanvouch.labs.runtime",
    "spanvouch.labs.frameworks",
    "spanvouch.labs.opslab",
    "spanvouch.labs.corpus",
    "spanvouch.evaluation.experiments",
    "spanvouch.evaluation.statistics",
)
CORE_FORBIDDEN_PREFIXES = (
    "spanvouch.labs",
    "spanvouch.evaluation",
    *PHASE5_EXPERIMENTAL_PREFIXES,
)
CONTRACT_FORBIDDEN_PREFIXES = (
    "fastapi",
    "langgraph",
    "spanvouch.adapters",
    "spanvouch.api",
    "spanvouch.diagnosis",
    "spanvouch.evaluation",
    "spanvouch.labs",
    "spanvouch.review",
    "spanvouch.trace",
    "spanvouch.verification",
    "sqlite3",
)
STAGE_A_FORBIDDEN_PREFIXES = (
    "spanvouch.evaluation.corpus.gold_specs",
    "spanvouch.evaluation.corpus.labels",
    "spanvouch.evaluation.diagnosis_labels",
    "spanvouch.evaluation.review_labels",
    "spanvouch.evaluation.statistics",
    "spanvouch.evaluation.provider_view",
    "spanvouch.labs.supportlab.scenarios",
)


def _matches_prefix(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _package_parts(path: Path, source_root: Path) -> tuple[str, ...]:
    return ("spanvouch", *path.relative_to(source_root).parent.parts)


def _resolve_from_import(path: Path, source_root: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = _package_parts(path, source_root)
    retained = package[: len(package) - (node.level - 1)]
    suffix = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join((*retained, *suffix))


def _imported_modules(path: Path, source_root: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from_import(path, source_root, node)
            if module:
                modules.add(module)
                modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def _forbidden_imports(
    roots: tuple[Path, ...], source_root: Path, prefixes: tuple[str, ...]
) -> dict[str, tuple[str, ...]]:
    violations: dict[str, tuple[str, ...]] = {}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            forbidden = tuple(
                sorted(
                    module
                    for module in _imported_modules(path, source_root)
                    if _matches_prefix(module, prefixes)
                )
            )
            if forbidden:
                violations[path.relative_to(source_root).as_posix()] = forbidden
    return violations


def test_outer_modules_have_only_the_new_locations() -> None:
    assert (SOURCE_ROOT / "labs" / "supportlab").is_dir()
    assert (SOURCE_ROOT / "labs" / "supportlab" / "invariants.py").is_file()
    assert (SOURCE_ROOT / "evaluation").is_dir()
    assert not (SOURCE_ROOT / "supportlab").exists()
    assert not (SOURCE_ROOT / "invariants" / "supportlab.py").exists()
    assert not (SOURCE_ROOT / "evals").exists()


def test_production_core_never_imports_labs_or_evaluation() -> None:
    roots = tuple(SOURCE_ROOT / root for root in CORE_ROOTS)
    assert _forbidden_imports(roots, SOURCE_ROOT, CORE_FORBIDDEN_PREFIXES) == {}


def test_phase5_stage_a_never_imports_labels_statistics_or_provider_views() -> None:
    roots = (
        SOURCE_ROOT / "evaluation" / "corpus" / "inventory.py",
        SOURCE_ROOT / "evaluation" / "corpus" / "generate.py",
        SOURCE_ROOT / "evaluation" / "run_phase5_corpus.py",
    )
    violations = {
        path.name: tuple(
            sorted(
                module
                for module in _imported_modules(path, SOURCE_ROOT)
                if _matches_prefix(module, STAGE_A_FORBIDDEN_PREFIXES)
            )
        )
        for path in roots
    }
    assert violations == {
        "inventory.py": (),
        "generate.py": (),
        "run_phase5_corpus.py": (),
    }


@pytest.mark.parametrize("module", PHASE5_EXPERIMENTAL_PREFIXES)
def test_production_core_scanner_rejects_phase5_experimental_packages(
    tmp_path: Path, module: str
) -> None:
    source_root = tmp_path / "spanvouch"
    diagnosis = source_root / "diagnosis"
    diagnosis.mkdir(parents=True)
    probe = diagnosis / "probe.py"
    probe.write_text(f"import {module}\n", encoding="utf-8")

    assert _forbidden_imports(
        (diagnosis,), source_root, CORE_FORBIDDEN_PREFIXES
    ) == {"diagnosis/probe.py": (module,)}


def test_contracts_never_import_higher_or_infrastructure_modules() -> None:
    assert _forbidden_imports(
        (SOURCE_ROOT / "contracts",), SOURCE_ROOT, CONTRACT_FORBIDDEN_PREFIXES
    ) == {}


def test_contract_sanitizer_does_not_own_trace_projection() -> None:
    sanitizer = SOURCE_ROOT / "contracts" / "sanitization.py"
    tree = ast.parse(sanitizer.read_text(encoding="utf-8"), filename=str(sanitizer))

    assert not {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"TraceProjector", "TraceProjectorPort"}
    }


def test_contract_scanner_captures_aliased_api_outer_adapter(tmp_path: Path) -> None:
    source_root = tmp_path / "spanvouch"
    contracts = source_root / "contracts"
    contracts.mkdir(parents=True)
    (contracts / "probe.py").write_text(
        "import spanvouch.api.app as web\nfrom spanvouch import api as api_member\n",
        encoding="utf-8",
    )

    assert _forbidden_imports(
        (contracts,), source_root, CONTRACT_FORBIDDEN_PREFIXES
    ) == {"contracts/probe.py": ("spanvouch.api", "spanvouch.api.app")}


def test_dependency_scanner_recurses_and_ignores_strings_and_comments(tmp_path: Path) -> None:
    source_root = tmp_path / "spanvouch"
    nested = source_root / "contracts" / "nested"
    nested.mkdir(parents=True)
    (source_root / "contracts" / "safe.py").write_text(
        'TEXT = "from spanvouch.labs import supportlab"\n# import sqlite3\n',
        encoding="utf-8",
    )
    probe = nested / "probe.py"
    probe.write_text("import spanvouch.labs.supportlab as support\n", encoding="utf-8")

    assert _forbidden_imports(
        (source_root / "contracts",), source_root, CORE_FORBIDDEN_PREFIXES
    ) == {"contracts/nested/probe.py": ("spanvouch.labs.supportlab",)}


@pytest.mark.parametrize(
    "statement, expected",
    (
        ("from ...labs import supportlab as lab", "spanvouch.labs.supportlab"),
        ("from ...evaluation.metrics import evaluate as run", "spanvouch.evaluation.metrics"),
        ("from spanvouch.labs.supportlab import graph as g", "spanvouch.labs.supportlab.graph"),
    ),
)
def test_dependency_scanner_resolves_relative_aliased_and_member_imports(
    tmp_path: Path, statement: str, expected: str
) -> None:
    source_root = tmp_path / "spanvouch"
    package = source_root / "diagnosis" / "nested"
    package.mkdir(parents=True)
    probe = package / "probe.py"
    probe.write_text(f"{statement}\n", encoding="utf-8")

    assert expected in _imported_modules(probe, source_root)
