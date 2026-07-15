from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase_1_delivery_configuration_is_reproducible() -> None:
    required_files = (
        ".dockerignore",
        "Dockerfile",
        "compose.yaml",
        ".github/workflows/ci.yml",
        "README.md",
        "docs/architecture/adr-001-traceir-boundary.md",
    )
    missing = [path for path in required_files if not (ROOT / path).is_file()]
    assert not missing, f"missing Phase 1 delivery files: {missing}"

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "arizephoenix/phoenix:latest" not in compose
    assert re.search(r"arizephoenix/phoenix@sha256:[0-9a-f]{64}", compose)

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Verify frozen dataset hashes" in workflow
    assert "docker compose config --quiet" in workflow
