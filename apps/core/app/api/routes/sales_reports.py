from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from apps.core.app.core.config import get_settings
from apps.core.app.db.session import get_db
from apps.core.app.repositories.iiko_sales import IikoSalesRepository

router = APIRouter()


def _money(value: Decimal | None) -> str:
    return format(value or Decimal("0"), "f")


@router.get("/daily")
def daily_sales_report(
    db: Annotated[Session, Depends(get_db)],
    business_date: Annotated[date, Query(alias="date")],
    organization_id: str | None = None,
    top_products: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, object]:
    settings = get_settings()
    resolved_organization_id = organization_id or settings.iiko_organization_id
    if not resolved_organization_id:
        raise HTTPException(status_code=422, detail="organization_id is required")

    repository = IikoSalesRepository(db)
    daily = repository.get_daily(resolved_organization_id, business_date)
    if daily is None:
        raise HTTPException(status_code=404, detail="sales day was not imported")

    payments = {
        "cash": Decimal("0"),
        "card": Decimal("0"),
        "other": Decimal("0"),
        "unknown": Decimal("0"),
    }
    for payment in repository.list_payments(resolved_organization_id, business_date):
        payments[payment.payment_category] = (
            payments.get(payment.payment_category, Decimal("0")) + payment.sales_amount
        )

    products = repository.list_top_products(
        resolved_organization_id,
        business_date,
        limit=top_products,
    )
    return {
        "status": daily.result_status,
        "organization_id": daily.organization_id,
        "business_date": daily.business_date.isoformat(),
        "gross_sales": _money(daily.gross_sales),
        "reported_discounts": _money(daily.reported_discounts),
        "reported_increases": _money(daily.reported_increases),
        "net_sales": _money(daily.net_sales),
        "unexplained_adjustment": _money(daily.unexplained_adjustment),
        "refunds": _money(daily.refunds),
        "checks_count": daily.checks_count,
        "average_check": _money(daily.average_check),
        "payments": {key: _money(value) for key, value in payments.items()},
        "top_products": [
            {
                "product_id": product.product_id,
                "name": product.product_name_snapshot,
                "quantity": format(product.quantity, "f"),
                "net_sales": _money(product.net_sales),
            }
            for product in products
        ],
        "source_checksum": daily.source_checksum,
        "imported_at": daily.imported_at.isoformat(),
        "reconciliation_error_code": daily.reconciliation_error_code,
    }
