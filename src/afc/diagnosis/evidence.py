import json
from collections.abc import Mapping
from hashlib import sha256
from types import MappingProxyType

from pydantic import JsonValue

from afc.diagnosis.models import EvidenceRef, EvidenceSelector
from afc.diagnosis.trace_view import DiagnosticTraceView


def canonical_json(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class EvidenceCatalog:
    def __init__(self, values: Mapping[str, JsonValue]) -> None:
        self._values = MappingProxyType(dict(values))

    @classmethod
    def from_view(cls, view: DiagnosticTraceView) -> "EvidenceCatalog":
        values: dict[str, JsonValue] = {}
        for span in view.spans:
            prefix = f"{span.span_id}::"
            span_values: dict[str, JsonValue] = {
                "name": span.name,
                "kind": span.kind.value,
                "status": span.status.value,
                "started_at": span.started_at.isoformat(),
                "ended_at": span.ended_at.isoformat(),
            }
            if span.parent_span_id is not None:
                span_values["parent_span_id"] = span.parent_span_id
            for key, value in span.attributes.items():
                span_values[f"attributes.{key}"] = value
            for field_path, value in span_values.items():
                selector = f"{prefix}{field_path}"
                if selector in values:
                    raise ValueError(f"duplicate evidence selector: {selector}")
                values[selector] = value
        return cls(dict(sorted(values.items())))

    @property
    def selectors(self) -> tuple[str, ...]:
        return tuple(self._values)

    def resolve(self, selector: EvidenceSelector, *, description: str) -> EvidenceRef:
        canonical_selector = selector.canonical
        value = self._values[canonical_selector]
        return EvidenceRef(
            evidence_id=f"ev-{sha256(canonical_selector.encode('utf-8')).hexdigest()[:16]}",
            span_id=selector.span_id,
            field_path=selector.field_path,
            observed_value=value,
            value_sha256=sha256(canonical_json(value).encode("utf-8")).hexdigest(),
            description=description,
        )
