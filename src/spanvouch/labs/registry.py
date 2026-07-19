from __future__ import annotations

from typing import cast

from spanvouch.contracts.sanitization import sanitize_diagnostic_value
from spanvouch.labs.opslab.environment import OpsLabEnvironmentRegistry
from spanvouch.labs.runtime import (
    LabEnvironment,
    LabEnvironmentRegistry,
    LabScenario,
    RuntimeFailure,
    RuntimeFailureCategory,
)
from spanvouch.labs.supportlab.environment import SupportLabEnvironmentRegistry


class LabRegistryIncompatibilityError(RuntimeError):
    def __init__(self, failure: RuntimeFailure) -> None:
        super().__init__(failure.code)
        self.failure = failure


class CombinedLabEnvironmentRegistry:
    def __init__(
        self,
        *,
        supportlab: LabEnvironmentRegistry | None = None,
        opslab: LabEnvironmentRegistry | None = None,
    ) -> None:
        self._supportlab = supportlab or SupportLabEnvironmentRegistry()
        self._opslab = opslab or OpsLabEnvironmentRegistry()

    def build(self, scenario: LabScenario) -> LabEnvironment:
        if scenario.domain == "supportlab":
            return self._supportlab.build(scenario)
        if scenario.domain == "opslab":
            return self._opslab.build(scenario)
        raise LabRegistryIncompatibilityError(
            RuntimeFailure.from_message(
                category=RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
                code="unsupported_domain",
                retryable=False,
                sanitized_message=cast(
                    str, sanitize_diagnostic_value(str(scenario.domain))
                ),
            )
        )
