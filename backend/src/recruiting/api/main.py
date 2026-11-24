"""ASGI entry point: `uvicorn recruiting.api.main:app`. Builds services from RAP_* settings."""

from recruiting.api.app import create_app
from recruiting.api.services import build_services
from recruiting.config import Settings

settings = Settings()
app = create_app(build_services(settings), settings.environment, settings.run_scheduler)
