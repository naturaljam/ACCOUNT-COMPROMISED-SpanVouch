from decimal import Decimal

import pytest

from afc.supportlab.policy import Approval, PolicyViolation
from afc.supportlab.repository import InMemorySupportRepository, build_seed_repository
from afc.supportlab.tools import RefundRejected, SupportTools


@pytest.mark.asyncio
async def test_submit_refund_requires_item_skus() -> None:
    tools = SupportTools(build_seed_repository())

    with pytest.raises(TypeError, match="item_skus"):
        await tools.submit_refund(
            customer_id="cust-001",
            order_id="order-001",
            amount=Decimal("19.99"),
            calculated_amount=Decimal("19.99"),
            reason="damaged item",
            idempotency_key="missing-item-selection",
            approval=Approval(approved_by="reviewer@example.test"),
        )


@pytest.mark.asyncio
async def test_submit_refund_does_not_require_deprecated_calculated_amount() -> None:
    tools = SupportTools(build_seed_repository())

    refund = await tools.submit_refund(
        customer_id="cust-001",
        order_id="order-001",
        amount=Decimal("19.99"),
        item_skus=("sku-red",),
        reason="damaged item",
        idempotency_key="no-deprecated-calculation",
        approval=Approval(approved_by="reviewer@example.test"),
    )

    assert refund.amount == Decimal("19.99")


@pytest.mark.asyncio
async def test_submit_refund_rejects_missing_approval() -> None:
    tools = SupportTools(build_seed_repository())

    with pytest.raises(RefundRejected) as error:
        await tools.submit_refund(
            customer_id="cust-001",
            order_id="order-001",
            amount=Decimal("19.99"),
            calculated_amount=Decimal("19.99"),
            item_skus=("sku-red",),
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
        "item_skus": ("sku-red",),
        "reason": "damaged item",
        "idempotency_key": "run-1-refund",
        "approval": Approval(approved_by="reviewer@example.test"),
    }

    first = await tools.submit_refund(**arguments)
    second = await tools.submit_refund(**arguments)

    assert first.refund_id == second.refund_id
    assert len(await repository.list_refunds("order-001")) == 1


@pytest.mark.asyncio
async def test_submit_refund_rejects_forged_calculated_amount() -> None:
    repository = build_seed_repository()
    tools = SupportTools(repository)

    with pytest.raises(RefundRejected) as error:
        await tools.submit_refund(
            customer_id="cust-001",
            order_id="order-001",
            amount=Decimal("100.00"),
            calculated_amount=Decimal("100.00"),
            reason="damaged item",
            idempotency_key="run-forged-calculation",
            approval=Approval(approved_by="reviewer@example.test"),
            item_skus=("sku-red",),
        )

    assert error.value.violations == (PolicyViolation.AMOUNT_EXCEEDS_CALCULATION,)
    assert await repository.list_refunds("order-001") == ()


@pytest.mark.asyncio
async def test_idempotency_key_is_scoped_to_the_order() -> None:
    seed_repository = build_seed_repository()
    customer = await seed_repository.get_customer("cust-001")
    first_order = await seed_repository.get_order("order-001")
    second_order = first_order.model_copy(update={"order_id": "order-002"})
    policy = await seed_repository.get_policy(first_order.policy_id)
    repository = InMemorySupportRepository(
        [customer], [first_order, second_order], [policy]
    )
    tools = SupportTools(repository)
    shared_arguments = {
        "customer_id": "cust-001",
        "amount": Decimal("19.99"),
        "calculated_amount": Decimal("19.99"),
        "item_skus": ("sku-red",),
        "reason": "damaged item",
        "idempotency_key": "shared-refund-key",
        "approval": Approval(approved_by="reviewer@example.test"),
    }

    first = await tools.submit_refund(order_id="order-001", **shared_arguments)
    second = await tools.submit_refund(order_id="order-002", **shared_arguments)

    assert first.order_id == "order-001"
    assert second.order_id == "order-002"
    assert first.refund_id != second.refund_id
    assert len(await repository.list_refunds("order-001")) == 1
    assert len(await repository.list_refunds("order-002")) == 1


@pytest.mark.asyncio
async def test_refund_id_uses_unambiguous_order_and_key_structure() -> None:
    seed_repository = build_seed_repository()
    customer = await seed_repository.get_customer("cust-001")
    seed_order = await seed_repository.get_order("order-001")
    colon_order = seed_order.model_copy(update={"order_id": "a:b"})
    plain_order = seed_order.model_copy(update={"order_id": "a"})
    policy = await seed_repository.get_policy(seed_order.policy_id)
    repository = InMemorySupportRepository(
        [customer], [colon_order, plain_order], [policy]
    )
    tools = SupportTools(repository)
    shared_arguments = {
        "customer_id": "cust-001",
        "amount": Decimal("19.99"),
        "item_skus": ("sku-red",),
        "reason": "damaged item",
        "approval": Approval(approved_by="reviewer@example.test"),
    }

    first = await tools.submit_refund(
        order_id="a:b", idempotency_key="c", **shared_arguments
    )
    second = await tools.submit_refund(
        order_id="a", idempotency_key="b:c", **shared_arguments
    )

    assert first.refund_id != second.refund_id
    assert len(await repository.list_refunds("a:b")) == 1
    assert len(await repository.list_refunds("a")) == 1


@pytest.mark.asyncio
async def test_submit_refund_allows_policy_without_approval() -> None:
    seed_repository = build_seed_repository()
    customer = await seed_repository.get_customer("cust-001")
    order = await seed_repository.get_order("order-001")
    policy = (await seed_repository.get_policy(order.policy_id)).model_copy(
        update={"requires_approval": False}
    )
    repository = InMemorySupportRepository([customer], [order], [policy])
    tools = SupportTools(repository)

    refund = await tools.submit_refund(
        customer_id="cust-001",
        order_id="order-001",
        amount=Decimal("19.99"),
        calculated_amount=Decimal("19.99"),
        item_skus=("sku-red",),
        reason="damaged item",
        idempotency_key="approval-waived",
        approval=None,
    )

    assert refund.approved_by is None
    assert await repository.list_refunds("order-001") == (refund,)


@pytest.mark.asyncio
async def test_calculate_refund_uses_the_requested_item_set() -> None:
    tools = SupportTools(build_seed_repository())

    amount = await tools.calculate_refund("order-001", ("sku-red",))

    assert amount == Decimal("19.99")
    with pytest.raises(KeyError, match="sku-missing"):
        await tools.calculate_refund("order-001", ("sku-missing",))


@pytest.mark.parametrize(
    ("item_skus", "message"),
    [
        (("sku-red", "sku-red"), "duplicate"),
        ((), "at least one"),
    ],
)
@pytest.mark.asyncio
async def test_calculate_refund_rejects_invalid_item_sets(
    item_skus: tuple[str, ...], message: str
) -> None:
    tools = SupportTools(build_seed_repository())

    with pytest.raises(ValueError, match=message):
        await tools.calculate_refund("order-001", item_skus)
