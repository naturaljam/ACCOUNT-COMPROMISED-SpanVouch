from __future__ import annotations

import ast
from pathlib import Path

from spanvouch.observability.tracing import build_test_tracer

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "spanvouch"
CANONICAL_SUPPORTLAB_SCOPE = "spanvouch.labs.supportlab"
LEGACY_SUPPORTLAB_SCOPE = "spanvouch.supportlab"


def _string_literal_locations(value: str) -> tuple[str, ...]:
    locations: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Constant) and node.value == value
            for node in ast.walk(tree)
        ):
            locations.append(path.relative_to(SOURCE_ROOT).as_posix())
    return tuple(locations)


def test_supportlab_tracer_uses_canonical_instrumentation_scope() -> None:
    tracer, exporter = build_test_tracer()

    with tracer.start_as_current_span("scope-probe"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].instrumentation_scope.name == CANONICAL_SUPPORTLAB_SCOPE


def test_runtime_identity_scan_rejects_legacy_supportlab_scope() -> None:
    assert _string_literal_locations(LEGACY_SUPPORTLAB_SCOPE) == ()
    assert _string_literal_locations(CANONICAL_SUPPORTLAB_SCOPE) == (
        "observability/tracing.py",
    )
