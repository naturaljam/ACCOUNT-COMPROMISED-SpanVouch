from decimal import Decimal, InvalidOperation

from spanvouch.contracts.diagnosis import EvidenceRef, EvidenceSelector
from spanvouch.contracts.trace import DiagnosticSpan
from spanvouch.failure_types import FailureType
from spanvouch.verification.invariants import (
    InvariantResult,
    InvariantRule,
    InvariantStatus,
    RuleContext,
    RuleScope,
    Severity,
)

KNOWN_TOOLS = frozenset(
    {
        "get_customer",
        "get_order",
        "get_refund_policy",
        "calculate_refund",
        "submit_refund",
        "handoff_to_human",
    }
)


def _evidence(
    context: RuleContext, span: DiagnosticSpan, field_path: str, description: str
) -> EvidenceRef:
    return context.evidence.resolve(
        EvidenceSelector(span_id=span.span_id, field_path=field_path),
        description=description,
    )


def _result(
    rule: InvariantRule,
    *,
    status: InvariantStatus,
    explanation: str,
    failure_type: FailureType | None = None,
    evidence: tuple[EvidenceRef, ...] = (),
    scope: RuleScope = RuleScope.SUPPORTED,
) -> InvariantResult:
    failed = status is InvariantStatus.FAILED
    return InvariantResult(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        status=status,
        severity=Severity.ERROR if failed else Severity.INFO,
        failure_type=failure_type,
        scope=scope,
        evidence=evidence,
        explanation=explanation,
        hard_failure=failed,
    )


def _spans_named(context: RuleContext, name: str) -> tuple[DiagnosticSpan, ...]:
    return tuple(span for span in context.view.spans if span.name == name)


class KnownToolRule:
    rule_id = "tool.name.known"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        unknown = next(
            (
                span
                for span in context.view.spans
                if span.kind.value == "tool" and span.name not in KNOWN_TOOLS
            ),
            None,
        )
        if unknown is None:
            return _result(self, status=InvariantStatus.PASSED, explanation="All tools are known.")
        return _result(
            self,
            status=InvariantStatus.FAILED,
            failure_type=FailureType.WRONG_TOOL,
            evidence=(
                _evidence(context, unknown, "name", "Unknown tool name."),
                _evidence(context, unknown, "status", "Unknown tool call status."),
            ),
            explanation="The trace invokes a tool outside the SupportLab registry.",
        )


class SubmitRefundArgumentsRule:
    rule_id = "submit_refund.arguments"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        submits = _spans_named(context, "submit_refund")
        calculations = _spans_named(context, "calculate_refund")
        if not submits or not calculations:
            return _result(
                self,
                status=InvariantStatus.NOT_APPLICABLE,
                explanation="Refund submission or calculation is absent.",
            )
        submit, calculation = submits[-1], calculations[-1]
        amount = submit.attributes.get("tool.arguments.amount")
        calculated = calculation.attributes.get("tool.result")
        error = submit.attributes.get("tool.error.message")
        try:
            mismatch = Decimal(str(amount)) != Decimal(str(calculated))
        except InvalidOperation:
            mismatch = True
        if mismatch or (isinstance(error, str) and "amount_exceeds_calculation" in error):
            return _result(
                self,
                status=InvariantStatus.FAILED,
                failure_type=FailureType.INVALID_ARGUMENT,
                evidence=(
                    _evidence(
                        context,
                        submit,
                        "attributes.tool.arguments.amount",
                        "Submitted refund amount.",
                    ),
                    _evidence(
                        context,
                        calculation,
                        "attributes.tool.result",
                        "Server-calculated refund amount.",
                    ),
                ),
                explanation="The submitted amount differs from the server calculation.",
            )
        return _result(
            self,
            status=InvariantStatus.PASSED,
            explanation="Refund arguments match the server calculation.",
        )


class SubmitRefundPolicyRule:
    rule_id = "submit_refund.policy"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        submits = _spans_named(context, "submit_refund")
        if not submits:
            return _result(
                self,
                status=InvariantStatus.NOT_APPLICABLE,
                explanation="No refund submission is present.",
            )
        submit = submits[-1]
        approval = submit.attributes.get("tool.arguments.approval")
        error_type = submit.attributes.get("tool.error.type")
        error = submit.attributes.get("tool.error.message")
        if approval == "none" and error_type == "RefundRejected" and error == "missing_approval":
            return _result(
                self,
                status=InvariantStatus.FAILED,
                failure_type=FailureType.POLICY_VIOLATION,
                evidence=(
                    _evidence(
                        context,
                        submit,
                        "attributes.tool.arguments.approval",
                        "Refund approval value.",
                    ),
                    _evidence(
                        context,
                        submit,
                        "attributes.tool.error.message",
                        "Policy rejection reason.",
                    ),
                ),
                explanation="The refund was attempted without required approval.",
            )
        return _result(
            self, status=InvariantStatus.PASSED, explanation="No policy violation found."
        )


