# Agent Failure Clinic Phase 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first testable vertical slice of Agent Failure Clinic: a reproducible SupportLab tool agent, canonical TraceIR, eight fault classes, deterministic ground truth, trace ingestion, and baseline evaluation over 20 generated runs.

**Architecture:** Keep the target agent, trace model, fault harness, evaluation code, and API as separate modules connected through typed protocols. LangGraph controls the SupportLab run, OpenTelemetry records agent/tool spans, TraceIR is the stable boundary consumed by later diagnosis workflows, and all Phase 1 model decisions are scripted so tests and dataset generation require no paid API.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic v2, LangGraph, OpenTelemetry, pytest, Ruff, mypy, Docker Compose, Phoenix OSS, GitHub Actions.

## Global Constraints

- Python must remain `>=3.12,<3.13`; the verified local interpreter is Python 3.12.7.
- Use a `src/` package layout and keep domain modules independent of FastAPI, Docker, Phoenix, and GitHub SDKs.
- Phase 1 must not call DeepSeek or require any paid API; scripted decisions provide deterministic traces.
- Support exactly one target application, SupportLab, and one canonical trace schema, TraceIR v1.
- The failure taxonomy is fixed to eight failure classes plus `NO_FAILURE`.
- All timestamps are timezone-aware UTC values; all persisted IDs are stable strings, not process-local object IDs.
- Money uses `Decimal`, never binary floating point.
- Tests must not depend on execution order, external network access, or a developer's local database.
- Generated datasets must be deterministic for a fixed seed and must never contain credentials or real personal data.
- Do not add React, PostgreSQL persistence, Celery, DeepSeek integration, diagnosis agents, repair agents, or GitHub PR creation in this phase.
- Use TDD for every behavior: failing test, observed failure, minimal implementation, passing test, then commit.

---

## File Map

```text
pyproject.toml                         # Python metadata, dependencies and tool configuration
README.md                              # Phase 1 quickstart and verified commands
.github/workflows/ci.yml               # Lint, type-check, test and dataset determinism checks
Dockerfile                             # Reproducible API image
compose.yaml                           # API and Phoenix development services

src/afc/__init__.py                    # Package version
src/afc/api/app.py                     # FastAPI factory
src/afc/api/routes/health.py           # Health endpoint
src/afc/api/routes/traces.py           # Trace ingestion endpoint
src/afc/trace_ir/models.py             # Canonical TraceIR v1 Pydantic models
src/afc/trace_ir/mapper.py             # OTel ReadableSpan to TraceIR mapping
src/afc/trace_ir/repository.py         # TraceRepository protocol and in-memory implementation
src/afc/observability/tracing.py       # Testable OTel tracer/exporter construction
src/afc/supportlab/models.py            # Customer, order, policy and refund domain models
src/afc/supportlab/repository.py        # SupportRepository protocol and in-memory fixture store
src/afc/supportlab/policy.py            # Deterministic refund precondition checks
src/afc/supportlab/tools.py             # Tool façade with idempotent refund submission
src/afc/supportlab/scenarios.py         # FailureType, fault profiles and 20 deterministic scenarios
src/afc/supportlab/decision.py          # DecisionModel protocol and scripted implementation
src/afc/supportlab/graph.py             # LangGraph target agent and result contract
src/afc/evals/baselines.py              # final-state and rule-only baselines
src/afc/evals/generate_dataset.py       # Deterministic 20-run JSONL generator

tests/api/test_health.py
tests/api/test_traces.py
tests/trace_ir/test_models.py
tests/trace_ir/test_mapper.py
tests/supportlab/test_repository.py
tests/supportlab/test_policy.py
tests/supportlab/test_tools.py
tests/supportlab/test_scenarios.py
tests/supportlab/test_decision.py
tests/supportlab/test_graph.py
tests/evals/test_baselines.py
tests/evals/test_generate_dataset.py

evals/datasets/supportlab-v1/manifest.json
evals/datasets/supportlab-v1/traces.jsonl
evals/datasets/supportlab-v1/labels.jsonl
docs/architecture/adr-001-traceir-boundary.md
```

## Task 1: Bootstrap the Python Package and Health API

**Files:**
- Create: `pyproject.toml`
- Create: `src/afc/__init__.py`
- Create: `src/afc/api/__init__.py`
- Create: `src/afc/api/routes/__init__.py`
- Create: `src/afc/api/routes/health.py`
- Create: `src/afc/api/app.py`
- Create: `tests/api/test_health.py`

**Interfaces:**
- Consumes: Python 3.12 and uv available on PATH.
- Produces: `afc.api.app.create_app() -> FastAPI`, module-level `app`, and `GET /health -> {"status": "ok", "service": "agent-failure-clinic"}`.

- [ ] **Step 1: Create project metadata and tool configuration**

Create `pyproject.toml`:

```toml
[project]
name = "agent-failure-clinic"
version = "0.1.0"
description = "Trace diagnosis and regression infrastructure for tool-using agents"
readme = "README.md"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115,<1",
  "httpx>=0.28,<1",
  "langgraph>=0.4,<2",
  "opentelemetry-api>=1.31,<2",
  "opentelemetry-sdk>=1.31,<2",
  "pydantic>=2.10,<3",
  "uvicorn[standard]>=0.34,<1",
]

[dependency-groups]
dev = [
  "mypy>=1.15,<2",
  "pytest>=8.3,<9",
  "pytest-asyncio>=0.25,<2",
  "pytest-cov>=6,<8",
  "ruff>=0.11,<1",
]

[project.scripts]
afc-generate-dataset = "afc.evals.generate_dataset:main"

[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/afc"]

[tool.pytest.ini_options]
addopts = "-q --strict-markers --strict-config"
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["afc"]
```

- [ ] **Step 2: Resolve and install the environment**

Run:

```bash
uv sync --group dev
```

Expected: exit code 0, `.venv` and `uv.lock` created, no dependency resolution error.

- [ ] **Step 3: Write the failing health endpoint test**

Create `tests/api/test_health.py`:

```python
from fastapi.testclient import TestClient

from afc.api.app import create_app


def test_health_returns_service_identity() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "agent-failure-clinic",
    }
```

- [ ] **Step 4: Run the test and observe the missing application failure**

Run:

```bash
uv run pytest tests/api/test_health.py -v
```

Expected: FAIL during collection with `ModuleNotFoundError` for `afc.api.app`.

- [ ] **Step 5: Implement the package and health route**

Create `src/afc/__init__.py`:

```python
__version__ = "0.1.0"
```

Create empty `src/afc/api/__init__.py` and `src/afc/api/routes/__init__.py`.

Create `src/afc/api/routes/health.py`:

```python
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["agent-failure-clinic"]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="agent-failure-clinic")
```

Create `src/afc/api/app.py`:

```python
from fastapi import FastAPI

from afc.api.routes.health import router as health_router


def create_app() -> FastAPI:
    application = FastAPI(title="Agent Failure Clinic", version="0.1.0")
    application.include_router(health_router)
    return application


app = create_app()
```

- [ ] **Step 6: Verify the endpoint and static checks**

Run:

```bash
uv run pytest tests/api/test_health.py -v
uv run ruff check src tests
uv run mypy
```

Expected: health test PASS; Ruff and mypy exit 0.

- [ ] **Step 7: Commit the bootstrap**

```bash
git add pyproject.toml uv.lock src/afc tests/api/test_health.py
git commit -m "chore: bootstrap AFC Python service"
```

## Task 2: Define the Canonical TraceIR v1 Contract

