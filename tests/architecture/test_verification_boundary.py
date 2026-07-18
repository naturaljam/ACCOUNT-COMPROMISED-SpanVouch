from pathlib import Path


def test_verification_module_is_populated_with_deterministic_core() -> None:
    root = Path("src/spanvouch/verification")
    expected = {
        "deterministic.py",
        "invariants.py",
        "invariant_engine.py",
        "verdicts.py",
    }
    assert expected <= {path.name for path in root.glob("*.py")}


def test_verification_module_does_not_import_review_or_infrastructure() -> None:
    root = Path("src/spanvouch/verification")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    forbidden = (
        "spanvouch.review",
        "fastapi",
        "langgraph",
        "sqlite3",
        "spanvouch.labs",
        "spanvouch.evaluation",
    )
    assert not {name for name in forbidden if name in source}