class StepBudgetRule:
    rule_id = "run.step_budget"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        root = next(span for span in context.view.spans if span.parent_span_id is None)
        outcome = root.attributes.get("run.outcome")
        tool_spans = [span for span in context.view.spans if span.kind.value == "tool"]
        repeated = [
            span for span in tool_spans if sum(item.name == span.name for item in tool_spans) > 1
        ]
        if outcome == "step_limit" and repeated:
            last = repeated[-1]
            return _result(
                self,
                status=InvariantStatus.FAILED,
                failure_type=FailureType.LOOP_OR_BUDGET_EXHAUSTION,
                evidence=(
                    _evidence(context, last, "name", "Last repeated tool call."),
                    _evidence(context, root, "attributes.run.outcome", "Run outcome."),
                ),
                explanation="Repeated tool calls exhausted the step budget.",
            )
        return _result(self, status=InvariantStatus.PASSED, explanation="Step budget is intact.")


class FinalStateRule:
    rule_id = "run.final_state"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        root = next(span for span in context.view.spans if span.parent_span_id is None)
        submits = _spans_named(context, "submit_refund")
        message = root.attributes.get("run.final_message")
        if (
            root.attributes.get("run.outcome") == "succeeded"
            and submits
            and submits[-1].status.value == "ok"
            and isinstance(message, str)
            and "without a refund record" in message
        ):
            return _result(
                self,
                status=InvariantStatus.FAILED,
                failure_type=FailureType.INVALID_FINAL_STATE,
                evidence=(
                    _evidence(
                        context,
                        root,
                        "attributes.run.final_message",
                        "Contradictory final message.",
                    ),
                    _evidence(
                        context,
                        submits[-1],
                        "attributes.tool.result",
                        "Recorded refund result.",
                    ),
                ),
                explanation="The final message contradicts the successful refund result.",
            )
        return _result(
            self, status=InvariantStatus.PASSED, explanation="Final state is consistent."
        )


class MissingPreconditionGuard:
    rule_id = "scope.missing_precondition"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        submits = _spans_named(context, "submit_refund")
        policies = _spans_named(context, "get_refund_policy")
        if submits and not policies:
            return _result(
                self,
                status=InvariantStatus.FAILED,
                evidence=(
                    _evidence(
                        context,
                        submits[-1],
                        "name",
                        "High-risk call made without a policy lookup.",
                    ),
                ),
                scope=RuleScope.UNSUPPORTED_GUARD,
                explanation="A required precondition is missing and is outside MVP scope.",
            )
        return _result(
            self,
            status=InvariantStatus.PASSED,
            scope=RuleScope.UNSUPPORTED_GUARD,
            explanation="No missing-precondition scope signal found.",
        )


class IgnoredToolErrorGuard:
    rule_id = "scope.ignored_tool_error"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        root = next(span for span in context.view.spans if span.parent_span_id is None)
        failed_tools = tuple(
            span
            for span in context.view.spans
            if span.kind.value == "tool" and span.status.value == "error"
        )
        if root.attributes.get("run.outcome") == "succeeded" and failed_tools:
            failed = failed_tools[-1]
            return _result(
                self,
                status=InvariantStatus.FAILED,
                evidence=(
                    _evidence(context, failed, "status", "Failed tool status."),
                    _evidence(
                        context,
                        root,
                        "attributes.run.outcome",
                        "Run incorrectly reports success.",
                    ),
                ),
                scope=RuleScope.UNSUPPORTED_GUARD,
                explanation="A tool error was ignored and is outside MVP scope.",
            )
        return _result(
            self,
            status=InvariantStatus.PASSED,
            scope=RuleScope.UNSUPPORTED_GUARD,
            explanation="No ignored-tool-error scope signal found.",
        )


class ContextCorruptionGuard:
    rule_id = "scope.context_corruption"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        submits = _spans_named(context, "submit_refund")
        customers = _spans_named(context, "get_customer")
        if not submits:
            return _result(
                self,
                status=InvariantStatus.NOT_APPLICABLE,
                scope=RuleScope.UNSUPPORTED_GUARD,
                explanation="No refund submission is present.",
            )
        submit = submits[-1]
        submitted_customer = submit.attributes.get("tool.arguments.customer_id")
        known_customer = (
            customers[-1].attributes.get("tool.arguments.customer_id") if customers else None
        )
        error = submit.attributes.get("tool.error.message")
        if error == "customer_mismatch" or (
            known_customer is not None and submitted_customer != known_customer
        ):
            evidence = [
                _evidence(
                    context,
                    submit,
                    "attributes.tool.arguments.customer_id",
                    "Submitted customer identity.",
                )
            ]
            if error is not None:
                evidence.append(
                    _evidence(
                        context,
                        submit,
                        "attributes.tool.error.message",
                        "Identity mismatch error.",
                    )
                )
            return _result(
                self,
                status=InvariantStatus.FAILED,
                evidence=tuple(evidence),
                scope=RuleScope.UNSUPPORTED_GUARD,
                explanation="Customer context is inconsistent and outside MVP scope.",
            )
        return _result(
            self,
            status=InvariantStatus.PASSED,
            scope=RuleScope.UNSUPPORTED_GUARD,
            explanation="No context-corruption scope signal found.",
        )


def supported_rules() -> tuple[InvariantRule, ...]:
    return (
        KnownToolRule(),
        SubmitRefundArgumentsRule(),
        SubmitRefundPolicyRule(),
        StepBudgetRule(),
        FinalStateRule(),
    )


def unsupported_guards() -> tuple[InvariantRule, ...]:
    return (
        MissingPreconditionGuard(),
        IgnoredToolErrorGuard(),
        ContextCorruptionGuard(),
    )


def supportlab_rules() -> tuple[InvariantRule, ...]:
    return supported_rules() + unsupported_guards()
