from fastapi import FastAPI, Request

from recruiting.api import (
    admin,
    approvals,
    candidates,
    companies,
    engagement,
    followups,
    inbound,
    jobs,
    reports,
    submissions,
)
from recruiting.api.audit import AccessLog
from recruiting.api.services import Services


def create_app(services: Services, environment: str = "local") -> FastAPI:
    app = FastAPI(title="Recruiting Agent Platform", version="0.1.0")
    app.state.services = services
    app.state.access_log = AccessLog()

    @app.middleware("http")
    async def log_denied_access(request: Request, call_next):
        response = await call_next(request)
        if response.status_code in (401, 403):
            app.state.access_log.record(request, response.status_code)
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": environment}

    app.include_router(approvals.router)
    app.include_router(admin.router)
    app.include_router(companies.router)
    app.include_router(jobs.router)
    app.include_router(candidates.router)
    app.include_router(engagement.router)
    app.include_router(submissions.router)
    app.include_router(followups.router)
    app.include_router(reports.router)
    app.include_router(inbound.router)
    return app
