from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.core.app.core.config import get_settings
from apps.core.app.db.session import get_db
from apps.core.app.services.iiko_sales_automation import (
    create_sales_automation_config_from_settings,
    sales_automation_status,
)

router = APIRouter()


@router.get("/status")
def get_sales_automation_status(
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    settings = get_settings()
    return sales_automation_status(
        db,
        create_sales_automation_config_from_settings(settings),
    )
