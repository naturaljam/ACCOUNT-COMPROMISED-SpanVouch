from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from spanvouch.labs.supportlab.models import Order, RefundPolicy


class PolicyViolation(StrEnum):
    CUSTOMER_MISMATCH = "customer_mismatch"
    STATUS_NOT_REFUNDABLE = "status_not_refundable"
    AMOUNT_EXCEEDS_CALCULATION = "amount_exceeds_calculation"
    AMOUNT_EXCEEDS_POLICY = "amount_exceeds_policy"
    MISSING_APPROVAL = "missing_approval"


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    approved_by: str


class RefundDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    allowed: bool
    violations: tuple[PolicyViolation, ...]


def evaluate_refund(
    *,
    customer_id: str,
    order: Order,
    policy: RefundPolicy,
    requested_amount: Decimal,
    calculated_amount: Decimal,
    approval: Approval | None,
) -> RefundDecision:
    violations: list[PolicyViolation] = []
    if customer_id != order.customer_id:
        violations.append(PolicyViolation.CUSTOMER_MISMATCH)
    if order.status not in policy.refundable_statuses:
        violations.append(PolicyViolation.STATUS_NOT_REFUNDABLE)
    if requested_amount > calculated_amount:
        violations.append(PolicyViolation.AMOUNT_EXCEEDS_CALCULATION)
    if requested_amount > policy.max_refund:
        violations.append(PolicyViolation.AMOUNT_EXCEEDS_POLICY)
    if policy.requires_approval and approval is None:
        violations.append(PolicyViolation.MISSING_APPROVAL)
    return RefundDecision(allowed=not violations, violations=tuple(violations))
