from spanvouch.adapters.models.deepseek import DeepSeekConfig, DeepSeekProvider
from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.diagnosis.errors import ProviderConfigurationError
from spanvouch.diagnosis.llm_diagnoser import LlmDiagnoser
from spanvouch.diagnosis.protocols import Diagnoser
from spanvouch.diagnosis.rule_diagnoser import RuleDiagnoser
from spanvouch.labs.supportlab.invariants import supportlab_rules
from spanvouch.review.policy import DEFAULT_REVIEW_POLICY_VERSION
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine
from spanvouch.verification.protocols import Verifier
from spanvouch.verification.semantic import SemanticVerifier


def default_runtime() -> tuple[dict[str, Diagnoser], DeterministicVerifier, Verifier | None]:
    diagnosers, deterministic_verifier = deterministic_runtime()
    semantic_verifier: Verifier | None = None
    try:
        deepseek_config = DeepSeekConfig.from_env()
    except ProviderConfigurationError:
        pass
    else:
        provider = DeepSeekProvider(deepseek_config)
        diagnosers[DiagnoserKind.DEEPSEEK.value] = LlmDiagnoser(provider)
        semantic_verifier = SemanticVerifier(
            provider,
            provider_id="deepseek",
            model="deepseek-v4-flash",
        )
    return diagnosers, deterministic_verifier, semantic_verifier


def deterministic_runtime() -> tuple[dict[str, Diagnoser], DeterministicVerifier]:
    engine = InvariantEngine(supportlab_rules())
    diagnosers: dict[str, Diagnoser] = {
        DiagnoserKind.RULES.value: RuleDiagnoser(engine)
    }
    deterministic_verifier = DeterministicVerifier(
        engine,
        policy_version=DEFAULT_REVIEW_POLICY_VERSION,
    )
    return diagnosers, deterministic_verifier
