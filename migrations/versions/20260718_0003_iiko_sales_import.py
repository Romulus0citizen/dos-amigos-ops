"""Add read-only iiko sales import tables.

Revision ID: 20260718_0003
Revises: 20260717_0002
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0003"
down_revision: str | None = "20260717_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "iiko_sales_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=120), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("dataset", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_rows", sa.Integer(), nullable=False),
        sa.Column("persisted_rows", sa.Integer(), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message_redacted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iiko_sales_sync_runs_org_dates",
        "iiko_sales_sync_runs",
        ["organization_id", "date_from", "date_to"],
    )

    op.create_table(
        "iiko_sales_daily",
        sa.Column("organization_id", sa.String(length=120), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("gross_sales", sa.Numeric(18, 2), nullable=False),
        sa.Column("reported_discounts", sa.Numeric(18, 2), nullable=False),
        sa.Column("reported_increases", sa.Numeric(18, 2), nullable=False),
        sa.Column("net_sales", sa.Numeric(18, 2), nullable=False),
        sa.Column("unexplained_adjustment", sa.Numeric(18, 2), nullable=False),
        sa.Column("refunds", sa.Numeric(18, 2), nullable=False),
        sa.Column("checks_count", sa.Integer(), nullable=False),
        sa.Column("guests_count", sa.Integer(), nullable=True),
        sa.Column("average_check", sa.Numeric(18, 2), nullable=False),
        sa.Column("source_rows_count", sa.Integer(), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("result_status", sa.String(length=30), nullable=False),
        sa.Column("reconciliation_error_code", sa.String(length=120), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "business_date"),
        sa.UniqueConstraint(
            "organization_id",
            "business_date",
            name="uq_iiko_sales_daily_org_date",
        ),
    )

    op.create_table(
        "iiko_sales_daily_payments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=120), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("payment_type_id", sa.String(length=120), nullable=True),
        sa.Column("payment_type_key", sa.String(length=255), nullable=False),
        sa.Column("payment_type_name", sa.String(length=255), nullable=False),
        sa.Column("payment_category", sa.String(length=30), nullable=False),
        sa.Column("sales_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("refund_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("transactions_count", sa.Integer(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "business_date",
            "payment_type_key",
            name="uq_iiko_sales_daily_payment_key",
        ),
    )

    op.create_table(
        "iiko_sales_daily_products",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=120), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("product_id", sa.String(length=120), nullable=False),
        sa.Column("product_size_id", sa.String(length=120), nullable=True),
        sa.Column("product_size_key", sa.String(length=120), nullable=False),
        sa.Column("product_name_snapshot", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("gross_sales", sa.Numeric(18, 2), nullable=False),
        sa.Column("discounts", sa.Numeric(18, 2), nullable=False),
        sa.Column("net_sales", sa.Numeric(18, 2), nullable=False),
        sa.Column("refund_quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("refund_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "business_date",
            "product_id",
            "product_size_key",
            name="uq_iiko_sales_daily_product_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("iiko_sales_daily_products")
    op.drop_table("iiko_sales_daily_payments")
    op.drop_table("iiko_sales_daily")
    op.drop_index("ix_iiko_sales_sync_runs_org_dates", table_name="iiko_sales_sync_runs")
    op.drop_table("iiko_sales_sync_runs")