**Files:**
- Create: `src/afc/trace_ir/__init__.py`
- Create: `src/afc/trace_ir/models.py`
- Create: `tests/trace_ir/test_models.py`

**Interfaces:**
- Consumes: Pydantic v2.
- Produces: `SpanKind`, `SpanStatus`, `TraceSpan`, `TraceIR`, and `TraceIR.span_by_id(span_id: str) -> TraceSpan`.

- [ ] **Step 1: Write failing schema tests**

Create `tests/trace_ir/test_models.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from afc.trace_ir.models import SpanKind, SpanStatus, TraceIR, TraceSpan


NOW = datetime(2026, 7, 15, tzinfo=UTC)


def make_span(span_id: str, parent_span_id: str | None = None) -> TraceSpan:
    return TraceSpan(
        trace_id="trace-1",
        span_id=span_id,
        parent_span_id=parent_span_id,
        name="supportlab.step",
        kind=SpanKind.AGENT,
        status=SpanStatus.OK,
        started_at=NOW,
        ended_at=NOW + timedelta(milliseconds=10),
        attributes={"agent.name": "supportlab"},
    )


def test_trace_accepts_a_connected_span_tree() -> None:
    trace = TraceIR(
        trace_id="trace-1",
        run_id="run-1",
        spans=[make_span("root"), make_span("child", "root")],
    )

    assert trace.span_by_id("child").parent_span_id == "root"


def test_trace_rejects_orphan_parent() -> None:
    with pytest.raises(ValidationError, match="missing parent span"):
        TraceIR(
            trace_id="trace-1",
            run_id="run-1",
            spans=[make_span("child", "missing")],
        )


def test_trace_rejects_duplicate_span_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate span_id"):
        TraceIR(
            trace_id="trace-1",
            run_id="run-1",
            spans=[make_span("same"), make_span("same")],
        )


def test_span_rejects_naive_or_reverse_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TraceSpan(
            trace_id="trace-1",
            span_id="bad-timezone",
            name="bad",
            kind=SpanKind.TOOL,
            status=SpanStatus.ERROR,
            started_at=datetime(2026, 7, 15),
            ended_at=datetime(2026, 7, 15),
        )

    with pytest.raises(ValidationError, match="ended_at"):
        TraceSpan(
            trace_id="trace-1",
            span_id="reverse",
            name="bad",
            kind=SpanKind.TOOL,
            status=SpanStatus.ERROR,
            started_at=NOW,
            ended_at=NOW - timedelta(seconds=1),
        )
```

- [ ] **Step 2: Confirm schema tests fail before the contract exists**

Run:

```bash
uv run pytest tests/trace_ir/test_models.py -v
```

Expected: FAIL during collection because `afc.trace_ir.models` does not exist.

- [ ] **Step 3: Implement TraceIR v1**

Create empty `src/afc/trace_ir/__init__.py`.

Create `src/afc/trace_ir/models.py`:

```python
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class SpanKind(StrEnum):
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    APPROVAL = "approval"
    WORKFLOW = "workflow"


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    UNSET = "unset"


class TraceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = Field(min_length=1)
    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    name: str = Field(min_length=1)
    kind: SpanKind
    status: SpanStatus = SpanStatus.UNSET
    started_at: datetime
    ended_at: datetime
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("started_at and ended_at must be timezone-aware")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        return self


class TraceIR(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    spans: list[TraceSpan] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_span_tree(self) -> Self:
        ids = [span.span_id for span in self.spans]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate span_id in trace")
        known_ids = set(ids)
        for span in self.spans:
            if span.trace_id != self.trace_id:
                raise ValueError("span trace_id does not match trace")
            if span.parent_span_id is not None and span.parent_span_id not in known_ids:
                raise ValueError(f"missing parent span: {span.parent_span_id}")
            if span.parent_span_id == span.span_id:
                raise ValueError("span cannot parent itself")
        return self

    def span_by_id(self, span_id: str) -> TraceSpan:
        for span in self.spans:
            if span.span_id == span_id:
                return span
        raise KeyError(span_id)
```

- [ ] **Step 4: Verify TraceIR behavior**

Run:

```bash
uv run pytest tests/trace_ir/test_models.py -v
uv run ruff check src/afc/trace_ir tests/trace_ir
uv run mypy
```

Expected: 4 tests PASS; Ruff and mypy exit 0.

- [ ] **Step 5: Commit the stable trace boundary**

```bash
git add src/afc/trace_ir tests/trace_ir/test_models.py
git commit -m "feat: define canonical TraceIR contract"
```

## Task 3: Implement the SupportLab Domain Repository

**Files:**
- Create: `src/afc/supportlab/__init__.py`
- Create: `src/afc/supportlab/models.py`
- Create: `src/afc/supportlab/repository.py`
- Create: `tests/supportlab/test_repository.py`

**Interfaces:**
- Consumes: Pydantic and `Decimal`.
- Produces: `Customer`, `OrderItem`, `Order`, `RefundPolicy`, `RefundRecord`, `SupportRepository`, `InMemorySupportRepository`, and `build_seed_repository()`.

- [ ] **Step 1: Write failing repository tests**

Create `tests/supportlab/test_repository.py`:

```python
from decimal import Decimal

import pytest

from afc.supportlab.models import OrderStatus, RefundRecord
from afc.supportlab.repository import build_seed_repository


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
```

- [ ] **Step 2: Confirm the domain tests fail**

Run:

```bash
uv run pytest tests/supportlab/test_repository.py -v
```

Expected: FAIL during collection because `afc.supportlab` does not exist.

- [ ] **Step 3: Implement immutable domain models**

Create empty `src/afc/supportlab/__init__.py`.

Create `src/afc/supportlab/models.py`:

```python
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
    approved_by: str = Field(min_length=1)
```

- [ ] **Step 4: Implement the repository protocol and fixture store**

Create `src/afc/supportlab/repository.py`:

```python
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
```

- [ ] **Step 5: Verify repository behavior**

Run:

```bash
uv run pytest tests/supportlab/test_repository.py -v
uv run ruff check src/afc/supportlab tests/supportlab/test_repository.py
uv run mypy
```

Expected: 2 tests PASS; Ruff and mypy exit 0.

- [ ] **Step 6: Commit the domain fixture**

```bash
git add src/afc/supportlab tests/supportlab/test_repository.py
git commit -m "feat: add SupportLab domain repository"
```

## Task 4: Enforce Refund Policy and Idempotent Tools

**Files:**
- Create: `src/afc/supportlab/policy.py`
- Create: `src/afc/supportlab/tools.py`
- Create: `tests/supportlab/test_policy.py`
- Create: `tests/supportlab/test_tools.py`

**Interfaces:**
- Consumes: `SupportRepository`, `Order`, `RefundPolicy`, `RefundRecord`.
- Produces: `Approval`, `PolicyViolation`, `RefundDecision`, `evaluate_refund(...)`, and `SupportTools` methods matching the design tool names.

- [ ] **Step 1: Write failing policy tests**

Create `tests/supportlab/test_policy.py`:

```python
from decimal import Decimal

from afc.supportlab.models import OrderStatus
from afc.supportlab.policy import Approval, PolicyViolation, evaluate_refund
from afc.supportlab.repository import build_seed_repository


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
```

- [ ] **Step 2: Run policy tests and observe the missing module**

Run:

```bash
uv run pytest tests/supportlab/test_policy.py -v
```

Expected: FAIL during collection because `afc.supportlab.policy` does not exist.

- [ ] **Step 3: Implement deterministic refund policy**

Create `src/afc/supportlab/policy.py`:

