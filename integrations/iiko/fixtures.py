from __future__ import annotations

from decimal import Decimal
from typing import Any


def synthetic_olap_sales_payload(
    *,
    organization_id: str,
    business_date: str,
) -> dict[str, Any]:
    return {
        "daily": {
            "data": [
                {
                    "OpenDate.Typed": business_date,
                    "Department.Id": organization_id,
                    "Storned": "FALSE",
                    "OrderDeleted": "NOT_DELETED",
                    "DishSumInt": Decimal("34645"),
                    "DishDiscountSumInt": Decimal("31955.25"),
                    "DiscountSum": Decimal("1000"),
                    "IncreaseSum": Decimal("0"),
                    "DishReturnSum": Decimal("0"),
                    "UniqOrderId.OrdersCount": Decimal("10"),
                }
            ],
            "summary": [],
        },
        "payments": {
            "data": [
                {
                    "OpenDate.Typed": business_date,
                    "Department.Id": organization_id,
                    "PayTypes.Group": "CASH",
                    "PayTypes.GUID": "synthetic-cash",
                    "PayTypes": "Synthetic cash",
                    "Storned": "FALSE",
                    "OrderDeleted": "NOT_DELETED",
                    "DishSumInt": Decimal("10000"),
                    "DishDiscountSumInt": Decimal("10000"),
                    "DiscountSum": Decimal("0"),
                    "IncreaseSum": Decimal("0"),
                    "DishReturnSum": Decimal("0"),
                    "UniqOrderId.OrdersCount": Decimal("7"),
                },
                {
                    "OpenDate.Typed": business_date,
                    "Department.Id": organization_id,
                    "PayTypes.Group": "CARD",
                    "PayTypes.GUID": "synthetic-card",
                    "PayTypes": "Synthetic card",
                    "Storned": "FALSE",
                    "OrderDeleted": "NOT_DELETED",
                    "DishSumInt": Decimal("24645"),
                    "DishDiscountSumInt": Decimal("21955.25"),
                    "DiscountSum": Decimal("1000"),
                    "IncreaseSum": Decimal("0"),
                    "DishReturnSum": Decimal("0"),
                    "UniqOrderId.OrdersCount": Decimal("6"),
                },
            ],
            "summary": [],
        },
        "products": {
            "data": [
                {
                    "OpenDate.Typed": business_date,
                    "Department.Id": organization_id,
                    "DishId": "synthetic-product-1",
                    "DishName": "Synthetic product A",
                    "DishSize.Id": None,
                    "Storned": "FALSE",
                    "OrderDeleted": "NOT_DELETED",
                    "DishAmountInt": Decimal("50"),
                    "DishSumInt": Decimal("20000"),
                    "DishDiscountSumInt": Decimal("18000"),
                    "DishReturnSum": Decimal("0"),
                },
                {
                    "OpenDate.Typed": business_date,
                    "Department.Id": organization_id,
                    "DishId": "synthetic-product-2",
                    "DishName": "Synthetic product B",
                    "DishSize.Id": "synthetic-size-1",
                    "Storned": "FALSE",
                    "OrderDeleted": "NOT_DELETED",
                    "DishAmountInt": Decimal("33"),
                    "DishSumInt": Decimal("14645"),
                    "DishDiscountSumInt": Decimal("13955.25"),
                    "DishReturnSum": Decimal("0"),
                },
            ],
            "summary": [],
        },
    }
