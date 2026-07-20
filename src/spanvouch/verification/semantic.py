from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictFloat,
    ValidationError,
    model_validator,
)

from spanvouch.contracts.diagnosis import ClaimStage, EvidenceSelector, ProviderUsage
from spanvouch.contracts.verification import (
    EvidenceGap,
    FindingCode,
    FindingSeverity,
    VerificationFinding,
    VerificationInput,
    VerifierKind,
    VerifierProvenance,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import (
    canonical_json,
    canonical_sha256,
)
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ModelProvider
from spanvouch.failure_types import FailureType
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.prompting import SemanticPromptBuilder

_SEMANTIC_POLICY_VERSION = "semantic-policy-v1"
_SEMANTIC_SCHEMA_VERSION = "semantic-verifier-schema-v1"
_INVALID_MESSAGE = "The semantic verifier returned structurally invalid output."
_GAP_INSTRUCTION = "Add decisive evidence for the cited diagnosis claim."


class _SemanticFindingDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Literal[
        FindingCode.ALTERNATIVE_HYPOTHESIS,
        FindingCode.SEMANTIC_SUPPORT_MISSING,
    ]
    message: str = Field(min_length=1, max_length=500)
    selectors: tuple[str, ...] = Field(min_length=1)


class _SemanticGapDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_code: Literal[FindingCode.SEMANTIC_SUPPORT_MISSING]
    claim_index: int = Field(ge=0)
    stage: ClaimStage
    required_evidence_kind: Literal["semantic_support"]
    selectors: tuple[str, ...] = Field(min_length=1)


class _SemanticDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: VerifierVerdict
    findings: tuple[_SemanticFindingDraft, ...] = Field(max_length=5)
    evidence_gaps: tuple[_SemanticGapDraft, ...] = Field(max_length=3)
    alternative_failure_type: FailureType | None
    confidence: StrictFloat = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        if self.verdict is VerifierVerdict.VERIFIED:
            if self.findings or self.evidence_gaps or self.alternative_failure_type is not None:
                raise ValueError("verified output cannot contain findings or alternatives")
        elif self.verdict is VerifierVerdict.NEEDS_EVIDENCE:
            if not self.evidence_gaps:
                raise ValueError("needs_evidence output requires at least one evidence gap")
            if self.alternative_failure_type is not None:
                raise ValueError("needs_evidence output cannot contain an alternative type")
            if not any(
                finding.code is FindingCode.SEMANTIC_SUPPORT_MISSING for finding in self.findings
            ):
                raise ValueError("needs_evidence output requires a support finding")
        elif self.evidence_gaps:
            raise ValueError("review_required output cannot contain evidence gaps")
        if self.alternative_failure_type is not None and not any(
            finding.code is FindingCode.ALTERNATIVE_HYPOTHESIS for finding in self.findings
        ):
            raise ValueError("alternative type requires an alternative finding")
        return self


def _stable_id(prefix: str, payload: object) -> str:
    digest = sha256(canonical_json(cast(JsonValue, payload)).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError("selector references must be unique")
    return tuple(sorted(materialized))


class SemanticVerifier:
    kind: str = VerifierKind.SEMANTIC

    def __init__(
        self,
        provider: ModelProvider,
        *,
        provider_id: str,
        model: str,
        prompt_version: str = "semantic-verifier-v1",
        generation: GenerationConfig | None = None,
        diagnosis_messages: tuple[ChatMessage, ...] = (),
    ) -> None:
        if not provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if not model.strip():
            raise ValueError("model must be non-empty")
        self._provider = provider
        self._provider_id = provider_id
        self._generation = (
            GenerationConfig.model_validate(generation.model_dump(mode="python"))
            if generation is not None
            else GenerationConfig(model=model)
        )
        if self._generation.model != model:
            raise ValueError("generation model must match semantic verifier model")
        self._prompt_version = prompt_version
        self._diagnosis_messages = tuple(
            ChatMessage.model_validate(message.model_dump(mode="python"))
            for message in diagnosis_messages
        )
        self._prompt_builder = SemanticPromptBuilder(prompt_version=prompt_version)
        version_source = f"{prompt_version}:{model}:{_SEMANTIC_SCHEMA_VERSION}"
        self.version_fingerprint = sha256(version_source.encode("utf-8")).hexdigest()

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        if canonical_sha256(input_.report) != input_.report_sha256:
            return self._preflight_invalid_report(input_)
        view = input_.snapshot.trace_view()
        catalog = EvidenceCatalog.from_view(view)
        if not self._report_evidence_is_consistent(input_, catalog):
            return self._preflight_invalid_report(input_)
        prepared = (
            self._prompt_builder.shared(
                self._diagnosis_messages, input_, catalog, self._generation
            )
            if self._diagnosis_messages
            else self._prompt_builder.isolated(input_, catalog, self._generation)
        )
        messages = prepared.messages
        prompt_sha256 = prepared.prompt_sha256
        started_at = datetime.now(UTC)
        response = await self._provider.complete(messages, prepared.generation)
        completed_at = datetime.now(UTC)
        provenance = VerifierProvenance(
            verifier_kind=self.kind,
            verifier_version=self.version_fingerprint,
            policy_version=_SEMANTIC_POLICY_VERSION,
            prompt_version=self._prompt_version,
            prompt_sha256=prompt_sha256,
            model=response.model,
            provider=self._provider_id,
        )
        run_seed = {
            "verifier_version": self.version_fingerprint,
            "input_sha256": input_.snapshot.input_sha256,
            "report_sha256": input_.report_sha256,
            "revision_number": input_.revision_number,
            "prompt_sha256": prompt_sha256,
            "request_id": response.usage.request_id,
        }
        run_id = _stable_id("verifier", run_seed)
        if response.finish_reason != "stop" or not response.content.strip():
            return self._invalid_report(
                run_id,
                input_.report_sha256,
                input_.revision_number,
                provenance,
                response.usage,
                started_at,
                completed_at,
            )
        try:
            draft = _SemanticDraft.model_validate_json(response.content)
            return self._resolve_draft(
                draft,
                input_,
                catalog,
                run_id,
                provenance,
                response.usage,
                started_at,
                completed_at,
            )
        except (KeyError, ValidationError, ValueError):
            return self._invalid_report(
                run_id,
                input_.report_sha256,
                input_.revision_number,
                provenance,
                response.usage,
                started_at,
                completed_at,
            )

    @staticmethod
    def _report_evidence_is_consistent(
        input_: VerificationInput,
        catalog: EvidenceCatalog,
    ) -> bool:
        report = input_.report
        if report.trace_id != input_.snapshot.trace_id or report.run_id != input_.snapshot.run_id:
            return False
        legal_selectors = set(catalog.selectors)
        evidence_ids = tuple(evidence.evidence_id for evidence in report.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            return False
        evidence_by_id = {evidence.evidence_id: evidence for evidence in report.evidence}
        for evidence in report.evidence:
            if evidence.canonical not in legal_selectors:
                return False
            resolved = catalog.resolve(
                EvidenceSelector(
                    span_id=evidence.span_id,
                    field_path=evidence.field_path,
                ),
                description=evidence.description,
            )
            if resolved.evidence_id != evidence.evidence_id:
                return False
        for claim in report.causal_chain:
            if len(claim.evidence_ids) != len(set(claim.evidence_ids)):
                return False
            if any(evidence_id not in evidence_by_id for evidence_id in claim.evidence_ids):
                return False
        return True

    def _preflight_invalid_report(self, input_: VerificationInput) -> VerifierReport:
        run_id = _stable_id(
            "verifier",
            {
                "verifier_version": self.version_fingerprint,
                "input_sha256": input_.snapshot.input_sha256,
                "report_sha256": input_.report_sha256,
                "revision_number": input_.revision_number,
                "failure": "invalid_verifier_input",
            },
        )
        provenance = VerifierProvenance(
            verifier_kind=self.kind,
            verifier_version=self.version_fingerprint,
            policy_version=_SEMANTIC_POLICY_VERSION,
            model=self._generation.model,
            provider=self._provider_id,
        )
        return self._invalid_report(
            run_id,
            input_.report_sha256,
            input_.revision_number,
            provenance,
            None,
            input_.snapshot.created_at,
            input_.snapshot.created_at,
        )

    def _resolve_draft(
        self,
        draft: _SemanticDraft,
        input_: VerificationInput,
        catalog: EvidenceCatalog,
        run_id: str,
        provenance: VerifierProvenance,
        usage: ProviderUsage,
        started_at: datetime,
        completed_at: datetime,
    ) -> VerifierReport:
        legal_selectors = set(catalog.selectors)
        findings: list[VerificationFinding] = []
        for index, finding_draft in enumerate(draft.findings):
            selectors = _sorted_unique(finding_draft.selectors)
            if not set(selectors) <= legal_selectors:
                raise KeyError("unknown finding selector")
            related_span_ids = tuple(
                sorted({selector.partition("::")[0] for selector in selectors})
            )
            finding_seed = {
                "run_id": run_id,
                "index": index,
                "code": finding_draft.code.value,
                "selectors": selectors,
            }
            findings.append(
                VerificationFinding(
                    finding_id=_stable_id("finding", finding_seed),
                    code=finding_draft.code,
                    severity=(
                        FindingSeverity.HARD
                        if finding_draft.code is FindingCode.SEMANTIC_SUPPORT_MISSING
                        else FindingSeverity.ADVISORY
                    ),
                    message=finding_draft.message,
                    revisable=(
                        draft.verdict is VerifierVerdict.NEEDS_EVIDENCE
                        and finding_draft.code is FindingCode.SEMANTIC_SUPPORT_MISSING
                    ),
                    related_selectors=selectors,
                    related_span_ids=related_span_ids,
                )
            )
        gaps: list[EvidenceGap] = []
        for index, gap_draft in enumerate(draft.evidence_gaps):
            if gap_draft.claim_index >= len(input_.report.causal_chain):
                raise KeyError("unknown diagnosis claim")
            claim = input_.report.causal_chain[gap_draft.claim_index]
            if gap_draft.stage is not claim.stage:
                raise ValueError("evidence gap stage does not match diagnosis claim")
            selectors = _sorted_unique(gap_draft.selectors)
            if not set(selectors) <= legal_selectors:
                raise KeyError("unknown gap selector")
            related_span_ids = tuple(
                sorted({selector.partition("::")[0] for selector in selectors})
            )
            gap_seed = {
                "run_id": run_id,
                "index": index,
                "claim_index": gap_draft.claim_index,
                "selectors": selectors,
            }
            gaps.append(
                EvidenceGap(
                    gap_id=_stable_id("gap", gap_seed),
                    finding_code=gap_draft.finding_code,
                    claim_index=gap_draft.claim_index,
                    stage=gap_draft.stage,
                    required_evidence_kind=gap_draft.required_evidence_kind,
                    allowed_selectors=selectors,
                    related_span_ids=related_span_ids,
                    instruction=_GAP_INSTRUCTION,
                )
            )
        return VerifierReport(
            verifier_run_id=run_id,
            revision_number=input_.revision_number,
            report_sha256=input_.report_sha256,
            verifier_kind=self.kind,
            verdict=draft.verdict,
            findings=tuple(findings),
            evidence_gaps=tuple(gaps),
            alternative_failure_type=draft.alternative_failure_type,
            confidence=float(draft.confidence),
            provenance=provenance,
            usage=usage,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _invalid_report(
        self,
        run_id: str,
        report_sha256: str,
        revision_number: int,
        provenance: VerifierProvenance,
        usage: ProviderUsage | None,
        started_at: datetime,
        completed_at: datetime,
    ) -> VerifierReport:
        finding = VerificationFinding(
            finding_id=_stable_id(
                "finding",
                {"run_id": run_id, "code": FindingCode.INVALID_VERIFIER_OUTPUT.value},
            ),
            code=FindingCode.INVALID_VERIFIER_OUTPUT,
            severity=FindingSeverity.HARD,
            message=_INVALID_MESSAGE,
            revisable=False,
        )
        return VerifierReport(
            verifier_run_id=run_id,
            revision_number=revision_number,
            report_sha256=report_sha256,
            verifier_kind=self.kind,
            verdict=VerifierVerdict.REVIEW_REQUIRED,
            findings=(finding,),
            provenance=provenance,
            usage=usage,
            started_at=started_at,
            completed_at=completed_at,
        )
