from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

ORG_ID = "department-1"
BUSINESS_DATE = "2026-07-16"
TOKEN = "123e4567-e89b-12d3-a456-426614174000"


def olap_sales_payload() -> dict[str, Any]:
    return {
        "daily": {
            "data": [
                {
                    "OpenDate.Typed": BUSINESS_DATE,
                    "Department.Id": ORG_ID,
                    "Storned": "FALSE",
                    "OrderDeleted": "NOT_DELETED",
                    "DishSumInt": Decimal("34645"),
                    "DishDiscountSumInt": Decimal("31955.25"),
                    "DiscountSum": Decimal("1000"),
                    "IncreaseSum": Decimal("0"),
                    "DishReturnSum": Decimal("0"),
                    "UniqOrderId.OrdersCount": Decimal("10"),
                    "Extra.Unknown": "ignored",
                }
            ],
            "summary": [],
        },
        "payments": {
            "data": [
                {
                    "OpenDate.Typed": BUSINESS_DATE,
                    "Department.Id": ORG_ID,
                    "PayTypes.Group": "CASH",
                    "PayTypes.GUID": "pay-cash",
                    "PayTypes": "Cash",
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
                    "OpenDate.Typed": BUSINESS_DATE,
                    "Department.Id": ORG_ID,
                    "PayTypes.Group": "CARD",
                    "PayTypes.GUID": "pay-card",
                    "PayTypes": "Card",
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
                    "OpenDate.Typed": BUSINESS_DATE,
                    "Department.Id": ORG_ID,
                    "DishId": "product-1",
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
                    "OpenDate.Typed": BUSINESS_DATE,
                    "Department.Id": ORG_ID,
                    "DishId": "product-2",
                    "DishName": "Synthetic product B",
                    "DishSize.Id": "size-1",
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


def copied_payload() -> dict[str, Any]:
    return deepcopy(olap_sales_payload())
