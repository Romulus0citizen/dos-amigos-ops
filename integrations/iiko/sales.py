from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from integrations.iiko.schemas import ResultStatus

CENT = Decimal("0.01")
ZERO = Decimal("0")
NORMAL_STORNED = "FALSE"
NORMAL_ORDER_DELETED = "NOT_DELETED"
DISCOUNT_RECONCILIATION_ERROR = "IIKO_DISCOUNT_RECONCILIATION_MISMATCH"


class PaymentCategory(StrEnum):
    CASH = "cash"
    CARD = "card"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SalesDailySummary:
    organization_id: str
    business_date: date
    currency_code: str | None
    gross_sales: Decimal
    reported_discounts: Decimal
    reported_increases: Decimal
    net_sales: Decimal
    unexplained_adjustment: Decimal
    refunds: Decimal
    checks_count: int
    guests_count: int | None
    average_check: Decimal
    source_rows_count: int
    source_checksum: str
    result_status: ResultStatus
    reconciliation_error_code: str | None = None


@dataclass(frozen=True)
class SalesPaymentSummary:
    organization_id: str
    business_date: date
    payment_type_id: str | None
    payment_type_name: str
    payment_category: PaymentCategory
    sales_amount: Decimal
    refund_amount: Decimal
    transactions_count: int | None

    @property
    def payment_type_key(self) -> str:
        if self.payment_type_id:
            return self.payment_type_id
        normalized_name = " ".join(self.payment_type_name.lower().split())
        return f"name:{normalized_name or 'unknown'}"


@dataclass(frozen=True)
class SalesProductSummary:
    organization_id: str
    business_date: date
    product_id: str
    product_name_snapshot: str
    product_size_id: str | None
    quantity: Decimal
    gross_sales: Decimal
    discounts: Decimal
    net_sales: Decimal
    refund_quantity: Decimal | None
    refund_amount: Decimal

    @property
    def product_size_key(self) -> str:
        return self.product_size_id or "__none__"


@dataclass(frozen=True)
class NormalizedSalesReport:
    daily: SalesDailySummary
    payments: list[SalesPaymentSummary]
    products: list[SalesProductSummary]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def result_status(self) -> ResultStatus:
        return self.daily.result_status