```python
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from afc.supportlab.models import Order, RefundPolicy


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
```

- [ ] **Step 4: Verify policy tests pass**

Run:

```bash
uv run pytest tests/supportlab/test_policy.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Write failing tool tests**

Create `tests/supportlab/test_tools.py`:

```python
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
```

- [ ] **Step 6: Run tool tests and observe the missing façade**

Run:

```bash
uv run pytest tests/supportlab/test_tools.py -v
```

Expected: FAIL during collection because `afc.supportlab.tools` does not exist.

- [ ] **Step 7: Implement the tool façade**

Create `src/afc/supportlab/tools.py`:

```python
from decimal import Decimal
from uuid import uuid5, NAMESPACE_URL

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
```

- [ ] **Step 8: Verify policy and tool behavior**

Run:

```bash
uv run pytest tests/supportlab/test_policy.py tests/supportlab/test_tools.py -v
uv run ruff check src/afc/supportlab tests/supportlab
uv run mypy
```

Expected: 5 tests PASS; Ruff and mypy exit 0.

- [ ] **Step 9: Commit the policy boundary**

```bash
git add src/afc/supportlab/policy.py src/afc/supportlab/tools.py tests/supportlab
git commit -m "feat: enforce SupportLab refund policy"
```

## Task 5: Freeze the Failure Taxonomy and Scenario Matrix

**Files:**
- Create: `src/afc/supportlab/scenarios.py`
- Create: `tests/supportlab/test_scenarios.py`

**Interfaces:**
- Consumes: SupportLab fixture IDs and refund workflow semantics.
- Produces: `FailureType`, `FaultProfile`, `Scenario`, and `build_scenarios(seed: int = 20260715) -> tuple[Scenario, ...]` with exactly 20 stable scenarios.

- [ ] **Step 1: Write failing taxonomy tests**

Create `tests/supportlab/test_scenarios.py`:

```python
from collections import Counter

from afc.supportlab.scenarios import FailureType, Scenario, build_scenarios


def test_scenario_matrix_has_stable_size_and_distribution() -> None:
    scenarios = build_scenarios()
    counts = Counter(item.expected_failure for item in scenarios)

    assert len(scenarios) == 20
    assert len({item.scenario_id for item in scenarios}) == 20
    assert counts[FailureType.NO_FAILURE] == 4
    for failure_type in set(FailureType) - {FailureType.NO_FAILURE}:
        assert counts[failure_type] == 2


def test_scenario_generation_is_deterministic() -> None:
    first = [item.model_dump(mode="json") for item in build_scenarios(seed=7)]
    second = [item.model_dump(mode="json") for item in build_scenarios(seed=7)]

    assert first == second
```

- [ ] **Step 2: Confirm scenario tests fail**

Run:

```bash
uv run pytest tests/supportlab/test_scenarios.py -v
```

Expected: FAIL during collection because `afc.supportlab.scenarios` does not exist.

- [ ] **Step 3: Implement the fixed taxonomy and deterministic matrix**

Create `src/afc/supportlab/scenarios.py`:

```python
from enum import StrEnum
from random import Random

from pydantic import BaseModel, ConfigDict


class FailureType(StrEnum):
    NO_FAILURE = "no_failure"
    WRONG_TOOL = "wrong_tool"
    INVALID_ARGUMENT = "invalid_argument"
    MISSING_PRECONDITION = "missing_precondition"
    IGNORED_TOOL_ERROR = "ignored_tool_error"
    CONTEXT_CORRUPTION = "context_corruption"
    POLICY_VIOLATION = "policy_violation"
    LOOP_OR_BUDGET_EXHAUSTION = "loop_or_budget_exhaustion"
    INVALID_FINAL_STATE = "invalid_final_state"


class FaultProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    wrong_tool: bool = False
    invalid_amount: bool = False
    skip_policy: bool = False
    ignore_tool_error: bool = False
    poisoned_context: bool = False
    bypass_approval: bool = False
    repeat_lookup: bool = False
    false_success: bool = False


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str
    user_request: str
    customer_id: str = "cust-001"
    order_id: str = "order-001"
    expected_failure: FailureType
    expected_critical_operation: str
    fault: FaultProfile


_FAULTS: dict[FailureType, tuple[str, FaultProfile]] = {
    FailureType.WRONG_TOOL: ("get_account", FaultProfile(wrong_tool=True)),
    FailureType.INVALID_ARGUMENT: ("submit_refund", FaultProfile(invalid_amount=True)),
    FailureType.MISSING_PRECONDITION: ("get_refund_policy", FaultProfile(skip_policy=True)),
    FailureType.IGNORED_TOOL_ERROR: ("submit_refund", FaultProfile(ignore_tool_error=True)),
    FailureType.CONTEXT_CORRUPTION: ("submit_refund", FaultProfile(poisoned_context=True)),
    FailureType.POLICY_VIOLATION: ("submit_refund", FaultProfile(bypass_approval=True)),
    FailureType.LOOP_OR_BUDGET_EXHAUSTION: ("get_order", FaultProfile(repeat_lookup=True)),
    FailureType.INVALID_FINAL_STATE: ("finalize", FaultProfile(false_success=True)),
}


def build_scenarios(seed: int = 20260715) -> tuple[Scenario, ...]:
    scenarios = [
        Scenario(
            scenario_id=f"clean-{index:02d}",
            user_request="Refund the damaged red item from order-001.",
            expected_failure=FailureType.NO_FAILURE,
            expected_critical_operation="none",
            fault=FaultProfile(),
        )
        for index in range(1, 5)
    ]
    for failure_type, (operation, profile) in _FAULTS.items():
        for index in range(1, 3):
            scenarios.append(
                Scenario(
                    scenario_id=f"{failure_type.value}-{index:02d}",
                    user_request="Refund the damaged red item from order-001.",
                    expected_failure=failure_type,
                    expected_critical_operation=operation,
                    fault=profile,
                )
            )
    random = Random(seed)
    random.shuffle(scenarios)
    return tuple(scenarios)
```

- [ ] **Step 4: Verify taxonomy stability**

Run:

```bash
uv run pytest tests/supportlab/test_scenarios.py -v
uv run ruff check src/afc/supportlab/scenarios.py tests/supportlab/test_scenarios.py
uv run mypy
```

Expected: 2 tests PASS; each failure type has the specified count.

- [ ] **Step 5: Commit the scenario contract**

```bash
git add src/afc/supportlab/scenarios.py tests/supportlab/test_scenarios.py
git commit -m "feat: define SupportLab failure scenarios"
```

## Task 6: Add the Decision Protocol and Scripted Model

**Files:**
- Create: `src/afc/supportlab/decision.py`
- Create: `tests/supportlab/test_decision.py`

**Interfaces:**
- Consumes: `Scenario` and `FaultProfile`.
- Produces: `DecisionKind`, `AgentDecision`, `DecisionContext`, `DecisionModel.next_decision(context)`, and `ScriptedDecisionModel`.

- [ ] **Step 1: Write failing scripted-decision tests**

Create `tests/supportlab/test_decision.py`:

```python
import pytest

from afc.supportlab.decision import DecisionContext, DecisionKind, ScriptedDecisionModel
from afc.supportlab.scenarios import FailureType, Scenario, build_scenarios


def scenario_for(failure_type: FailureType) -> Scenario:
    return next(item for item in build_scenarios() if item.expected_failure is failure_type)


