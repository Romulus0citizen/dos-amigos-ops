from datetime import date

from integrations.iiko.probe_sales import (
    SALES_DISCOVERY_ERROR_CODE,
    build_blocked_sales_discovery_report,
    default_probe_date,
)


def test_blocked_sales_discovery_report_is_sanitized_and_explicit() -> None:
    report = build_blocked_sales_discovery_report(
        requested_date=date(2026, 7, 16),
        organization_id="department-1",
    )

    rendered = repr(report)

    assert report["status"] == "partial"
    assert report["error_code"] == SALES_DISCOVERY_ERROR_CODE
    assert report["sales_source_confirmed"] is True
    assert report["endpoint"] == "/api/v2/reports/olap"
    assert "DishDiscountSumInt" in report["fields"]
    assert "password" not in rendered.lower()
    assert "token" not in rendered.lower()
    assert "database_url" not in rendered.lower()


def test_default_probe_date_uses_previous_calendar_day() -> None:
    assert default_probe_date(date(2026, 7, 18)) == date(2026, 7, 17)
