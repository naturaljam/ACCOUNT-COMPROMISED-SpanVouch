from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OrderStatus(StrEnum):
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Customer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    customer_id: str
    name: str


class OrderItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sku: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(gt=0)

    @property
    def subtotal(self) -> Decimal:
        return self.unit_price * self.quantity


class Order(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    order_id: str
    customer_id: str
    policy_id: str
    status: OrderStatus
    items: tuple[OrderItem, ...]

    @property
    def total(self) -> Decimal:
        return sum((item.subtotal for item in self.items), start=Decimal("0"))


class RefundPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    policy_id: str
    refundable_statuses: frozenset[OrderStatus]
    max_refund: Decimal = Field(gt=0)
    requires_approval: bool = True


class RefundRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    refund_id: str
    order_id: str
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    approved_by: str | None = Field(default=None, min_length=1)
