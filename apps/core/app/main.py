from fastapi import FastAPI

from apps.core.app.api.routes.health import router as health_router
from apps.core.app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(health_router, prefix="/health", tags=["health"])


@app.get("/", tags=["service"])
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "environment": settings.app_env,
        "status": "running",
    }
