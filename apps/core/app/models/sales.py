from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.app.models.base import Base


def _uuid_string() -> str:
    return str(uuid4())


class IikoSalesSyncRun(Base):
    __tablename__ = "iiko_sales_sync_runs"
    __table_args__ = (
        Index("ix_iiko_sales_sync_runs_org_dates", "organization_id", "date_from", "date_to"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_string)
    organization_id: Mapped[str] = mapped_column(String(120), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    dataset: Mapped[str] = mapped_column(String(100), nullable=False, default="orders_or_sales")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    persisted_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_checksum: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class IikoSalesDaily(Base):
    __tablename__ = "iiko_sales_daily"
    __table_args__ = (
        UniqueConstraint("organization_id", "business_date", name="uq_iiko_sales_daily_org_date"),
    )

    organization_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    gross_sales: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    reported_discounts: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    reported_increases: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    net_sales: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    unexplained_adjustment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    refunds: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    checks_count: Mapped[int] = mapped_column(Integer, nullable=False)
    guests_count: Mapped[int | None] = mapped_column(Integer)
    average_check: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    source_rows_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    result_status: Mapped[str] = mapped_column(String(30), nullable=False)
    reconciliation_error_code: Mapped[str | None] = mapped_column(String(120))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IikoSalesDailyPayment(Base):
    __tablename__ = "iiko_sales_daily_payments"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "business_date",
            "payment_type_key",
            name="uq_iiko_sales_daily_payment_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_string)
    organization_id: Mapped[str] = mapped_column(String(120), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_type_id: Mapped[str | None] = mapped_column(String(120))
    payment_type_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_type_name: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_category: Mapped[str] = mapped_column(String(30), nullable=False)
    sales_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    transactions_count: Mapped[int | None] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IikoSalesDailyProduct(Base):
    __tablename__ = "iiko_sales_daily_products"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "business_date",
            "product_id",
            "product_size_key",
            name="uq_iiko_sales_daily_product_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_string)
    organization_id: Mapped[str] = mapped_column(String(120), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    product_id: Mapped[str] = mapped_column(String(120), nullable=False)
    product_size_id: Mapped[str | None] = mapped_column(String(120))
    product_size_key: Mapped[str] = mapped_column(String(120), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    gross_sales: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    discounts: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    net_sales: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    refund_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
