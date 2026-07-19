from fastapi import FastAPI

from apps.core.app.api.routes.health import router as health_router
from apps.core.app.api.routes.report_outbox import router as report_outbox_router
from apps.core.app.api.routes.sales_automation import router as sales_automation_router
from apps.core.app.api.routes.sales_reports import router as sales_reports_router
from apps.core.app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(
    sales_reports_router,
    prefix="/api/v1/reports/sales",
    tags=["sales-reports"],
)
app.include_router(
    sales_automation_router,
    prefix="/api/v1/operations/sales-automation",
    tags=["sales-automation"],
)
app.include_router(
    report_outbox_router,
    prefix="/api/v1/internal/report-outbox",
    tags=["internal-report-outbox"],
)


@app.get("/", tags=["service"])
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "environment": settings.app_env,
        "status": "running",
    }
