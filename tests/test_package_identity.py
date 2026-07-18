from __future__ import annotations

import importlib.util
import subprocess
import sys

import spanvouch


def test_spanvouch_is_the_only_public_import_root() -> None:
    legacy_import = "af" + "c"
    assert spanvouch.__name__ == "spanvouch"
    assert importlib.util.find_spec(legacy_import) is None


def test_clean_interpreter_cannot_import_afc() -> None:
    legacy_import = "af" + "c"
    completed = subprocess.run(
        [sys.executable, "-c", "import " + legacy_import],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "No module named '" + legacy_import + "'" in completed.stderr
