from pathlib import Path

from afc.evals.run_diagnosis_eval import main


def test_rule_cli_writes_byte_exact_artifact(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert main(["--output", str(first)]) == 0
    assert main(["--output", str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
