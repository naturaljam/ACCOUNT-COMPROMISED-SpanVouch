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