@pytest.mark.asyncio
async def test_clean_script_checks_order_policy_amount_then_refunds() -> None:
    model = ScriptedDecisionModel(scenario_for(FailureType.NO_FAILURE))

    names = []
    for step in range(6):
        decision = await model.next_decision(DecisionContext(step=step, observations=()))
        names.append(decision.tool_name or decision.kind.value)

    assert names == [
        "get_customer",
        "get_order",
        "get_refund_policy",
        "calculate_refund",
        "submit_refund",
        DecisionKind.FINAL.value,
    ]


@pytest.mark.asyncio
async def test_loop_fault_repeats_until_budget_boundary() -> None:
    model = ScriptedDecisionModel(scenario_for(FailureType.LOOP_OR_BUDGET_EXHAUSTION))

    decisions = [
        await model.next_decision(DecisionContext(step=step, observations=()))
        for step in range(8)
    ]

    assert all(item.tool_name == "get_order" for item in decisions)
```

- [ ] **Step 2: Run tests and observe the missing decision module**

Run:

```bash
uv run pytest tests/supportlab/test_decision.py -v
```

Expected: FAIL during collection because `afc.supportlab.decision` does not exist.

- [ ] **Step 3: Implement the typed scripted model**

Create `src/afc/supportlab/decision.py`:

```python
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from afc.supportlab.scenarios import Scenario


class DecisionKind(StrEnum):
    TOOL = "tool"
    FINAL = "final"


class AgentDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: DecisionKind
    tool_name: str | None = None
    arguments: dict[str, str] = Field(default_factory=dict)
    message: str | None = None


class DecisionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step: int = Field(ge=0)
    observations: tuple[str, ...]


class DecisionModel(Protocol):
    async def next_decision(self, context: DecisionContext) -> AgentDecision: ...


class ScriptedDecisionModel:
    _CLEAN_TOOLS = (
        "get_customer",
        "get_order",
        "get_refund_policy",
        "calculate_refund",
        "submit_refund",
    )

    def __init__(self, scenario: Scenario) -> None:
        self._scenario = scenario

    async def next_decision(self, context: DecisionContext) -> AgentDecision:
        fault = self._scenario.fault
        if fault.repeat_lookup:
            return AgentDecision(
                kind=DecisionKind.TOOL,
                tool_name="get_order",
                arguments={"order_id": self._scenario.order_id},
            )
        tools = list(self._CLEAN_TOOLS)
        if fault.wrong_tool:
            tools[0] = "get_account"
        if fault.skip_policy:
            tools.remove("get_refund_policy")
        if context.step >= len(tools):
            message = "Refund submitted successfully."
            if fault.false_success:
                message = "Refund submitted successfully without a refund record."
            return AgentDecision(kind=DecisionKind.FINAL, message=message)
        tool_name = tools[context.step]
        arguments = self._arguments_for(tool_name)
        return AgentDecision(kind=DecisionKind.TOOL, tool_name=tool_name, arguments=arguments)

    def _arguments_for(self, tool_name: str) -> dict[str, str]:
        fault = self._scenario.fault
        if tool_name == "get_customer":
            return {"customer_id": self._scenario.customer_id}
        if tool_name in {"get_order", "get_refund_policy"}:
            return {"order_id": self._scenario.order_id}
        if tool_name == "calculate_refund":
            return {"order_id": self._scenario.order_id, "item_skus": "sku-red"}
        amount = "200.00" if fault.invalid_amount else "19.99"
        customer_id = "attacker-customer" if fault.poisoned_context else self._scenario.customer_id
        approval_missing = fault.bypass_approval or fault.ignore_tool_error
        return {
            "customer_id": customer_id,
            "order_id": self._scenario.order_id,
            "amount": amount,
            "calculated_amount": "19.99",
            "reason": "damaged item",
            "idempotency_key": f"{self._scenario.scenario_id}-refund",
            "approval": "none" if approval_missing else "reviewer@example.test",
            "ignore_error": "true" if fault.ignore_tool_error else "false",
        }
```

- [ ] **Step 4: Verify deterministic decisions**

Run:

```bash
uv run pytest tests/supportlab/test_decision.py -v
uv run ruff check src/afc/supportlab/decision.py tests/supportlab/test_decision.py
uv run mypy
```

Expected: 2 tests PASS; Ruff and mypy exit 0.

- [ ] **Step 5: Commit the model seam**

```bash
git add src/afc/supportlab/decision.py tests/supportlab/test_decision.py
git commit -m "feat: add scripted SupportLab decision model"
```

## Task 7: Record and Map OpenTelemetry Spans

**Files:**
- Create: `src/afc/observability/__init__.py`
- Create: `src/afc/observability/tracing.py`
- Create: `src/afc/trace_ir/mapper.py`
- Create: `tests/trace_ir/test_mapper.py`

**Interfaces:**
- Consumes: OpenTelemetry `ReadableSpan` objects.
- Produces: `build_test_tracer() -> tuple[Tracer, InMemorySpanExporter]` and `map_spans(run_id: str, spans: Sequence[ReadableSpan]) -> TraceIR`.

- [ ] **Step 1: Write a failing OTel mapping test**

Create `tests/trace_ir/test_mapper.py`:

```python
from opentelemetry.trace import Status, StatusCode

from afc.observability.tracing import build_test_tracer
from afc.trace_ir.mapper import map_spans
from afc.trace_ir.models import SpanKind, SpanStatus


def test_otel_spans_map_to_connected_trace_ir() -> None:
    tracer, exporter = build_test_tracer()
    with tracer.start_as_current_span(
        "supportlab.run",
        attributes={"openinference.span.kind": "AGENT"},
    ):
        with tracer.start_as_current_span(
            "get_order",
            attributes={
                "openinference.span.kind": "TOOL",
                "tool.name": "get_order",
                "tool.arguments.order_id": "order-001",
            },
        ) as tool_span:
            tool_span.set_status(Status(StatusCode.OK))

    trace = map_spans("run-001", exporter.get_finished_spans())

    assert len(trace.spans) == 2
    tool = next(span for span in trace.spans if span.kind is SpanKind.TOOL)
    assert tool.status is SpanStatus.OK
    assert tool.parent_span_id is not None
    assert tool.attributes["tool.name"] == "get_order"
```

- [ ] **Step 2: Run the mapper test and observe missing modules**

Run:

```bash
uv run pytest tests/trace_ir/test_mapper.py -v
```

Expected: FAIL during collection because `afc.observability.tracing` does not exist.

- [ ] **Step 3: Implement an isolated in-memory tracer**

Create empty `src/afc/observability/__init__.py`.

Create `src/afc/observability/tracing.py`:

```python
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer


def build_test_tracer() -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "supportlab"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("afc.supportlab"), exporter
```

- [ ] **Step 4: Implement the OTel-to-TraceIR mapper**

Create `src/afc/trace_ir/mapper.py`:

```python
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode
from pydantic import JsonValue

from afc.trace_ir.models import SpanKind, SpanStatus, TraceIR, TraceSpan


_KIND_MAP = {
    "AGENT": SpanKind.AGENT,
    "LLM": SpanKind.LLM,
    "TOOL": SpanKind.TOOL,
    "RETRIEVER": SpanKind.RETRIEVAL,
    "CHAIN": SpanKind.WORKFLOW,
}


def _to_datetime(nanoseconds: int) -> datetime:
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, tz=UTC)


def _status(span: ReadableSpan) -> SpanStatus:
    if span.status.status_code is StatusCode.OK:
        return SpanStatus.OK
    if span.status.status_code is StatusCode.ERROR:
        return SpanStatus.ERROR
    return SpanStatus.UNSET


