from __future__ import annotations

import pytest

from spanvouch.security.identity import Principal, Role
from spanvouch.security.policy import AuthorizationError, Capability, Policy


@pytest.mark.parametrize("capability", list(Capability))
def test_system_admin_has_every_capability(capability: Capability) -> None:
    principal = Principal(key_id="admin-key", project_id=None, roles=(Role.ADMIN,))

    Policy.require(principal, capability)


@pytest.mark.parametrize(
    "capability",
    (
        Capability.READ_PROJECT,
        Capability.INGEST_TRACE,
        Capability.RUN_DIAGNOSIS,
        Capability.CREATE_REVIEW,
        Capability.RESUME_REVIEW,
    ),
)
def test_operator_can_run_operational_work(capability: Capability) -> None:
    principal = Principal(key_id="operator-key", project_id="project-a", roles=(Role.OPERATOR,))

    Policy.require(principal, capability)


@pytest.mark.parametrize(
    "capability",
    (Capability.MANAGE_PROJECTS, Capability.MANAGE_KEYS, Capability.DECIDE_REVIEW),
)
def test_operator_cannot_manage_or_decide_reviews(capability: Capability) -> None:
    principal = Principal(key_id="operator-key", project_id="project-a", roles=(Role.OPERATOR,))

    with pytest.raises(AuthorizationError, match="not authorized"):
        Policy.require(principal, capability)


def test_reviewer_can_decide_and_view_without_operational_write_access() -> None:
    principal = Principal(key_id="reviewer-key", project_id="project-a", roles=(Role.REVIEWER,))

    Policy.require(principal, Capability.READ_PROJECT)
    Policy.require(principal, Capability.DECIDE_REVIEW)
    with pytest.raises(AuthorizationError, match="not authorized"):
        Policy.require(principal, Capability.INGEST_TRACE)


def test_viewer_is_read_only() -> None:
    principal = Principal(key_id="viewer-key", project_id="project-a", roles=(Role.VIEWER,))

    Policy.require(principal, Capability.READ_PROJECT)
    with pytest.raises(AuthorizationError, match="not authorized"):
        Policy.require(principal, Capability.CREATE_REVIEW)
