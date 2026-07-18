from decimal import Decimal

from spanvouch.supportlab.models import OrderStatus
from spanvouch.supportlab.policy import Approval, PolicyViolation, evaluate_refund
from spanvouch.supportlab.repository import build_seed_repository


async def test_refund_requires_all_preconditions() -> None:
    repository = build_seed_repository()
    order = await repository.get_order("order-001")
    policy = await repository.get_policy(order.policy_id)

    decision = evaluate_refund(
        customer_id="cust-001",
        order=order,
        policy=policy,
        requested_amount=Decimal("19.99"),
        calculated_amount=Decimal("19.99"),
        approval=None,
    )

    assert not decision.allowed
    assert decision.violations == (PolicyViolation.MISSING_APPROVAL,)


async def test_refund_rejects_customer_amount_and_status_violations() -> None:
    repository = build_seed_repository()
    order = (await repository.get_order("order-001")).model_copy(
        update={"status": OrderStatus.CANCELLED}
    )
    policy = await repository.get_policy(order.policy_id)

    decision = evaluate_refund(
        customer_id="cust-other",
        order=order,
        policy=policy,
        requested_amount=Decimal("200.00"),
        calculated_amount=Decimal("19.99"),
        approval=Approval(approved_by="reviewer@example.test"),
    )

    assert set(decision.violations) == {
        PolicyViolation.CUSTOMER_MISMATCH,
        PolicyViolation.STATUS_NOT_REFUNDABLE,
        PolicyViolation.AMOUNT_EXCEEDS_CALCULATION,
        PolicyViolation.AMOUNT_EXCEEDS_POLICY,
    }