def map_spans(run_id: str, spans: Sequence[ReadableSpan]) -> TraceIR:
    if not spans:
        raise ValueError("cannot map an empty span sequence")
    trace_id = format(spans[0].context.trace_id, "032x") if spans[0].context else ""
    mapped: list[TraceSpan] = []
    for span in spans:
        assert span.context is not None
        assert span.start_time is not None
        assert span.end_time is not None
        attributes = {str(key): cast(JsonValue, value) for key, value in (span.attributes or {}).items()}
        kind_name = str(attributes.get("openinference.span.kind", "CHAIN")).upper()
        mapped.append(
            TraceSpan(
                trace_id=trace_id,
                span_id=format(span.context.span_id, "016x"),
                parent_span_id=(
                    format(span.parent.span_id, "016x") if span.parent is not None else None
                ),
                name=span.name,
                kind=_KIND_MAP.get(kind_name, SpanKind.WORKFLOW),
                status=_status(span),
                started_at=_to_datetime(span.start_time),
                ended_at=_to_datetime(span.end_time),
                attributes=attributes,
            )
        )
    return TraceIR(trace_id=trace_id, run_id=run_id, spans=mapped)
```

- [ ] **Step 5: Verify mapping and schema compatibility**

Run:

```bash
uv run pytest tests/trace_ir/test_models.py tests/trace_ir/test_mapper.py -v
uv run ruff check src/afc/observability src/afc/trace_ir tests/trace_ir
uv run mypy
```

Expected: all TraceIR tests PASS; Ruff and mypy exit 0.

- [ ] **Step 6: Commit the trace instrumentation seam**

```bash
git add src/afc/observability src/afc/trace_ir/mapper.py tests/trace_ir/test_mapper.py
git commit -m "feat: map OpenTelemetry spans to TraceIR"
```

## Task 8: Execute SupportLab as a Bounded LangGraph Agent

**Files:**
- Create: `src/afc/supportlab/graph.py`
- Create: `tests/supportlab/test_graph.py`

**Interfaces:**
- Consumes: `DecisionModel`, `SupportTools`, `Scenario`, and OTel `Tracer`.
- Produces: `SupportRunResult` and `run_support_scenario(scenario, tools, decision_model, tracer, max_steps=8) -> SupportRunResult`.

- [ ] **Step 1: Write failing graph tests for success and bounded failure**

Create `tests/supportlab/test_graph.py`:

```python
import pytest

from afc.observability.tracing import build_test_tracer
from afc.supportlab.decision import ScriptedDecisionModel
from afc.supportlab.graph import RunOutcome, run_support_scenario
from afc.supportlab.repository import build_seed_repository
from afc.supportlab.scenarios import FailureType, Scenario, build_scenarios
from afc.supportlab.tools import SupportTools


def scenario_for(failure_type: FailureType) -> Scenario:
    return next(item for item in build_scenarios() if item.expected_failure is failure_type)


@pytest.mark.asyncio
async def test_clean_scenario_creates_one_refund() -> None:
    scenario = scenario_for(FailureType.NO_FAILURE)
    repository = build_seed_repository()
    tracer, exporter = build_test_tracer()

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(repository),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=tracer,
    )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert len(await repository.list_refunds("order-001")) == 1
    assert any(span.name == "submit_refund" for span in exporter.get_finished_spans())


@pytest.mark.asyncio
async def test_loop_scenario_stops_at_max_steps() -> None:
    scenario = scenario_for(FailureType.LOOP_OR_BUDGET_EXHAUSTION)
    tracer, _ = build_test_tracer()

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(build_seed_repository()),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=tracer,
        max_steps=4,
    )

    assert result.outcome is RunOutcome.STEP_LIMIT
    assert result.steps == 4
```

- [ ] **Step 2: Run graph tests and observe the missing graph**

Run:

```bash
uv run pytest tests/supportlab/test_graph.py -v
```

Expected: FAIL during collection because `afc.supportlab.graph` does not exist.

- [ ] **Step 3: Implement the bounded LangGraph state machine**

Create `src/afc/supportlab/graph.py`:

```python
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypedDict, cast

from langgraph.graph import END, StateGraph
from opentelemetry.trace import Status, StatusCode, Tracer
from pydantic import BaseModel, ConfigDict

from afc.supportlab.decision import DecisionContext, DecisionKind, DecisionModel
from afc.supportlab.policy import Approval
from afc.supportlab.scenarios import Scenario
from afc.supportlab.tools import RefundRejected, SupportTools


class RunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STEP_LIMIT = "step_limit"


class SupportRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str
    outcome: RunOutcome
    steps: int
    observations: tuple[str, ...]
    final_message: str | None


class SupportState(TypedDict):
    step: int
    observations: list[str]
    next_tool: str | None
    next_arguments: dict[str, str]
    final_message: str | None
    outcome: str | None


async def run_support_scenario(
    *,
    scenario: Scenario,
    tools: SupportTools,
    decision_model: DecisionModel,
    tracer: Tracer,
    max_steps: int = 8,
) -> SupportRunResult:
    async def decide(state: SupportState) -> dict[str, Any]:
        if state["step"] >= max_steps:
            return {"outcome": RunOutcome.STEP_LIMIT.value}
        decision = await decision_model.next_decision(
            DecisionContext(step=state["step"], observations=tuple(state["observations"]))
        )
        if decision.kind is DecisionKind.FINAL:
            return {
                "final_message": decision.message,
                "outcome": RunOutcome.SUCCEEDED.value,
            }
        return {"next_tool": decision.tool_name, "next_arguments": decision.arguments}

    async def execute(state: SupportState) -> dict[str, Any]:
        tool_name = state["next_tool"]
        arguments = state["next_arguments"]
        assert tool_name is not None
        with tracer.start_as_current_span(
            tool_name,
            attributes={
                "openinference.span.kind": "TOOL",
                "tool.name": tool_name,
                **{f"tool.arguments.{key}": value for key, value in arguments.items()},
            },
        ) as span:
            try:
                if tool_name == "get_customer":
                    result = await tools.get_customer(arguments["customer_id"])
                elif tool_name == "get_order":
                    result = await tools.get_order(arguments["order_id"])
                elif tool_name == "get_refund_policy":
                    result = await tools.get_refund_policy(arguments["order_id"])
                elif tool_name == "calculate_refund":
                    result = await tools.calculate_refund(
                        arguments["order_id"], tuple(arguments["item_skus"].split(","))
                    )
                elif tool_name == "submit_refund":
                    approval_value = arguments["approval"]
                    approval = (
                        None
                        if approval_value == "none"
                        else Approval(approved_by=approval_value)
                    )
                    result = await tools.submit_refund(
                        customer_id=arguments["customer_id"],
                        order_id=arguments["order_id"],
                        amount=Decimal(arguments["amount"]),
                        calculated_amount=Decimal(arguments["calculated_amount"]),
                        reason=arguments["reason"],
                        idempotency_key=arguments["idempotency_key"],
                        approval=approval,
                    )
                else:
                    raise KeyError(f"unknown tool: {tool_name}")
                span.set_status(Status(StatusCode.OK))
                observation = str(result)
                span.set_attribute("tool.result", observation)
            except (KeyError, RefundRejected) as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                span.set_attribute("tool.error.type", type(error).__name__)
                span.set_attribute("tool.error.message", str(error))
                observation = f"ERROR:{type(error).__name__}:{error}"
                if arguments.get("ignore_error") != "true":
                    return {
                        "observations": [*state["observations"], observation],
                        "step": state["step"] + 1,
                        "outcome": RunOutcome.FAILED.value,
                    }
            return {
                "observations": [*state["observations"], observation],
                "step": state["step"] + 1,
                "next_tool": None,
                "next_arguments": {},
            }

    def after_decide(state: SupportState) -> str:
        return "end" if state["outcome"] is not None else "execute"

    def after_execute(state: SupportState) -> str:
        return "end" if state["outcome"] is not None else "decide"

    builder = StateGraph(SupportState)
    builder.add_node("decide", decide)
    builder.add_node("execute", execute)
    builder.set_entry_point("decide")
    builder.add_conditional_edges("decide", after_decide, {"execute": "execute", "end": END})
    builder.add_conditional_edges("execute", after_execute, {"decide": "decide", "end": END})
    graph = builder.compile()
    initial: SupportState = {
        "step": 0,
        "observations": [],
        "next_tool": None,
        "next_arguments": {},
        "final_message": None,
        "outcome": None,
    }
    with tracer.start_as_current_span(
        "supportlab.run",
        attributes={
            "openinference.span.kind": "AGENT",
            "scenario.id": scenario.scenario_id,
            "scenario.expected_failure": scenario.expected_failure.value,
        },
    ) as run_span:
        final = cast(SupportState, await graph.ainvoke(initial))
        run_span.set_attribute("run.outcome", final["outcome"] or RunOutcome.FAILED.value)
        if final["final_message"] is not None:
            run_span.set_attribute("run.final_message", final["final_message"])
    return SupportRunResult(
        scenario_id=scenario.scenario_id,
        outcome=RunOutcome(final["outcome"] or RunOutcome.FAILED.value),
        steps=final["step"],
        observations=tuple(final["observations"]),
        final_message=final["final_message"],
    )
