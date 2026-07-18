from datetime import date
from decimal import Decimal

import pytest
from fixtures_iiko_sales import BUSINESS_DATE, ORG_ID, copied_payload

from integrations.iiko.sales import (
    DISCOUNT_RECONCILIATION_ERROR,
    PaymentCategory,
    SalesNormalizationError,
    calculate_sales_checksum,
    decimal_from_value,
    normalize_iiko_sales_payload,
)
from integrations.iiko.schemas import ResultStatus


def normalize(payload: dict | None = None, payment_map: dict[str, str] | None = None):
    return normalize_iiko_sales_payload(
        payload or copied_payload(),
        organization_id=ORG_ID,
        business_date=date.fromisoformat(BUSINESS_DATE),
        payment_category_map=payment_map,
    )


def test_daily_checks_count_does_not_sum_payment_rows_for_mixed_payments() -> None:
    report = normalize()

    assert report.daily.checks_count == 10
    assert sum(payment.transactions_count or 0 for payment in report.payments) == 0


def test_payment_totals_equal_daily_net_sales() -> None:
    report = normalize()

    assert sum(payment.sales_amount for payment in report.payments) == report.daily.net_sales


def test_product_totals_equal_daily_gross_and_net_sales() -> None:
    report = normalize()

    assert sum(product.quantity for product in report.products) == Decimal("83")
    assert sum(product.gross_sales for product in report.products) == report.daily.gross_sales
    assert sum(product.net_sales for product in report.products) == report.daily.net_sales


def test_discount_reconciliation_mismatch_marks_daily_partial() -> None:
    report = normalize()

    assert report.daily.result_status is ResultStatus.PARTIAL
    assert report.daily.reconciliation_error_code == DISCOUNT_RECONCILIATION_ERROR
    assert report.daily.unexplained_adjustment == Decimal("1689.75")


def test_non_cash_without_mapping_becomes_other_and_partial() -> None:
    payload = copied_payload()
    payload["payments"]["data"][1]["PayTypes.Group"] = "NON_CASH"

    report = normalize(payload)

    assert report.payments[1].payment_category is PaymentCategory.OTHER
    assert report.result_status is ResultStatus.PARTIAL


def test_explicit_payment_guid_mapping_has_priority() -> None:
    payload = copied_payload()
    payload["payments"]["data"][1]["PayTypes.Group"] = "NON_CASH"

    report = normalize(payload, payment_map={"pay-card": "card"})

    assert report.payments[1].payment_category is PaymentCategory.CARD


def test_unknown_payment_group_becomes_unknown_and_partial() -> None:
    payload = copied_payload()
    payload["payments"]["data"][1]["PayTypes.Group"] = "CRYPTO"

    report = normalize(payload)

    assert report.payments[1].payment_category is PaymentCategory.UNKNOWN
    assert report.result_status is ResultStatus.PARTIAL


def test_unknown_order_status_marks_result_unknown() -> None:
    payload = copied_payload()
    payload["daily"]["data"].append(
        {
            **payload["daily"]["data"][0],
            "Storned": "MAYBE",
            "DishSumInt": Decimal("1"),
        }
    )

    report = normalize(payload)

    assert report.result_status is ResultStatus.UNKNOWN
    assert "unknown_order_status" in report.diagnostics["error_codes"]


def test_decimal_parser_rejects_float_values() -> None:
    with pytest.raises(SalesNormalizationError):
        decimal_from_value(1.1, field_name="money")


def test_zero_refund_is_valid_but_refund_quantity_is_partial_and_null() -> None:
    report = normalize()

    assert report.daily.refunds == Decimal("0.00")
    assert report.products[0].refund_quantity is None
    assert report.diagnostics["refund_quantity_status"] == "partial"


def test_checksum_is_independent_of_response_row_order() -> None:
    payload_a = copied_payload()
    payload_b = copied_payload()
    payload_b["payments"]["data"] = list(reversed(payload_b["payments"]["data"]))
    payload_b["products"]["data"] = list(reversed(payload_b["products"]["data"]))

    report_a = normalize(payload_a)
    report_b = normalize(payload_b)

    assert report_a.daily.source_checksum == report_b.daily.source_checksum


def test_mismatching_organization_is_unknown_validation_error() -> None:
    payload = copied_payload()
    payload["daily"]["data"][0]["Department.Id"] = "other"

    with pytest.raises(SalesNormalizationError) as error:
        normalize(payload)

    assert error.value.error_code == "organization_mismatch"


def test_zero_sales_day_is_valid() -> None:
    payload = {"daily": {"data": []}, "payments": {"data": []}, "products": {"data": []}}

    report = normalize(payload)

    assert report.daily.net_sales == Decimal("0.00")
    assert report.daily.checks_count == 0


def test_canonical_checksum_ignores_dict_field_order() -> None:
    left = [{"a": Decimal("1"), "b": "x"}]
    right = [{"b": "x", "a": Decimal("1")}]

    assert calculate_sales_checksum(left) == calculate_sales_checksum(right)
