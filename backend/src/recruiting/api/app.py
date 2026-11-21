from fastapi import FastAPI

from recruiting.api import approvals
from recruiting.api.services import Services


def create_app(services: Services, environment: str = "local") -> FastAPI:
    app = FastAPI(title="Recruiting Agent Platform", version="0.1.0")
    app.state.services = services

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": environment}

    app.include_router(approvals.router)
    return app