```

- [ ] **Step 4: Strengthen the trace evidence assertions**

Replace the final span assertion in `test_clean_scenario_creates_one_refund` with:

```python
    finished_spans = exporter.get_finished_spans()
    run_span = next(span for span in finished_spans if span.name == "supportlab.run")
    refund_span = next(span for span in finished_spans if span.name == "submit_refund")
    assert run_span.attributes is not None
    assert refund_span.attributes is not None
    assert run_span.attributes["run.outcome"] == RunOutcome.SUCCEEDED.value
    assert refund_span.attributes["tool.name"] == "submit_refund"
    assert "tool.result" in refund_span.attributes
```

- [ ] **Step 5: Verify bounded execution and span production**

Run:

```bash
uv run pytest tests/supportlab/test_graph.py -v
uv run ruff check src/afc/supportlab/graph.py tests/supportlab/test_graph.py
uv run mypy
```

Expected: 2 tests PASS; Ruff and mypy exit 0.

- [ ] **Step 6: Commit the target Agent**

```bash
git add src/afc/supportlab/graph.py tests/supportlab/test_graph.py
git commit -m "feat: execute bounded SupportLab agent"
```

## Task 9: Generate the 20-Run Dataset and Baselines

**Files:**
- Create: `src/afc/evals/__init__.py`
- Create: `src/afc/evals/baselines.py`
- Create: `src/afc/evals/generate_dataset.py`
- Create: `tests/evals/test_baselines.py`
- Create: `tests/evals/test_generate_dataset.py`
- Generate: `evals/datasets/supportlab-v1/manifest.json`
- Generate: `evals/datasets/supportlab-v1/traces.jsonl`
- Generate: `evals/datasets/supportlab-v1/labels.jsonl`

**Interfaces:**
- Consumes: `build_scenarios`, `run_support_scenario`, `map_spans`.
- Produces: `BaselinePrediction`, `final_state_baseline`, `rule_only_baseline`, `generate_dataset(output_dir, seed) -> DatasetManifest`, and the `afc-generate-dataset` CLI.

- [ ] **Step 1: Write failing baseline tests**

Create `tests/evals/test_baselines.py`:

```python
from afc.evals.baselines import final_state_baseline, rule_only_baseline
from afc.supportlab.scenarios import FailureType


def test_final_state_baseline_only_detects_explicit_failures() -> None:
    prediction = final_state_baseline(outcome="failed", final_message=None)
    assert prediction.failure_type is FailureType.INVALID_FINAL_STATE


def test_rule_only_baseline_maps_policy_error() -> None:
    prediction = rule_only_baseline(
        observations=("ERROR:RefundRejected:missing_approval",),
        steps=5,
        max_steps=8,
    )
    assert prediction.failure_type is FailureType.POLICY_VIOLATION
```

- [ ] **Step 2: Confirm baseline tests fail**

Run:

```bash
uv run pytest tests/evals/test_baselines.py -v
```

Expected: FAIL during collection because `afc.evals.baselines` does not exist.

- [ ] **Step 3: Implement explicit weak baselines**

Create empty `src/afc/evals/__init__.py`.

Create `src/afc/evals/baselines.py`:

```python
from pydantic import BaseModel, ConfigDict

from afc.supportlab.scenarios import FailureType


class BaselinePrediction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    failure_type: FailureType
    evidence: tuple[str, ...]


def final_state_baseline(*, outcome: str, final_message: str | None) -> BaselinePrediction:
    if outcome != "succeeded" or final_message is None:
        return BaselinePrediction(
            failure_type=FailureType.INVALID_FINAL_STATE,
            evidence=(f"outcome={outcome}",),
        )
    return BaselinePrediction(failure_type=FailureType.NO_FAILURE, evidence=("final_message",))


def rule_only_baseline(
    *, observations: tuple[str, ...], steps: int, max_steps: int
) -> BaselinePrediction:
    if steps >= max_steps:
        return BaselinePrediction(
            failure_type=FailureType.LOOP_OR_BUDGET_EXHAUSTION,
            evidence=(f"steps={steps}",),
        )
    for observation in observations:
        if "RefundRejected" in observation:
            return BaselinePrediction(
                failure_type=FailureType.POLICY_VIOLATION,
                evidence=(observation,),
            )
        if "KeyError" in observation:
            return BaselinePrediction(
                failure_type=FailureType.WRONG_TOOL,
                evidence=(observation,),
            )
    return BaselinePrediction(failure_type=FailureType.NO_FAILURE, evidence=())
```

- [ ] **Step 4: Verify baseline tests pass**

Run:

```bash
uv run pytest tests/evals/test_baselines.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Write a failing dataset determinism test**

Create `tests/evals/test_generate_dataset.py`:

```python
import json
from pathlib import Path

import pytest

from afc.evals.generate_dataset import generate_dataset


@pytest.mark.asyncio
async def test_dataset_generation_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_manifest = await generate_dataset(first, seed=7)
    second_manifest = await generate_dataset(second, seed=7)

    assert first_manifest == second_manifest
    assert (first / "traces.jsonl").read_bytes() == (second / "traces.jsonl").read_bytes()
    assert (first / "labels.jsonl").read_bytes() == (second / "labels.jsonl").read_bytes()
    labels = [json.loads(line) for line in (first / "labels.jsonl").read_text().splitlines()]
    assert len(labels) == 20
```

- [ ] **Step 6: Run the generator test and observe the missing module**

Run:

```bash
uv run pytest tests/evals/test_generate_dataset.py -v
```

Expected: FAIL during collection because `afc.evals.generate_dataset` does not exist.

- [ ] **Step 7: Implement deterministic dataset generation**

Create `src/afc/evals/generate_dataset.py`:

