from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from afc.supportlab.models import Customer, Order, RefundPolicy, RefundRecord
from afc.supportlab.policy import Approval, PolicyViolation, evaluate_refund
from afc.supportlab.repository import SupportRepository


class RefundRejected(RuntimeError):
    def __init__(self, violations: tuple[PolicyViolation, ...]) -> None:
        super().__init__(",".join(violations))
        self.violations = violations


class SupportTools:
    def __init__(self, repository: SupportRepository) -> None:
        self._repository = repository

    async def get_customer(self, customer_id: str) -> Customer:
        return await self._repository.get_customer(customer_id)

    async def get_order(self, order_id: str) -> Order:
        return await self._repository.get_order(order_id)

    async def get_refund_policy(self, order_id: str) -> RefundPolicy:
        order = await self._repository.get_order(order_id)
        return await self._repository.get_policy(order.policy_id)

    async def calculate_refund(self, order_id: str, item_skus: tuple[str, ...]) -> Decimal:
        order = await self._repository.get_order(order_id)
        items_by_sku = {item.sku: item for item in order.items}
        missing = [sku for sku in item_skus if sku not in items_by_sku]
        if missing:
            raise KeyError(",".join(missing))
        return sum((items_by_sku[sku].subtotal for sku in item_skus), start=Decimal("0"))

    async def submit_refund(
        self,
        *,
        customer_id: str,
        order_id: str,
        amount: Decimal,
        calculated_amount: Decimal,
        reason: str,
        idempotency_key: str,
        approval: Approval | None,
    ) -> RefundRecord:
        order = await self._repository.get_order(order_id)
        policy = await self._repository.get_policy(order.policy_id)
        decision = evaluate_refund(
            customer_id=customer_id,
            order=order,
            policy=policy,
            requested_amount=amount,
            calculated_amount=calculated_amount,
            approval=approval,
        )
        if not decision.allowed:
            raise RefundRejected(decision.violations)
        assert approval is not None
        refund_id = str(uuid5(NAMESPACE_URL, idempotency_key))
        return await self._repository.save_refund(
            RefundRecord(
                refund_id=refund_id,
                order_id=order_id,
                amount=amount,
                reason=reason,
                idempotency_key=idempotency_key,
                approved_by=approval.approved_by,
            )
        )
