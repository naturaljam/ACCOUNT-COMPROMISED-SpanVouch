from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from afc.supportlab.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    RefundPolicy,
    RefundRecord,
)


class SupportRepository(Protocol):
    async def get_customer(self, customer_id: str) -> Customer: ...
    async def get_order(self, order_id: str) -> Order: ...
    async def get_policy(self, policy_id: str) -> RefundPolicy: ...
    async def save_refund(self, refund: RefundRecord) -> RefundRecord: ...
    async def list_refunds(self, order_id: str) -> Sequence[RefundRecord]: ...


class InMemorySupportRepository:
    def __init__(
        self,
        customers: Sequence[Customer],
        orders: Sequence[Order],
        policies: Sequence[RefundPolicy],
    ) -> None:
        self._customers = {item.customer_id: item for item in customers}
        self._orders = {item.order_id: item for item in orders}
        self._policies = {item.policy_id: item for item in policies}
        self._refunds_by_key: dict[str, RefundRecord] = {}

    async def get_customer(self, customer_id: str) -> Customer:
        return self._customers[customer_id]

    async def get_order(self, order_id: str) -> Order:
        return self._orders[order_id]

    async def get_policy(self, policy_id: str) -> RefundPolicy:
        return self._policies[policy_id]

    async def save_refund(self, refund: RefundRecord) -> RefundRecord:
        existing = self._refunds_by_key.get(refund.idempotency_key)
        if existing is not None:
            return existing
        self._refunds_by_key[refund.idempotency_key] = refund
        return refund

    async def list_refunds(self, order_id: str) -> Sequence[RefundRecord]:
        return tuple(
            refund for refund in self._refunds_by_key.values() if refund.order_id == order_id
        )


def build_seed_repository() -> InMemorySupportRepository:
    customer = Customer(customer_id="cust-001", name="Demo Customer")
    order = Order(
        order_id="order-001",
        customer_id=customer.customer_id,
        policy_id="standard",
        status=OrderStatus.DELIVERED,
        items=(OrderItem(sku="sku-red", quantity=1, unit_price=Decimal("19.99")),),
    )
    policy = RefundPolicy(
        policy_id="standard",
        refundable_statuses=frozenset({OrderStatus.DELIVERED}),
        max_refund=Decimal("100.00"),
    )
    return InMemorySupportRepository([customer], [order], [policy])