```python
import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from afc.observability.tracing import build_test_tracer
from afc.supportlab.decision import ScriptedDecisionModel
from afc.supportlab.graph import run_support_scenario
from afc.supportlab.repository import build_seed_repository
from afc.supportlab.scenarios import build_scenarios
from afc.supportlab.tools import SupportTools
from afc.trace_ir.mapper import map_spans
from afc.trace_ir.models import TraceIR


class DatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = "supportlab-v1"
    schema_version: str = "1.0"
    seed: int
    trace_count: int
    traces_sha256: str
    labels_sha256: str


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> str:
    content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.write_text(content, encoding="utf-8", newline="\n")
    return hashlib.sha256(content.encode()).hexdigest()


def _normalize_trace(trace: TraceIR, sequence: int) -> TraceIR:
    ordered = sorted(trace.spans, key=lambda span: (span.started_at, span.span_id))
    id_map = {span.span_id: f"span-{index:03d}" for index, span in enumerate(ordered)}
    trace_id = f"supportlab-trace-{sequence:03d}"
    base_time = datetime(2026, 7, 15, tzinfo=UTC) + timedelta(seconds=sequence)
    normalized = []
    for index, span in enumerate(ordered):
        started_at = base_time + timedelta(milliseconds=index * 10)
        normalized.append(
            span.model_copy(
                update={
                    "trace_id": trace_id,
                    "span_id": id_map[span.span_id],
                    "parent_span_id": (
                        id_map[span.parent_span_id] if span.parent_span_id is not None else None
                    ),
                    "started_at": started_at,
                    "ended_at": started_at + timedelta(milliseconds=5),
                }
            )
        )
    return TraceIR(
        trace_id=trace_id,
        run_id=trace.run_id,
        spans=normalized,
    )


async def generate_dataset(output_dir: Path, seed: int = 20260715) -> DatasetManifest:
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for sequence, scenario in enumerate(build_scenarios(seed), start=1):
        tracer, exporter = build_test_tracer()
        result = await run_support_scenario(
            scenario=scenario,
            tools=SupportTools(build_seed_repository()),
            decision_model=ScriptedDecisionModel(scenario),
            tracer=tracer,
        )
        trace = _normalize_trace(
            map_spans(scenario.scenario_id, exporter.get_finished_spans()),
            sequence,
        )
        trace_rows.append(trace.model_dump(mode="json"))
        label_rows.append(
            {
                "run_id": scenario.scenario_id,
                "failure_type": scenario.expected_failure.value,
                "critical_operation": scenario.expected_critical_operation,
                "observed_outcome": result.outcome.value,
            }
        )
    traces_hash = _write_jsonl(output_dir / "traces.jsonl", trace_rows)
    labels_hash = _write_jsonl(output_dir / "labels.jsonl", label_rows)
    manifest = DatasetManifest(
        seed=seed,
        trace_count=len(trace_rows),
        traces_sha256=traces_hash,
        labels_sha256=labels_hash,
    )
    (output_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evals/datasets/supportlab-v1"))
    parser.add_argument("--seed", type=int, default=20260715)
    arguments = parser.parse_args()
    asyncio.run(generate_dataset(arguments.output, arguments.seed))


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Verify generator behavior and produce the committed fixture**

Run:

```bash
uv run pytest tests/evals/test_baselines.py tests/evals/test_generate_dataset.py -v
uv run afc-generate-dataset --output evals/datasets/supportlab-v1 --seed 20260715
uv run afc-generate-dataset --output .cache/supportlab-v1-check --seed 20260715
```

Expected: tests PASS; both generated manifests contain `trace_count: 20`; hashes in `evals/datasets/supportlab-v1/manifest.json` equal hashes in `.cache/supportlab-v1-check/manifest.json`.

- [ ] **Step 9: Verify full static checks**

Run:

```bash
uv run ruff check src tests
uv run mypy
uv run pytest -v
```

Expected: all commands exit 0.

- [ ] **Step 10: Commit the dataset and baselines**

```bash
git add src/afc/evals tests/evals evals/datasets/supportlab-v1
git commit -m "feat: generate SupportLab trace dataset"
```

## Task 10: Expose Trace Ingestion Through a Repository Boundary

**Files:**
- Create: `src/afc/trace_ir/repository.py`
- Create: `src/afc/api/routes/traces.py`
- Modify: `src/afc/api/app.py`
- Create: `tests/api/test_traces.py`

**Interfaces:**
- Consumes: `TraceIR`.
- Produces: `TraceRepository.save(trace)`, `TraceRepository.get(trace_id)`, `InMemoryTraceRepository`, and `POST /v1/traces` returning `201 {trace_id, run_id, span_count}`.

- [ ] **Step 1: Write failing ingestion tests**

Create `tests/api/test_traces.py`:

```python
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from afc.api.app import create_app
from afc.trace_ir.repository import InMemoryTraceRepository


def valid_trace_payload() -> dict[str, object]:
    now = datetime(2026, 7, 15, tzinfo=UTC).isoformat()
    return {
        "schema_version": "1.0",
        "trace_id": "trace-api-1",
        "run_id": "run-api-1",
        "spans": [
            {
                "trace_id": "trace-api-1",
                "span_id": "root",
                "parent_span_id": None,
                "name": "supportlab.run",
                "kind": "agent",
                "status": "ok",
                "started_at": now,
                "ended_at": now,
                "attributes": {},
            }
        ],
    }


def test_trace_ingestion_returns_created_summary() -> None:
    app = create_app(trace_repository=InMemoryTraceRepository())
    client = TestClient(app)

    response = client.post("/v1/traces", json=valid_trace_payload())

    assert response.status_code == 201
    assert response.json() == {
        "trace_id": "trace-api-1",
        "run_id": "run-api-1",
        "span_count": 1,
    }


def test_trace_ingestion_rejects_orphan_span() -> None:
    payload = valid_trace_payload()
    spans = payload["spans"]
    assert isinstance(spans, list)
    assert isinstance(spans[0], dict)
    spans[0]["parent_span_id"] = "missing"
    client = TestClient(create_app(trace_repository=InMemoryTraceRepository()))

    response = client.post("/v1/traces", json=payload)

    assert response.status_code == 422
```

- [ ] **Step 2: Run tests and observe the missing repository**

Run:

```bash
uv run pytest tests/api/test_traces.py -v
```

Expected: FAIL during collection because `afc.trace_ir.repository` does not exist.

- [ ] **Step 3: Implement the repository protocol**

Create `src/afc/trace_ir/repository.py`:

```python
from typing import Protocol

from afc.trace_ir.models import TraceIR


class TraceRepository(Protocol):
    async def save(self, trace: TraceIR) -> TraceIR: ...
    async def get(self, trace_id: str) -> TraceIR: ...


class InMemoryTraceRepository:
    def __init__(self) -> None:
        self._traces: dict[str, TraceIR] = {}

    async def save(self, trace: TraceIR) -> TraceIR:
        existing = self._traces.get(trace.trace_id)
        if existing is not None and existing != trace:
            raise ValueError(f"trace_id conflict: {trace.trace_id}")
        self._traces[trace.trace_id] = trace
        return trace

    async def get(self, trace_id: str) -> TraceIR:
        return self._traces[trace_id]
```

- [ ] **Step 4: Implement the trace route and dependency injection**

Create `src/afc/api/routes/traces.py`:

```python
from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict

from afc.trace_ir.models import TraceIR
from afc.trace_ir.repository import TraceRepository


