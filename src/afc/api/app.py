from fastapi import FastAPI

from afc.api.routes.diagnoses import build_diagnosis_router
from afc.api.routes.health import router as health_router
from afc.api.routes.traces import build_trace_router
from afc.diagnosis.deepseek import DeepSeekConfig, DeepSeekProvider
from afc.diagnosis.errors import ProviderConfigurationError
from afc.diagnosis.llm_diagnoser import LlmDiagnoser
from afc.diagnosis.models import DiagnoserKind
from afc.diagnosis.protocols import Diagnoser
from afc.diagnosis.rule_diagnoser import RuleDiagnoser
from afc.diagnosis.service import DiagnosisService
from afc.invariants.engine import InvariantEngine
from afc.invariants.supportlab import supportlab_rules
from afc.trace_ir.repository import InMemoryTraceRepository, TraceRepository


def build_default_diagnosis_service() -> DiagnosisService:
    diagnosers: dict[DiagnoserKind, Diagnoser] = {
        DiagnoserKind.RULES: RuleDiagnoser(InvariantEngine(supportlab_rules()))
    }
    try:
        deepseek_config = DeepSeekConfig.from_env()
    except ProviderConfigurationError:
        pass
    else:
        diagnosers[DiagnoserKind.DEEPSEEK] = LlmDiagnoser(
            DeepSeekProvider(deepseek_config)
        )
    return DiagnosisService(diagnosers)


def create_app(
    trace_repository: TraceRepository | None = None,
    diagnosis_service: DiagnosisService | None = None,
) -> FastAPI:
    repository = trace_repository or InMemoryTraceRepository()
    service = diagnosis_service or build_default_diagnosis_service()
    application = FastAPI(title="Agent Failure Clinic", version="0.1.0")
    application.include_router(health_router)
    application.include_router(build_trace_router(repository))
    application.include_router(build_diagnosis_router(repository, service))
    return application


app = create_app()
