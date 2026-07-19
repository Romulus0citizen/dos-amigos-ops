from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.core.app.models.sales import (
    HermesReportOutbox,
    IikoSalesAutomationRun,
    IikoSalesDaily,
    IikoSalesDailyPayment,
    IikoSalesDailyProduct,
    IikoSalesSyncRun,
)
from apps.core.app.services.iiko_sales_automation import build_sales_daily_hermes_payload

ORG_ID = "department-1"
NOW = datetime(2026, 7, 17, tzinfo=UTC)


def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        IikoSalesSyncRun.__table__,
        IikoSalesDaily.__table__,
        IikoSalesDailyPayment.__table__,
        IikoSalesDailyProduct.__table__,
        IikoSalesAutomationRun.__table__,
        HermesReportOutbox.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_daily(
    session: Session,
    business_date: date,
    *,
    net_sales: Decimal,
    checks_count: int = 3,
    gross_sales: Decimal | None = None,
    reported_discounts: Decimal = Decimal("10.00"),
    status: str = "proven",
    reconciliation_error_code: str | None = None,
) -> None:
    average_check = Decimal("0") if checks_count == 0 else net_sales / checks_count
    session.add(
        IikoSalesDaily(
            organization_id=ORG_ID,
            business_date=business_date,
            currency_code="RUB",
            gross_sales=gross_sales or net_sales + reported_discounts,
            reported_discounts=reported_discounts,
            reported_increases=Decimal("0.00"),
            net_sales=net_sales,
            unexplained_adjustment=Decimal("0.00"),
            refunds=Decimal("0.00"),
            checks_count=checks_count,
            guests_count=None,
            average_check=average_check,
            source_rows_count=1,
            source_checksum=f"{business_date:%Y%m%d}".ljust(64, "0"),
            result_status=status,
            reconciliation_error_code=reconciliation_error_code,
            imported_at=NOW,
            updated_at=NOW,
        )
    )


def add_payment(
    session: Session,
    business_date: date,
    category: str,
    amount: Decimal,
) -> None:
    session.add(
        IikoSalesDailyPayment(
            organization_id=ORG_ID,
            business_date=business_date,
            payment_type_id=f"pay-{category}",
            payment_type_key=f"pay-{category}",
            payment_type_name=category.title(),
            payment_category=category,
            sales_amount=amount,
            refund_amount=Decimal("0.00"),
            transactions_count=1,
            imported_at=NOW,
            updated_at=NOW,
        )
    )


def add_product(
    session: Session,
    business_date: date,
    product_id: str,
    name: str,
    net_sales: Decimal,
    quantity: Decimal,
) -> None:
    session.add(
        IikoSalesDailyProduct(
            organization_id=ORG_ID,
            business_date=business_date,
            product_id=product_id,
            product_size_id=None,
            product_size_key="",
            product_name_snapshot=name,
            quantity=quantity,
            gross_sales=net_sales,
            discounts=Decimal("0.00"),
            net_sales=net_sales,
            refund_quantity=Decimal("0.0000"),
            refund_amount=Decimal("0.00"),
            imported_at=NOW,
            updated_at=NOW,
        )
    )