class TraceCreated(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    trace_id: str
    run_id: str
    span_count: int


def build_trace_router(repository: TraceRepository) -> APIRouter:
    router = APIRouter(prefix="/v1/traces", tags=["traces"])

    @router.post("", response_model=TraceCreated, status_code=status.HTTP_201_CREATED)
    async def create_trace(trace: TraceIR) -> TraceCreated:
        saved = await repository.save(trace)
        return TraceCreated(
            trace_id=saved.trace_id,
            run_id=saved.run_id,
            span_count=len(saved.spans),
        )

    return router
```

Replace `src/afc/api/app.py` with:

```python
from fastapi import FastAPI

from afc.api.routes.health import router as health_router
from afc.api.routes.traces import build_trace_router
from afc.trace_ir.repository import InMemoryTraceRepository, TraceRepository


def create_app(trace_repository: TraceRepository | None = None) -> FastAPI:
    repository = trace_repository or InMemoryTraceRepository()
    application = FastAPI(title="Agent Failure Clinic", version="0.1.0")
    application.include_router(health_router)
    application.include_router(build_trace_router(repository))
    return application


app = create_app()
```

- [ ] **Step 5: Verify API contracts**

Run:

```bash
uv run pytest tests/api/test_health.py tests/api/test_traces.py -v
uv run ruff check src/afc/api src/afc/trace_ir/repository.py tests/api
uv run mypy
```

Expected: 3 API tests PASS; Ruff and mypy exit 0.

- [ ] **Step 6: Commit the ingestion boundary**

```bash
git add src/afc/api src/afc/trace_ir/repository.py tests/api
git commit -m "feat: expose TraceIR ingestion API"
```

## Task 11: Add Reproducible Containers, CI, ADR, and Quickstart

**Files:**
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `.github/workflows/ci.yml`
- Create: `README.md`
- Create: `docs/architecture/adr-001-traceir-boundary.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `afc.api.app:app`, the dataset CLI, and all Phase 1 tests.
- Produces: `docker compose up api phoenix`, a deterministic CI workflow, and documented Phase 1 acceptance commands.

- [ ] **Step 1: Add container ignore rules**

Create `.dockerignore`:

```text
.git
.venv
.cache
__pycache__
*.pyc
htmlcov
.pytest_cache
.mypy_cache
.ruff_cache
node_modules
postgres_data
redis_data
phoenix_data
```

- [ ] **Step 2: Add the API image**

Create `Dockerfile`:

```dockerfile
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "afc.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Add API and Phoenix Compose services**

Create `compose.yaml`:

```yaml
services:
  api:
    build: .
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 5s
      timeout: 3s
      retries: 10

  phoenix:
    image: arizephoenix/phoenix:latest
    ports:
      - "6006:6006"
      - "4317:4317"
    volumes:
      - phoenix_data:/root/.phoenix

volumes:
  phoenix_data:
```

The initial plan intentionally uses `latest` only for the local Phoenix viewer. During execution, run `docker image inspect arizephoenix/phoenix:latest --format '{{index .RepoDigests 0}}'`, replace `latest` with the returned immutable digest in the same task, and commit the digest. The API image remains fully pinned by `uv.lock` and the uv image tag.

- [ ] **Step 4: Add CI with dataset determinism enforcement**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.8.15"
          enable-cache: true
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: uv sync --frozen --group dev
      - run: uv run ruff check src tests
      - run: uv run mypy
      - run: uv run pytest --cov=afc --cov-report=term-missing
      - run: uv run afc-generate-dataset --output .cache/ci-dataset --seed 20260715
      - name: Verify frozen dataset hashes
        shell: python
        run: |
          import json
          from pathlib import Path
          expected = json.loads(Path("evals/datasets/supportlab-v1/manifest.json").read_text())
          actual = json.loads(Path(".cache/ci-dataset/manifest.json").read_text())
          assert expected == actual, (expected, actual)
      - run: docker compose config --quiet
```

- [ ] **Step 5: Write the TraceIR architecture decision**

Create `docs/architecture/adr-001-traceir-boundary.md`:

```markdown
# ADR-001: Use TraceIR as the Stable Diagnosis Boundary

## Status

Accepted on 2026-07-15.

## Context

Agent frameworks and observability backends expose different trace shapes. Diagnosis, regression generation, and evaluation must not depend directly on a LangGraph, Phoenix, or provider-specific object.

## Decision

AFC accepts OpenTelemetry/OpenInference-style spans through an adapter and converts them into immutable TraceIR v1 objects. Domain and evaluation modules depend only on TraceIR. The first adapter maps in-process OpenTelemetry `ReadableSpan` values; future adapters must preserve TraceIR invariants and pass the same contract tests.

## Consequences

- Positive: diagnosis code is portable and deterministic tests do not require Phoenix.
- Positive: source-specific secrets and unsupported attributes can be removed at the adapter boundary.
- Negative: the adapter owns schema evolution and must reject unsupported or malformed span graphs.
- Constraint: TraceIR v1 changes require a schema version change or backward-compatible optional fields.
```

- [ ] **Step 6: Write the verified Phase 1 quickstart**

Create `README.md`:

```markdown
# Agent Failure Clinic

Agent Failure Clinic turns failed tool-agent traces into evidence-backed diagnoses and regression artifacts. Phase 1 provides the reproducible SupportLab target agent, TraceIR v1, 20 labeled traces, weak baselines, and a trace ingestion API.

## Requirements

- Python 3.12
- uv 0.8.15 or newer in the 0.8 series
- Docker with Compose v2

## Local verification

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy
uv run pytest -v
uv run afc-generate-dataset --output .cache/readme-check --seed 20260715
docker compose config --quiet
```

## Run the API and trace viewer

```bash
docker compose up --build api phoenix
```

- AFC API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs
- Phoenix: http://localhost:6006

## Phase 1 dataset

`evals/datasets/supportlab-v1` contains 20 deterministic traces: four correct controls and two examples for each of the eight fixed failure classes. `manifest.json` records hashes used by CI to detect unreviewed dataset drift.

## Design documents

- `docs/superpowers/specs/2026-07-15-agent-failure-clinic-design.md`
- `docs/research/agent-project-landscape.md`
```

- [ ] **Step 7: Extend project ignore rules for generated verification data**

Ensure `.gitignore` contains these entries exactly once:

```text
.cache/
phoenix_data/
```

- [ ] **Step 8: Run the full Phase 1 acceptance suite**

Run:

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy
uv run pytest --cov=afc --cov-report=term-missing
uv run afc-generate-dataset --output .cache/final-check --seed 20260715
docker compose config --quiet
docker compose build api
```

Expected: all commands exit 0; 20 dataset labels generated; committed and regenerated manifest hashes match; API image builds.

- [ ] **Step 9: Smoke-test the containerized API**

Run:

```bash
docker compose up -d api
docker compose ps api
curl --fail http://localhost:8000/health
docker compose down
```

Expected: API becomes `healthy`; curl returns `{"status":"ok","service":"agent-failure-clinic"}`; Compose shuts down without removing committed files.

- [ ] **Step 10: Commit Phase 1 delivery infrastructure**

```bash
git add Dockerfile compose.yaml .dockerignore .github/workflows/ci.yml README.md .gitignore docs/architecture
git commit -m "chore: add Phase 1 delivery pipeline"
```

## Phase 1 Completion Gate

Before starting the diagnosis-workflow plan, run all commands below from a clean checkout:

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy
uv run pytest --cov=afc --cov-report=term-missing
uv run afc-generate-dataset --output .cache/completion-check --seed 20260715
docker compose config --quiet
docker compose build api
git diff --exit-code
git status --short
```

Completion evidence must show:

- all tests pass with no warning promoted to an error;
- Ruff and mypy exit 0;
- `manifest.json` matches the regenerated dataset hash;
- the API image builds and its health endpoint was smoke-tested;
- the working tree is clean;
- Git history contains one focused commit per task boundary.
