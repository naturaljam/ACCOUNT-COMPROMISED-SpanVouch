from decimal import Decimal

import pytest
from pydantic import ValidationError

from afc.supportlab.models import Order, OrderItem, OrderStatus, RefundRecord
from afc.supportlab.repository import build_seed_repository


def test_order_rejects_duplicate_item_skus() -> None:
    duplicate_items = (
        OrderItem(sku="sku-duplicate", quantity=1, unit_price=Decimal("10.00")),
        OrderItem(sku="sku-duplicate", quantity=2, unit_price=Decimal("20.00")),
    )

    with pytest.raises(ValidationError, match="duplicate item SKUs"):
        Order(
            order_id="order-duplicate-skus",
            customer_id="cust-001",
            policy_id="standard",
            status=OrderStatus.DELIVERED,
            items=duplicate_items,
        )


@pytest.mark.asyncio
async def test_seed_repository_links_customer_order_and_policy() -> None:
    repository = build_seed_repository()

    customer = await repository.get_customer("cust-001")
    order = await repository.get_order("order-001")
    policy = await repository.get_policy("standard")

    assert customer.customer_id == order.customer_id
    assert order.status is OrderStatus.DELIVERED
    assert policy.max_refund == Decimal("100.00")


@pytest.mark.asyncio
async def test_refund_write_is_idempotent() -> None:
    repository = build_seed_repository()
    refund = RefundRecord(
        refund_id="refund-001",
        order_id="order-001",
        amount=Decimal("19.99"),
        reason="damaged item",
        idempotency_key="idem-001",
        approved_by="reviewer@example.test",
    )

    first = await repository.save_refund(refund)
    second = await repository.save_refund(refund)

    assert first == second
    assert len(await repository.list_refunds("order-001")) == 1