class SalesNormalizationError(ValueError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def decimal_from_value(value: Any, *, field_name: str) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        result = Decimal(value.strip() or "0")
    else:
        raise SalesNormalizationError(
            "invalid_decimal_field",
            f"Field {field_name} must be Decimal-compatible",
        )

    if not result.is_finite():
        raise SalesNormalizationError("invalid_decimal_field", f"Field {field_name} is not finite")
    return result


def date_from_value(value: Any, *, field_name: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise SalesNormalizationError("invalid_date_field", f"Field {field_name} is not a date")


def int_from_decimal(value: Decimal, *, field_name: str) -> int:
    if value != value.to_integral_value():
        raise SalesNormalizationError(
            "invalid_integer_field", f"Field {field_name} is not integral"
        )
    result = int(value)
    if result < 0:
        raise SalesNormalizationError("invalid_integer_field", f"Field {field_name} is negative")
    return result


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def categorize_payment(
    group: str | None, guid: str | None, mapping: dict[str, str]
) -> PaymentCategory:
    if guid and guid in mapping:
        return PaymentCategory(mapping[guid])

    if group == "CASH":
        return PaymentCategory.CASH
    if group == "CARD":
        return PaymentCategory.CARD
    if group == "NON_CASH":
        return PaymentCategory.OTHER
    return PaymentCategory.UNKNOWN


def _normal_sales_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    normal_rows: list[dict[str, Any]] = []
    unknown_storned: set[str] = set()
    unknown_deleted: set[str] = set()

    for row in rows:
        storned = str(row.get("Storned", ""))
        deleted = str(row.get("OrderDeleted", ""))
        if storned == NORMAL_STORNED and deleted == NORMAL_ORDER_DELETED:
            normal_rows.append(row)
            continue
        if storned not in {NORMAL_STORNED, "TRUE"}:
            unknown_storned.add(storned)
        if deleted not in {NORMAL_ORDER_DELETED, "DELETED"}:
            unknown_deleted.add(deleted)

    return normal_rows, unknown_storned, unknown_deleted


def _validate_row_scope(
    rows: list[dict[str, Any]],
    *,
    organization_id: str,
    business_date: date,
) -> None:
    for row in rows:
        if str(row.get("Department.Id")) != organization_id:
            raise SalesNormalizationError("organization_mismatch", "OLAP row organization mismatch")
        row_date = date_from_value(row.get("OpenDate.Typed"), field_name="OpenDate.Typed")
        if row_date != business_date:
            raise SalesNormalizationError("business_date_mismatch", "OLAP row date mismatch")


def _sum(rows: list[dict[str, Any]], field_name: str) -> Decimal:
    return sum(
        (decimal_from_value(row.get(field_name), field_name=field_name) for row in rows), ZERO
    )


def normalize_iiko_sales_payload(
    payload: dict[str, Any],
    *,
    organization_id: str,
    business_date: date,
    payment_category_map: dict[str, str] | None = None,
) -> NormalizedSalesReport:
    payment_category_map = payment_category_map or {}
    daily_rows = list(payload.get("daily", {}).get("data", []))
    payment_rows = list(payload.get("payments", {}).get("data", []))
    product_rows = list(payload.get("products", {}).get("data", []))
    if (
        not isinstance(daily_rows, list)
        or not isinstance(payment_rows, list)
        or not isinstance(product_rows, list)
    ):
        raise SalesNormalizationError("invalid_olap_payload", "OLAP payload data must be lists")

    all_rows = [*daily_rows, *payment_rows, *product_rows]
    _validate_row_scope(all_rows, organization_id=organization_id, business_date=business_date)

    normal_daily, unknown_storned, unknown_deleted = _normal_sales_rows(daily_rows)
    normal_payments, payment_unknown_storned, payment_unknown_deleted = _normal_sales_rows(
        payment_rows
    )
    normal_products, product_unknown_storned, product_unknown_deleted = _normal_sales_rows(
        product_rows
    )
    unknown_storned.update(payment_unknown_storned)
    unknown_storned.update(product_unknown_storned)
    unknown_deleted.update(payment_unknown_deleted)
    unknown_deleted.update(product_unknown_deleted)

    gross_sales = quantize_money(_sum(normal_daily, "DishSumInt"))
    reported_discounts = quantize_money(_sum(normal_daily, "DiscountSum"))
    reported_increases = quantize_money(_sum(normal_daily, "IncreaseSum"))
    net_sales = quantize_money(_sum(normal_daily, "DishDiscountSumInt"))
    refunds = quantize_money(_sum(normal_daily, "DishReturnSum"))
    checks_count = int_from_decimal(
        _sum(normal_daily, "UniqOrderId.OrdersCount"), field_name="checks"
    )
    average_check = quantize_money(net_sales / Decimal(checks_count)) if checks_count else ZERO
    unexplained_adjustment = quantize_money(
        gross_sales - reported_discounts + reported_increases - net_sales
    )

    payments: list[SalesPaymentSummary] = []
    unknown_payment_groups: set[str] = set()
    non_cash_without_mapping = False
    for row in normal_payments:
        group = str(row.get("PayTypes.Group", ""))
        guid_value = row.get("PayTypes.GUID")
        guid = str(guid_value) if guid_value else None
        category = categorize_payment(group, guid, payment_category_map)
        if category is PaymentCategory.UNKNOWN:
            unknown_payment_groups.add(group)
        if group == "NON_CASH" and not (guid and guid in payment_category_map):
            non_cash_without_mapping = True
        payments.append(
            SalesPaymentSummary(
                organization_id=organization_id,
                business_date=business_date,
                payment_type_id=guid,
                payment_type_name=str(row.get("PayTypes") or "Unknown"),
                payment_category=category,
                sales_amount=quantize_money(
                    decimal_from_value(
                        row.get("DishDiscountSumInt"), field_name="DishDiscountSumInt"
                    )
                ),
                refund_amount=quantize_money(
                    decimal_from_value(row.get("DishReturnSum"), field_name="DishReturnSum")
                ),
                transactions_count=None,
            )
        )

    products: list[SalesProductSummary] = []
    for row in normal_products:
        product_id = str(row.get("DishId") or "")
        if not product_id:
            raise SalesNormalizationError("missing_product_id", "Product row is missing DishId")
        product_size_value = row.get("DishSize.Id")
        gross = quantize_money(decimal_from_value(row.get("DishSumInt"), field_name="DishSumInt"))
        net = quantize_money(
            decimal_from_value(row.get("DishDiscountSumInt"), field_name="DishDiscountSumInt")
        )
        products.append(
            SalesProductSummary(
                organization_id=organization_id,
                business_date=business_date,
                product_id=product_id,
                product_name_snapshot=str(row.get("DishName") or ""),
                product_size_id=str(product_size_value) if product_size_value else None,
                quantity=decimal_from_value(row.get("DishAmountInt"), field_name="DishAmountInt"),
                gross_sales=gross,
                discounts=quantize_money(gross - net),
                net_sales=net,
                refund_quantity=None,
                refund_amount=quantize_money(
                    decimal_from_value(row.get("DishReturnSum"), field_name="DishReturnSum")
                ),
            )
        )

    payment_total = quantize_money(sum((payment.sales_amount for payment in payments), ZERO))
    product_net_total = quantize_money(sum((product.net_sales for product in products), ZERO))
    product_gross_total = quantize_money(sum((product.gross_sales for product in products), ZERO))
    diagnostics: dict[str, Any] = {
        "unknown_storned": sorted(unknown_storned),
        "unknown_order_deleted": sorted(unknown_deleted),
        "unknown_payment_groups": sorted(unknown_payment_groups),
        "non_cash_without_mapping": non_cash_without_mapping,
        "payment_total": str(payment_total),
        "product_net_total": str(product_net_total),
        "product_gross_total": str(product_gross_total),
        "refund_quantity_status": ResultStatus.PARTIAL.value,
        "refund_amount_status": ResultStatus.PARTIAL.value,
    }

    error_codes: list[str] = []
    result_status = ResultStatus.PROVEN
    if abs(unexplained_adjustment) > CENT:
        result_status = ResultStatus.PARTIAL
        error_codes.append(DISCOUNT_RECONCILIATION_ERROR)
    if non_cash_without_mapping or unknown_payment_groups:
        result_status = ResultStatus.PARTIAL
    if unknown_storned or unknown_deleted:
        result_status = ResultStatus.UNKNOWN
        error_codes.append("unknown_order_status")
    if abs(payment_total - net_sales) > CENT:
        result_status = ResultStatus.UNKNOWN
        error_codes.append("payment_total_mismatch")
    if abs(product_net_total - net_sales) > CENT or abs(product_gross_total - gross_sales) > CENT:
        result_status = ResultStatus.UNKNOWN
        error_codes.append("product_total_mismatch")

    checksum = calculate_sales_checksum(
        {
            "daily_rows": normal_daily,
            "payment_rows": normal_payments,
            "product_rows": normal_products,
            "organization_id": organization_id,
            "business_date": business_date,
        }
    )
    daily = SalesDailySummary(
        organization_id=organization_id,
        business_date=business_date,
        currency_code=None,
        gross_sales=gross_sales,
        reported_discounts=reported_discounts,
        reported_increases=reported_increases,
        net_sales=net_sales,
        unexplained_adjustment=unexplained_adjustment,
        refunds=refunds,
        checks_count=checks_count,
        guests_count=None,
        average_check=average_check,
        source_rows_count=len(normal_daily) + len(normal_payments) + len(normal_products),
        source_checksum=checksum,
        result_status=result_status,
        reconciliation_error_code=error_codes[0] if error_codes else None,
    )
    diagnostics["error_codes"] = error_codes
    return NormalizedSalesReport(
        daily=daily, payments=payments, products=products, diagnostics=diagnostics
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted(
            (_canonicalize(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    return value


def calculate_sales_checksum(value: Any) -> str:
    canonical = json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
