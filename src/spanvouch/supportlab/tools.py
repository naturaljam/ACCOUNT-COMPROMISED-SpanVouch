from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from spanvouch.supportlab.models import Customer, Order, RefundPolicy, RefundRecord
from spanvouch.supportlab.policy import Approval, PolicyViolation, evaluate_refund
from spanvouch.supportlab.repository import SupportRepository


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
        if not item_skus:
            raise ValueError("at least one item SKU is required")
        if len(set(item_skus)) != len(item_skus):
            raise ValueError("duplicate item SKUs are not allowed")
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
        item_skus: tuple[str, ...],
        reason: str,
        idempotency_key: str,
        approval: Approval | None,
        calculated_amount: Decimal | None = None,
    ) -> RefundRecord:
        """Persist a policy-compliant refund using a server-side SKU calculation.

        ``calculated_amount`` is a deprecated, ignored compatibility argument.
        ``item_skus`` is required so authorization never widens a missing selection
        to the whole order.
        """
        order = await self._repository.get_order(order_id)
        policy = await self._repository.get_policy(order.policy_id)
        trusted_calculated_amount = await self.calculate_refund(order_id, item_skus)
        decision = evaluate_refund(
            customer_id=customer_id,
            order=order,
            policy=policy,
            requested_amount=amount,
            calculated_amount=trusted_calculated_amount,
            approval=approval,
        )
        if not decision.allowed:
            raise RefundRejected(decision.violations)
        order_namespace = uuid5(NAMESPACE_URL, order_id)
        refund_id = str(uuid5(order_namespace, idempotency_key))
        return await self._repository.save_refund(
            RefundRecord(
                refund_id=refund_id,
                order_id=order_id,
                amount=amount,
                reason=reason,
                idempotency_key=idempotency_key,
                approved_by=approval.approved_by if approval is not None else None,
            )
        )