def test_daily_coo_report_includes_comparisons_payments_top_facts_and_attention() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        add_daily(
            session,
            date(2026, 7, 16),
            net_sales=Decimal("42850.00"),
            checks_count=35,
            gross_sales=Decimal("44059.75"),
            reported_discounts=Decimal("1209.75"),
            status="partial",
            reconciliation_error_code="IIKO_DISCOUNT_RECONCILIATION_MISMATCH",
        )
        add_daily(session, date(2026, 7, 15), net_sales=Decimal("40000.00"))
        add_daily(session, date(2026, 7, 9), net_sales=Decimal("50000.00"))
        add_payment(session, date(2026, 7, 16), "cash", Decimal("10000.00"))
        add_payment(session, date(2026, 7, 16), "card", Decimal("30000.00"))
        add_payment(session, date(2026, 7, 16), "other", Decimal("2000.00"))
        add_payment(session, date(2026, 7, 16), "unknown", Decimal("850.00"))
        add_product(
            session,
            date(2026, 7, 16),
            "p1",
            "Тако",
            Decimal("20000.00"),
            Decimal("14.0000"),
        )
        add_product(
            session,
            date(2026, 7, 16),
            "p2",
            "Буррито",
            Decimal("12000.00"),
            Decimal("2.5000"),
        )
        add_product(
            session,
            date(2026, 7, 16),
            "p3",
            "Кесадилья",
            Decimal("8000.00"),
            Decimal("2.0000"),
        )
        add_product(
            session,
            date(2026, 7, 16),
            "p4",
            "Сальса",
            Decimal("2850.00"),
            Decimal("1.0000"),
        )
        session.commit()

        payload = build_sales_daily_hermes_payload(
            session,
            ORG_ID,
            date(2026, 7, 16),
            markdown_top_products=3,
        )

    assert payload.payload_markdown.startswith("Dos Amigos — итоги 16.07.2026")
    assert "Выручка: 42 850 ₽" in payload.payload_markdown
    assert "Средний чек: 1 224,29 ₽" in payload.payload_markdown
    assert "Скидки: 1 209,75 ₽" in payload.payload_markdown
    assert "К предыдущему дню: +7,1 %" in payload.payload_markdown
    assert "К тому же дню прошлой недели: -14,3 %" in payload.payload_markdown
    assert "— наличные: 10 000 ₽" in payload.payload_markdown
    assert "— карта: 30 000 ₽" in payload.payload_markdown
    assert "— другие: 2 850 ₽" in payload.payload_markdown
    assert "1. Тако — 14 шт. / 20 000 ₽" in payload.payload_markdown
    assert "2. Буррито — 2,5 шт. / 12 000 ₽" in payload.payload_markdown
    assert "3. Кесадилья" in payload.payload_markdown
    assert "4. Сальса" not in payload.payload_markdown
    assert "Факты:" in payload.payload_markdown
    assert "Выручка выросла к предыдущему дню на 7,1 %." in payload.payload_markdown
    assert "Выручка снизилась к тому же дню прошлой недели на 14,3 %." in payload.payload_markdown
    assert "Выручка за день" not in payload.payload_markdown
    assert "Чеков за день" not in payload.payload_markdown
    assert "Требует внимания:" in payload.payload_markdown
    assert "result_status=partial" not in payload.payload_markdown
    assert "IIKO_DISCOUNT_RECONCILIATION_MISMATCH" not in payload.payload_markdown
    assert "Данные импортированы частично" not in payload.payload_markdown
    assert "проверьте полноту выгрузки" not in payload.payload_markdown
    assert (
        "В iiko обнаружено расхождение между суммой скидок и итоговой выручкой. "
        "Продажи загружены, но показатель скидок требует сверки."
    ) in payload.payload_markdown
    assert payload.payload_json["comparison_previous_day"] == "+7,1 %"
    assert payload.payload_json["comparison_same_weekday_previous_week"] == "-14,3 %"


def test_daily_coo_report_says_no_data_when_comparisons_are_missing() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        add_daily(session, date(2026, 7, 16), net_sales=Decimal("150.00"))
        session.commit()

        payload = build_sales_daily_hermes_payload(session, ORG_ID, date(2026, 7, 16))

    assert "К предыдущему дню: нет данных для сравнения" in payload.payload_markdown
    assert "К тому же дню прошлой недели: нет данных для сравнения" in payload.payload_markdown
    assert payload.payload_json["comparison_previous_day"] == "нет данных для сравнения"
    assert (
        payload.payload_json["comparison_same_weekday_previous_week"] == "нет данных для сравнения"
    )


def test_daily_coo_report_handles_zero_comparison_sales_without_division_error() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        add_daily(session, date(2026, 7, 16), net_sales=Decimal("150.00"))
        add_daily(session, date(2026, 7, 15), net_sales=Decimal("0.00"))
        session.commit()

        payload = build_sales_daily_hermes_payload(session, ORG_ID, date(2026, 7, 16))

    assert "К предыдущему дню: нет данных для сравнения" in payload.payload_markdown
