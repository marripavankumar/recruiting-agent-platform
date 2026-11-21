from fastapi import FastAPI

from recruiting.config import Settings

settings = Settings()
app = FastAPI(title="Recruiting Agent Platform", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}
