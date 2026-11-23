from fastapi import FastAPI

from recruiting.api import admin, approvals, candidates, companies, engagement, inbound, jobs
from recruiting.api.services import Services


def create_app(services: Services, environment: str = "local") -> FastAPI:
    app = FastAPI(title="Recruiting Agent Platform", version="0.1.0")
    app.state.services = services

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": environment}

    app.include_router(approvals.router)
    app.include_router(admin.router)
    app.include_router(companies.router)
    app.include_router(jobs.router)
    app.include_router(candidates.router)
    app.include_router(engagement.router)
    app.include_router(inbound.router)
    return app
