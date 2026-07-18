from collections.abc import Mapping
from hashlib import sha256
from types import MappingProxyType

from pydantic import JsonValue

from spanvouch.contracts.trace import DiagnosticContext, DiagnosticTraceView
from spanvouch.contracts.versioning import (
    canonical_json as canonical_json,
)
from spanvouch.contracts.versioning import (
    canonical_sha256 as canonical_sha256,
)
from spanvouch.diagnosis.models import EvidenceRef, EvidenceSelector


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

    @classmethod
    def from_context(cls, context: DiagnosticContext) -> "EvidenceCatalog":
        return cls.from_view(context.view)

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
            value_sha256=canonical_sha256(value),
            description=description,
        )
