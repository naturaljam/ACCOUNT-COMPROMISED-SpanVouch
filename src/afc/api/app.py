from fastapi import FastAPI

from afc.api.routes.health import router as health_router


def create_app() -> FastAPI:
    application = FastAPI(title="Agent Failure Clinic", version="0.1.0")
    application.include_router(health_router)
    return application


app = create_app()
