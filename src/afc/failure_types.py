from enum import StrEnum


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


SUPPORTED_DIAGNOSIS_FAILURE_TYPES = frozenset(
    {
        FailureType.WRONG_TOOL,
        FailureType.INVALID_ARGUMENT,
        FailureType.POLICY_VIOLATION,
        FailureType.LOOP_OR_BUDGET_EXHAUSTION,
        FailureType.INVALID_FINAL_STATE,
    }
)
