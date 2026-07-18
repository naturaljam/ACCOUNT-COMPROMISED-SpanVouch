from __future__ import annotations

import importlib.util
import subprocess
import sys

import spanvouch


def test_spanvouch_is_the_only_public_import_root() -> None:
    assert spanvouch.__name__ == "spanvouch"
    assert importlib.util.find_spec("afc") is None


def test_clean_interpreter_cannot_import_afc() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import afc"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "No module named 'afc'" in completed.stderr
