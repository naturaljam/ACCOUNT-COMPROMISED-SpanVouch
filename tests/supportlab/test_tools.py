from decimal import Decimal

import pytest

from afc.supportlab.policy import Approval, PolicyViolation
from afc.supportlab.repository import build_seed_repository
from afc.supportlab.tools import RefundRejected, SupportTools


@pytest.mark.asyncio
async def test_submit_refund_rejects_missing_approval() -> None:
    tools = SupportTools(build_seed_repository())

    with pytest.raises(RefundRejected) as error:
        await tools.submit_refund(
            customer_id="cust-001",
            order_id="order-001",
            amount=Decimal("19.99"),
            calculated_amount=Decimal("19.99"),
            reason="damaged item",
            idempotency_key="run-1-refund",
            approval=None,
        )

    assert error.value.violations == (PolicyViolation.MISSING_APPROVAL,)


@pytest.mark.asyncio
async def test_submit_refund_is_idempotent_after_approval() -> None:
    repository = build_seed_repository()
    tools = SupportTools(repository)
    arguments = {
        "customer_id": "cust-001",
        "order_id": "order-001",
        "amount": Decimal("19.99"),
        "calculated_amount": Decimal("19.99"),
        "reason": "damaged item",
        "idempotency_key": "run-1-refund",
        "approval": Approval(approved_by="reviewer@example.test"),
    }

    first = await tools.submit_refund(**arguments)
    second = await tools.submit_refund(**arguments)

    assert first.refund_id == second.refund_id
    assert len(await repository.list_refunds("order-001")) == 1


@pytest.mark.asyncio
async def test_calculate_refund_uses_the_requested_item_set() -> None:
    tools = SupportTools(build_seed_repository())

    amount = await tools.calculate_refund("order-001", ("sku-red",))

    assert amount == Decimal("19.99")
    with pytest.raises(KeyError, match="sku-missing"):
        await tools.calculate_refund("order-001", ("sku-missing",))
