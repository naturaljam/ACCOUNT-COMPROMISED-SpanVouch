import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "spanvouch"
CORE_ROOTS = (
    SOURCE_ROOT / "contracts",
    SOURCE_ROOT / "trace",
    SOURCE_ROOT / "diagnosis",
    SOURCE_ROOT / "verification",
    SOURCE_ROOT / "review",
)
FORBIDDEN_PROVIDER_PREFIXES = (
    "spanvouch.adapters.models.deepseek",
    "spanvouch.diagnosis.deepseek",
)
ALLOWED_ADAPTER_IMPORTERS = {
    "api/app.py",
    "evals/run_diagnosis_eval.py",
    "evals/run_review_eval.py",
}


def _resolve_from_import(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = ("spanvouch", *path.relative_to(SOURCE_ROOT).parent.parts)
    retained = package[: len(package) - (node.level - 1)]
    suffix = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join((*retained, *suffix))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from_import(path, node)
            modules.add(module)
            modules.update(
                f"{module}.{alias.name}" for alias in node.names if module
            )
    return modules


def _provider_imports(roots: tuple[Path, ...]) -> dict[str, tuple[str, ...]]:
    violations: dict[str, tuple[str, ...]] = {}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            forbidden = tuple(
                sorted(
                    module
                    for module in _imports(path)
                    if any(
                        module == prefix or module.startswith(f"{prefix}.")
                        for prefix in FORBIDDEN_PROVIDER_PREFIXES
                    )
                )
            )
            if forbidden:
                try:
                    label = path.relative_to(SOURCE_ROOT).as_posix()
                except ValueError:
                    label = path.relative_to(root).as_posix()
                violations[label] = forbidden
    return violations


def _deepseek_endpoint_literals(roots: tuple[Path, ...]) -> tuple[str, ...]:
    violations: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "api.deepseek.com" in node.value
                for node in ast.walk(tree)
            ):
                violations.append(path.relative_to(SOURCE_ROOT).as_posix())
    return tuple(violations)


def test_model_adapter_and_semantic_verifier_have_canonical_locations() -> None:
    assert (SOURCE_ROOT / "adapters" / "models" / "deepseek.py").is_file()
    assert (SOURCE_ROOT / "verification" / "semantic.py").is_file()
    assert not (SOURCE_ROOT / "diagnosis" / "deepseek.py").exists()
    assert not (SOURCE_ROOT / "review" / "semantic_verifier.py").exists()


def test_core_does_not_import_concrete_model_providers() -> None:
    assert _provider_imports(CORE_ROOTS) == {}
    assert _deepseek_endpoint_literals(CORE_ROOTS) == ()


def test_only_composition_roots_import_deepseek_adapter() -> None:
    importers = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if any(
            module == "spanvouch.adapters.models.deepseek"
            or module.startswith("spanvouch.adapters.models.deepseek.")
            for module in _imports(path)
        )
    }
    assert importers == ALLOWED_ADAPTER_IMPORTERS


def test_boundary_scanner_uses_import_syntax_not_string_contents(tmp_path: Path) -> None:
    safe = tmp_path / "safe.py"
    safe.write_text(
        'TEXT = "spanvouch.adapters.models.deepseek https://api.deepseek.com"\n',
        encoding="utf-8",
    )
    unsafe = tmp_path / "unsafe.py"
    unsafe.write_text(
        "from spanvouch.adapters.models.deepseek import DeepSeekProvider\n",
        encoding="utf-8",
    )

    assert _provider_imports((tmp_path,)) == {
        "unsafe.py": (
            "spanvouch.adapters.models.deepseek",
            "spanvouch.adapters.models.deepseek.DeepSeekProvider",
        )
    }


def test_boundary_scanner_resolves_relative_aliased_provider_imports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "spanvouch"
    diagnosis_root = package_root / "diagnosis"
    diagnosis_root.mkdir(parents=True)
    source = diagnosis_root / "probe.py"
    source.write_text(
        "from ..adapters.models.deepseek import DeepSeekProvider as Provider\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(_imports.__globals__, "SOURCE_ROOT", package_root)

    assert "spanvouch.adapters.models.deepseek" in _imports(source)
