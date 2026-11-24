from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from recruiting.api import (
    admin,
    agents,
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
from recruiting.api.deps import UserDep
from recruiting.api.services import Services
from recruiting.scheduler import start_in_background


def create_app(
    services: Services, environment: str = "local", run_scheduler: bool = False
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        stop = start_in_background(services) if run_scheduler else None
        yield
        if stop is not None:
            stop.set()

    app = FastAPI(title="Recruiting Agent Platform", version="0.1.0", lifespan=lifespan)
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

    @app.get("/me")
    def me(user: UserDep) -> dict:
        """The signed-in user: the web app shows the screens for this role."""
        return {
            "id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role.value,
            "client_company_id": user.client_company_id,
            "environment": environment,
        }

    app.include_router(approvals.router)
    app.include_router(admin.router)
    app.include_router(companies.router)
    app.include_router(jobs.router)
    app.include_router(candidates.router)
    app.include_router(engagement.router)
    app.include_router(submissions.router)
    app.include_router(followups.router)
    app.include_router(reports.router)
    app.include_router(agents.router)
    app.include_router(inbound.router)
    return app
